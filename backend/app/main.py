"""HTTP API for the invoice pipeline.

Runs execute in a background thread; the UI follows progress over Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import shutil
import threading
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ask, config, connectors, db, inbox, notify, refdata
from . import rules as custom_rules
from .extraction import (classify_pages, group_pages, llm_available, llm_model, llm_provider, normalize_po, plausible_vendor_name,
                         ocr_engine_name, parse_date, split_pdf)
from .pipeline import STAGES, process

@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    connectors.init()        # intake connectors (cloud storage, SFTP, triggers, reports): session zamp-d0's module
    connectors.wire(_start)
    if not os.getenv("ZAMP_NO_WORKERS"):  # tests turn background workers off
        inbox.start_watchers(_start)
        notify.start_sla_watcher()
        refdata.start_sync_loop()
        connectors.start_scheduler(_start)
    yield


app = FastAPI(title="Zamp AP — Invoice to Decision", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(connectors.router)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _launch(path: str, filename: str, source: str, **kw) -> str:
    run_id = uuid.uuid4().hex
    db.create_run(run_id, filename, path, source, **kw)
    db.audit(run_id, "user" if source == "upload" else "demo", "submitted", filename)
    threading.Thread(target=process, args=(run_id, path, filename), daemon=True).start()
    return run_id


def _start(path: Path, filename: str, source: str, email_id: str | None = None) -> dict:
    """One PDF in -> one run, or one run per invoice when the PDF holds several (ai24c-style page grouping)."""
    pages = []
    try:
        if pymupdf.open(str(path)).page_count > 1:
            pages = classify_pages(str(path))
    except Exception:  # unreadable PDF: let the pipeline report it
        pages = []
    groups = group_pages(pages) if pages else []
    if len(groups) <= 1:
        return {"id": _launch(str(path), filename, source, email_id=email_id), "ids": None}
    batch_id = uuid.uuid4().hex
    stem = Path(filename).stem
    ids = []
    for i, ((part, label), g) in enumerate(zip(split_pdf(str(path), groups, stem), groups), start=1):
        pg = f"page {g[0]}" if len(g) == 1 else f"pages {g[0]}-{g[-1]}"
        ids.append(_launch(part, label, source, batch_id=batch_id, batch_label=f"Invoice {i} of {len(groups)} · {pg}",
                           email_id=email_id))
    db.audit(None, "system", "pdf_split", f"{filename}: {len(groups)} invoices in {len(pages)} pages -> {groups}")
    return {"id": ids[0], "ids": ids, "batch_id": batch_id}


# ------------------------------------------------------------------ runs
@app.post("/api/runs")
async def create_run(file: UploadFile = File(...)):
    name = Path(file.filename or "invoice.pdf").name
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File larger than 15 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Only PDF invoices are accepted")
    dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
    dest.write_bytes(data)
    return _start(dest, name, "upload")


@app.post("/api/runs/sample/{filename}")
def run_sample(filename: str):
    src = (config.SAMPLES_DIR / Path(filename).name)
    if not src.exists() or src.suffix.lower() != ".pdf":
        raise HTTPException(404, "Unknown sample")
    dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{src.name}"
    shutil.copyfile(src, dest)
    return _start(dest, src.name, "sample")


@app.get("/api/runs")
def list_runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    run["audit"] = db.audit_for(run_id)
    run["batch"] = db.batch_runs(run["batch_id"]) if run.get("batch_id") else None
    run["notifications"] = db.list_notifications(run_id)
    em = db.get_email(run["email_id"]) if run.get("email_id") else None
    run["email"] = {k: em[k] for k in ("id", "from_addr", "from_name", "subject", "received_at", "source")} if em else None
    run.pop("file_path", None)
    return run


class Question(BaseModel):
    question: str
    history: list[dict] = []


@app.post("/api/runs/{run_id}/ask")
def ask_run(run_id: str, body: Question):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Ask a question")
    if len(q) > 2000:
        raise HTTPException(400, "Question too long")
    return ask.answer(run, q, body.history)


# ------------------------------------------------------------------ inbox (email intake)
@app.get("/api/inbox")
def list_inbox():
    out = []
    for e in db.list_emails():
        runs = [db.get_run(r) for r in e["run_ids"]]
        e["runs"] = [{"id": r["id"], "status": r["status"], "decision": r["decision"], "invoice_number": r["invoice_number"],
                      "vendor_name": r["vendor_name"], "total": r["total"], "currency": r["currency"], "filename": r["filename"]}
                     for r in runs if r]
        out.append(e)
    return out


@app.get("/api/inbox/stats")
def inbox_stats():
    emails = db.list_emails(1000)
    pending = 0
    for e in emails:
        for rid in e["run_ids"]:
            r = db.get_run(rid)
            if r and r["status"] in ("queued", "running"):
                pending += 1
    return {"total": len(emails), "unprocessed": pending,
            "no_pdf": sum(1 for e in emails if e["status"] == "no_pdf"),
            "duplicates": sum(1 for e in emails if e["status"] == "duplicate_email"),
            "imap_enabled": inbox.imap_enabled(), "drop_folder": str(config.INBOX_DROP_DIR), **inbox.STATUS}


@app.get("/api/inbox/samples")
def inbox_samples():
    out = []
    for p in inbox.sample_emails():
        e = inbox.parse_eml(p.read_bytes())
        out.append({"file": p.name, "from": e["from_addr"], "from_name": e["from_name"], "subject": e["subject"],
                    "attachments": [a["filename"] for a in e["attachments"]], "note": _SAMPLE_NOTES.get(p.name, "")})
    return out


_SAMPLE_NOTES = {
    "01_northwind_invoice.eml": "Normal vendor email with the invoice attached.",
    "02_bluepeak_lookalike_domain.eml": "Bank-change invoice sent from bluepeak-billing.example (look-alike of bluepeak.example).",
    "03_meridian_two_attachments.eml": "Invoice PDF plus a CSV detention log; only the PDF becomes a run.",
    "04_atelier_nord_eur.eml": "German vendor billing in EUR with European number and date formats.",
    "05_no_attachment.eml": "Vendor forgot the attachment; a reply asking for the PDF is drafted.",
}


@app.post("/api/inbox/sample/{filename}")
def inbox_sample(filename: str):
    p = config.SAMPLES_DIR / "emails" / Path(filename).name
    if not p.exists():
        raise HTTPException(404, "Unknown sample email")
    return inbox.ingest(p.read_bytes(), "sample", _start)


@app.post("/api/inbox/upload")
async def inbox_upload(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Email larger than 25 MB")
    name = (file.filename or "").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        raise HTTPException(415, "That's a PDF — use Process invoice, or upload the .eml email it came in")
    try:
        return inbox.ingest(data, "upload", _start)
    except Exception as e:
        raise HTTPException(400, f"Could not read email: {e}")


@app.post("/api/inbox/poll")
def inbox_poll():
    folder = inbox.scan_drop_folder(_start)
    imap_n, err = 0, None
    if inbox.imap_enabled():
        try:
            imap_n = inbox.poll_imap(_start)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    return {"folder": folder, "imap": imap_n, "imap_error": err}


@app.get("/api/inbox/{email_id}")
def inbox_email(email_id: str):
    e = db.get_email(email_id)
    if not e:
        raise HTTPException(404, "Email not found")
    e["notifications"] = [n for n in db.list_notifications() if n.get("email_id") == email_id]
    return e


# ------------------------------------------------------------------ notifications, learning
@app.get("/api/notifications")
def notifications(run_id: str | None = None, limit: int = 100):
    return db.list_notifications(run_id, limit)


@app.post("/api/notifications/check-sla")
def check_sla_now():
    return {"escalated": notify.check_sla()}


@app.get("/api/memory")
def vendor_memory():
    names = {v["vendor_id"]: v["name"] for v in config.load_reference()["vendors"]}
    return [{**m, "vendor_name": names.get(m["vendor_id"])} for m in db.memory_for()]


# ------------------------------------------------------------------ reference data: import, live sheets, edits
def _ref_err(prefix: str, e: Exception):
    raise HTTPException(400, f"{prefix}: {e}")


@app.post("/api/reference/import/upload")
async def ref_upload(kind: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Import failed: the file is empty. Choose a CSV or Excel file with data.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Import failed: the file is larger than 25 MB. Split it and import in parts.")
    try:
        return refdata.stage_upload(kind, data, Path(file.filename or "upload.csv").name)
    except refdata.ImportErrorUser as e:
        _ref_err("Import failed", e)


class ImportStep(BaseModel):
    token: str
    mapping: dict | None = None
    mode: str = "merge"


@app.post("/api/reference/import/preview")
def ref_preview(body: ImportStep):
    try:
        return refdata.preview(body.token, body.mapping)
    except refdata.ImportErrorUser as e:
        _ref_err("Import failed", e)


@app.post("/api/reference/import/commit")
def ref_commit(body: ImportStep):
    try:
        return refdata.commit(body.token, body.mapping, body.mode)
    except refdata.ImportErrorUser as e:
        _ref_err("Import failed", e)


@app.get("/api/reference/template/{kind}")
def ref_template(kind: str, format: str = "csv"):
    if kind not in refdata.FIELDS or format not in ("csv", "xlsx"):
        raise HTTPException(404, "Unknown template")
    data, ctype = refdata.template(kind, format)
    name = {"po": "purchase_orders", "grn": "goods_receipts", "vendor": "vendors"}[kind]
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}_template.{format}"'})


@app.get("/api/reference/fields")
def ref_fields():
    return refdata.FIELDS_PUBLIC


class SourceIn(BaseModel):
    name: str
    kind: str
    url: str
    interval_min: int = 60


@app.get("/api/reference/sources")
def ref_sources():
    return refdata.list_sources()


@app.post("/api/reference/sources")
def ref_add_source(body: SourceIn):
    try:
        return refdata.add_source(body.name, body.kind, body.url, body.interval_min)
    except refdata.ImportErrorUser as e:
        _ref_err("Could not add source", e)


@app.post("/api/reference/sources/{sid}/sync")
def ref_sync_source(sid: str):
    try:
        return refdata.sync_source(sid)
    except KeyError:
        raise HTTPException(404, "Source not found")


@app.delete("/api/reference/sources/{sid}")
def ref_remove_source(sid: str):
    if not refdata.remove_source(sid):
        raise HTTPException(404, "Source not found")
    return {"ok": True}


@app.get("/api/reference/pos")
def ref_pos(vendor_id: str | None = None):
    return refdata.open_pos(vendor_id)


@app.get("/api/reference/po/{po_number}")
def ref_po(po_number: str):
    d = refdata.po_detail(po_number)
    if not d:
        raise HTTPException(404, "PO not found")
    return d


@app.put("/api/reference/po/{po_number}")
def ref_po_update(po_number: str, body: dict):
    try:
        return refdata.update_po(po_number, body)
    except KeyError:
        raise HTTPException(404, "PO not found")
    except refdata.ImportErrorUser as e:
        _ref_err("Update failed", e)


class ReceiptIn(BaseModel):
    lines: list[dict]
    date: str | None = None
    grn: str | None = None


@app.post("/api/reference/po/{po_number}/receipts")
def ref_add_receipt(po_number: str, body: ReceiptIn):
    try:
        return refdata.add_receipt(po_number, body.lines, body.date, body.grn)
    except KeyError:
        raise HTTPException(404, "PO not found")
    except refdata.ImportErrorUser as e:
        _ref_err("Receipt not saved", e)


@app.get("/api/reference/history")
def ref_history(limit: int = 200):
    return refdata.history(limit)


# ------------------------------------------------------------------ custom rules
@app.get("/api/rules")
def rules_list():
    return custom_rules.list_rules()


@app.get("/api/rules/schema")
def rules_schema():
    return custom_rules.schema()


@app.get("/api/teams")
def teams():
    return custom_rules.teams()


@app.post("/api/rules")
def rules_create(body: dict):
    try:
        return custom_rules.create(body)
    except custom_rules.RuleError as e:
        raise HTTPException(400, str(e))


@app.put("/api/rules/{rule_id}")
def rules_update(rule_id: str, body: dict):
    try:
        return custom_rules.update(rule_id, body)
    except KeyError:
        raise HTTPException(404, "Rule not found")
    except custom_rules.RuleError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/rules/{rule_id}")
def rules_delete(rule_id: str):
    if not custom_rules.delete(rule_id):
        raise HTTPException(404, "Rule not found")
    return {"ok": True}


class RuleGen(BaseModel):
    text: str


@app.post("/api/rules/generate")
def rules_generate(body: RuleGen):
    """Plain-English policy -> draft rules (AI when a key is configured, phrase parser otherwise). Never saves."""
    try:
        return custom_rules.generate(body.text)
    except custom_rules.RuleError as e:
        raise HTTPException(400, str(e))


class RuleTest(BaseModel):
    rule: dict
    run_id: str | None = None


@app.post("/api/rules/test")
def rules_test(body: RuleTest):
    """Dry-run a (possibly unsaved) rule against one run, or against every completed run when run_id is omitted."""
    try:
        rule = custom_rules.validate(body.rule)
    except custom_rules.RuleError as e:
        raise HTTPException(400, str(e))
    if body.run_id:
        run = db.get_run(body.run_id)
        if not run or not run.get("extracted"):
            raise HTTPException(404, "Run not found or has no extracted data")
        matched, detail = custom_rules.evaluate(rule, custom_rules.facts_for_run(run))
        return {"matched": matched, "detail": detail, "describe": custom_rules.describe(rule)}
    hits = []
    runs = [r for r in db.list_runs(1000) if r["status"] == "completed" and not r.get("superseded_by")]
    for r in runs:
        full = db.get_run(r["id"])
        if not full.get("extracted"):
            continue
        matched, detail = custom_rules.evaluate(rule, custom_rules.facts_for_run(full))
        if matched:
            hits.append({"run_id": r["id"], "vendor_name": r["vendor_name"], "invoice_number": r["invoice_number"],
                         "total": r["total"], "currency": r["currency"], "detail": detail})
    return {"matched": bool(hits), "detail": f"Would trigger on {len(hits)} of {len(runs)} processed invoice(s)",
            "describe": custom_rules.describe(rule), "hits": hits[:50]}


@app.delete("/api/memory/{mem_id}")
def forget(mem_id: int):
    if not db.delete_memory(mem_id):
        raise HTTPException(404, "Rule not found")
    db.audit(None, "user", "memory_deleted", str(mem_id))
    return {"ok": True}


CORRECTABLE = {"vendor_name": str, "vendor_tax_id": str, "invoice_number": str, "invoice_date": str,
               "due_date": str, "po_number": str, "currency": str,
               "subtotal": float, "tax_amount": float, "total": float}


class Correction(BaseModel):
    fields: dict
    reviewer: str = "AP Reviewer"
    note: str = ""


@app.post("/api/runs/{run_id}/correct")
def correct(run_id: str, body: Correction):
    """Reviewer fixes extracted values -> the whole pipeline re-runs on the same file with those values.
    The original run stays untouched (audit trail) and is marked as superseded."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] != "completed":
        raise HTTPException(409, "Run still in progress")
    if run["ledger_posted"]:
        raise HTTPException(409, "Invoice already posted to the ledger; corrections would double-post")
    if run.get("superseded_by"):
        raise HTTPException(409, "A corrected version of this run already exists")
    clean = {}
    for k, v in body.fields.items():
        if k not in CORRECTABLE:
            raise HTTPException(400, f"Field '{k}' cannot be corrected")
        if v in (None, ""):
            clean[k] = None
            continue
        try:
            clean[k] = round(float(str(v).replace(",", "").replace("$", "")), 2) if CORRECTABLE[k] is float else str(v).strip()
        except ValueError:
            raise HTTPException(400, f"'{v}' is not a valid amount for {k}")
        if k in ("invoice_date", "due_date"):
            iso = parse_date(clean[k])
            if not iso:
                raise HTTPException(400, f"'{v}' is not a recognisable date")
            clean[k] = iso
        if k == "po_number":
            clean[k] = normalize_po(clean[k]) or clean[k]
    if not clean:
        raise HTTPException(400, "No corrections given")
    merged = {**(run.get("overrides") or {}), **clean}
    new_id = _launch(run["file_path"], run["filename"], run["source"], batch_id=run.get("batch_id"),
                     batch_label=run.get("batch_label"), parent_run_id=run_id, overrides=merged)
    db.update_run(run_id, superseded_by=new_id,
                  review_status="superseded" if run["review_status"] == "pending" else run["review_status"])
    db.audit(run_id, body.reviewer, "corrected_and_rerun",
             f"{', '.join(f'{k}={v}' for k, v in clean.items())}{' — ' + body.note if body.note else ''} → run {new_id[:8]}")
    db.audit(new_id, body.reviewer, "created_from_correction", f"from run {run_id[:8]}")
    return {"id": new_id}


@app.get("/api/runs/{run_id}/export")
def export(run_id: str, format: str = "json"):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    ex = run.get("extracted") or {}
    dec = run.get("decision_detail") or {}
    stem = Path(run["filename"]).stem
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["field", "value"])
        for k in ("vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "po_number",
                  "currency", "subtotal", "tax_amount", "total"):
            w.writerow([k, ex.get(k)])
        w.writerow(["decision", run.get("decision")])
        w.writerow(["route_to", run.get("route_to")])
        w.writerow(["headline", dec.get("headline")])
        w.writerow([])
        w.writerow(["line", "sku", "description", "qty", "unit_price", "amount"])
        for i, li in enumerate(ex.get("line_items", []), start=1):
            w.writerow([i, li.get("sku"), li.get("description"), li.get("qty"), li.get("unit_price"), li.get("amount")])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{stem}_extraction.csv"'})
    payload = {"run_id": run_id, "file": run["filename"], "extracted": {k: v for k, v in ex.items() if k != "raw_text_excerpt"},
               "decision": dec, "stages": [{"key": s["key"], "status": s["status"], "summary": s["summary"],
                                            "checks": s["checks"]} for s in run["stages"]]}
    return Response(json.dumps(payload, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{stem}_result.json"'})


@app.get("/api/runs/{run_id}/file")
def run_file(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return FileResponse(run["file_path"], media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{run["filename"]}"'})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """SSE stream: pushes the full run snapshot whenever it changes, ends when the run finishes."""
    if not db.get_run(run_id):
        raise HTTPException(404, "Run not found")

    async def gen():
        last = None
        for _ in range(1200):  # hard stop after ~5 minutes
            run = db.get_run(run_id)
            if run["updated_at"] != last:
                last = run["updated_at"]
                run.pop("file_path", None)
                yield f"data: {json.dumps(run, default=str)}\n\n"
            if run["status"] in ("completed", "failed"):
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Review(BaseModel):
    action: str  # approve | reject
    reviewer: str = "AP Reviewer"
    note: str = ""


@app.post("/api/runs/{run_id}/review")
def review(run_id: str, body: Review):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] != "completed":
        raise HTTPException(409, "Run still in progress")
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if run["review_status"] in ("approved", "rejected"):
        raise HTTPException(409, f"Already {run['review_status']} by {run['reviewer']}")
    if run["decision"] != "NEEDS_REVIEW":
        raise HTTPException(409, "Only runs routed for review can be decided by a reviewer")
    if body.action == "approve" and not body.note.strip():
        raise HTTPException(400, "A note is required to approve an exception (audit trail)")
    status = "approved" if body.action == "approve" else "rejected"
    db.update_run(run_id, review_status=status, reviewer=body.reviewer, review_note=body.note, reviewed_at=db.now_iso())
    if status == "approved" and not run["ledger_posted"] and run["vendor_id"]:
        ex = run["extracted"] or {}
        lines = []
        for st in run["stages"]:
            if st["key"] == "three_way":
                lines = [{"po_line": l.get("po_line"), "qty": l.get("qty"), "amount": l.get("amount")}
                         for l in st.get("data", {}).get("lines", []) if l.get("po_line")]
        db.post_to_ledger(run_id, {
            "invoice_number": ex.get("invoice_number") or run_id[:8], "vendor_id": run["vendor_id"],
            "po_number": run["po_number"], "invoice_date": ex.get("invoice_date"), "subtotal": ex.get("subtotal"),
            "tax": ex.get("tax_amount"), "total": ex.get("total"), "status": "approved (exception)", "lines": lines})
    db.audit(run_id, body.reviewer, f"review_{status}", body.note)
    return get_run(run_id)


# ------------------------------------------------------------------ reference & dashboards
@app.get("/api/samples")
def samples():
    cat = json.loads((config.SAMPLES_DIR / "catalog.json").read_text(encoding="utf-8"))
    return cat


@app.get("/api/samples/{filename}")
def sample_file(filename: str):
    p = config.SAMPLES_DIR / Path(filename).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


@app.get("/api/reference")
def reference():
    ref = config.load_reference()
    ledger = db.ledger_entries()
    pos = []
    for p in ref["purchase_orders"]:
        total = round(sum(l["qty"] * l["unit_price"] for l in p["lines"]), 2)
        billed = round(sum(e["subtotal"] or 0 for e in ledger if e["po_number"] == p["po_number"]), 2)
        received = {}
        for g in ref["goods_receipts"]:
            if g["po_number"] == p["po_number"]:
                for l in g["lines"]:
                    received[l["line"]] = received.get(l["line"], 0) + l["qty_received"]
        vendor = next(v["name"] for v in ref["vendors"] if v["vendor_id"] == p["vendor_id"])
        pos.append({**p, "vendor_name": vendor, "total": total, "billed": billed, "remaining": round(total - billed, 2),
                    "lines": [{**l, "received": received.get(l["line"], 0)} for l in p["lines"]]})
    mem = db.memory_for()
    vendors = [{**v, "bank": {**v["bank"], "account_number": "••••" + v["bank"]["account_number"][-4:]},
                "learned": [m for m in mem if m["vendor_id"] == v["vendor_id"] and m["kind"] != "correction"]}
               for v in ref["vendors"]]
    return {"company": ref["company"], "vendors": vendors, "purchase_orders": pos,
            "goods_receipts": ref["goods_receipts"], "ledger": ledger, "fx_rates": ref.get("fx_rates"),
            "teams": ref.get("teams")}


def _usd(r: dict) -> float:
    """Run total in base currency (older runs without FX are USD)."""
    if r.get("base_total") is not None:
        return r["base_total"]
    return r.get("total") or 0


def _since(days: int | None) -> str | None:
    """ISO cut-off for the dashboard's date range (None = all time)."""
    if not days or days <= 0:
        return None
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@app.get("/api/analytics")
def analytics(days: int | None = None):
    """Everything the dashboard's analytics widgets need, in USD. `days` limits to runs received in the last N days."""
    from datetime import datetime, timedelta, timezone

    cut = _since(days)
    runs = [r for r in db.list_runs(5000) if r["status"] == "completed" and not r.get("superseded_by")
            and (cut is None or r["created_at"] >= cut)]
    full = {r["id"]: db.get_run(r["id"]) for r in runs}
    ref = config.load_reference()
    vendor_names = {v["vendor_id"]: v["name"] for v in ref["vendors"]}

    # money saved / protected
    def checks_of(rid):
        return [c for s in full[rid]["stages"] for c in s["checks"]]

    dup = [r for r in runs if any(c["id"] in ("invoice_number_dup", "file_hash") and c["status"] == "fail" for c in checks_of(r["id"]))]
    fraud = [r for r in runs if any(c["id"] in ("bank_account", "routing", "sender") and c["status"] == "fail" for c in checks_of(r["id"]))]
    held = [r for r in runs if r["decision"] == "NEEDS_REVIEW"]
    on_hold_value = 0.0
    for r in held:
        a = (full[r["id"]]["decision_detail"] or {}).get("amounts", {})
        rate = r.get("fx_rate") or 1
        on_hold_value += (a.get("on_hold") if a.get("on_hold") is not None else a.get("invoice_total") or 0) * rate

    # exceptions by reason and by vendor
    reasons: dict[str, int] = {}
    for r in runs:
        for c in checks_of(r["id"]):
            if c["status"] == "fail" and c.get("outcome"):
                reasons[c["label"].split(" — ")[0]] = reasons.get(c["label"].split(" — ")[0], 0) + 1
    by_vendor: dict[str, dict] = {}
    for r in runs:
        # matched vendors keep their master name; unmatched junk (a body line read as a name) groups as unknown
        key = r["vendor_name"] if r.get("vendor_id") or plausible_vendor_name(r["vendor_name"]) else "Unknown vendor"
        v = by_vendor.setdefault(key, {"vendor": key, "runs": 0, "approved": 0, "review": 0, "rejected": 0, "value_usd": 0.0})
        v["runs"] += 1
        v["value_usd"] = round(v["value_usd"] + _usd(r), 2)
        v[{"APPROVED": "approved", "NEEDS_REVIEW": "review", "REJECTED": "rejected"}[r["decision"]]] += 1
    for v in by_vendor.values():
        v["exception_rate"] = round((v["review"] + v["rejected"]) / v["runs"], 3) if v["runs"] else 0

    # payment calendar (approved, incl. reviewer-approved exceptions)
    cal: dict[str, dict] = {}
    for r in runs:
        d = full[r["id"]]["decision_detail"] or {}
        pay = d.get("payment")
        if r["decision"] == "APPROVED" and pay:
            day = pay["scheduled_for"]
            amt = _usd(r)
        elif r["review_status"] == "approved":
            day = (full[r["id"]]["extracted"] or {}).get("due_date") or r["reviewed_at"][:10]
            amt = (d.get("amounts", {}).get("recommended_payment") or r["total"] or 0) * (r.get("fx_rate") or 1)
        else:
            continue
        slot = cal.setdefault(day, {"date": day, "amount_usd": 0.0, "invoices": []})
        slot["amount_usd"] = round(slot["amount_usd"] + amt, 2)
        slot["invoices"].append({"id": r["id"], "vendor": r["vendor_name"], "invoice": r["invoice_number"], "usd": round(amt, 2)})

    # daily trend + processing time by method
    trend: dict[str, dict] = {}
    for r in runs:
        day = r["created_at"][:10]
        t = trend.setdefault(day, {"date": day, "APPROVED": 0, "NEEDS_REVIEW": 0, "REJECTED": 0})
        t[r["decision"]] += 1
    methods: dict[str, list[int]] = {}
    for r in runs:
        if r["extraction_method"] and r["duration_ms"]:
            methods.setdefault(r["extraction_method"], []).append(r["duration_ms"])

    # review SLA health
    now = datetime.now(timezone.utc)
    pending = [r for r in runs if r["review_status"] == "pending"]
    sla = {"pending": len(pending), "overdue": sum(1 for r in pending if r["sla_due_at"] and r["sla_due_at"] < now.isoformat()),
           "escalated": sum(1 for r in runs if r["escalated_at"]),
           "resolved": sum(1 for r in runs if r["review_status"] in ("approved", "rejected"))}
    ages = []
    for r in pending:
        ages.append({"id": r["id"], "vendor": r["vendor_name"], "team": r["route_to"],
                     "age_hours": round((now - datetime.fromisoformat(r["created_at"])).total_seconds() / 3600, 1),
                     "due": r["sla_due_at"]})

    mem = db.memory_for()
    corrections: dict[str, int] = {}
    for m in mem:
        if m["kind"] == "correction":
            corrections[m["value"]] = corrections.get(m["value"], 0) + 1

    currencies: dict[str, dict] = {}
    for r in runs:
        if r["total"] is None:  # amount never read: says nothing about currency mix
            continue
        c = currencies.setdefault(r["currency"] or "USD", {"currency": r["currency"] or "USD", "invoices": 0, "amount": 0.0, "usd": 0.0})
        c["invoices"] += 1
        c["amount"] = round(c["amount"] + (r["total"] or 0), 2)
        c["usd"] = round(c["usd"] + _usd(r), 2)

    total = len(runs)
    return {
        "totals": {"invoices": total, "value_usd": round(sum(_usd(r) for r in runs), 2),
                   "touchless_rate": round(sum(1 for r in runs if r["decision"] == "APPROVED") / total, 3) if total else None},
        "protected": {"duplicates": len(dup), "duplicates_usd": round(sum(_usd(r) for r in dup), 2),
                      "fraud_signals": len(fraud), "fraud_usd": round(sum(_usd(r) for r in fraud), 2),
                      "on_hold_usd": round(on_hold_value, 2)},
        "exception_reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()), key=lambda x: -x["count"]),
        "vendors": sorted(by_vendor.values(), key=lambda v: (-v["exception_rate"], -v["runs"])),
        "payment_calendar": sorted(cal.values(), key=lambda x: x["date"]),
        "trend": sorted(trend.values(), key=lambda x: x["date"]),
        "processing_ms": {k: round(sum(v) / len(v)) for k, v in methods.items()},
        "sla": sla, "pending_ages": sorted(ages, key=lambda a: -a["age_hours"]),
        "learning": {"rules": sum(1 for m in mem if m["kind"] != "correction"),
                     "rule_hits": sum(m["hits"] for m in mem if m["kind"] != "correction"),
                     "corrections_by_field": sorted(({"field": k, "count": v} for k, v in corrections.items()), key=lambda x: -x["count"])},
        "currencies": sorted(currencies.values(), key=lambda c: -c["usd"]),
        "fx": ref.get("fx_rates"),
        "vendor_names": vendor_names,
    }


@app.get("/api/stats")
def stats(days: int | None = None):
    cut = _since(days)
    runs = [r for r in db.list_runs(1000) if cut is None or r["created_at"] >= cut]
    done = [r for r in runs if r["status"] == "completed"]
    dec = Counter(r["decision"] for r in done)
    routes = Counter(r["route_to"] for r in done if r["decision"] == "NEEDS_REVIEW" and r["review_status"] == "pending")
    durations = [r["duration_ms"] for r in done if r["duration_ms"]]
    return {
        "total_runs": len(runs),
        "completed": len(done),
        "failed": sum(1 for r in runs if r["status"] == "failed"),
        "in_progress": sum(1 for r in runs if r["status"] in ("queued", "running")),
        "decisions": {k: dec.get(k, 0) for k in ("APPROVED", "NEEDS_REVIEW", "REJECTED")},
        "touchless_rate": round(dec.get("APPROVED", 0) / len(done), 3) if done else None,
        "pending_review": sum(1 for r in done if r["review_status"] == "pending"),
        "review_queues": dict(routes),
        "value_processed": round(sum(_usd(r) for r in done), 2),
        "value_approved": round(sum(_usd(r) for r in done if r["decision"] == "APPROVED" or r["review_status"] == "approved"), 2),
        "value_blocked": round(sum(_usd(r) for r in done if r["decision"] == "REJECTED"), 2),
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
        "methods": dict(Counter(r["extraction_method"] for r in done if r["extraction_method"])),
    }


@app.get("/api/meta")
def meta():
    return {"stages": [{"key": k, "name": n, "description": d} for k, n, d in STAGES],
            "llm_available": llm_available(), "llm_provider": llm_provider(), "llm_model": llm_model(),
            "ocr_engine": ocr_engine_name(), "extraction_mode": config.EXTRACTION_MODE,
            "notify_channel": "slack" if config.SLACK_WEBHOOK_URL else "email" if config.SMTP_HOST else "outbox",
            "imap_enabled": inbox.imap_enabled(), "base_currency": config.load_reference()["company"].get("base_currency", "USD")}


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def put_settings(values: dict):
    clean = {}
    for k, v in values.items():
        if k not in config.DEFAULT_SETTINGS:
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{k} must be a number")
        if num < 0:
            raise HTTPException(400, f"{k} must be >= 0")
        clean[k] = int(num) if isinstance(config.DEFAULT_SETTINGS[k], int) else num
    db.audit(None, "user", "settings_changed", json.dumps(clean))
    return db.update_settings(clean)


@app.post("/api/reset")
def reset():
    db.reset_demo()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}


# Serve the built React app from the same process when available (single-URL deploy).
_dist = config.ROOT.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        f = _dist / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(_dist / "index.html")
