"""The invoice pipeline: eight visible stages from PDF to decision.

Design principle: *the model reads, the rules decide.* Extraction may use OCR or an LLM,
but every decision below is deterministic, explainable and driven by configurable
tolerances, so the same invoice always gets the same answer and an auditor can see why.
"""
from __future__ import annotations

import hashlib
import re
import time
import traceback
from datetime import date, datetime, timedelta
from typing import Any, Callable

from rapidfuzz import fuzz

from . import db, memory, notify
from . import rules as custom_rules
from .config import load_reference
from .extraction import extract, inspect_pdf, normalize_po, today

STAGES = [
    ("intake", "Intake", "Fingerprint the file and inspect the PDF"),
    ("extract", "Extract", "Read header fields, line items, totals and bank details"),
    ("validate", "Validate", "Completeness, arithmetic and date sanity"),
    ("vendor", "Vendor check", "Match vendor master, status and remit-to bank account"),
    ("duplicate", "Duplicate check", "Compare against ledger and previously processed files"),
    ("po_match", "PO match", "Find the purchase order (explicit or inferred)"),
    ("three_way", "3-way match", "Invoice vs PO vs goods receipt, line by line, incl. prior billing"),
    ("decision", "Decision", "Apply policy and produce a reasoned outcome"),
]

ROUTE_PRIORITY = ["Treasury & Security", "Procurement", "Receiving", "AP Specialist", "Finance Manager"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def check(cid: str, label: str, status: str, message: str, outcome: str | None = None,
          route: str | None = None, severity: str = "low", **extra) -> dict:
    return {"id": cid, "label": label, "status": status, "message": message, "outcome": outcome,
            "route": route, "severity": severity, **extra}


SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "C$"}
_CUR = {"code": "USD"}  # currency of the invoice being processed (runs are serialised)


def money(v: float | None, cur: str | None = None) -> str:
    if v is None:
        return "n/a"
    code = cur or _CUR["code"]
    sym = SYMBOLS.get(code)
    return f"{sym}{v:,.2f}" if sym else f"{code} {v:,.2f}"


def _domain(addr: str | None) -> str:
    return (addr or "").split("@")[-1].strip().lower().strip(">")


def _root(domain: str) -> str:
    """'mail.bluepeak.example' -> 'bluepeak'."""
    parts = [p for p in domain.split(".") if p]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def _clean_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9& ]", " ", (s or "").lower())
    s = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|company|the|and)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _core_number(s: str | None) -> str:
    """Longest digit run, leading zeros stripped: 'INV-NW-10482' and 'NW10482' -> '10482'."""
    runs = re.findall(r"\d+", s or "")
    return max(runs, key=len).lstrip("0") if runs else ""


def _d(s: str | None) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# invoice types: credit note, prepayment/deposit, standard invoice
# --------------------------------------------------------------------------- #
CREDIT_RE = re.compile(r"\b(credit\s*(?:note|memo)|gutschrift)\b", re.I)
PREPAY_RE = re.compile(r"\b(deposit\s*invoice|advance\s*payment|pre-?payment|down\s*payment|milestone\s*(?:billing|payment)"
                       r"|progress\s*(?:billing|payment|claim)|deposit\s*of\s*\d+\s*%|\d+\s*%\s*deposit)\b", re.I)
ORIG_INV_RE = re.compile(r"\b(?:original\s*invoice|against\s*invoice|credit\s*for\s*invoice|relates\s*to\s*invoice|reference\s*invoice)"
                         r"\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/_.]{3,})", re.I)
DISCOUNT_RES = [
    re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*/\s*(\d+)\s*,?\s*net\s*(\d+)", re.I),                                   # 2/10 Net 30
    re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:discount\s*)?(?:if\s*paid\s*)?(?:within|in)\s*(\d+)\s*days", re.I),   # 2% within 10 days
]


def _doc_text(inv: dict) -> str:
    parts = [inv.get("raw_text_excerpt") or ""]
    parts += [c.get("evidence", "") for c in inv.get("cues", [])]
    parts += [i.get("description", "") for i in inv.get("line_items", [])]
    return "\n".join(parts)


def classify(inv: dict) -> str:
    """'credit_note' | 'prepayment' | 'invoice'. A negative total is always a credit."""
    text = _doc_text(inv)
    if (inv.get("total") is not None and inv["total"] < 0) or CREDIT_RE.search(text):
        return "credit_note"
    if PREPAY_RE.search(text):
        return "prepayment"
    return "invoice"


def parse_discount(*texts: str | None) -> dict | None:
    """'2/10 Net 30' or '2% within 10 days' -> {pct, days, net, text}."""
    for t in texts:
        for rx in DISCOUNT_RES:
            m = rx.search(t or "")
            if m:
                pct, days = float(m.group(1)), int(m.group(2))
                net = int(m.group(3)) if m.lastindex and m.lastindex >= 3 else None
                if 0 < pct < 20 and 0 < days <= 60:
                    return {"pct": pct, "days": days, "net": net, "text": m.group(0).strip()}
    return None


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
class Run:
    def __init__(self, run_id: str, path: str, filename: str):
        self.id = run_id
        self.path = path
        self.filename = filename
        self.settings = db.get_settings()
        self.ref = load_reference()
        self.vendors = {v["vendor_id"]: v for v in self.ref["vendors"]}
        self.pos = {p["po_number"]: p for p in self.ref["purchase_orders"]}
        self.stages = [{"key": k, "name": n, "description": d, "status": "pending", "summary": "",
                        "checks": [], "logs": [], "data": {}, "started_at": None, "duration_ms": None}
                       for k, n, d in STAGES]
        self.ctx: dict[str, Any] = {}
        self.t0 = time.perf_counter()

    # ----- persistence of live progress
    def _flush(self, **extra):
        db.update_run(self.id, stages_json=self.stages, **extra)

    def stage(self, key: str) -> dict:
        return next(s for s in self.stages if s["key"] == key)

    def run_stage(self, key: str, fn: Callable[[dict, Callable[[str], None]], None]):
        st = self.stage(key)
        st["status"] = "running"
        st["started_at"] = db.now_iso()
        self._flush()
        t = time.perf_counter()

        def log(msg: str):
            st["logs"].append({"t": round((time.perf_counter() - t) * 1000), "msg": msg})
            self._flush()

        fn(st, log)
        elapsed = (time.perf_counter() - t) * 1000
        pace = self.settings.get("stage_min_ms", 0) or 0
        if elapsed < pace:
            time.sleep((pace - elapsed) / 1000)
        st["duration_ms"] = round(elapsed)
        if st["status"] == "running":
            # red only when something blocks payment; fixable issues show amber
            blocking = any(c["status"] == "fail" and c["outcome"] == "reject" for c in st["checks"])
            issues = any(c["status"] in ("fail", "warn") for c in st["checks"])
            st["status"] = "failed" if blocking else "warning" if issues else "passed"
        self._flush()

    def skip_rest(self, after: str, reason: str):
        hit = False
        for st in self.stages:
            if hit and st["status"] == "pending" and st["key"] != "decision":
                st["status"] = "skipped"
                st["summary"] = reason
            if st["key"] == after:
                hit = True

    def all_checks(self) -> list[dict]:
        return [dict(c, stage=s["key"]) for s in self.stages for c in s["checks"]]

    # ----- main
    def execute(self):
        db.update_run(self.id, status="running", stages_json=self.stages)
        db.audit(self.id, "system", "run_started", self.filename)
        _CUR["code"] = "USD"
        row = db.get_run(self.id) or {}
        self.ctx["email"] = db.get_email(row["email_id"]) if row.get("email_id") else None
        try:
            self.run_stage("intake", self.s_intake)
            self.run_stage("extract", self.s_extract)
            self.run_stage("validate", self.s_validate)
            self.run_stage("vendor", self.s_vendor)
            if self.ctx.get("stop"):
                self.skip_rest("vendor", self.ctx["stop"])
            else:
                self.run_stage("duplicate", self.s_duplicate)
                self.run_stage("po_match", self.s_po_match)
                self.run_stage("three_way", self.s_three_way)
            self.run_stage("decision", self.s_decision)
            dec = self.ctx["decision"]
            inv = self.ctx.get("inv", {})
            vendor = self.ctx.get("vendor")
            db.update_run(
                self.id, status="completed", decision=dec["decision"], route_to=dec.get("route_to"),
                vendor_id=vendor["vendor_id"] if vendor else None,
                vendor_name=vendor["name"] if vendor else inv.get("vendor_name"),
                invoice_number=inv.get("invoice_number"),
                po_number=(self.ctx.get("po") or {}).get("po_number") or inv.get("po_number"),
                total=inv.get("total"), currency=inv.get("currency") or "USD",
                duration_ms=round((time.perf_counter() - self.t0) * 1000),
                decision_json=dec, stages_json=self.stages,
                review_status="pending" if dec["decision"] == "NEEDS_REVIEW" else None,
            )
            if dec["decision"] == "APPROVED":
                db.post_to_ledger(self.id, self.ledger_entry())
                db.audit(self.id, "system", "posted_to_ledger", f"{inv.get('invoice_number')} {money(inv.get('total'))}")
            db.audit(self.id, "system", "decision", f"{dec['decision']}: {dec['headline']}")
            fx = self.ctx.get("fx") or {}
            due = None
            try:
                due = notify.on_decision(self.id, dec, inv, vendor)
            except Exception as e:  # notifications must never break a decision
                db.audit(self.id, "system", "notify_failed", str(e))
            db.update_run(self.id, base_total=fx.get("base_total"), fx_rate=fx.get("rate"), sla_due_at=due)
            if due:
                db.audit(self.id, "system", "routed", f"{dec['route_to']} notified · SLA due {due}")
            learned = memory.learn(self.id, inv, vendor, lambda m: None)
            for item in learned:
                db.audit(self.id, "system", "learned", item)
            if learned:
                dec["learned"] = learned
                db.update_run(self.id, decision_json=dec)
        except Exception as e:  # pragma: no cover - surfaced in UI
            for st in self.stages:
                if st["status"] == "running":
                    st["status"] = "failed"
                    st["logs"].append({"t": 0, "msg": f"Error: {e}"})
            db.update_run(self.id, status="failed", error=f"{type(e).__name__}: {e}", stages_json=self.stages,
                          duration_ms=round((time.perf_counter() - self.t0) * 1000))
            db.audit(self.id, "system", "run_failed", traceback.format_exc()[-1500:])

    def ledger_entry(self) -> dict:
        inv = self.ctx["inv"]
        return {
            "invoice_number": inv.get("invoice_number"), "vendor_id": self.ctx["vendor"]["vendor_id"],
            "po_number": (self.ctx.get("po") or {}).get("po_number"), "invoice_date": inv.get("invoice_date"),
            "subtotal": inv.get("subtotal"), "tax": inv.get("tax_amount"), "total": inv.get("total"),
            "status": "approved",
            "lines": [{"po_line": m["po_line"], "qty": m["qty"], "amount": m["amount"], **({"kind": m["kind"]} if m.get("kind") else {})}
                      for m in self.ctx.get("line_matches", []) if m.get("po_line") or m.get("kind")]
                     + ([{"po_line": None, "qty": 0, "amount": self.ctx["deposit_offset"], "kind": "deposit_applied"}]
                        if self.ctx.get("deposit_offset") else []),
        }

    # ======================================================================= #
    # Stage 1 - intake
    # ======================================================================= #
    def s_intake(self, st, log):
        raw = open(self.path, "rb").read()
        h = hashlib.sha256(raw).hexdigest()
        info = inspect_pdf(self.path)
        self.ctx["hash"] = h
        self.ctx["pdf"] = info
        db.update_run(self.id, file_hash=h)
        log(f"SHA-256 {h[:16]}…  ({len(raw) / 1024:.1f} KB)")
        log(f"{info['page_count']} page(s); text layer: {'yes' if info['has_text_layer'] else 'no'}; embedded images: {info['images']}")
        st["data"] = {"sha256": h, "size_kb": round(len(raw) / 1024, 1), **info}
        kind = "machine-readable PDF" if info["has_text_layer"] else "scanned image (no text layer)"
        st["checks"].append(check("pdf_readable", "PDF readable", "pass", f"{info['page_count']} page(s), {kind}"))
        if info["encrypted"]:
            st["checks"].append(check("pdf_encrypted", "Encrypted PDF", "fail", "File is password protected",
                                      "reject", "Vendor", "high"))
        st["summary"] = kind

    # ======================================================================= #
    # Stage 2 - extraction
    # ======================================================================= #
    def s_extract(self, st, log):
        inv = extract(self.path, self.ctx["pdf"]["has_text_layer"], log)
        overrides = (db.get_run(self.id) or {}).get("overrides") or {}
        if overrides:
            inv["corrected_fields"] = {}
            for k, v in overrides.items():
                inv["corrected_fields"][k] = {"was": inv.get(k), "now": v}
                inv[k] = v
                inv.setdefault("field_confidence", {})[k] = 1.0
            log(f"Applied reviewer corrections: {', '.join(f'{k} → {v}' for k, v in overrides.items())}")
            st["checks"].append(check("corrected", "Reviewer corrections", "info",
                                      "Human-verified values used for " + ", ".join(overrides)))
        for note in memory.apply(inv, overrides, log):
            st["checks"].append(check(note["id"], note["label"], note["status"], note["message"]))
        if not inv.get("currency"):
            inv["currency_assumed"] = True
        self.ctx["inv"] = inv
        db.update_run(self.id, extracted_json=inv, extraction_method=inv["method"],
                      invoice_number=inv.get("invoice_number"), total=inv.get("total"),
                      vendor_name=inv.get("vendor_name"))
        fc = inv.get("field_confidence", {})
        found = [k for k in ("vendor_name", "invoice_number", "invoice_date", "po_number", "subtotal", "tax_amount", "total")
                 if inv.get(k) not in (None, "")]
        log(f"Found {len(found)}/7 header fields and {len(inv.get('line_items', []))} line item(s)")
        min_c = self.settings["min_field_confidence"]
        low = {k: fc[k] for k in ("invoice_number", "total", "invoice_date", "vendor_name") if k in fc and fc[k] < min_c}
        if low:
            st["checks"].append(check("field_confidence", "Extraction confidence", "warn",
                                      "Low confidence on " + ", ".join(f"{k} ({v:.0%})" for k, v in low.items()),
                                      "review", "AP Specialist", "medium"))
        else:
            avg = sum(fc.values()) / len(fc) if fc else 0
            st["checks"].append(check("field_confidence", "Extraction confidence", "pass",
                                      f"Average field confidence {avg:.0%} (threshold {min_c:.0%})"))
        if inv["method"] == "ocr":
            st["checks"].append(check("ocr", "OCR quality", "pass" if inv.get("ocr_confidence", 0) >= 0.85 else "warn",
                                      f"Scanned document read with local OCR, mean confidence {inv.get('ocr_confidence', 0):.0%}",
                                      None if inv.get("ocr_confidence", 0) >= 0.85 else "review", "AP Specialist"))
        cc = inv.get("cross_check")
        if cc and cc["differences"]:
            st["checks"].append(check("cross_check", "LLM vs text-layer cross-check", "warn",
                                      "; ".join(f"{d['field']}: model={d['llm']} vs text={d['text_layer']}" for d in cc["differences"]),
                                      "review", "AP Specialist", "medium"))
        elif cc:
            st["checks"].append(check("cross_check", "LLM vs text-layer cross-check", "pass",
                                      "Model output agrees with the PDF text layer on all key fields"))
        for cue in inv.get("cues", []):
            st["checks"].append(check(f"cue_{cue['type']}", "Document cue", "info", f"“{cue['evidence'].strip()}”"))
        st["data"] = {"method": inv["method"], "fields_found": found, "line_items": len(inv.get("line_items", [])),
                      "llm_usage": inv.get("llm_usage")}
        engine = inv.get("ocr_engine")
        provider = (inv.get("llm_usage") or {}).get("provider")
        st["summary"] = {"text": "Parsed text layer",
                         "ocr": "Read scan with " + ("Google Vision" if engine == "google-vision" else "local OCR"),
                         "llm": ("OpenAI vision" if provider == "openai" else "Claude") + " extraction"}[inv["method"]] \
            + f" · {len(found)}/7 fields"

    # ======================================================================= #
    # Stage 3 - validation
    # ======================================================================= #
    def s_validate(self, st, log):
        inv, S = self.ctx["inv"], self.settings
        C = st["checks"]
        missing = [label for key, label in (("invoice_number", "invoice number"), ("invoice_date", "invoice date"),
                                            ("vendor_name", "vendor name"), ("total", "total amount"))
                   if inv.get(key) in (None, "")]
        self.ctx["missing"] = missing
        if missing:
            stmt = any(c["type"] == "statement" for c in inv.get("cues", []))
            C.append(check("required_fields", "Required fields", "fail",
                           f"Missing {', '.join(missing)}" + (" — document looks like a statement, not a tax invoice" if stmt else ""),
                           "reject", "Vendor", "high"))
        else:
            C.append(check("required_fields", "Required fields", "pass", "Invoice number, date, vendor and total present"))

        doc_type = inv.get("doc_type") or classify(inv)
        inv["doc_type"] = doc_type
        self.ctx["doc_type"] = doc_type
        if doc_type == "credit_note":
            m = ORIG_INV_RE.search(_doc_text(inv))
            self.ctx["credit_ref"] = m.group(1) if m else None
            C.append(check("doc_type", "Document type", "info",
                           "Credit note: reduces what we owe the vendor; it is applied against payables, never paid out"
                           + (f" (credits invoice {m.group(1)})" if m else "")))
            log(f"Credit note detected{' for ' + m.group(1) if m else ''}")
        elif doc_type == "prepayment":
            C.append(check("doc_type", "Document type", "info", "Deposit / advance invoice: billed before delivery against the PO's agreed prepayment"))
            log("Prepayment (deposit) invoice detected")
        else:
            C.append(check("doc_type", "Document type", "pass", "Standard invoice"))

        items = inv.get("line_items", [])
        tol = max(S["amount_tolerance_abs"], 0.01)
        if items:
            bad = [i for i in items if i.get("qty") and i.get("unit_price") is not None
                   and abs(i["qty"] * i["unit_price"] - i["amount"]) > 0.02]
            C.append(check("line_math", "Line arithmetic", "fail" if bad else "pass",
                           f"{len(bad)} line(s) where qty × price ≠ amount" if bad else f"{len(items)} line(s): qty × unit price = amount",
                           "review" if bad else None, "AP Specialist", "medium"))
            lines_sum = round(sum(i["amount"] for i in items), 2)
            if inv.get("subtotal") is not None:
                diff = round(lines_sum - inv["subtotal"], 2)
                ok = abs(diff) <= 0.05
                C.append(check("subtotal", "Lines reconcile to subtotal", "pass" if ok else "fail",
                               f"Σ lines {money(lines_sum)} vs subtotal {money(inv['subtotal'])}" + ("" if ok else f" (Δ {money(diff)})"),
                               None if ok else "review", "AP Specialist", "medium"))
        else:
            C.append(check("line_items", "Line items", "warn", "No itemised lines found — invoice is bundled; matching falls back to header totals"))

        sub, tax, tot = inv.get("subtotal"), inv.get("tax_amount"), inv.get("total")
        if inv.get("tax_inclusive"):
            C.append(check("tax_mode", "Tax treatment", "info", "Total is tax-inclusive; tax shown for information"))
        if sub is not None and tot is not None and not inv.get("tax_inclusive"):
            calc = round(sub + (tax or 0), 2)
            ok = abs(calc - tot) <= 0.05
            C.append(check("total_math", "Subtotal + tax = total", "pass" if ok else "fail",
                           f"{money(sub)} + {money(tax or 0)} = {money(calc)} vs stated {money(tot)}",
                           None if ok else "review", "AP Specialist", "medium"))
        if sub and tax and inv.get("tax_rate"):
            eff = tax / sub
            ok = abs(eff - inv["tax_rate"]) < 0.005
            C.append(check("tax_rate", "Tax rate consistency", "pass" if ok else "warn",
                           f"Stated {inv['tax_rate']:.2%}, effective {eff:.2%}"))

        today_ = today()
        d = _d(inv.get("invoice_date"))
        if d:
            age = (today_ - d).days
            if d > today_ + timedelta(days=1):
                C.append(check("date_future", "Invoice date", "fail", f"Dated in the future ({d})", "review", "AP Specialist", "medium"))
            elif age > S["max_invoice_age_days"]:
                C.append(check("date_stale", "Invoice date", "warn", f"{age} days old (limit {S['max_invoice_age_days']})",
                               "review", "AP Specialist", "medium"))
            else:
                C.append(check("date", "Invoice date", "pass", f"{d.isoformat()} ({age} days ago)"))
            due = _d(inv.get("due_date"))
            if due and due < d:
                C.append(check("due_date", "Due date", "warn", "Due date precedes invoice date"))
        fails = sum(1 for c in C if c["status"] == "fail")
        st["summary"] = "All validations passed" if not fails else f"{fails} validation failure(s)"
        log(st["summary"])

    # ======================================================================= #
    # Stage 4 - vendor
    # ======================================================================= #
    def s_vendor(self, st, log):
        inv = self.ctx["inv"]
        C = st["checks"]
        name = inv.get("vendor_name") or ""
        tax_id = inv.get("vendor_tax_id")
        best, score, how = None, 0.0, ""
        if tax_id:
            for v in self.vendors.values():
                if v["tax_id"] == tax_id:
                    best, score, how = v, 100.0, "tax ID"
        if not best:
            learned = memory.learned_aliases()
            for v in self.vendors.values():
                for cand in [v["name"], *v.get("aliases", [])]:
                    s = fuzz.token_set_ratio(_clean_name(name), _clean_name(cand))
                    if s > score:
                        best, score, how = v, s, "name"
                for cand in learned.get(v["vendor_id"], []):
                    s = fuzz.token_set_ratio(_clean_name(name), _clean_name(cand))
                    if s > score:
                        best, score, how = v, s, "learned alias"
        if score < 85:  # last resort: look for any master name in the full text
            text = _clean_name(inv.get("raw_text_excerpt", ""))
            for v in self.vendors.values():
                if _clean_name(v["name"]) in text:
                    best, score, how = v, 86.0, "document text"
        if not best or score < 85:
            log(f"No vendor-master match for '{name}' (best score {score:.0f})")
            C.append(check("vendor_master", "Vendor master", "fail", f"'{name or 'unknown'}' is not an approved vendor",
                           "review", "Procurement", "high"))
            st["summary"] = "Unknown vendor"
            self.ctx["vendor"] = None
            self.ctx["stop"] = "Unknown vendor — no PO matching attempted"
            return
        v = best
        self.ctx["vendor"] = v
        log(f"Matched {v['vendor_id']} {v['name']} by {how} (score {score:.0f})")
        C.append(check("vendor_master", "Vendor master", "pass", f"{v['name']} ({v['vendor_id']}) matched by {how}, score {score:.0f}"))

        # ---- currency & FX
        rates = self.ref.get("fx_rates", {})
        base = self.ref["company"].get("base_currency", "USD")
        if inv.get("currency_assumed"):
            inv["currency"] = v.get("currency", base)
            C.append(check("currency", "Currency", "info", f"No currency printed; vendor master currency {inv['currency']} assumed"))
        elif inv.get("currency") != v.get("currency", base):
            C.append(check("currency", "Currency", "fail",
                           f"Invoice is in {inv['currency']} but {v['name']} is set up to bill in {v.get('currency', base)}",
                           "review", "AP Specialist", "medium"))
        else:
            C.append(check("currency", "Currency", "pass", f"{inv['currency']} matches vendor master"))
        _CUR["code"] = inv.get("currency") or base
        rate = rates.get("to_usd", {}).get(inv.get("currency") or base)
        if rate is None:
            C.append(check("fx", "FX rate", "fail", f"No {base} rate for {inv.get('currency')}", "review", "AP Specialist", "medium"))
            self.ctx["fx"] = {"rate": None, "base_total": None}
        else:
            base_total = round((inv.get("total") or 0) * rate, 2)
            self.ctx["fx"] = {"rate": rate, "base_total": base_total, "currency": inv.get("currency"), "as_of": rates.get("as_of")}
            if inv.get("currency") != base:
                log(f"FX: {inv.get('currency')} {inv.get('total'):,.2f} × {rate} = {base} {base_total:,.2f} (rates as of {rates.get('as_of')})")
                C.append(check("fx", "FX conversion", "info",
                               f"{money(inv.get('total'))} ≈ {money(base_total, base)} at {rate} ({rates.get('source')}, {rates.get('as_of')}); "
                               f"approval limits apply to the {base} amount"))

        # ---- email sender (when the invoice arrived by email)
        em = self.ctx.get("email")
        if em and em.get("from_addr"):
            sd, vd = _domain(em["from_addr"]), _domain(v.get("email"))
            if sd == vd:
                C.append(check("sender", "Email sender", "pass", f"Sent from {em['from_addr']} — matches vendor domain {vd}"))
            else:
                sim = fuzz.ratio(_root(sd), _root(vd))
                lookalike = sim >= 70 or (_root(vd) and _root(vd) in sd)
                if lookalike:
                    C.append(check("sender", "Email sender", "fail",
                                   f"Sent from {em['from_addr']}: look-alike of the vendor's real domain {vd} "
                                   f"(similarity {sim:.0f}%). Typical of invoice-redirect fraud.",
                                   "review", "Treasury & Security", "critical"))
                    self.ctx["spoofed_sender"] = True
                else:
                    C.append(check("sender", "Email sender", "warn",
                                   f"Sent from {em['from_addr']}, not the vendor's domain {vd} (could be a forward or personal mailbox)",
                                   "review", "AP Specialist", "medium"))
        if tax_id and tax_id != v["tax_id"]:
            C.append(check("tax_id", "Tax ID", "fail", f"Invoice shows {tax_id}, master has {v['tax_id']}",
                           "review", "Procurement", "high"))
        elif tax_id:
            C.append(check("tax_id", "Tax ID", "pass", f"{tax_id} matches master"))
        if v["status"] != "approved":
            C.append(check("vendor_status", "Vendor status", "fail",
                           f"Vendor is {v['status'].upper()}. {v.get('status_reason', '')}".strip(),
                           "reject", "Procurement", "critical"))
            self.ctx["stop"] = f"Vendor {v['status']} — payment blocked"
        else:
            C.append(check("vendor_status", "Vendor status", "pass", f"Approved · {v['payment_terms']}"))

        bank = inv.get("bank") or {}
        acct = _digits(bank.get("account_number"))
        master = v["bank"]
        cues = {c["type"] for c in inv.get("cues", [])} | (
            {"bank_change"} if any(re.search(r"bank", c["evidence"], re.I) and re.search(r"chang|new|updat", c["evidence"], re.I)
                                   for c in inv.get("cues", []) if c["type"] == "llm") else set())
        if acct and acct != _digits(master["account_number"]):
            sev_msg = (f"Remit-to account ••••{acct[-4:]} at {bank.get('bank_name') or 'unknown bank'} does not match "
                       f"master ••••{master['account_number'][-4:]} at {master['bank_name']}")
            if "bank_change" in cues:
                sev_msg += ". Invoice text announces a bank change"
            if "urgency" in cues:
                sev_msg += " and pressures for fast payment"
            C.append(check("bank_account", "Remit-to bank account", "fail", sev_msg + " — classic payment-fraud pattern.",
                           "review", "Treasury & Security", "critical"))
            self.ctx["bank_mismatch"] = True
        elif acct:
            C.append(check("bank_account", "Remit-to bank account", "pass",
                           f"••••{acct[-4:]} matches vendor master ({master['bank_name']})"))
        else:
            C.append(check("bank_account", "Remit-to bank account", "info", "No bank details on invoice; master account will be used"))
        if bank.get("routing_number") and bank["routing_number"] != master["routing_number"] and not self.ctx.get("bank_mismatch"):
            C.append(check("routing", "Routing number", "fail", "Routing number differs from vendor master",
                           "review", "Treasury & Security", "critical"))
        st["summary"] = f"{v['name']} · {v['status']}"

    # ======================================================================= #
    # Stage 5 - duplicates
    # ======================================================================= #
    def s_duplicate(self, st, log):
        inv, v, S = self.ctx["inv"], self.ctx["vendor"], self.settings
        C = st["checks"]
        prior_runs = db.runs_with_hash(self.ctx["hash"], self.id)
        posted = [r for r in prior_runs if r["ledger_posted"]]
        if posted:
            C.append(check("file_hash", "Identical file", "fail",
                           f"This exact file was already processed and posted (run {posted[0]['id'][:8]}, {posted[0]['created_at'][:10]})",
                           "reject", "Vendor", "high", match_run=posted[0]["id"]))
        elif prior_runs:
            C.append(check("file_hash", "Identical file", "info",
                           f"Same file processed {len(prior_runs)} time(s) before but never posted"))
        else:
            C.append(check("file_hash", "Identical file", "pass", "File fingerprint not seen before"))

        entries = db.ledger_entries(vendor_id=v["vendor_id"])
        log(f"Comparing against {len(entries)} ledger invoice(s) for {v['name']}")
        num = inv.get("invoice_number")
        norm, core = db.norm_invoice_number(num), _core_number(num)
        d = _d(inv.get("invoice_date"))
        hit = None
        for e in entries:
            same_amt = inv.get("total") is not None and abs((e["total"] or 0) - inv["total"]) <= 0.01
            same_date = e["invoice_date"] == inv.get("invoice_date")
            if norm and e["invoice_number_norm"] == norm:
                hit = (e, "exact", "Same invoice number")
                break
            if core and len(core) >= 4 and _core_number(e["invoice_number"]) == core and (same_amt or same_date):
                hit = (e, "fuzzy", f"Invoice number '{num}' is a reformatted '{e['invoice_number']}'"
                                   f"{' with identical amount' if same_amt else ''}{' and date' if same_date else ''}")
                break
        if hit:
            e, kind, why = hit
            C.append(check("invoice_number_dup", "Duplicate invoice", "fail",
                           f"{why} — already {e['status']} in ledger ({e['invoice_date']}, {money(e['total'])}, {e['po_number']})",
                           "reject", "Vendor", "high", match=e["invoice_number"], match_status=e["status"]))
            self.ctx["duplicate_of"] = e
        else:
            C.append(check("invoice_number_dup", "Duplicate invoice number", "pass", f"'{num}' not found in ledger (exact or normalised)"))
            near = []
            if d and inv.get("total") is not None:
                for e in entries:
                    ed = _d(e["invoice_date"])
                    if ed and abs((ed - d).days) <= S["duplicate_window_days"] and abs((e["total"] or 0) - inv["total"]) <= 0.01:
                        near.append(e)
            if near:
                C.append(check("amount_window", "Same amount, same vendor", "warn",
                               f"{len(near)} invoice(s) for {money(inv['total'])} within {S['duplicate_window_days']} days "
                               f"({', '.join(n['invoice_number'] for n in near)})", "review", "AP Specialist", "medium"))
            else:
                C.append(check("amount_window", "Same amount, same vendor", "pass",
                               f"No other {money(inv.get('total'))} invoice within {S['duplicate_window_days']} days"))
        if self.ctx.get("doc_type") == "credit_note":
            ref = self.ctx.get("credit_ref")
            amt = abs(inv.get("total") or 0)
            orig = None
            if ref:
                rn, rc = db.norm_invoice_number(ref), _core_number(ref)
                orig = next((e for e in entries if e["invoice_number_norm"] == rn
                             or (rc and len(rc) >= 4 and _core_number(e["invoice_number"]) == rc)), None)
            if orig:
                self.ctx["credit_original"] = orig
                over = amt > abs(orig["total"] or 0) + S["amount_tolerance_abs"]
                C.append(check("credit_original", "Original invoice", "fail" if over else "pass",
                               f"Credits {orig['invoice_number']} ({orig['invoice_date']}, {money(orig['total'])}, {orig['status']})"
                               + (f", but the credit {money(amt)} is larger than the original" if over else f"; credit {money(amt)}"),
                               "review" if over else None, "AP Specialist", "medium"))
            else:
                C.append(check("credit_original", "Original invoice", "warn",
                               (f"Referenced invoice {ref} is not in the ledger for {v['name']}" if ref else
                                "Credit note does not name the invoice it credits")
                               + "; AP must choose which open payable to apply it to", "review", "AP Specialist", "medium"))
        st["summary"] = "Duplicate detected" if hit or posted else "No duplicates"

    # ======================================================================= #
    # Stage 6 - PO match
    # ======================================================================= #
    def _line_similarity(self, item: dict, pol: dict) -> float:
        if item.get("sku") and item["sku"].upper() == pol["sku"].upper():
            return 100.0
        return float(fuzz.token_set_ratio(_clean_name(item.get("description", "")), _clean_name(pol["description"])))

    def s_po_match(self, st, log):
        inv, v, S = self.ctx["inv"], self.ctx["vendor"], self.settings
        C = st["checks"]
        ref = normalize_po(inv.get("po_number"))
        po = None
        if not ref and self.ctx.get("doc_type") == "credit_note":
            C.append(check("po_inference", "PO", "info", "Credit note without a PO reference; matched to the original invoice instead"))
            self.ctx["po_source"] = "none"
            st["summary"] = "Not needed (credit note)"
            return
        if not ref and v.get("po_required") is False:
            self._non_po(st, log)
            return
        if ref:
            po = self.pos.get(ref) or next((p for p in self.pos.values() if _digits(p["po_number"]) == _digits(ref)), None)
            if not po:
                C.append(check("po_exists", "PO reference", "fail", f"{ref} does not exist in the procurement system",
                               "review", "Procurement", "high"))
                st["summary"] = "PO not found"
                return
            log(f"Invoice references {ref}")
            if po["vendor_id"] != v["vendor_id"]:
                C.append(check("po_vendor", "PO belongs to vendor", "fail",
                               f"{po['po_number']} was issued to {self.vendors[po['vendor_id']]['name']}, not {v['name']}",
                               "review", "Procurement", "high"))
            else:
                C.append(check("po_exists", "PO reference", "pass", f"{po['po_number']} found, issued {po['date']} to {v['name']}"))
            self.ctx["po_source"] = "explicit"
        else:
            log("No PO reference printed — inferring from vendor's open POs")
            open_pos = [p for p in self.pos.values() if p["vendor_id"] == v["vendor_id"] and p["status"] == "open"]
            if not open_pos:
                C.append(check("po_inference", "PO inference", "fail",
                               f"No PO on invoice and {v['name']} has no open POs — non-PO spend needs budget-owner approval",
                               "review", "Procurement", "high"))
                st["summary"] = "No PO"
                return
            items = inv.get("line_items", [])
            scored = []
            for p in open_pos:
                po_total = sum(l["qty"] * l["unit_price"] for l in p["lines"])
                billed = sum(e["subtotal"] or 0 for e in db.ledger_entries(po_number=p["po_number"]))
                remaining = po_total - billed
                if items:
                    sims = [max(self._line_similarity(i, l) for l in p["lines"]) for i in items]
                    coverage = sum(1 for s in sims if s >= 75) / len(items)
                    line_score = sum(sims) / len(sims) / 100
                else:
                    coverage, line_score = 0.0, 0.0
                sub = inv.get("subtotal") or 0
                amount_fit = 1.0 if sub <= remaining * 1.05 else max(0.0, 1 - (sub - remaining) / max(remaining, 1))
                score = 0.45 * coverage + 0.30 * line_score + 0.25 * amount_fit
                scored.append({"po_number": p["po_number"], "score": round(score, 3), "coverage": round(coverage, 2),
                               "line_similarity": round(line_score, 2), "amount_fit": round(amount_fit, 2),
                               "remaining": round(remaining, 2)})
                log(f"  {p['po_number']}: lines {coverage:.0%} covered, similarity {line_score:.0%}, amount fit {amount_fit:.0%} → {score:.2f}")
            scored.sort(key=lambda s: -s["score"])
            st["data"]["candidates"] = scored
            best = scored[0]
            margin = best["score"] - (scored[1]["score"] if len(scored) > 1 else 0)
            conf = best["score"] if len(scored) == 1 else min(best["score"], 0.5 + margin)
            conf = round(conf, 2)
            if best["score"] >= S["po_inference_min_confidence"] and (len(scored) == 1 or margin >= 0.2):
                po = self.pos[best["po_number"]]
                C.append(check("po_inference", "PO inferred", "warn",
                               f"No PO printed; matched {po['po_number']} with {conf:.0%} confidence "
                               f"(beat {len(scored) - 1} other open PO(s) by {margin:.2f}). Vendor should quote PO numbers.",
                               None, None, "low", confidence=conf))
                self.ctx["po_source"] = "inferred"
            else:
                C.append(check("po_inference", "PO inference", "fail",
                               f"No PO printed and no confident match (best {best['po_number']} at {best['score']:.2f})",
                               "review", "AP Specialist", "medium"))
                st["summary"] = "PO ambiguous"
                return
        self.ctx["po"] = po
        if inv.get("currency") and po.get("currency") and inv["currency"] != po["currency"]:
            C.append(check("po_currency", "PO currency", "fail",
                           f"Invoice billed in {inv['currency']} but {po['po_number']} was raised in {po['currency']}",
                           "review", "Procurement", "high"))
        if po["status"] != "open" and self.ctx.get("doc_type") == "credit_note":
            C.append(check("po_status", "PO status", "info", f"{po['po_number']} is {po['status']}; a credit against a completed order is expected"))
        elif po["status"] != "open":
            C.append(check("po_status", "PO status", "fail", f"{po['po_number']} is {po['status'].upper()} — no further billing expected",
                           "review", "Procurement", "high"))
        else:
            C.append(check("po_status", "PO status", "pass", "Open"))
        st["data"]["po_number"] = po["po_number"]
        st["data"]["source"] = self.ctx["po_source"]
        st["summary"] = f"{po['po_number']} ({self.ctx['po_source']})"

    def _non_po(self, st, log):
        """Utilities, rent, telecom: the vendor master says no PO is required. Check the bill against the
        vendor's usual amount and route by size instead of failing the PO match."""
        inv, v, S = self.ctx["inv"], self.ctx["vendor"], self.settings
        C = st["checks"]
        cat = v.get("category") or "non-PO"
        self.ctx["po_source"] = "none"
        self.ctx["non_po"] = True
        C.append(check("non_po", "Non-PO invoice", "pass",
                       f"{v['name']} is a {cat.lower()} vendor: no PO required. Coded to {v.get('cost_center') or 'its default cost centre'}"))
        total = inv.get("total") or 0
        hist = [e["total"] for e in db.ledger_entries(vendor_id=v["vendor_id"])
                if e["total"] and e["invoice_number"] != inv.get("invoice_number")]
        if hist:
            avg = sum(hist) / len(hist)
            band = S.get("non_po_variance_pct", 25) / 100
            if total > avg * (1 + band):
                C.append(check("non_po_usual", "Usual amount", "warn",
                               f"{money(total)} is {total / avg - 1:.0%} above this vendor's average of {money(avg)} over {len(hist)} bill(s)",
                               "review", "AP Specialist", "medium"))
            else:
                C.append(check("non_po_usual", "Usual amount", "pass",
                               f"{money(total)} vs average {money(avg)} over {len(hist)} previous bill(s)"))
        else:
            C.append(check("non_po_usual", "Usual amount", "info", "First bill from this vendor; no history to compare"))
        limit = v.get("non_po_limit") or S.get("non_po_limit", 5000)
        usd = (self.ctx.get("fx") or {}).get("base_total") or total
        if usd > limit:
            C.append(check("non_po_limit", "Non-PO spend limit", "warn",
                           f"{money(usd, 'USD')} is above the {money(limit, 'USD')} limit for spend without a PO: budget owner sign-off needed",
                           "review", "Finance Manager", "medium"))
        log(f"Non-PO {cat} bill; PO match not required")
        st["data"]["source"] = "none"
        st["summary"] = f"Non-PO ({cat})"

    # ======================================================================= #
    # Stage 7 - 3-way match
    # ======================================================================= #
    def s_three_way(self, st, log):
        inv, S, po = self.ctx["inv"], self.settings, self.ctx.get("po")
        C = st["checks"]
        if self.ctx.get("doc_type") == "credit_note":
            st["status"] = "skipped"
            st["summary"] = "Credit note: checked against the original invoice"
            return
        if not po:
            st["status"] = "skipped"
            st["summary"] = "Not required (non-PO invoice)" if self.ctx.get("non_po") else "No PO to match against"
            return
        prior = [e for e in db.ledger_entries(po_number=po["po_number"])
                 if not (self.ctx.get("duplicate_of") and e["id"] == self.ctx["duplicate_of"]["id"])]
        receipts = [g for g in self.ref["goods_receipts"] if g["po_number"] == po["po_number"]]
        received = {}
        for g in receipts:
            for l in g["lines"]:
                received[l["line"]] = received.get(l["line"], 0) + l["qty_received"]
        # deposits are advances on the same PO value: they don't consume PO lines, they are netted off the final invoice
        deposits = [e for e in prior if any(l.get("kind") == "prepayment" for l in e["lines"])]
        dep_sub = sum(e["subtotal"] or 0 for e in deposits)
        dep_open = round(sum(e["total"] or 0 for e in deposits)
                         - sum(l.get("amount", 0) for e in prior for l in e["lines"] if l.get("kind") == "deposit_applied"), 2)
        billed_qty, billed_amt = {}, 0.0
        for e in prior:
            if e in deposits:
                continue
            billed_amt += e["subtotal"] or 0
            for l in e["lines"]:
                if l.get("po_line") is not None:
                    billed_qty[l["po_line"]] = billed_qty.get(l["po_line"], 0) + l["qty"]
        log(f"{len(prior)} prior invoice(s) on {po['po_number']} totalling {money(billed_amt)}; {len(receipts)} goods receipt(s)"
            + (f"; deposits {money(dep_sub)}" if deposits else ""))
        po_total = round(sum(l["qty"] * l["unit_price"] for l in po["lines"]), 2)

        if self.ctx.get("doc_type") == "prepayment":
            pct = float(po.get("prepayment_pct", S.get("max_prepayment_pct", 30)) or 0)
            sub = inv.get("subtotal") if inv.get("subtotal") is not None else (inv.get("total") or 0)
            allowed = round(po_total * pct / 100, 2)
            so_far = round(dep_sub + sub, 2)
            if pct <= 0:
                C.append(check("prepayment", "Deposit allowed", "fail", f"{po['po_number']} has no agreed deposit; billing before delivery needs Procurement approval",
                               "review", "Procurement", "high"))
            elif so_far > allowed + S["amount_tolerance_abs"]:
                C.append(check("prepayment", "Deposit allowed", "fail",
                               f"Deposits {money(so_far)} would exceed the agreed {pct:g}% ({money(allowed)}) of {po['po_number']}"
                               + (f"; {money(dep_sub)} already billed" if dep_sub else ""), "review", "Procurement", "high"))
            else:
                C.append(check("prepayment", "Deposit allowed", "pass",
                               f"Deposit {money(sub)} is {sub / po_total:.0%} of {po['po_number']} ({money(po_total)}); agreed up to {pct:g}%. "
                               f"It will be deducted from the final invoice."))
            self.ctx["line_matches"] = [{"po_line": None, "qty": 0, "amount": sub, "kind": "prepayment"}]
            self.ctx["payable"] = inv.get("total")
            st["data"] = {"lines": [], "po_total": po_total, "deposit_pct_allowed": pct, "deposits_before": round(dep_sub, 2)}
            st["summary"] = "Deposit within agreed terms" if C and C[-1]["status"] == "pass" else "Deposit outside agreed terms"
            return

        price_tol = S["price_tolerance_pct"] / 100
        items = inv.get("line_items", [])
        matches, used, services = [], set(), []
        payable_sub = 0.0
        table = []
        for it in items:
            cands = sorted(((self._line_similarity(it, l), l) for l in po["lines"] if l["line"] not in used),
                           key=lambda x: -x[0])
            row = {"description": it["description"], "sku": it.get("sku"), "qty": it.get("qty"),
                   "unit_price": it.get("unit_price"), "amount": it["amount"]}
            if not cands or cands[0][0] < 65:
                row.update(status="fail", note="Not on PO")
                C.append(check("unplanned_line", "Line not on PO", "fail",
                               f"“{it['description']}” {money(it['amount'])} has no matching PO line — needs change order or credit note",
                               "review", "Procurement", "high", amount=it["amount"]))
                matches.append({"po_line": None, "qty": it.get("qty"), "amount": it["amount"]})
                table.append(row)
                continue
            sim, pol = cands[0]
            used.add(pol["line"])
            qty = it.get("qty") or 1
            unit = it.get("unit_price") if it.get("unit_price") is not None else it["amount"] / qty
            var = (unit - pol["unit_price"]) / pol["unit_price"] if pol["unit_price"] else 0
            prev = billed_qty.get(pol["line"], 0)
            # services / subscriptions have no goods receipt: 2-way match (invoice vs PO) instead of 3-way
            service = pol.get("type") == "service" or po.get("receipt_required") is False
            if service:
                services.append(pol["description"])
            rec = pol["qty"] if service else received.get(pol["line"], 0)
            row.update(po_line=pol["line"], po_description=pol["description"], po_qty=pol["qty"], po_price=pol["unit_price"],
                       price_variance_pct=round(var * 100, 2), previously_billed=prev, received=rec, similarity=round(sim))
            notes, status = [], "pass"
            if abs(var) > price_tol:
                status = "fail"
                notes.append(f"price {var:+.1%} vs PO")
                C.append(check("price", f"Price — {pol['description']}", "fail",
                               f"Billed {money(unit)} vs PO {money(pol['unit_price'])} ({var:+.2%}, tolerance ±{price_tol:.0%})",
                               "review", "Procurement", "medium"))
            elif abs(var) > 0.0001:
                notes.append(f"price {var:+.2%} within tolerance")
                C.append(check("price", f"Price — {pol['description']}", "warn",
                               f"Billed {money(unit)} vs PO {money(pol['unit_price'])} ({var:+.2%}) — within ±{price_tol:.0%} tolerance, "
                               f"variance {money((unit - pol['unit_price']) * qty)} accepted"))
            if prev + qty > pol["qty"]:
                status = "fail"
                notes.append("over-ordered")
                C.append(check("qty_po", f"Quantity vs PO — {pol['description']}", "fail",
                               f"{prev:g} already billed + {qty:g} now = {prev + qty:g} > {pol['qty']:g} ordered",
                               "review", "Procurement", "high"))
            if prev + qty > rec:
                status = "fail"
                short = prev + qty - rec
                notes.append(f"{short:g} not received")
                C.append(check("qty_grn", f"Goods receipt — {pol['description']}", "fail",
                               f"Billing {qty:g}{f' (cumulative {prev + qty:g})' if prev else ''} but only {rec:g} received — {short:g} unit(s) "
                               f"({money(short * unit)}) billed ahead of delivery", "review", "Receiving", "high"))
            payable_qty = max(0.0, min(qty, rec - prev, pol["qty"] - prev))
            payable_sub += payable_qty * min(unit, pol["unit_price"] * (1 + price_tol))
            row.update(status=status if status != "pass" or not notes else "warn", note="; ".join(notes) or "Match")
            matches.append({"po_line": pol["line"], "qty": qty, "amount": it["amount"]})
            table.append(row)
        if not items:  # bundled invoice: header-level check only
            payable_sub = inv.get("subtotal") or 0
            C.append(check("header_only", "Header-level match", "warn", "No lines to match; compared totals only"))
        if services:
            C.append(check("service_match", "2-way match (services)", "pass",
                           f"{len(services)} service line(s) matched to the PO only; no goods receipt expected for services"))

        sub = inv.get("subtotal") or sum(i["amount"] for i in items)
        cum = round(billed_amt + sub, 2)
        limit = po_total * (1 + S["po_overrun_tolerance_pct"] / 100)
        remaining = round(po_total - billed_amt, 2)
        if cum > limit + S["amount_tolerance_abs"]:
            over = round(cum - po_total, 2)
            C.append(check("po_balance", "Cumulative billing vs PO", "fail",
                           f"Prior billing {money(billed_amt)} + this invoice {money(sub)} = {money(cum)} exceeds PO value "
                           f"{money(po_total)} by {money(over)} ({over / po_total:.1%}). Remaining PO balance was {money(remaining)}.",
                           "review", "Procurement", "high", over=over))
            payable_sub = min(payable_sub, max(remaining, 0))
        else:
            C.append(check("po_balance", "Cumulative billing vs PO", "pass",
                           f"{money(cum)} of {money(po_total)} billed after this invoice ({cum / po_total:.0%})"
                           + (f" — split billing: invoice {len(prior) + 1} on this PO" if prior else "")))
        if inv.get("tax_amount") and sub:
            eff = inv["tax_amount"] / sub
            if abs(eff - po.get("tax_rate", 0)) > 0.005:
                C.append(check("tax_vs_po", "Tax vs PO", "warn",
                               f"Invoice tax {eff:.2%} vs PO tax code {po.get('tax_rate', 0):.2%}", "review", "AP Specialist", "medium"))
            else:
                C.append(check("tax_vs_po", "Tax vs PO", "pass", f"{eff:.2%} matches PO tax code"))
        tax_ratio = (inv.get("tax_amount") or 0) / sub if sub else 0
        self.ctx["line_matches"] = matches
        self.ctx["payable"] = round(payable_sub * (1 + tax_ratio), 2)
        if dep_open > 0.01:
            offset = round(min(dep_open, inv.get("total") or 0), 2)
            self.ctx["deposit_offset"] = offset
            self.ctx["payable"] = round(max(0.0, self.ctx["payable"] - offset), 2)
            C.append(check("prepayment_netting", "Deposit deducted", "info",
                           f"Deposit {money(offset)} already paid on {', '.join(e['invoice_number'] for e in deposits)} is deducted: "
                           f"pay {money((inv.get('total') or 0) - offset)} of {money(inv.get('total'))}"))
        st["data"] = {"lines": table, "po_total": po_total, "billed_before": round(billed_amt, 2), "cumulative": cum,
                      "remaining_before": remaining, "receipts": [g["grn"] for g in receipts],
                      "prior_invoices": [e["invoice_number"] for e in prior]}
        fails = sum(1 for c in C if c["status"] == "fail")
        st["summary"] = "Invoice, PO and receipt agree" if not fails else f"{fails} discrepancy(ies)"

    # ======================================================================= #
    # Stage 8 - decision
    # ======================================================================= #
    def s_decision(self, st, log):
        inv, S = self.ctx.get("inv", {}), self.settings
        # user-defined rules (Rules & settings) run after the built-in checks and act like them
        if inv:
            v = self.ctx.get("vendor") or {}
            facts = custom_rules.facts(inv, v.get("vendor_id"), v.get("name"), (self.ctx.get("po") or {}).get("po_number"),
                                       (self.ctx.get("fx") or {}).get("base_total"))
            extra = custom_rules.run_checks(facts)
            st["checks"].extend(extra)
            fired = [c for c in extra if c["status"] == "fail"]
            if extra:
                log(f"Custom rules: {len(extra)} evaluated, {len(fired)} triggered"
                    + (f" ({', '.join(c['label'] for c in fired)})" if fired else ""))
        # a credit note is never "approved for payment": AP applies it against what we owe
        if self.ctx.get("doc_type") == "credit_note":
            orig = self.ctx.get("credit_original")
            st["checks"].append(check("credit_note", "Credit note", "warn",
                                      f"Credit of {money(abs(inv.get('total') or 0))} to apply against "
                                      f"{orig['invoice_number'] if orig else 'an open payable for this vendor'}; nothing is paid out",
                                      "review", "AP Specialist", "medium"))
        # early-payment discount (e.g. 2/10 Net 30): informational, never blocks
        disc = self._discount(inv, self.ctx.get("vendor")) if inv and self.ctx.get("doc_type") != "credit_note" else None
        self.ctx["discount"] = disc
        if disc:
            if disc["open"]:
                st["checks"].append(check("early_discount", "Early-payment discount", "pass",
                                          f"{disc['text']}: pay by {disc['by']} to save {money(disc['amount'])} ({disc['pct']:g}%)"))
            else:
                st["checks"].append(check("early_discount", "Early-payment discount", "warn",
                                          f"{disc['text']}: the discount window closed on {disc['by']}; {money(disc['amount'])} can no longer be saved"))
        checks = self.all_checks()
        rejects = [c for c in checks if c["outcome"] == "reject"]
        reviews = [c for c in checks if c["outcome"] == "review"]
        vendor = self.ctx.get("vendor")
        total = inv.get("total") or 0
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}

        if rejects:
            decision = "REJECTED"
            drivers = sorted(rejects, key=lambda c: sev_rank.get(c["severity"], 4))
            route = "Vendor" if any(c["route"] == "Vendor" for c in rejects) else drivers[0]["route"]
        elif reviews:
            decision = "NEEDS_REVIEW"
            drivers = sorted(reviews, key=lambda c: (sev_rank.get(c["severity"], 4), ROUTE_PRIORITY.index(c["route"]) if c["route"] in ROUTE_PRIORITY else 9))
            route = next((r for r in ROUTE_PRIORITY if any(c["route"] == r for c in reviews)), "AP Specialist")
        elif ((self.ctx.get("fx") or {}).get("base_total") or total) > S["approval_limit"]:
            decision = "NEEDS_REVIEW"
            bt = (self.ctx.get("fx") or {}).get("base_total") or total
            drivers = [check("approval_limit", "Approval limit", "warn",
                             f"{money(total)}" + (f" (≈ {money(bt, 'USD')})" if _CUR['code'] != 'USD' else "")
                             + f" exceeds auto-approval limit {money(S['approval_limit'], 'USD')}", "review", "Finance Manager", "medium")]
            st["checks"].append(drivers[0])
            route = "Finance Manager"
        else:
            decision = "APPROVED"
            drivers = []
            route = None

        st["checks"].append(check("policy", "Policy outcome", {"APPROVED": "pass", "NEEDS_REVIEW": "warn", "REJECTED": "fail"}[decision],
                                  f"{len(rejects)} blocking, {len(reviews)} review, "
                                  f"{sum(1 for c in checks if c['status'] == 'pass')} passed checks"))

        payable = self.ctx.get("payable")
        headline, actions, email, note = self._narrative(decision, drivers, inv, vendor, payable)
        passed = sum(1 for c in checks if c["status"] == "pass")
        considered = sum(1 for c in checks if c["status"] in ("pass", "warn", "fail"))
        fc = inv.get("field_confidence", {})
        confidence = round(min(fc.get("total", 0.9), fc.get("invoice_number", 0.9) or 0.9) if inv.get("invoice_number") else 0.5, 2)
        dec = {
            "decision": decision, "headline": headline, "route_to": route,
            "reasons": [{"text": c["message"], "label": c["label"], "severity": c["severity"], "stage": c.get("stage", "decision"),
                         "route": c["route"], "custom": bool(c.get("custom_rule"))} for c in drivers],
            "actions": actions, "vendor_email": email, "internal_note": note,
            "amounts": {"invoice_total": total,
                        "recommended_payment": None if self.ctx.get("doc_type") == "credit_note" else
                        payable if decision == "NEEDS_REVIEW" and payable is not None and payable < total - 0.01 else
                        (round(total - (self.ctx.get("deposit_offset") or 0), 2) if decision == "APPROVED" else None),
                        "on_hold": round(total - payable, 2) if decision == "NEEDS_REVIEW" and payable is not None and payable < total - 0.01
                        and self.ctx.get("doc_type") != "credit_note" else None,
                        "deposit_deducted": self.ctx.get("deposit_offset"),
                        "early_discount": (self.ctx.get("discount") or {}).get("amount") if (self.ctx.get("discount") or {}).get("open") else None},
            "doc_type": self.ctx.get("doc_type", "invoice"),
            "currency": _CUR["code"],
            "fx": self.ctx.get("fx") if _CUR["code"] != "USD" else None,
            "payment": self._payment_plan(inv, vendor) if decision == "APPROVED" else None,
            "checks_passed": passed, "checks_total": considered, "extraction_confidence": confidence,
            "po": (self.ctx.get("po") or {}).get("po_number"), "po_source": self.ctx.get("po_source"),
        }
        self.ctx["decision"] = dec
        log(f"{decision}: {headline}")
        st["summary"] = {"APPROVED": "Approved for payment", "NEEDS_REVIEW": f"Routed to {route}", "REJECTED": "Rejected"}[decision]
        st["status"] = {"APPROVED": "passed", "NEEDS_REVIEW": "warning", "REJECTED": "failed"}[decision]

    def _discount(self, inv, vendor):
        d = parse_discount(_doc_text(inv), (vendor or {}).get("payment_terms"))
        if not d:
            return None
        by = (_d(inv.get("invoice_date")) or today()) + timedelta(days=d["days"])
        base = (inv.get("total") or 0) - (self.ctx.get("deposit_offset") or 0)
        return {**d, "by": by.isoformat(), "amount": round(base * d["pct"] / 100, 2), "open": by >= today()}

    def _payment_plan(self, inv, vendor):
        if not vendor:
            return None
        m = re.search(r"net\s*(\d+)", vendor["payment_terms"] or "", re.I)
        terms = int(m.group(1)) if m else int(re.sub(r"\D", "", vendor["payment_terms"] or "")[:3] or 30)
        d = _d(inv.get("invoice_date")) or today()
        due = _d(inv.get("due_date")) or d + timedelta(days=terms)
        amount = round((inv.get("total") or 0) - (self.ctx.get("deposit_offset") or 0), 2)
        plan = {"scheduled_for": due.isoformat(), "terms": vendor["payment_terms"], "amount": amount,
                "to_account": f"{vendor['bank']['bank_name']} ••••{vendor['bank']['account_number'][-4:]} (vendor master)"}
        disc = self.ctx.get("discount")
        if disc and disc["open"]:  # paying early is worth it: schedule for the discount date
            plan.update(scheduled_for=disc["by"], discount={"pct": disc["pct"], "amount": disc["amount"], "by": disc["by"],
                                                            "pay": round(amount - disc["amount"], 2)})
        return plan

    def _narrative(self, decision, drivers, inv, vendor, payable):
        num = inv.get("invoice_number") or "(no number)"
        vname = vendor["name"] if vendor else (inv.get("vendor_name") or "Unknown vendor")
        total = inv.get("total")
        po = (self.ctx.get("po") or {}).get("po_number")
        ids = {d["id"] for d in drivers}
        actions: list[str] = []
        email = None
        note = ""
        to = vendor["email"] if vendor else None
        sig = "\n\nRegards,\nAccounts Payable\nLumen Retail Group Inc."

        if decision == "APPROVED":
            src = " (PO inferred)" if self.ctx.get("po_source") == "inferred" else ""
            offset = self.ctx.get("deposit_offset")
            disc = self.ctx.get("discount")
            if self.ctx.get("doc_type") == "prepayment":
                headline = f"{vname} deposit invoice {num} for {money(total)} is within the agreed prepayment on {po} — approved."
            elif self.ctx.get("non_po"):
                cat = ((vendor or {}).get("category") or "non-PO").lower()
                headline = f"{vname} {num} for {money(total)} is a {cat} bill in the usual range — approved without a PO."
            elif any(c["id"] == "service_match" for c in self.all_checks()):
                headline = f"{vname} {num} for {money(total)} matches {po}{src} (services, 2-way match) — approved for payment."
            else:
                headline = f"{vname} {num} for {money(total)} matches {po}{src} and goods receipt — approved for payment."
            if offset:
                headline += f" Deposit {money(offset)} deducted: pay {money((total or 0) - offset)}."
            actions = ["Posted to invoice ledger", "Payment scheduled per vendor terms to the master-file bank account"]
            if disc and disc["open"]:
                actions[1] = f"Payment scheduled for {disc['by']} to take the {disc['pct']:g}% early-payment discount ({money(disc['amount'])})"
            if self.ctx.get("po_source") == "inferred":
                actions.append("Ask vendor to quote PO numbers on future invoices")
            return headline, actions, None, note

        if decision == "REJECTED":
            if "invoice_number_dup" in ids or "file_hash" in ids:
                dup = self.ctx.get("duplicate_of")
                headline = (f"Duplicate: {num} is a resubmission of {dup['invoice_number']} ({money(dup['total'])}), "
                            f"already {dup['status']}." if dup else f"Duplicate: this file was already processed and posted.")
                actions = ["Do not pay", "Reply to vendor with original payment reference"]
                email = {"to": to, "subject": f"Re: Invoice {num} — already {dup['status'] if dup else 'processed'}",
                         "body": f"Hello {vname} team,\n\nWe received invoice {num} for {money(total)}. Our records show this "
                                 f"is the same invoice as {dup['invoice_number'] if dup else num} dated {dup['invoice_date'] if dup else ''}, "
                                 f"which has already been {dup['status'] if dup else 'processed'}. We will not process it again.\n\n"
                                 f"If you believe this is a different charge, please send a new invoice with a unique invoice number "
                                 f"and reference the purchase order.{sig}"}
            elif "vendor_status" in ids:
                headline = f"{vname} is suspended in the vendor master — invoice {num} cannot be paid."
                actions = ["Do not pay", "Notify Procurement (vendor owner)", "Hold invoice until vendor is reinstated"]
                email = {"to": to, "subject": f"Invoice {num} — account on hold",
                         "body": f"Hello {vname} team,\n\nWe are unable to process invoice {num} ({money(total)}) because your "
                                 f"supplier account is currently on hold with our Procurement team. Please contact your "
                                 f"procurement contact to resolve the account status; we will process the invoice once it is reinstated.{sig}"}
            elif "required_fields" in ids:
                miss = ", ".join(self.ctx.get("missing", []))
                headline = f"Incomplete document from {vname}: missing {miss}. Returned for a corrected invoice."
                actions = ["Return to vendor", "Request a compliant tax invoice"]
                email = {"to": to, "subject": f"Corrected invoice required{' — ' + po if po else ''}",
                         "body": f"Hello {vname} team,\n\nWe received a document for {money(total)}"
                                 f"{' against ' + po if po else ''}, but it is missing the {miss}. We can only pay against a "
                                 f"valid invoice that shows a unique invoice number, invoice date, our PO number, and the tax breakdown.\n\n"
                                 f"Please send a corrected invoice to ap@lumenretail.example.{sig}"}
            else:
                headline = f"{num} rejected: " + "; ".join(d["message"] for d in drivers[:2])
                actions = ["Return to vendor"]
            return headline, actions, email, note

        # NEEDS_REVIEW
        top = drivers[0]
        if self.ctx.get("doc_type") == "credit_note" and not ({"bank_account", "routing", "sender"} & ids):
            orig = self.ctx.get("credit_original")
            amt = abs(total or 0)
            headline = (f"Credit note {num} from {vname} for {money(amt)}"
                        + (f" credits {orig['invoice_number']} ({orig['status']})." if orig else ", but the invoice it credits was not identified.")
                        + " Apply it against open payables; nothing is paid out.")
            actions = [f"Apply {money(amt)} against {'the next payment to ' + vname if orig and orig['status'] == 'paid' else (orig['invoice_number'] if orig else 'an open invoice')}",
                       "Record the credit in the ledger and update the vendor balance",
                       "If the original was paid and no further invoices are expected, request a refund"]
            return headline, actions, None, note
        if "sender" in ids and self.ctx.get("spoofed_sender") and not ("bank_account" in ids or "routing" in ids):
            em = self.ctx.get("email") or {}
            headline = (f"Possible invoice-redirect fraud: {num} arrived from {em.get('from_addr')}, a look-alike of "
                        f"{vname}'s real domain. Hold until the vendor confirms on a known contact.")
            actions = ["Do not pay or reply to the sender",
                       f"Call {vname} on the master-file number {vendor['phone']} to confirm the invoice",
                       "Report the look-alike domain to Security / email gateway block list"]
            return headline, actions, None, f"Sender domain differs from vendor master ({vendor['email']})."
        if "bank_account" in ids or "routing" in ids:
            v = vendor
            headline = (f"Possible payment fraud: {num} asks for payment to a bank account that is not on file for {vname}. "
                        f"Amounts and PO match — which is exactly why this pattern works.")
            if self.ctx.get("spoofed_sender"):
                headline += f" It also came from a look-alike domain ({(self.ctx.get('email') or {}).get('from_addr')})."
            actions = ["Do not pay or update the vendor master from this invoice",
                       f"Verify by calling {vname} on the master-file number {v['phone']} (not contact details on the invoice)",
                       "If confirmed, Treasury updates bank details through the vendor-change process with dual approval",
                       "If not confirmed, report to Security as attempted BEC fraud"]
            note = (f"Invoice remit-to differs from master ({v['bank']['bank_name']} ••••{v['bank']['account_number'][-4:]}). "
                    f"Invoice contains urgency/bank-change language. Do not reply to the sender's email.")
            return headline, actions, None, note
        if "po_balance" in ids or "unplanned_line" in ids:
            over = next((d.get("over") for d in drivers if d["id"] == "po_balance"), None)
            extra = [d for d in drivers if d["id"] == "unplanned_line"]
            headline = (f"Split billing on {po} exceeds the PO" + (f" by {money(over)}" if over else "") +
                        (f": '{extra[0]['message'].split('”')[0].strip('“')}' is not on the PO." if extra else "."))
            if payable is not None:
                headline += f" PO-backed portion {money(payable)} can be paid; {money((total or 0) - payable)} held."
            actions = [f"Approve short-payment of {money(payable)} (PO-backed services)" if payable else "Hold invoice",
                       "Procurement to decide: raise change order for the extra charge, or dispute it",
                       "Request credit note / revised invoice from vendor if disputed"]
            email = {"to": to, "subject": f"Invoice {num} — charges outside {po}",
                     "body": f"Hello {vname} team,\n\nInvoice {num} ({money(total)}) brings total billing on {po} above the "
                             f"approved PO value. " + (f"The line '{extra[0]['message'].split('”')[0].strip('“')}' "
                                                         f"({money(extra[0].get('amount'))}) was not part of the PO. " if extra else "")
                             + f"We are processing {money(payable)} for the services covered by the PO. For the remainder, "
                               f"please share supporting documentation (e.g. signed detention log) so our Procurement team can "
                               f"review, or issue a revised invoice.{sig}"}
            return headline, actions, email, note
        if any(d["id"] == "qty_grn" for d in drivers):
            grn = [d for d in drivers if d["id"] == "qty_grn"]
            headline = (f"{num} bills goods that have not been received: " + "; ".join(g["message"].split(" — ")[0] for g in grn)
                        + ". " + (f"Received portion {money(payable)} can be released." if payable else ""))
            if self.ctx.get("po_source") == "inferred":
                headline = f"PO {po} inferred from line items. " + headline
            actions = [f"Pay received portion {money(payable)}" if payable else "Hold invoice",
                       "Receiving to confirm whether remaining units arrived (GRN missing?) or are back-ordered",
                       "Auto-release the balance when the goods receipt is posted"]
            email = {"to": to, "subject": f"Invoice {num} — quantity not yet received",
                     "body": f"Hello {vname} team,\n\nThank you for invoice {num}. Our goods receipt shows fewer units delivered than "
                             f"invoiced ({grn[0]['message'].split(' — ')[1] if ' — ' in grn[0]['message'] else ''}). We will pay for the "
                             f"delivered quantity now and the balance once delivery is confirmed. Please let us know the expected "
                             f"delivery date for the remaining units, and quote our PO number ({po}) on future invoices.{sig}"}
            return headline, actions, email, note
        headline = f"{num} needs review by {top['route']}: {top['message']}"
        actions = [f"{d['route']}: {d['label']} — {d['message']}" for d in drivers[:4]]
        return headline, actions, None, note


_serial = __import__("threading").Lock()


def process(run_id: str, path: str, filename: str) -> None:
    # One invoice at a time: duplicate checks and PO-balance checks read the ledger that the
    # previous invoice may have just written, so parallel runs could double-spend a PO.
    with _serial:
        Run(run_id, path, filename).execute()
