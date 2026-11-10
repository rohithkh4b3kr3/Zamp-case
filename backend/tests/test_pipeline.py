"""End-to-end and unit tests for the invoice pipeline.

Run:  .venv/Scripts/python -m pytest -q
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pymupdf
import pytest

import os  # noqa: E402

os.environ["ZAMP_NO_WORKERS"] = "1"  # no background inbox/SLA threads in tests
# tests are deterministic and offline: never call a paid LLM even if backend/.env has a key
for _k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
    os.environ[_k] = ""
os.environ["EXTRACTION_MODE"] = "local"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import config, db  # noqa: E402
from app.extraction import _split_merged, normalize_po, parse_date  # noqa: E402
from app.pipeline import process  # noqa: E402
from generate_samples import BASE_CSS, BILL_TO  # noqa: E402

ALL_SAMPLES = json.loads((config.SAMPLES_DIR / "catalog.json").read_text(encoding="utf-8"))
# single-invoice samples; the multi-invoice batch is split by the API and has its own test
SAMPLES = [s for s in ALL_SAMPLES if "multi_invoice" not in s["file"] and s["kind"] != "learning"]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LIVE_REFERENCE_PATH", tmp_path / "reference_live.json")  # never touch real data
    config._ref_cache["data"] = None
    db._conn = None
    db.init_db()
    db.update_settings({"stage_min_ms": 0})
    yield
    # let background pipeline threads finish before the connection goes away
    import time as _t
    for _ in range(240):
        if not any(r["status"] in ("queued", "running") for r in db.list_runs(1000)):
            break
        _t.sleep(0.25)
    with db._lock:
        db._conn.close()
        db._conn = None


def run_file(path: Path, tmp_path: Path) -> dict:
    rid = uuid.uuid4().hex
    dest = tmp_path / f"{rid[:6]}_{path.name}"
    shutil.copyfile(path, dest)
    db.create_run(rid, path.name, str(dest), "test")
    process(rid, str(dest), path.name)
    run = db.get_run(rid)
    assert run["status"] == "completed", run["error"]
    return run


def make_pdf(tmp_path: Path, name: str, pages: list[str]) -> Path:
    doc = pymupdf.open()
    for html in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 802), html, css=BASE_CSS)
    p = tmp_path / name
    doc.save(p)
    return p


# --------------------------------------------------------------------------- prepared demo set
@pytest.mark.parametrize("sample", SAMPLES, ids=[s["file"] for s in SAMPLES])
def test_sample_decisions(sample, tmp_path):
    run = run_file(config.SAMPLES_DIR / sample["file"], tmp_path)
    assert run["decision"] == sample["expected"], run["decision_detail"]["headline"]


def test_routes_and_amounts(tmp_path):
    by = {s["file"]: run_file(config.SAMPLES_DIR / s["file"], tmp_path) for s in SAMPLES}
    meridian = by["03_edge_split_billing_meridian.pdf"]
    assert meridian["route_to"] == "Procurement"
    assert meridian["decision_detail"]["amounts"]["recommended_payment"] == 4200.0
    assert meridian["decision_detail"]["amounts"]["on_hold"] == 1850.0
    assert by["04_edge_no_po_partial_receipt_crestline.pdf"]["route_to"] == "Receiving"
    assert by["04_edge_no_po_partial_receipt_crestline.pdf"]["decision_detail"]["po_source"] == "inferred"
    bp = by["06_edge_bank_change_bluepeak.pdf"]
    assert bp["route_to"] == "Treasury & Security"
    assert bp["decision_detail"]["vendor_email"] is None  # never reply to a possible fraudster
    assert by["01_happy_northwind.pdf"]["ledger_posted"] == 1


def test_rerun_same_invoice_is_duplicate(tmp_path):
    first = run_file(config.SAMPLES_DIR / "01_happy_northwind.pdf", tmp_path)
    second = run_file(config.SAMPLES_DIR / "01_happy_northwind.pdf", tmp_path)
    assert first["decision"] == "APPROVED"
    assert second["decision"] == "REJECTED"


def test_tighter_price_tolerance_changes_outcome(tmp_path):
    db.update_settings({"price_tolerance_pct": 1.0})
    run = run_file(config.SAMPLES_DIR / "04_edge_no_po_partial_receipt_crestline.pdf", tmp_path)
    checks = [c for s in run["stages"] for c in s["checks"] if c["id"] == "price"]
    assert any(c["status"] == "fail" for c in checks)


def test_approval_limit(tmp_path):
    db.update_settings({"approval_limit": 5000})
    run = run_file(config.SAMPLES_DIR / "01_happy_northwind.pdf", tmp_path)
    assert run["decision"] == "NEEDS_REVIEW"
    assert run["route_to"] == "Finance Manager"


# --------------------------------------------------------------------------- unseen formats
def test_unknown_vendor_goes_to_procurement(tmp_path):
    p = make_pdf(tmp_path, "acme.pdf", [f"""
        <h1>Acme Widgets GmbH</h1><p>Tax ID: 12-3456789</p>
        <p><b>Invoice Number:</b> AW-5001<br><b>Invoice Date:</b> 2026-11-03</p>{BILL_TO}
        <table class="items"><tr><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
        <tr><td>Widget</td><td>10</td><td>50.00</td><td>500.00</td></tr></table>
        <p>Subtotal 500.00</p><p><b>Total Due</b> 500.00</p>"""])
    run = run_file(p, tmp_path)
    assert run["decision"] == "NEEDS_REVIEW"
    assert run["route_to"] == "Procurement"


def test_bundled_invoice_without_line_items(tmp_path):
    p = make_pdf(tmp_path, "bundled.pdf", [f"""
        <h1>Northwind Office Supplies Inc.</h1><p>Tax ID: 94-3318275</p>
        <p><b>Bill No:</b> NW-77001<br><b>Date:</b> 11/05/2026<br><b>Order Ref:</b> PO-2026-0412</p>{BILL_TO}
        <p>Office furniture and paper as per order (bundled)</p>
        <table class="totals"><tr><td>Subtotal</td><td>8,050.00</td></tr>
        <tr><td>Sales Tax (8%)</td><td>644.00</td></tr><tr><td><b>Amount Due</b></td><td>8,694.00</td></tr></table>"""])
    run = run_file(p, tmp_path)
    ex = run["extracted"]
    assert ex["invoice_number"] == "NW-77001"
    assert ex["invoice_date"] == "2026-11-05"
    assert ex["po_number"] == "PO-2026-0412"
    assert ex["total"] == 8694.0
    assert run["decision"] in ("APPROVED", "NEEDS_REVIEW")  # header-level match, no crash


def test_tax_inclusive_total(tmp_path):
    p = make_pdf(tmp_path, "incl.pdf", [f"""
        <h1>Harbor Print &amp; Signage</h1>
        <p><b>Invoice #:</b> HPS-4410<br><b>Invoice Date:</b> 5 Nov 2026<br><b>PO Number:</b> PO-2026-0461</p>{BILL_TO}
        <table class="items"><tr><th>Item</th><th>Description</th><th>Qty</th><th>Rate</th><th>Amount</th></tr>
        <tr><td>HP-BAN-6X3</td><td>Vinyl banner 6x3 ft</td><td>2</td><td>145.00</td><td>290.00</td></tr></table>
        <p><b>Total (incl. tax)</b> 290.00</p><p>Includes sales tax of 21.48</p>"""])
    run = run_file(p, tmp_path)
    assert run["extracted"]["tax_inclusive"] is True
    assert run["extracted"]["invoice_date"] == "2026-11-05"


def test_multipage_invoice(tmp_path):
    p = make_pdf(tmp_path, "multi.pdf", [
        f"""<h1>Crestline Industrial Parts</h1><p>Federal Tax ID 36-4471120</p>
            <p><b>Invoice Number:</b> CIP-90001<br><b>Invoice Date:</b> November 8, 2026<br><b>PO Number:</b> PO-2026-0449</p>{BILL_TO}
            <p>Continued on next page</p>""",
        """<table class="items"><tr><th>Description</th><th>Part No.</th><th>Qty</th><th>Unit Price</th><th>Ext. Price</th></tr>
           <tr><td>Pressure relief safety valve 2 in.</td><td>CR-SV-0200</td><td>10</td><td>240.00</td><td>2,400.00</td></tr></table>
           <p>Merchandise Total 2,400.00</p><p>Sales Tax 8.00% 192.00</p><p><b>Invoice Total</b> 2,592.00</p>""",
    ])
    run = run_file(p, tmp_path)
    assert len(run["extracted"]["line_items"]) == 1
    assert run["extracted"]["total"] == 2592.0
    assert run["decision"] == "APPROVED"  # GRN-88402 received the 10 valves
    # field boxes point at the right page
    assert run["extracted"]["field_boxes"]["invoice_number"]["page"] == 1
    assert run["extracted"]["line_items"][0]["box"]["page"] == 2


def test_po_belonging_to_other_vendor(tmp_path):
    p = make_pdf(tmp_path, "wrongpo.pdf", [f"""
        <h1>Northwind Office Supplies Inc.</h1><p>Tax ID: 94-3318275</p>
        <p><b>Invoice No:</b> INV-NW-10999<br><b>Invoice Date:</b> 2026-11-06<br><b>PO Number:</b> PO-2026-0430</p>{BILL_TO}
        <table class="items"><tr><th>SKU</th><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
        <tr><td>NW-PP-A4</td><td>A4 copy paper</td><td>10</td><td>6.50</td><td>65.00</td></tr></table>
        <p>Subtotal 65.00</p><p>Sales Tax (8%) 5.20</p><p><b>Total Due</b> 70.20</p>"""])
    run = run_file(p, tmp_path)
    assert run["decision"] == "NEEDS_REVIEW"
    assert any(c["id"] == "po_vendor" for s in run["stages"] for c in s["checks"])


def test_non_pdf_garbage_does_not_crash(tmp_path):
    p = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(p)
    rid = uuid.uuid4().hex
    db.create_run(rid, "blank.pdf", str(p), "test")
    process(rid, str(p), "blank.pdf")
    run = db.get_run(rid)
    assert run["status"] in ("completed", "failed")
    if run["status"] == "completed":
        assert run["decision"] != "APPROVED"


# --------------------------------------------------------------------------- ai24c-style features
def test_field_boxes_for_text_and_scan(tmp_path):
    for f in ("01_happy_northwind.pdf", "02_scanned_harbor.pdf"):
        ex = run_file(config.SAMPLES_DIR / f, tmp_path)["extracted"]
        boxes = ex["field_boxes"]
        for key in ("invoice_number", "invoice_date", "po_number", "total"):
            b = boxes[key]
            assert b["page"] == 1 and 0 <= b["x0"] < b["x1"] <= 1 and 0 <= b["y0"] < b["y1"] <= 1, (f, key, b)
        # invoice number box is a single cell, not a whole row
        assert boxes["invoice_number"]["x1"] - boxes["invoice_number"]["x0"] < 0.5, (f, boxes["invoice_number"])
        assert all("box" in li for li in ex["line_items"])
        json.dumps(ex)  # serialisable (no numpy floats)


def test_multi_invoice_pdf_is_split():
    from app.extraction import classify_pages, group_pages
    pages = classify_pages(str(config.SAMPLES_DIR / "09_extra_multi_invoice_batch_crestline.pdf"))
    assert group_pages(pages) == [[1], [2, 3]]
    for f in ("01_happy_northwind.pdf", "05_edge_duplicate_northwind.pdf"):
        assert group_pages(classify_pages(str(config.SAMPLES_DIR / f))) == [[1]]


def test_batch_sample_end_to_end(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    import time
    with TestClient(app) as client:
        r = client.post("/api/runs/sample/09_extra_multi_invoice_batch_crestline.pdf").json()
        assert len(r["ids"]) == 2
        for _ in range(120):
            runs = [client.get(f"/api/runs/{i}").json() for i in r["ids"]]
            if all(x["status"] == "completed" for x in runs):
                break
            time.sleep(0.25)
        assert [x["invoice_number"] for x in runs] == ["CIP-78002", "CIP-78003"]
        assert [x["decision"] for x in runs] == ["APPROVED", "APPROVED"]
        assert runs[1]["extracted"]["total"] == 1728.0  # totals were on page 3
        assert runs[0]["batch"] and len(runs[0]["batch"]) == 2


def test_correction_reruns_pipeline(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    import time
    with TestClient(app) as client:
        rid = client.post("/api/runs/sample/08_extra_missing_fields_harbor.pdf").json()["id"]
        for _ in range(80):
            run = client.get(f"/api/runs/{rid}").json()
            if run["status"] == "completed":
                break
            time.sleep(0.25)
        assert run["decision"] == "REJECTED"
        bad = client.post(f"/api/runs/{rid}/correct", json={"fields": {"invoice_date": "not a date"}})
        assert bad.status_code == 400
        new = client.post(f"/api/runs/{rid}/correct",
                          json={"fields": {"invoice_number": "HPS-3320", "invoice_date": "Nov 6, 2026"}, "reviewer": "T"}).json()["id"]
        for _ in range(80):
            run2 = client.get(f"/api/runs/{new}").json()
            if run2["status"] == "completed":
                break
            time.sleep(0.25)
        assert run2["parent_run_id"] == rid
        assert run2["extracted"]["invoice_number"] == "HPS-3320"
        assert run2["extracted"]["invoice_date"] == "2026-11-06"
        assert run2["extracted"]["corrected_fields"]["invoice_number"]["was"] is None
        assert client.get(f"/api/runs/{rid}").json()["superseded_by"] == new
        # banners were already billed in full by the earlier invoice -> still not payable, but no longer for missing fields
        assert "required_fields" not in [c["id"] for s in run2["stages"] for c in s["checks"] if c["status"] == "fail"]
        assert client.post(f"/api/runs/{rid}/correct", json={"fields": {"total": 1}}).status_code == 409


def test_export(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    run = run_file(config.SAMPLES_DIR / "01_happy_northwind.pdf", tmp_path)
    with TestClient(app) as client:
        j = client.get(f"/api/runs/{run['id']}/export?format=json")
        assert j.status_code == 200 and j.json()["extracted"]["invoice_number"] == "INV-NW-10611"
        c = client.get(f"/api/runs/{run['id']}/export?format=csv")
        assert "invoice_number,INV-NW-10611" in c.text and "NW-CH-220" in c.text


# --------------------------------------------------------------------------- unit
@pytest.mark.parametrize("s,iso", [
    ("2026-11-04", "2026-11-04"), ("09/28/2026", "2026-09-28"), ("06 Nov 2026", "2026-11-06"),
    ("Nov 5, 2026", "2026-11-05"), ("November 7, 2026", "2026-11-07"), ("nonsense", None),
])
def test_parse_date(s, iso):
    assert parse_date(s) == iso


@pytest.mark.parametrize("s,po", [("PO-2026-0412", "PO-2026-0412"), ("PO 2026-0390", "PO-2026-0390"), ("2026/398", "PO-2026-0398")])
def test_normalize_po(s, po):
    assert normalize_po(s) == po


def test_split_merged_ocr_tokens():
    assert _split_merged("20001.15", 2300.0) == (2000.0, 1.15)
    assert _split_merged("4145.00", 580.0) == (4.0, 145.0)
    assert _split_merged("12.00", 999.0) is None
