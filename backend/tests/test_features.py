"""Tests for email intake, learning, notifications/SLA, ask, currency and analytics."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from test_pipeline import BILL_TO, config, db, fresh_db, make_pdf, run_file  # noqa: F401  (fixture re-export)

from app.main import app
from app import notify


def _wait(client, rid, n=160):
    for _ in range(n):
        r = client.get(f"/api/runs/{rid}").json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.25)
    raise AssertionError("run did not finish")


def _checks(run):
    return [c for s in run["stages"] for c in s["checks"]]


# --------------------------------------------------------------------------- currency
def test_foreign_currency_eur(tmp_path):
    run = run_file(config.SAMPLES_DIR / "10_extra_foreign_currency_eur.pdf", tmp_path)
    assert run["decision"] == "APPROVED", run["decision_detail"]["headline"]
    assert run["currency"] == "EUR"
    assert run["base_total"] == round(12700 * 1.0815, 2)
    dec = run["decision_detail"]
    assert dec["currency"] == "EUR" and dec["fx"]["rate"] == 1.0815
    assert any(c["id"] == "fx" for c in _checks(run))
    assert "€12,700.00" in dec["headline"]


def test_currency_mismatch_goes_to_review():
    db.update_settings({"approval_limit": 1000})  # keep the run unposted so it can be corrected
    with TestClient(app) as client:
        rid = client.post("/api/runs/sample/10_extra_foreign_currency_eur.pdf").json()["id"]
        _wait(client, rid)
        new = client.post(f"/api/runs/{rid}/correct", json={"fields": {"currency": "GBP"}}).json()["id"]
        run = _wait(client, new)
        failing = {c["id"] for c in _checks(run) if c["status"] == "fail"}
        assert {"currency", "po_currency"} <= failing
        assert run["decision"] == "NEEDS_REVIEW"


def test_approval_limit_uses_usd(tmp_path):
    db.update_settings({"approval_limit": 13000})  # EUR 12,700 < 13,000 but USD 13,735 > 13,000
    run = run_file(config.SAMPLES_DIR / "10_extra_foreign_currency_eur.pdf", tmp_path)
    assert run["decision"] == "NEEDS_REVIEW" and run["route_to"] == "Finance Manager"


# --------------------------------------------------------------------------- learning
def test_learning_date_format_from_correction():
    with TestClient(app) as client:
        first = _wait(client, client.post("/api/runs/sample/11_learn_dates_harbor_first.pdf").json()["id"])
        assert first["extracted"]["invoice_date"] == "2026-03-11"           # read US-style
        assert first["decision"] == "NEEDS_REVIEW"                          # looks 8 months old
        assert "date_ambiguous" in [c["id"] for c in _checks(first)]
        fixed = _wait(client, client.post(f"/api/runs/{first['id']}/correct",
                                          json={"fields": {"invoice_date": "2026-11-03"}}).json()["id"])
        assert fixed["decision"] == "APPROVED", fixed["decision_detail"]["headline"]
        assert any("day-first" in x for x in fixed["decision_detail"].get("learned", []))
        mem = client.get("/api/memory").json()
        assert any(m["kind"] == "date_format" and m["value"] == "DMY" and m["vendor_id"] == "V-1006" for m in mem)
        second = _wait(client, client.post("/api/runs/sample/12_learn_dates_harbor_second.pdf").json()["id"])
        assert second["extracted"]["invoice_date"] == "2026-11-07"          # not July 11
        assert any(c["id"] == "learned_date" for c in _checks(second))
        assert second["decision"] == "APPROVED", second["decision_detail"]["headline"]
        rule = next(m for m in client.get("/api/memory").json() if m["kind"] == "date_format")
        assert rule["hits"] == 1
        assert client.delete(f"/api/memory/{rule['id']}").status_code == 200


def test_learning_vendor_alias(tmp_path):
    body = """<h1>NWOS Trading Division</h1>
        <p><b>Invoice No:</b> {no}<br><b>Invoice Date:</b> 2026-11-05<br><b>PO Number:</b> PO-2026-0412</p>{bill}
        <table class="items"><tr><th>SKU</th><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
        <tr><td>NW-PP-A4</td><td>A4 copy paper</td><td>{q}</td><td>6.50</td><td>{amt}</td></tr></table>
        <p>Subtotal {amt}</p><p>Sales Tax (8%) {tax}</p><p><b>Total Due</b> {tot}</p>"""
    p = make_pdf(tmp_path, "alias.pdf", [body.format(no="INV-NW-20001", bill=BILL_TO, q=10, amt="65.00", tax="5.20", tot="70.20")])
    run = run_file(p, tmp_path)
    assert run["route_to"] == "Procurement" and run["vendor_id"] is None  # unknown name
    with TestClient(app) as client:
        fixed = _wait(client, client.post(f"/api/runs/{run['id']}/correct",
                                          json={"fields": {"vendor_name": "Northwind Office Supplies Inc."}}).json()["id"])
        assert fixed["vendor_id"] == "V-1001"
        assert any(m["kind"] == "alias" and m["value"] == "NWOS Trading Division" for m in client.get("/api/memory").json())
    p2 = make_pdf(tmp_path, "alias2.pdf", [body.format(no="INV-NW-20002", bill=BILL_TO, q=5, amt="32.50", tax="2.60", tot="35.10")])
    again = run_file(p2, tmp_path)
    assert again["vendor_id"] == "V-1001"  # matched by the learned alias, no human needed
    assert any(c["id"] == "learned_alias" for c in _checks(again))


# --------------------------------------------------------------------------- email intake
def test_email_intake():
    with TestClient(app) as client:
        e1 = client.post("/api/inbox/sample/01_northwind_invoice.eml").json()
        assert e1["status"] == "processed" and len(e1["run_ids"]) == 1
        r1 = _wait(client, e1["run_ids"][0])
        assert r1["decision"] == "APPROVED" and r1["email"]["from_addr"] == "billing@northwind-supplies.example"
        assert any(c["id"] == "sender" and c["status"] == "pass" for c in _checks(r1))

        e2 = client.post("/api/inbox/sample/02_bluepeak_lookalike_domain.eml").json()
        r2 = _wait(client, e2["run_ids"][0])
        sender = next(c for c in _checks(r2) if c["id"] == "sender")
        assert sender["status"] == "fail" and "look-alike" in sender["message"]
        assert r2["route_to"] == "Treasury & Security" and "look-alike" in r2["decision_detail"]["headline"]

        e3 = client.post("/api/inbox/sample/03_meridian_two_attachments.eml").json()
        assert len(e3["run_ids"]) == 1 and "non-PDF" in e3["note"]
        _wait(client, e3["run_ids"][0])

        e4 = client.post("/api/inbox/sample/04_atelier_nord_eur.eml").json()
        r4 = _wait(client, e4["run_ids"][0])
        assert r4["decision"] == "APPROVED" and r4["currency"] == "EUR"

        e5 = client.post("/api/inbox/sample/05_no_attachment.eml").json()
        assert e5["status"] == "no_pdf"
        drafts = [n for n in client.get("/api/notifications").json() if n["kind"] == "vendor_draft" and n["email_id"] == e5["id"]]
        assert drafts and drafts[0]["status"] == "draft"

        dup = client.post("/api/inbox/sample/01_northwind_invoice.eml").json()
        assert dup["status"] == "duplicate_email" and dup["run_ids"] == []
        inbox = client.get("/api/inbox").json()
        assert inbox[0]["id"] == dup["id"] and len(inbox) == 6
        assert client.post("/api/inbox/upload", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 415
        assert client.get("/api/inbox/stats").json()["duplicates"] == 1


def test_drop_folder(tmp_path, monkeypatch):
    import os
    from app import inbox
    from app.main import _start
    monkeypatch.setattr(config, "INBOX_DROP_DIR", tmp_path)
    src = config.SAMPLES_DIR / "emails" / "01_northwind_invoice.eml"
    dst = tmp_path / "in.eml"
    dst.write_bytes(src.read_bytes())
    old = time.time() - 10
    os.utime(dst, (old, old))
    assert inbox.scan_drop_folder(_start) == 1
    assert not dst.exists() and (tmp_path / "processed").exists()
    assert db.list_emails()[0]["source"] == "folder"


# --------------------------------------------------------------------------- notifications & SLA
def test_notifications_and_sla():
    db.update_settings({"sla_treasury_hours": 0.0})  # due immediately
    with TestClient(app) as client:
        r = _wait(client, client.post("/api/runs/sample/06_edge_bank_change_bluepeak.pdf").json()["id"])
        assert r["sla_due_at"]
        r = client.get(f"/api/runs/{r['id']}").json()
        routed = [n for n in r["notifications"] if n["kind"] == "routed"]
        assert routed and routed[0]["team"] == "Treasury & Security" and routed[0]["status"] == "simulated"
        time.sleep(1.1)
        assert notify.check_sla() == 1 and notify.check_sla() == 0  # escalates once
        esc = [n for n in client.get(f"/api/notifications?run_id={r['id']}").json() if n["kind"] == "escalation"]
        assert {n["team"] for n in esc} == {"Treasury & Security", "Finance Manager"}
        rej = _wait(client, client.post("/api/runs/sample/05_edge_duplicate_northwind.pdf").json()["id"])
        rej = client.get(f"/api/runs/{rej['id']}").json()
        assert any(n["kind"] == "vendor_draft" and n["status"] == "draft" for n in rej["notifications"])


# --------------------------------------------------------------------------- ask
def test_ask_rule_based():
    with TestClient(app) as client:
        r = _wait(client, client.post("/api/runs/sample/06_edge_bank_change_bluepeak.pdf").json()["id"])
        a = client.post(f"/api/runs/{r['id']}/ask", json={"question": "Is there a problem with the bank account?"}).json()
        assert a["mode"] == "rules" and "Remit-to bank account" in a["answer"]
        why = client.post(f"/api/runs/{r['id']}/ask", json={"question": "Why was this held?"}).json()
        assert "Possible payment fraud" in why["answer"]
        pay = client.post(f"/api/runs/{r['id']}/ask", json={"question": "How much can we pay?"}).json()
        assert "Nothing is payable" in pay["answer"]
        nxt = client.post(f"/api/runs/{r['id']}/ask", json={"question": "What should I do next?"}).json()
        assert "master-file number" in nxt["answer"]
        assert client.post(f"/api/runs/{r['id']}/ask", json={"question": "  "}).status_code == 400


# --------------------------------------------------------------------------- analytics
def test_analytics():
    with TestClient(app) as client:
        for f in ("01_happy_northwind.pdf", "05_edge_duplicate_northwind.pdf", "06_edge_bank_change_bluepeak.pdf",
                  "10_extra_foreign_currency_eur.pdf"):
            _wait(client, client.post(f"/api/runs/sample/{f}").json()["id"])
        a = client.get("/api/analytics").json()
        assert a["totals"]["invoices"] == 4
        assert a["protected"]["duplicates"] == 1 and a["protected"]["duplicates_usd"] == 2339.98
        assert a["protected"]["fraud_signals"] == 1
        assert {c["currency"] for c in a["currencies"]} == {"USD", "EUR"}
        assert a["payment_calendar"] and a["exception_reasons"] and a["vendors"]
        assert a["sla"]["pending"] == 1
