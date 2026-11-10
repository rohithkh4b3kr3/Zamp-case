"""Custom user rules: CRUD, validation, dry-run and effect on decisions."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from test_pipeline import config, db, fresh_db, run_file  # noqa: F401

from app.main import app


def _wait(client, rid, n=160):
    for _ in range(n):
        r = client.get(f"/api/runs/{rid}").json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.25)
    raise AssertionError("run did not finish")


def test_rules_crud_and_validation():
    with TestClient(app) as c:
        sch = c.get("/api/rules/schema").json()
        assert {"fields", "ops", "outcomes", "teams"} <= set(sch) and "Procurement" in sch["teams"]
        assert c.get("/api/teams").json()[-1] == "Vendor"
        bad = [
            {"name": "", "field": "total", "op": "gt", "value": 1, "outcome": "review"},
            {"name": "x", "field": "nope", "op": "gt", "value": 1, "outcome": "review"},
            {"name": "x", "field": "vendor_name", "op": "gt", "value": 1, "outcome": "review"},
            {"name": "x", "field": "total", "op": "gt", "value": "abc", "outcome": "review"},
            {"name": "x", "field": "total", "op": "gt", "value": 1, "outcome": "maybe"},
            {"name": "x", "field": "total", "op": "gt", "value": 1, "outcome": "review", "route_to": "Nobody"},
        ]
        for b in bad:
            assert c.post("/api/rules", json=b).status_code == 400, b
        r = c.post("/api/rules", json={"name": "Big invoices", "field": "total_usd", "op": "gt", "value": "10,000",
                                       "outcome": "review", "route_to": "Finance Manager", "message": "Over 10k needs CFO eyes"}).json()
        assert r["value"] == 10000.0 and r["enabled"] is True
        u = c.put(f"/api/rules/{r['id']}", json={"enabled": False}).json()
        assert u["enabled"] is False and u["name"] == "Big invoices"
        assert len(c.get("/api/rules").json()) == 1
        assert c.delete(f"/api/rules/{r['id']}").status_code == 200
        assert c.delete(f"/api/rules/{r['id']}").status_code == 404
        # review without a team defaults to AP Specialist; reject defaults to Vendor
        d = c.post("/api/rules", json={"name": "No PO", "field": "po_number", "op": "missing", "outcome": "reject"}).json()
        assert d["route_to"] == "Vendor" and d["value"] is None


def test_rule_changes_decision_and_shows_as_check():
    with TestClient(app) as c:
        rule = c.post("/api/rules", json={"name": "Northwind needs Procurement sign-off", "field": "vendor_name",
                                          "op": "contains", "value": "northwind", "outcome": "review",
                                          "route_to": "Procurement", "message": "Vendor on watch-list"}).json()
        run = _wait(c, c.post("/api/runs/sample/01_happy_northwind.pdf").json()["id"])
        assert run["decision"] == "NEEDS_REVIEW" and run["route_to"] == "Procurement"
        chk = next(ch for s in run["stages"] for ch in s["checks"] if ch["id"] == f"custom_{rule['id']}")
        assert chk["status"] == "fail" and chk["label"] == "Northwind needs Procurement sign-off"
        assert c.get("/api/rules").json()[0]["hits"] == 1
        # a non-matching rule is listed as a pass check
        other = _wait(c, c.post("/api/runs/sample/02_scanned_harbor.pdf").json()["id"])
        ok = next(ch for s in other["stages"] for ch in s["checks"] if ch["id"] == f"custom_{rule['id']}")
        assert ok["status"] == "pass" and other["decision"] == "APPROVED"


def test_reject_rule_and_numeric_ops():
    with TestClient(app) as c:
        c.post("/api/rules", json={"name": "Euro cap", "field": "total", "op": "gte", "value": 12700, "outcome": "reject",
                                   "message": "EUR invoices at or above 12,700 are not accepted"})
        run = _wait(c, c.post("/api/runs/sample/10_extra_foreign_currency_eur.pdf").json()["id"])
        assert run["decision"] == "REJECTED"


def test_dry_run():
    with TestClient(app) as c:
        a = _wait(c, c.post("/api/runs/sample/01_happy_northwind.pdf").json()["id"])
        _wait(c, c.post("/api/runs/sample/06_edge_bank_change_bluepeak.pdf").json()["id"])
        rule = {"name": "Over 10k", "field": "total_usd", "op": "gt", "value": 10000, "outcome": "review"}
        one = c.post("/api/rules/test", json={"rule": rule, "run_id": a["id"]}).json()
        assert one["matched"] is False and "total_usd" in one["detail"]
        allr = c.post("/api/rules/test", json={"rule": rule}).json()
        assert allr["matched"] is True and len(allr["hits"]) == 1 and allr["hits"][0]["invoice_number"] == "BPC-2026-1107"
        assert c.post("/api/rules/test", json={"rule": {**rule, "op": "contains"}}).status_code == 400
        assert c.get("/api/rules").json() == []  # dry-run never saves


def test_generate_rules_from_plain_english():
    with TestClient(app) as c:
        _wait(c, c.post("/api/runs/sample/06_edge_bank_change_bluepeak.pdf").json()["id"])
        g = c.post("/api/rules/generate", json={"text": "Send invoices over $10,000 to the Finance Manager"}).json()
        assert g["mode"] == "rules"  # no LLM key in tests -> phrase parser
        d = g["drafts"][0]
        assert d["valid"] and d["rule"]["field"] == "total_usd" and d["rule"]["op"] == "gt" and d["rule"]["value"] == 10000
        assert d["rule"]["route_to"] == "Finance Manager" and d["preview"]["matched"] == 1  # the $19,440 Bluepeak run
        assert c.get("/api/rules").json() == []  # generating never saves
        nothing = c.post("/api/rules/generate", json={"text": "make it nice please"}).json()
        assert nothing["drafts"] == [] and "No condition recognised" in nothing["notes"]
        assert c.post("/api/rules/generate", json={"text": "hi"}).status_code == 400
        # a draft can be saved as-is through the normal API
        saved = c.post("/api/rules", json=d["rule"]).json()
        assert saved["name"] and saved["value"] == 10000


def test_and_conditions_and_generator_combines():
    with TestClient(app) as c:
        _wait(c, c.post("/api/runs/sample/06_edge_bank_change_bluepeak.pdf").json()["id"])
        _wait(c, c.post("/api/runs/sample/10_extra_foreign_currency_eur.pdf").json()["id"])
        g = c.post("/api/rules/generate", json={"text": "Send invoices over $10,000 from Bluepeak to the Finance Manager"}).json()
        assert len(g["drafts"]) == 1 and "AND" in g["notes"]
        d = g["drafts"][0]
        assert d["rule"]["conditions"] == [{"field": "vendor_name", "op": "contains", "value": "Bluepeak"}]
        assert d["preview"]["matched"] == 1 and d["preview"]["of"] == 2  # EUR invoice is over 10k but not Bluepeak
        saved = c.post("/api/rules", json=d["rule"]).json()
        assert saved["conditions"][0]["field"] == "vendor_name"
        assert "and Vendor name contains Bluepeak" in c.post("/api/rules/test", json={"rule": saved}).json()["describe"]
        bad = c.post("/api/rules", json={**d["rule"], "conditions": [{"field": "vendor_name", "op": "gt", "value": 1}]})
        assert bad.status_code == 400
        orr = c.post("/api/rules/generate", json={"text": "Flag invoices over $5k or with no PO for procurement"}).json()
        assert len(orr["drafts"]) == 2
        # the saved AND rule fires only on the Bluepeak run
        run = _wait(c, c.post("/api/runs/sample/06_edge_bank_change_bluepeak.pdf").json()["id"])
        assert any(ch["id"] == f"custom_{saved['id']}" and ch["status"] == "fail" for s in run["stages"] for ch in s["checks"])
