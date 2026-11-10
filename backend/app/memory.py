"""Vendor memory: the process learns from reviewer corrections.

Learned rules (stored per vendor, visible and deletable in the UI):
  * alias        - a vendor-name spelling that was corrected to this vendor -> matched automatically next time
  * date_format  - DMY: the vendor writes 03/11/2026 meaning 3 November -> ambiguous dates read day-first
  * correction   - plain record of what reviewers fixed (feeds analytics: which fields / vendors need fixing)

Rules are deterministic and explainable: every time one fires, the run log says which rule and why.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from . import db
from .config import load_reference
from .extraction import is_ambiguous_date, parse_date


def _clean(s: str) -> str:
    s = re.sub(r"[^a-z0-9& ]", " ", (s or "").lower())
    s = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|company|gmbh|ag|the|and)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def learned_aliases() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in db.memory_for():
        if m["kind"] == "alias":
            out.setdefault(m["vendor_id"], []).append(m["value"])
    return out


def guess_vendor(inv: dict) -> dict | None:
    """Cheap early vendor match so learned rules can be applied before validation."""
    vendors = load_reference()["vendors"]
    tax = (inv.get("vendor_tax_id") or "").replace(" ", "")
    for v in vendors:
        if tax and v["tax_id"].replace(" ", "") == tax:
            return v
    name = _clean(inv.get("vendor_name") or "")
    if not name:
        return None
    learned = learned_aliases()
    best, score = None, 0.0
    for v in vendors:
        for cand in [v["name"], *v.get("aliases", []), *learned.get(v["vendor_id"], [])]:
            s = fuzz.token_set_ratio(name, _clean(cand))
            if s > score:
                best, score = v, s
    return best if score >= 85 else None


def apply(inv: dict, overrides: dict, log) -> list[dict]:
    """Apply learned rules to freshly extracted values. Returns check entries describing what happened."""
    notes: list[dict] = []
    v = guess_vendor(inv)
    raw = inv.get("invoice_date_raw")
    ambiguous = bool(raw) and is_ambiguous_date(raw)
    if not v:
        if ambiguous and "invoice_date" not in overrides:
            notes.append({"id": "date_ambiguous", "label": "Ambiguous date", "status": "info",
                          "message": f"“{raw}” could be month-first or day-first; read as US (MM/DD) → {inv.get('invoice_date')}."})
        return notes
    rules = db.memory_for(v["vendor_id"])
    date_rule = next((r for r in rules if r["kind"] == "date_format"), None)
    if ambiguous and "invoice_date" not in overrides:
        if date_rule and date_rule["value"] == "DMY":
            new = parse_date(raw, dayfirst=True)
            if new and new != inv.get("invoice_date"):
                log(f"Learned rule: {v['name']} writes dates day-first → “{raw}” is {new} (not {inv.get('invoice_date')})")
                notes.append({"id": "learned_date", "label": "Learned rule applied", "status": "info",
                              "message": f"{v['name']} writes dates as DD/MM/YYYY (learned from a reviewer correction). "
                                         f"“{raw}” read as {new} instead of {inv.get('invoice_date')}."})
                inv["invoice_date"] = new
                inv.setdefault("learned_applied", []).append({"rule": "date_format", "value": "DMY", "from": raw, "to": new})
                db.memory_hit(date_rule["id"])
                if inv.get("due_date_raw"):
                    inv["due_date"] = parse_date(inv["due_date_raw"], dayfirst=True) or inv.get("due_date")
        elif not date_rule:
            notes.append({"id": "date_ambiguous", "label": "Ambiguous date", "status": "info",
                          "message": f"“{raw}” could be month-first or day-first; read as US (MM/DD) → {inv.get('invoice_date')}. "
                                     f"If wrong, correct it once and the format is learned for {v['name']}."})
    alias = next((r for r in rules if r["kind"] == "alias" and _clean(r["value"]) == _clean(inv.get("vendor_name") or "")), None)
    if alias:
        log(f"Learned rule: “{inv.get('vendor_name')}” is a known alias of {v['name']}")
        notes.append({"id": "learned_alias", "label": "Learned rule applied", "status": "info",
                      "message": f"“{inv.get('vendor_name')}” recognised as {v['name']} (alias learned from a reviewer correction)."})
        db.memory_hit(alias["id"])
    return notes


def learn(run_id: str, inv: dict, vendor: dict | None, log) -> list[str]:
    """After a corrected re-run finishes: turn the reviewer's fixes into vendor rules."""
    fixes = inv.get("corrected_fields") or {}
    if not fixes or not vendor:
        return []
    learned: list[str] = []
    vid = vendor["vendor_id"]
    vf = fixes.get("vendor_name")
    if vf and vf.get("was") and _clean(vf["was"]) not in {_clean(vendor["name"]), *(_clean(a) for a in vendor.get("aliases", []))}:
        if db.add_memory(vid, "alias", vf["was"], f"corrected to {vendor['name']}", run_id):
            learned.append(f"“{vf['was']}” is {vendor['name']} (vendor alias)")
    df = fixes.get("invoice_date")
    raw = inv.get("invoice_date_raw")
    if df and raw and is_ambiguous_date(raw):
        if parse_date(raw, dayfirst=True) == df["now"]:
            if db.add_memory(vid, "date_format", "DMY", f"“{raw}” corrected to {df['now']}", run_id):
                learned.append(f"{vendor['name']} writes dates day-first (DD/MM/YYYY)")
        elif parse_date(raw) == df["now"]:
            db.add_memory(vid, "date_format", "MDY", f"“{raw}” confirmed as {df['now']}", run_id)
    for field, fx in fixes.items():
        db.add_memory(vid, "correction", field, f"{fx.get('was')!s} → {fx.get('now')!s}", run_id)
    for item in learned:
        log(f"Learned: {item}")
    return learned
