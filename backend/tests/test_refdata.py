"""Spreadsheet import, live sources, PO edits and manual PO choice."""
from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient

from test_pipeline import config, db, fresh_db, run_file  # noqa: F401

from app import refdata
from app.main import app
import json

SEED_POS = json.loads(config.REFERENCE_PATH.read_text(encoding="utf-8"))["purchase_orders"]

PO_CSV = (
    "PO #,Supplier,Order date,Line,Part No,Item description,Quantity,Unit Price,Tax %\n"
    "PO-2026-0601,Northwind Office Supplies,2026-11-01,1,NW-CH-220,Ergonomic mesh office chair,10,185.00,8\n"
    "PO-2026-0601,Northwind Office Supplies,2026-11-01,2,NW-PP-A4,A4 copy paper,20,6.50,8\n"
    "PO-2026-0602,Unknown Corp,2026-11-01,1,X-1,Widget,5,10,0\n"
    "PO-2026-0603,V-1004,2026-11-02,1,CR-HF-0750,Hydraulic fitting,abc,12.40,8\n"
    "PO-2026-0412,V-1001,2026-10-02,1,NW-CH-220,Ergonomic mesh office chair,40,185.00,8\n"
    "PO-2026-0412,V-1001,2026-10-02,2,NW-PP-A4,\"A4 copy paper, 80gsm (ream)\",100,6.75,8\n"
)


def _wait(client, rid, n=160):
    for _ in range(n):
        r = client.get(f"/api/runs/{rid}").json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.25)
    raise AssertionError


def test_auto_mapping_and_preview_errors():
    with TestClient(app) as c:
        up = c.post("/api/reference/import/upload", data={"kind": "po"},
                    files={"file": ("pos.csv", PO_CSV.encode(), "text/csv")}).json()
        m = up["mapping"]
        assert m["po_number"] == "PO #" and m["vendor"] == "Supplier" and m["qty"] == "Quantity"
        assert m["unit_price"] == "Unit Price" and m["sku"] == "Part No" and m["description"] == "Item description"
        assert m["tax_rate"] == "Tax %" and up["row_count"] == 6
        p = c.post("/api/reference/import/preview", json={"token": up["token"]}).json()
        assert p["new"] == ["PO-2026-0601"] and p["updated"] == ["PO-2026-0412"]
        msgs = {e["row"]: e["message"] for e in p["errors"]}
        assert "not in the vendor master" in msgs[4] and "must be a number" in msgs[5]
        # a required field left unmapped is a clear error
        bad = c.post("/api/reference/import/preview", json={"token": up["token"], "mapping": {**m, "qty": None}})
        assert bad.status_code == 400 and bad.json()["detail"].startswith("Import failed: no column mapped to Quantity")


def test_commit_merge_then_pipeline_uses_imported_po(tmp_path):
    with TestClient(app) as c:
        up = c.post("/api/reference/import/upload", data={"kind": "po"},
                    files={"file": ("pos.csv", PO_CSV.encode(), "text/csv")}).json()
        res = c.post("/api/reference/import/commit", json={"token": up["token"], "mode": "merge"}).json()
        assert res["new"] == 1 and res["updated"] == 1 and res["skipped_rows"] == 2
        po = c.get("/api/reference/po/PO-2026-0412").json()
        assert po["lines"][1]["unit_price"] == 6.75  # updated from the sheet
        assert c.get("/api/reference/po/PO-2026-0601").json()["tax_rate"] == 0.08  # "8" -> 8%
        # the happy-path invoice bills paper at 6.50 vs the new PO price 6.75: -3.7% is outside ±2%
        run = run_file(config.SAMPLES_DIR / "01_happy_northwind.pdf", tmp_path)
        price = [ch for s in run["stages"] for ch in s["checks"] if ch["id"] == "price"]
        assert price and price[0]["status"] == "fail"
        hist = c.get("/api/reference/history").json()
        assert hist[0]["action"] == "ref_import_po" and "1 new, 1 updated" in hist[0]["detail"]


def test_replace_mode_and_reset():
    with TestClient(app) as c:
        csv_ = "po_number,vendor,line,description,qty,unit_price\nPO-9,V-1001,1,Thing,1,5\n"
        up = c.post("/api/reference/import/upload", data={"kind": "po"}, files={"file": ("p.csv", csv_.encode(), "text/csv")}).json()
        p = c.post("/api/reference/import/preview", json={"token": up["token"]}).json()
        assert len(p["removed_if_replace"]) == len(SEED_POS)
        r = c.post("/api/reference/import/commit", json={"token": up["token"], "mode": "replace"}).json()
        assert r["removed"] == len(SEED_POS)
        assert [x["po_number"] for x in c.get("/api/reference/pos").json()] == ["PO-9"]
        c.post("/api/reset")
        assert len(c.get("/api/reference/pos").json()) == len(SEED_POS)  # back to seed data


def test_xlsx_vendor_and_grn_import():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Vendor Code", "Supplier Name", "VAT", "Billing Email", "Bank", "IBAN", "Currency"])
    ws.append(["V-2001", "Fjord Paper AS", "NO987654321", "faktura@fjord.example", "Nordic Bank", "NO93 8601 1117 947", "EUR"])
    ws.append(["", "Bad Email Co", "", "not-an-email", "", "", "USD"])
    buf = io.BytesIO()
    wb.save(buf)
    with TestClient(app) as c:
        up = c.post("/api/reference/import/upload", data={"kind": "vendor"},
                    files={"file": ("v.xlsx", buf.getvalue(), "application/octet-stream")}).json()
        assert up["mapping"]["vendor_id"] == "Vendor Code" and up["mapping"]["account_number"] == "IBAN"
        r = c.post("/api/reference/import/commit", json={"token": up["token"]}).json()
        assert r["new"] == 1 and r["skipped_rows"] == 1
        v = next(x for x in c.get("/api/reference").json()["vendors"] if x["vendor_id"] == "V-2001")
        assert v["currency"] == "EUR" and v["bank"]["account_number"].endswith("7947")
        grn = "Receipt No,PO,Line,Received\nGRN-7,PO-2026-0449,1,4\nGRN-8,PO-2026-0449,9,1\n"
        up = c.post("/api/reference/import/upload", data={"kind": "grn"}, files={"file": ("g.csv", grn.encode(), "text/csv")}).json()
        p = c.post("/api/reference/import/preview", json={"token": up["token"]}).json()
        assert p["new"] == ["GRN-7"] and "has no line 9" in p["errors"][0]["message"]


def test_template_downloads():
    with TestClient(app) as c:
        t = c.get("/api/reference/template/po")
        assert t.status_code == 200 and t.text.startswith("po_number,vendor,")
        x = c.get("/api/reference/template/vendor?format=xlsx")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        # a template round-trips through the importer cleanly
        up = c.post("/api/reference/import/upload", data={"kind": "po"}, files={"file": ("t.csv", t.content, "text/csv")}).json()
        p = c.post("/api/reference/import/preview", json={"token": up["token"]}).json()
        assert p["error_count"] == 0 and p["new"] == ["PO-2026-0500"]


def test_live_source(monkeypatch):
    csv_ = "po_number,vendor,line,description,qty,unit_price\nPO-2026-0700,V-1003,1,Freight Chicago to Dallas,2,4200\n"
    monkeypatch.setattr(refdata, "fetch", lambda url: (csv_.encode(), "sheet.csv"))
    monkeypatch.setattr(refdata, "_check_url", lambda url: None)
    with TestClient(app) as c:
        s = c.post("/api/reference/sources", json={"name": "AP PO tracker", "kind": "po",
                                                   "url": "https://docs.google.com/spreadsheets/d/abc123/edit#gid=42", "interval_min": 15}).json()
        assert s["last_status"] == "ok" and "1 new" in s["last_message"]
        assert c.get("/api/reference/po/PO-2026-0700").status_code == 200
        again = c.post(f"/api/reference/sources/{s['id']}/sync").json()
        assert "1 unchanged" in again["last_message"]
        assert c.delete(f"/api/reference/sources/{s['id']}").json() == {"ok": True}


def test_url_rules():
    assert refdata.normalise_url("https://docs.google.com/spreadsheets/d/AbC_1/edit#gid=7") == \
        "https://docs.google.com/spreadsheets/d/AbC_1/export?format=csv&gid=7"
    assert refdata.normalise_url("https://1drv.ms/x/s!abc").endswith("?download=1")
    for bad in ("http://example.com/x.csv", "https://localhost/x.csv", "https://127.0.0.1/x.csv"):
        with pytest.raises(refdata.ImportErrorUser):
            refdata._check_url(bad)


def test_edit_po_and_add_receipt(tmp_path):
    with TestClient(app) as c:
        # the Crestline partial-receipt case: receiving the missing 8 gauges turns review into approve
        r = c.post("/api/reference/po/PO-2026-0455/receipts", json={"lines": [{"line": 2, "qty_received": 8}], "grn": "GRN-99001"})
        assert r.status_code == 200 and r.json()["lines"][1]["received"] == 20
        assert c.post("/api/reference/po/PO-2026-0455/receipts", json={"lines": [{"line": 2, "qty_received": 1}], "grn": "GRN-99001"}).status_code == 400
        run = run_file(config.SAMPLES_DIR / "04_edge_no_po_partial_receipt_crestline.pdf", tmp_path)
        assert run["decision"] == "APPROVED", run["decision_detail"]["headline"]
        po = c.get("/api/reference/po/PO-2026-0461").json()
        lines = [{**l, "unit_price": 1.20} if l["line"] == 1 else l for l in po["lines"]]
        u = c.put("/api/reference/po/PO-2026-0461", json={"lines": lines, "status": "open"}).json()
        assert u["lines"][0]["unit_price"] == 1.2
        assert c.put("/api/reference/po/PO-2026-0461", json={"status": "weird"}).status_code == 400
        h = c.get("/api/reference/history").json()
        assert any("line 1 unit_price: 1.15 → 1.2" in x["detail"] for x in h)
        assert any(x["action"] == "ref_grn_added" for x in h)


def test_manual_po_choice_reruns(tmp_path):
    """Change PO from the run page: the vendor omitted the PO, a person picks it, the pipeline re-runs."""
    with TestClient(app) as c:
        rid = c.post("/api/runs/sample/04_edge_no_po_partial_receipt_crestline.pdf").json()["id"]
        first = _wait(c, rid)
        assert first["decision_detail"]["po_source"] == "inferred"
        pos = c.get("/api/reference/pos?vendor_id=V-1004").json()
        assert {p["po_number"] for p in pos} == {"PO-2026-0455", "PO-2026-0449"}
        new = c.post(f"/api/runs/{rid}/correct", json={"fields": {"po_number": "PO-2026-0455"}, "note": "picked manually"}).json()["id"]
        second = _wait(c, new)
        assert second["decision_detail"]["po_source"] == "explicit" and second["po_number"] == "PO-2026-0455"
