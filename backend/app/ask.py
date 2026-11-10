"""'Ask about this invoice' - answers questions about one run.

With an LLM key (OpenAI or Claude) the model answers, but only from the run's own record (extracted
fields, every check, the decision, PO / receipt data) which is passed as context; it is told not to invent.
Without a key, a rule-based answerer matches the question to the relevant checks, so the feature still works.
"""
from __future__ import annotations

import json
import re

from . import config
from .config import load_reference
from .extraction import llm_provider

SYSTEM = """You are an accounts-payable assistant embedded in an invoice-processing tool.
Answer questions about ONE invoice run using ONLY the JSON record provided. Rules:
- Ground every statement in the record (checks, extracted fields, decision, PO data). Quote amounts exactly.
- If the record does not contain the answer, say so plainly. Never invent facts, policies, or numbers.
- Be concise: 2-6 sentences or a short list. Plain language for a finance user.
- You cannot change the decision; point the user to the review actions if they ask to approve/reject."""


def _context(run: dict) -> dict:
    ex = {k: v for k, v in (run.get("extracted") or {}).items()
          if k not in ("raw_text_excerpt", "field_boxes")}
    for li in ex.get("line_items", []):
        li.pop("box", None)
    ref = load_reference()
    po = next((p for p in ref["purchase_orders"] if p["po_number"] == run.get("po_number")), None)
    vendor = next((v for v in ref["vendors"] if v["vendor_id"] == run.get("vendor_id")), None)
    if vendor:
        vendor = {**vendor, "bank": {**vendor["bank"], "account_number": "••••" + str(vendor["bank"]["account_number"])[-4:]}}
    return {
        "file": run.get("filename"), "status": run.get("status"), "decision": run.get("decision_detail"),
        "review": {"status": run.get("review_status"), "by": run.get("reviewer"), "note": run.get("review_note")},
        "extracted": ex, "vendor_master": vendor, "purchase_order": po,
        "checks": [{"stage": s["name"], "label": c["label"], "status": c["status"], "message": c["message"],
                    "outcome": c.get("outcome"), "route": c.get("route")} for s in run.get("stages", []) for c in s["checks"]],
    }


def _llm_answer(question: str, history: list[dict], ctx: dict) -> tuple[str, str]:
    record = json.dumps(ctx, default=str)[:60000]
    msgs = [*(h for h in history[-6:] if h.get("role") in ("user", "assistant") and h.get("content"))]
    user = f"Invoice run record (JSON):\n{record}\n\nQuestion: {question}"
    if llm_provider() == "openai":
        from openai import OpenAI
        kw = {"api_key": config.OPENAI_API_KEY}
        if config.OPENAI_BASE_URL:
            kw["base_url"] = config.OPENAI_BASE_URL
        r = OpenAI(**kw).chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "system", "content": SYSTEM}, *msgs, {"role": "user", "content": user}])
        return r.choices[0].message.content or "", config.OPENAI_MODEL
    import anthropic
    r = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY).messages.create(
        model=config.LLM_MODEL, max_tokens=2000, output_config={"effort": "low"}, system=SYSTEM,
        messages=[*msgs, {"role": "user", "content": user}])
    if r.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return "".join(b.text for b in r.content if b.type == "text"), config.LLM_MODEL


TOPICS = [
    (r"\b(why|reason|held|hold|review|reject|flag|problem|issue|wrong)\b", None),
    (r"\b(bank|account|iban|routing|fraud|remit|wire)\b", ("bank_account", "routing", "sender")),
    (r"\b(duplicate|again|already|resubmit|same invoice)\b", ("invoice_number_dup", "file_hash", "amount_window")),
    (r"\b(po|purchase order|order)\b", ("po_exists", "po_inference", "po_vendor", "po_status", "po_balance", "po_currency")),
    (r"\b(receiv|deliver|grn|quantity|qty|units?)\b", ("qty_grn", "qty_po")),
    (r"\b(price|cost|rate|variance|tolerance|more than)\b", ("price",)),
    (r"\b(tax|vat|gst)\b", ("tax_vs_po", "tax_rate", "total_math")),
    (r"\b(date|old|due|ambiguous)\b", ("date", "date_stale", "date_future", "due_date", "date_ambiguous", "learned_date")),
    (r"\b(currency|fx|euro|eur|usd|convert|exchange)\b", ("currency", "fx", "po_currency")),
    (r"\b(vendor|supplier|suspend)\b", ("vendor_master", "vendor_status", "tax_id", "learned_alias")),
    (r"\b(email|sender|domain)\b", ("sender",)),
    (r"\b(confiden|extract|ocr|read|scan)\b", ("field_confidence", "ocr", "cross_check")),
]


def _rule_answer(question: str, ctx: dict) -> str:
    q = question.lower()
    dec = ctx.get("decision") or {}
    checks = ctx["checks"]
    amounts = dec.get("amounts") or {}
    cur = dec.get("currency") or "USD"

    if re.search(r"\b(pay|paid|payment|how much|amount|release)\b", q):
        parts = []
        if dec.get("decision") == "APPROVED" and dec.get("payment"):
            p = dec["payment"]
            parts.append(f"Approved for the full {cur} {amounts.get('invoice_total', 0):,.2f}; payment is scheduled for "
                         f"{p['scheduled_for']} ({p['terms']}) to {p['to_account']}.")
        elif amounts.get("recommended_payment") is not None:
            parts.append(f"{cur} {amounts['recommended_payment']:,.2f} can be paid now; {cur} {amounts.get('on_hold') or 0:,.2f} is on hold.")
        else:
            parts.append(f"Nothing is payable yet: the invoice is {dec.get('decision', 'not decided').replace('_', ' ').lower()}.")
        if dec.get("fx"):
            parts.append(f"In USD that is about {dec['fx'].get('base_total'):,.2f} at {dec['fx'].get('rate')}.")
        return " ".join(parts)

    if re.search(r"\b(next|what (should|do) (i|we)|action|todo|to do)\b", q):
        acts = dec.get("actions") or []
        return "Next actions:\n" + "\n".join(f"{i + 1}. {a}" for i, a in enumerate(acts)) if acts else "No actions recorded."

    wanted: set[str] | None = set()
    for pattern, ids in TOPICS:
        if re.search(pattern, q):
            if ids is None:
                wanted = None  # 'why' -> all non-passing checks
                break
            wanted |= set(ids)
    if wanted is None or not wanted:
        bad = [c for c in checks if c["status"] in ("fail", "warn") and c.get("id") != "policy"]
        head = dec.get("headline", "")
        if not bad:
            return f"{head} All checks passed." if head else "All checks passed."
        return head + "\n\n" + "\n".join(f"• {c['label']} ({c['stage']}): {c['message']}" for c in bad[:6])
    rel = [c for c in checks if any(c["label"] and (c.get("id") == w) for w in wanted)]
    if not rel:  # checks carry no id in the context; match by label keywords instead
        rel = [c for c in checks if any(w.split("_")[0] in (c["label"] or "").lower() for w in wanted)]
    if not rel:
        return "I couldn't find anything about that in this run's checks. Try asking 'why was this held?'"
    return "\n".join(f"• {c['label']} — {c['status'].upper()}: {c['message']}" for c in rel[:6])


def answer(run: dict, question: str, history: list[dict]) -> dict:
    ctx = _context(run)
    # keep check ids for the rule engine
    i = 0
    for s in run.get("stages", []):
        for c in s["checks"]:
            ctx["checks"][i]["id"] = c.get("id")
            i += 1
    if llm_provider():
        try:
            text, model = _llm_answer(question, history, ctx)
            return {"answer": text.strip(), "mode": "llm", "model": model}
        except Exception as e:
            fallback = _rule_answer(question, ctx)
            return {"answer": fallback, "mode": "rules", "model": None, "note": f"LLM unavailable ({type(e).__name__}); rule-based answer"}
    return {"answer": _rule_answer(question, ctx), "mode": "rules", "model": None}
