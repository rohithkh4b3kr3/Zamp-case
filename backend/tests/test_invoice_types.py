"""Invoice types beyond the standard PO invoice: credit notes, services (2-way match), deposits and the
final invoice that nets them, early-payment discounts, and non-PO bills (utilities)."""
from __future__ import annotations

import copy

from test_pipeline import config, db, fresh_db, run_file  # noqa: F401  (fixture + env: local extraction only)

from app.pipeline import classify, parse_discount

S = config.SAMPLES_DIR


def _checks(run: dict, cid: str) -> list[dict]:
    return [c for s in run["stages"] for c in s["checks"] if c["id"] == cid]


def _edit_reference(fn) -> None:
    ref = copy.deepcopy(config.load_reference())
    fn(ref)
    config.save_reference(ref)  # fresh_db points LIVE_REFERENCE_PATH at tmp_path


def test_credit_note_is_applied_not_paid(tmp_path):
    run = run_file(S / "13_type_credit_note_northwind.pdf", tmp_path)
    d = run["decision_detail"]
    assert run["decision"] == "NEEDS_REVIEW" and run["route_to"] == "AP Specialist"
    assert d["doc_type"] == "credit_note"
    assert d["amounts"]["recommended_payment"] is None and d["payment"] is None
    assert _checks(run, "credit_original")[0]["status"] == "pass"  # matched to paid INV-NW-10482
    assert "INV-NW-10482" in d["headline"] and not run["ledger_posted"]


def test_services_use_two_way_match_and_discount(tmp_path):
    run = run_file(S / "14_type_services_discount_brightline.pdf", tmp_path)
    d = run["decision_detail"]
    assert run["decision"] == "APPROVED", d["headline"]
    assert _checks(run, "service_match")[0]["status"] == "pass"
    assert not _checks(run, "qty_grn")  # no goods receipt exists, and none is required
    disc = _checks(run, "early_discount")[0]
    assert "2/10" in disc["message"]
    if disc["status"] == "pass":  # window still open: pay on the discount date
        assert d["payment"]["discount"]["pct"] == 2.0 and d["payment"]["discount"]["amount"] == 264.0
        assert d["payment"]["scheduled_for"] == d["payment"]["discount"]["by"] == "2026-11-16"


def test_deposit_then_final_invoice_nets_the_deposit(tmp_path):
    dep = run_file(S / "15_type_deposit_keystone.pdf", tmp_path)
    assert dep["decision"] == "APPROVED" and dep["decision_detail"]["doc_type"] == "prepayment"
    assert _checks(dep, "prepayment")[0]["status"] == "pass"
    final = run_file(S / "16_type_final_after_deposit_keystone.pdf", tmp_path)
    amounts = final["decision_detail"]["amounts"]
    assert final["decision"] == "APPROVED", final["decision_detail"]["headline"]
    assert amounts["deposit_deducted"] == 4500.0 and amounts["recommended_payment"] == 11700.0
    assert _checks(final, "po_balance")[0]["status"] == "pass"  # deposit does not count twice against the PO
    entry = next(e for e in db.ledger_entries(po_number="PO-2026-0480") if e["invoice_number"] == "KF-2231")
    assert any(l.get("kind") == "deposit_applied" and l["amount"] == 4500.0 for l in entry["lines"])


def test_deposit_above_agreed_percentage_goes_to_procurement(tmp_path):
    _edit_reference(lambda r: next(p for p in r["purchase_orders"] if p["po_number"] == "PO-2026-0480").update(prepayment_pct=10))
    run = run_file(S / "15_type_deposit_keystone.pdf", tmp_path)
    assert run["decision"] == "NEEDS_REVIEW" and run["route_to"] == "Procurement"
    assert "agreed 10%" in _checks(run, "prepayment")[0]["message"]


def test_final_invoice_without_deposit_pays_in_full(tmp_path):
    run = run_file(S / "16_type_final_after_deposit_keystone.pdf", tmp_path)
    assert run["decision"] == "APPROVED"
    assert run["decision_detail"]["amounts"]["recommended_payment"] == 16200.0
    assert not _checks(run, "prepayment_netting")


def test_non_po_utility_bill(tmp_path):
    run = run_file(S / "17_type_non_po_utility_metro.pdf", tmp_path)
    assert run["decision"] == "APPROVED", run["decision_detail"]["headline"]
    assert _checks(run, "non_po")[0]["status"] == "pass"
    assert _checks(run, "non_po_usual")[0]["status"] == "pass"  # within 25% of the 2 seeded bills
    assert not _checks(run, "po_inference")  # no failing PO search for a non-PO vendor


def test_non_po_bill_over_limit_needs_budget_owner(tmp_path):
    _edit_reference(lambda r: next(v for v in r["vendors"] if v["vendor_id"] == "V-1008").update(non_po_limit=1000))
    run = run_file(S / "17_type_non_po_utility_metro.pdf", tmp_path)
    assert run["decision"] == "NEEDS_REVIEW" and run["route_to"] == "Finance Manager"
    assert _checks(run, "non_po_limit")[0]["status"] == "warn"


def test_non_po_bill_far_above_usual_goes_to_ap(tmp_path):
    with db._lock:  # usual bills were ~$600, this one is $1,312
        db.conn().execute("UPDATE ledger SET total = 600 WHERE vendor_id = 'V-1008'")
        db.conn().commit()
    run = run_file(S / "17_type_non_po_utility_metro.pdf", tmp_path)
    assert run["decision"] == "NEEDS_REVIEW" and run["route_to"] == "AP Specialist"
    assert "above this vendor's average" in _checks(run, "non_po_usual")[0]["message"]


def test_classify_and_parse_discount():
    assert classify({"raw_text_excerpt": "CREDIT NOTE\nCredit Note No: CN-1"}) == "credit_note"
    assert classify({"total": -50.0}) == "credit_note"
    assert classify({"raw_text_excerpt": "DEPOSIT INVOICE 30% deposit"}) == "prepayment"
    assert classify({"raw_text_excerpt": "Progress billing 2 of 4"}) == "prepayment"
    assert classify({"raw_text_excerpt": "INVOICE\nDeposit KF-D-2207 previously invoiced"}) == "invoice"
    assert parse_discount("Terms: 2/10 Net 30") == {"pct": 2.0, "days": 10, "net": 30, "text": "2/10 Net 30"}
    assert parse_discount("1.5% discount if paid within 15 days")["days"] == 15
    assert parse_discount("Net 30") is None and parse_discount(None, "Net 45") is None
