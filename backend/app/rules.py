"""User-defined rules: AP teams add their own checks without code.

A rule is   <field> <op> <value>  ->  outcome (review | reject) routed to a team, with a message.
Rules run in the decision stage, after the built-in checks, and show up as ordinary checks
(so they drive the decision, the routing, the audit trail and analytics exactly like built-in ones).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from typing import Any

from . import db
from .config import load_reference
from .extraction import today

FIELDS: list[dict[str, str]] = [
    {"id": "total", "label": "Invoice total (invoice currency)", "type": "number"},
    {"id": "total_usd", "label": "Invoice total in USD", "type": "number"},
    {"id": "subtotal", "label": "Subtotal", "type": "number"},
    {"id": "tax_amount", "label": "Tax amount", "type": "number"},
    {"id": "line_count", "label": "Number of line items", "type": "number"},
    {"id": "due_in_days", "label": "Days until due date", "type": "number"},
    {"id": "invoice_age_days", "label": "Invoice age (days)", "type": "number"},
    {"id": "vendor_name", "label": "Vendor name", "type": "text"},
    {"id": "vendor_id", "label": "Vendor ID", "type": "text"},
    {"id": "currency", "label": "Currency", "type": "text"},
    {"id": "po_number", "label": "PO number", "type": "text"},
    {"id": "invoice_number", "label": "Invoice number", "type": "text"},
    {"id": "doc_type", "label": "Document type (invoice / credit_note / prepayment)", "type": "text"},
    {"id": "non_po", "label": "Non-PO vendor (yes / no)", "type": "text"},
]
FIELD_TYPES = {f["id"]: f["type"] for f in FIELDS}

OPS: list[dict[str, Any]] = [
    {"id": "gt", "label": "is greater than", "types": ["number"]},
    {"id": "gte", "label": "is at least", "types": ["number"]},
    {"id": "lt", "label": "is less than", "types": ["number"]},
    {"id": "lte", "label": "is at most", "types": ["number"]},
    {"id": "eq", "label": "equals", "types": ["number", "text"]},
    {"id": "neq", "label": "does not equal", "types": ["number", "text"]},
    {"id": "contains", "label": "contains", "types": ["text"]},
    {"id": "missing", "label": "is missing", "types": ["number", "text"]},
]
OP_TYPES = {o["id"]: o["types"] for o in OPS}
OUTCOMES = ("review", "reject")

SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    field TEXT NOT NULL,
    op TEXT NOT NULL,
    value_json TEXT,
    outcome TEXT NOT NULL,
    route_to TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
"""


def init() -> None:
    with db._lock:
        c = db.conn()
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(custom_rules)")}
        if "conditions_json" not in cols:  # AND conditions beyond the first one
            c.execute("ALTER TABLE custom_rules ADD COLUMN conditions_json TEXT")
        c.commit()


def teams() -> list[str]:
    return [*load_reference().get("teams", {}).keys(), "Vendor"]


def schema() -> dict:
    return {"fields": FIELDS, "ops": OPS, "outcomes": list(OUTCOMES), "teams": teams()}


# --------------------------------------------------------------------------- storage
def _row(r) -> dict:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    d["value"] = json.loads(d.pop("value_json")) if d.get("value_json") is not None else None
    d["conditions"] = json.loads(d.pop("conditions_json", None) or "[]")
    return d


def list_rules() -> list[dict]:
    init()
    with db._lock:
        return [_row(r) for r in db.conn().execute("SELECT * FROM custom_rules ORDER BY created_at").fetchall()]


def get_rule(rule_id: str) -> dict | None:
    init()
    with db._lock:
        r = db.conn().execute("SELECT * FROM custom_rules WHERE id=?", (rule_id,)).fetchone()
    return _row(r) if r else None


class RuleError(ValueError):
    pass


MAX_EXTRA_CONDITIONS = 4


def _check_condition(field, op, value) -> tuple[str, str, Any]:
    if field not in FIELD_TYPES:
        raise RuleError(f"Unknown field '{field}'. Use one of: {', '.join(FIELD_TYPES)}.")
    if op not in OP_TYPES:
        raise RuleError(f"Unknown operator '{op}'. Use one of: {', '.join(OP_TYPES)}.")
    ftype = FIELD_TYPES[field]
    if ftype not in OP_TYPES[op]:
        raise RuleError(f"'{op}' does not apply to a {ftype} field. Pick a {ftype} operator.")
    if op == "missing":
        return field, op, None
    if ftype == "number":
        try:
            return field, op, float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            raise RuleError(f"'{field}' compares numbers; '{value}' is not a number.")
    value = str(value or "").strip()
    if not value:
        raise RuleError("Enter a value to compare against.")
    return field, op, value


def validate(rule: dict) -> dict:
    """Normalise and check a rule; raises RuleError with a message a user can act on."""
    name = str(rule.get("name") or "").strip()
    if not name:
        raise RuleError("Give the rule a name.")
    extra = []
    for c in (rule.get("conditions") or [])[: MAX_EXTRA_CONDITIONS + 1]:
        f, o, v = _check_condition(c.get("field"), c.get("op"), c.get("value"))
        extra.append({"field": f, "op": o, "value": v})
    if len(extra) > MAX_EXTRA_CONDITIONS:
        raise RuleError(f"A rule can have at most {MAX_EXTRA_CONDITIONS + 1} conditions.")
    field = rule.get("field")
    if field not in FIELD_TYPES:
        raise RuleError(f"Unknown field '{field}'. Use one of: {', '.join(FIELD_TYPES)}.")
    op = rule.get("op")
    if op not in OP_TYPES:
        raise RuleError(f"Unknown operator '{op}'. Use one of: {', '.join(OP_TYPES)}.")
    ftype = FIELD_TYPES[field]
    if ftype not in OP_TYPES[op]:
        raise RuleError(f"'{op}' does not apply to a {ftype} field. Pick a {ftype} operator.")
    value = rule.get("value")
    if op == "missing":
        value = None
    elif ftype == "number":
        try:
            value = float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            raise RuleError(f"'{field}' compares numbers; '{value}' is not a number.")
    else:
        value = str(value or "").strip()
        if not value:
            raise RuleError("Enter a value to compare against.")
    outcome = rule.get("outcome")
    if outcome not in OUTCOMES:
        raise RuleError("Outcome must be 'review' or 'reject'.")
    route = rule.get("route_to") or None
    if route and route not in teams():
        raise RuleError(f"Unknown team '{route}'. Use one of: {', '.join(teams())}.")
    if outcome == "review" and not route:
        route = "AP Specialist"
    if outcome == "reject" and not route:
        route = "Vendor"
    message = str(rule.get("message") or "").strip() or f"{name}: {describe({'field': field, 'op': op, 'value': value, 'conditions': extra})}"
    return {"name": name[:120], "enabled": bool(rule.get("enabled", True)), "field": field, "op": op, "value": value,
            "conditions": extra, "outcome": outcome, "route_to": route, "message": message[:500]}


def create(rule: dict) -> dict:
    init()
    r = validate(rule)
    rid, ts = uuid.uuid4().hex[:12], db.now_iso()
    with db._lock:
        db.conn().execute(
            """INSERT INTO custom_rules(id, name, enabled, field, op, value_json, outcome, route_to, message, created_at, updated_at,
                                        conditions_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, r["name"], int(r["enabled"]), r["field"], r["op"], json.dumps(r["value"]), r["outcome"], r["route_to"],
             r["message"], ts, ts, json.dumps(r["conditions"])))
        db.conn().commit()
    db.audit(None, "user", "rule_created", f"{r['name']}: {describe(r)} → {r['outcome']}")
    return get_rule(rid)


def update(rule_id: str, patch: dict) -> dict:
    cur = get_rule(rule_id)
    if not cur:
        raise KeyError(rule_id)
    r = validate({**cur, **patch})
    with db._lock:
        db.conn().execute(
            """UPDATE custom_rules SET name=?, enabled=?, field=?, op=?, value_json=?, outcome=?, route_to=?, message=?, updated_at=?,
                                      conditions_json=?
               WHERE id=?""",
            (r["name"], int(r["enabled"]), r["field"], r["op"], json.dumps(r["value"]), r["outcome"], r["route_to"],
             r["message"], db.now_iso(), json.dumps(r["conditions"]), rule_id))
        db.conn().commit()
    db.audit(None, "user", "rule_updated", f"{r['name']}: {describe(r)} → {r['outcome']} ({'on' if r['enabled'] else 'off'})")
    return get_rule(rule_id)


def delete(rule_id: str) -> bool:
    init()
    with db._lock:
        cur = db.conn().execute("DELETE FROM custom_rules WHERE id=?", (rule_id,))
        db.conn().commit()
    if cur.rowcount:
        db.audit(None, "user", "rule_deleted", rule_id)
    return cur.rowcount > 0


def _hit(rule_id: str) -> None:
    with db._lock:
        db.conn().execute("UPDATE custom_rules SET hits=hits+1 WHERE id=?", (rule_id,))
        db.conn().commit()


# --------------------------------------------------------------------------- evaluation
def _days(iso: str | None, ref: date) -> int | None:
    try:
        return (datetime.strptime(iso, "%Y-%m-%d").date() - ref).days if iso else None
    except ValueError:
        return None


def facts(inv: dict, vendor_id: str | None, vendor_name: str | None, po_number: str | None,
          total_usd: float | None) -> dict:
    t = today()
    age = _days(inv.get("invoice_date"), t)
    return {
        "total": inv.get("total"), "total_usd": total_usd if total_usd is not None else inv.get("total"),
        "subtotal": inv.get("subtotal"), "tax_amount": inv.get("tax_amount"),
        "line_count": len(inv.get("line_items") or []),
        "due_in_days": _days(inv.get("due_date"), t), "invoice_age_days": -age if age is not None else None,
        "vendor_name": vendor_name or inv.get("vendor_name"), "vendor_id": vendor_id,
        "currency": inv.get("currency"), "po_number": po_number or inv.get("po_number"),
        "invoice_number": inv.get("invoice_number"),
        "doc_type": inv.get("doc_type") or "invoice",
        "non_po": "yes" if next((v for v in load_reference()["vendors"] if v["vendor_id"] == vendor_id), {}).get("po_required") is False else "no",
    }


def facts_for_run(run: dict) -> dict:
    """Facts from a stored run (for the 'test rule' endpoint)."""
    inv = run.get("extracted") or {}
    total_usd = run.get("base_total")
    if total_usd is None and inv.get("total") is not None:
        rate = load_reference().get("fx_rates", {}).get("to_usd", {}).get(inv.get("currency") or "USD", 1.0)
        total_usd = round(inv["total"] * rate, 2)
    return facts(inv, run.get("vendor_id"), run.get("vendor_name"), run.get("po_number"), total_usd)


OP_WORDS = {o["id"]: o["label"] for o in OPS}


def _describe_one(c: dict) -> str:
    label = next((f["label"] for f in FIELDS if f["id"] == c["field"]), c["field"])
    if c["op"] == "missing":
        return f"{label} is missing"
    v = c.get("value")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{label} {OP_WORDS[c['op']]} {v}"


def describe(rule: dict) -> str:
    return " and ".join(_describe_one(c) for c in [rule, *(rule.get("conditions") or [])])


def evaluate(rule: dict, f: dict) -> tuple[bool, str]:
    """All conditions must match (AND). Returns (matched, explanation)."""
    details = []
    for c in [rule, *(rule.get("conditions") or [])]:
        ok, d = _evaluate_one(c, f)
        details.append(d)
        if not ok:
            return False, "; ".join(details)
    return True, "; ".join(details)


def _evaluate_one(rule: dict, f: dict) -> tuple[bool, str]:
    actual = f.get(rule["field"])
    op, want = rule["op"], rule.get("value")
    if op == "missing":
        return actual in (None, ""), f"{rule['field']} = {actual!r}"
    if actual in (None, ""):
        return False, f"{rule['field']} is missing on this invoice, so the rule cannot apply"
    if FIELD_TYPES[rule["field"]] == "number":
        try:
            a, w = float(actual), float(want)
        except (TypeError, ValueError):
            return False, f"{rule['field']} = {actual!r} is not a number"
        res = {"gt": a > w, "gte": a >= w, "lt": a < w, "lte": a <= w, "eq": abs(a - w) < 1e-9, "neq": abs(a - w) >= 1e-9}[op]
        shown = f"{a:,.2f}".rstrip("0").rstrip(".") if not float(a).is_integer() else f"{int(a):,}"
        return res, f"{rule['field']} = {shown}"
    a, w = str(actual).strip().lower(), str(want).strip().lower()
    res = {"eq": a == w, "neq": a != w, "contains": w in a}[op]
    return res, f"{rule['field']} = {actual!r}"


def run_checks(f: dict, count_hits: bool = True) -> list[dict]:
    """Checks for every enabled rule: fail (+outcome/route) when matched, pass otherwise."""
    out = []
    for r in list_rules():
        if not r["enabled"]:
            continue
        matched, detail = evaluate(r, f)
        if matched:
            if count_hits:
                _hit(r["id"])
            out.append({"id": f"custom_{r['id']}", "label": r["name"], "status": "fail", "message": f"{r['message']} ({detail})",
                        "outcome": r["outcome"], "route": r["route_to"], "severity": "high" if r["outcome"] == "reject" else "medium",
                        "custom_rule": r["id"]})
        else:
            out.append({"id": f"custom_{r['id']}", "label": r["name"], "status": "pass",
                        "message": f"Not triggered: {describe(r)} ({detail})", "outcome": None, "route": None,
                        "severity": "low", "custom_rule": r["id"]})
    return out


# --------------------------------------------------------------------------- AI rule generator
GEN_PROMPT = """You turn an accounts-payable manager's plain-English policy into rules for an invoice-checking engine.

Each rule is ONE condition:  <field> <op> <value>  ->  outcome (review | reject), routed to a team, with a short message.
Fields (use the id exactly):
{fields}
Operators: gt (greater than), gte (at least), lt (less than), lte (at most), eq (equals), neq (not equal),
contains (text contains, case-insensitive), missing (field is empty; value must be null).
Number fields only take gt/gte/lt/lte/eq/neq/missing. Text fields only take eq/neq/contains/missing.
Teams for route_to: {teams}. Use "Vendor" only with outcome "reject" (the invoice goes back to the vendor).

Guidance:
- Money thresholds without a currency mean USD -> use total_usd. Use total only when the user names the invoice's own currency.
- "flag", "hold", "check", "needs approval", "send to X" -> outcome review. "reject", "block", "never pay", "return" -> outcome reject.
- If no team is named: review -> "AP Specialist", reject -> "Vendor". "Finance", "CFO", "controller", "manager approval" -> "Finance Manager".
  "Fraud", "bank", "security" -> "Treasury & Security". "Buyer", "purchasing", "contract", "PO owner" -> "Procurement". "Warehouse", "delivery" -> "Receiving".
- name: short (max 6 words). message: one sentence a reviewer reads, saying why the invoice was stopped.
- When ALL of several conditions must hold (e.g. "invoices from Bluepeak over $5k"), return ONE rule: the first condition in
  field/op/value and the others in and_conditions (max 4). When the policy says "or", return separate rules.
- If something cannot be expressed with these fields at all, leave it out and say so in "notes". Never invent fields.
"""


def _gen_schema():
    from typing import Literal, Optional, Union
    from pydantic import BaseModel

    FieldId = Literal[tuple(FIELD_TYPES)]  # type: ignore[valid-type]
    OpId = Literal[tuple(OP_TYPES)]  # type: ignore[valid-type]

    class Cond(BaseModel):
        field: FieldId
        op: OpId
        value: Optional[Union[float, str]] = None

    class RuleDraft(BaseModel):
        name: str
        field: FieldId
        op: OpId
        value: Optional[Union[float, str]] = None
        and_conditions: list[Cond] = []
        outcome: Literal["review", "reject"]
        route_to: Optional[str] = None
        message: str

    class Generated(BaseModel):
        rules: list[RuleDraft]
        notes: str = ""

    return Generated


def _llm_generate(text: str) -> tuple[list[dict], str, str]:
    from . import config
    from .extraction import llm_provider

    fields = "\n".join(f"- {f['id']} ({f['type']}): {f['label']}" for f in FIELDS)
    system = GEN_PROMPT.format(fields=fields, teams=", ".join(teams()))
    Generated = _gen_schema()
    if llm_provider() == "openai":
        import instructor
        from openai import OpenAI
        client = instructor.from_openai(OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL))
        out = client.chat.completions.create(model=config.OPENAI_MODEL, response_model=Generated, max_retries=2,
                                             messages=[{"role": "system", "content": system}, {"role": "user", "content": text}])
        model = config.OPENAI_MODEL
    else:
        import anthropic
        resp = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY).messages.parse(
            model=config.LLM_MODEL, max_tokens=4000, output_config={"effort": "low"}, system=system,
            messages=[{"role": "user", "content": text}], output_format=Generated)
        if resp.parsed_output is None:
            raise RuntimeError(f"no structured output (stop_reason={resp.stop_reason})")
        out, model = resp.parsed_output, config.LLM_MODEL
    drafts = []
    for r in out.rules:
        d = r.model_dump()
        d["conditions"] = d.pop("and_conditions", [])
        drafts.append(d)
    return drafts, out.notes, model


_TEAM_WORDS = [
    (r"treasury|security|fraud|bank", "Treasury & Security"),
    (r"finance manager|finance|cfo|controller|manager approval", "Finance Manager"),
    (r"procurement|purchasing|buyer|po owner|contract", "Procurement"),
    (r"receiving|warehouse|delivery|goods receipt", "Receiving"),
    (r"\bap\b|accounts payable|ap specialist|ap team", "AP Specialist"),
]
_MONEY = r"\$?\s*([\d,]+(?:\.\d+)?k?)"


def _num(s: str) -> float:
    s = s.replace(",", "").strip()
    return float(s[:-1]) * 1000 if s.endswith("k") else float(s)


def _rule_based_generate(text: str) -> tuple[list[dict], str]:
    """Phrase parser used when no LLM is configured (or it fails). Handles the common policy shapes."""
    t = text.lower()
    outcome = "reject" if re.search(r"\b(reject|block|never pay|do not pay|don't pay|return)\b", t) else "review"
    route = next((team for pat, team in _TEAM_WORDS if re.search(pat, t)), None)
    rules: list[dict] = []

    def add(field, op, value, name):
        rules.append({"name": name, "field": field, "op": op, "value": value, "outcome": outcome, "route_to": route,
                      "message": text.strip().rstrip(".")[:300]})

    m = re.search(r"(?:over|above|greater than|more than|exceed\w*|at least|>=?)\s*" + _MONEY + r"(?!\s*(?:line|item|day))", t)
    if m:
        op = "gte" if re.search(r"at least|>=", m.group(0)) else "gt"
        add("total" if re.search(r"€|\beur\b|euro", t) else "total_usd", op, _num(m.group(1)), f"Invoices over {m.group(1)}")
    m = re.search(r"(?:under|below|less than|smaller than)\s*" + _MONEY + r"(?!\s*(?:line|item|day))", t)
    if m:
        add("total_usd", "lt", _num(m.group(1)), f"Invoices under {m.group(1)}")
    m = re.search(r"(?:vendor|supplier|from)\s+(?:is\s+|named\s+|called\s+)?[\"'“]?([A-Za-z][\w&.\- ]{2,40}?)[\"'”]?(?=\s+(?:should|must|need|needs|go|goes|to|are|is|over|above|with|that)\b|[.,]|$)", text, re.I)
    if m and not re.match(r"(?:any|all|a|the|an|new|unknown)\b", m.group(1), re.I):
        add("vendor_name", "contains", m.group(1).strip(), f"Vendor {m.group(1).strip()}")
    if re.search(r"\b(no|without|missing)\s+(a\s+)?(po|purchase order)", t):
        add("po_number", "missing", None, "No PO on invoice")
    m = re.search(r"\b(?:currency\s+(?:is\s+)?|in\s+|billed in\s+)(usd|eur|gbp|inr|cad)\b", t)
    if m:
        neg = re.search(r"\bnot\s+(?:in\s+)?" + m.group(1), t)
        add("currency", "neq" if neg else "eq", m.group(1).upper(), f"Currency {'not ' if neg else ''}{m.group(1).upper()}")
    elif re.search(r"foreign currency|non[- ]usd|not in usd", t):
        add("currency", "neq", "USD", "Foreign currency")
    m = re.search(r"(?:more than|over)\s+(\d+)\s+(?:line|item)", t)
    if m:
        add("line_count", "gt", float(m.group(1)), f"More than {m.group(1)} lines")
    m = re.search(r"due (?:in|within) (?:less than |under )?(\d+)\s+day", t)
    if m:
        add("due_in_days", "lt", float(m.group(1)), f"Due within {m.group(1)} days")
    m = re.search(r"older than (\d+)\s+day|more than (\d+)\s+days old", t)
    if m:
        d = m.group(1) or m.group(2)
        add("invoice_age_days", "gt", float(d), f"Older than {d} days")
    if not rules:
        return [], "No condition recognised. Try e.g. \u201cSend invoices over $10,000 to the Finance Manager\u201d."
    if len(rules) > 1 and not re.search(r"\bor\b|\beither\b", t):
        # "from Bluepeak over $10k" means both must hold: one rule with AND conditions
        first, rest = rules[0], rules[1:]
        first["conditions"] = [{"field": r["field"], "op": r["op"], "value": r["value"]} for r in rest]
        first["name"] = " + ".join(r["name"] for r in rules)[:60]
        return [first], "All conditions must match (AND)."
    return rules, ("Separate rules: each triggers on its own (OR)." if len(rules) > 1 else "")


def preview_counts(rule: dict) -> dict:
    """How many processed invoices this rule would have caught."""
    total, hits = 0, []
    for r in db.list_runs(1000):
        if r["status"] != "completed" or r.get("superseded_by"):
            continue
        full = db.get_run(r["id"])
        if not full.get("extracted"):
            continue
        total += 1
        ok, _ = evaluate(rule, facts_for_run(full))
        if ok:
            hits.append({"run_id": r["id"], "vendor_name": r["vendor_name"], "invoice_number": r["invoice_number"]})
    return {"matched": len(hits), "of": total, "examples": hits[:5]}


def generate(text: str) -> dict:
    from .extraction import _short_err, llm_available

    text = (text or "").strip()
    if len(text) < 6:
        raise RuleError("Describe the rule in a sentence, e.g. “Send invoices over $10,000 to the Finance Manager”.")
    if len(text) > 1500:
        raise RuleError("Keep the description under 1,500 characters.")
    mode, model, note_extra = "rules", None, ""
    drafts, notes = None, ""
    if llm_available():
        try:
            drafts, notes, model = _llm_generate(text)
            mode = "llm"
        except Exception as e:
            why = "OpenAI rejected the API key in backend/.env" if "401" in str(e) else _short_err(e)
            note_extra = f"AI unavailable ({why}); used the built-in phrase parser."
    if drafts is None:
        drafts, notes = _rule_based_generate(text)
    out = []
    for d in drafts[:6]:
        try:
            r = validate(d)
            out.append({"rule": r, "describe": describe(r), "valid": True, "error": None, "preview": preview_counts(r)})
        except RuleError as e:
            out.append({"rule": d, "describe": None, "valid": False, "error": str(e), "preview": None})
    db.audit(None, "user", "rule_generated", f"{mode}: “{text[:200]}” -> {len(out)} draft(s)")
    return {"mode": mode, "model": model, "notes": " ".join(x for x in (notes, note_extra) if x).strip(), "drafts": out}
