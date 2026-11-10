"""Email intake: invoices arrive by email, as in the problem statement.

Three ways in, one code path:
  * upload an .eml file in the UI (or load the prepared sample emails)
  * drop .eml / .pdf files into storage/inbox_drop/ (watched every few seconds)
  * IMAP polling of a real mailbox when IMAP_HOST / IMAP_USER / IMAP_PASSWORD are set

Each email is recorded (sender, subject, attachments) and every PDF attachment becomes a run linked to it,
so the vendor stage can check the sender domain against the vendor master.
"""
from __future__ import annotations

import email
import imaplib
import re
import shutil
import threading
import time
import uuid
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Callable

from . import config, db, notify

StartFn = Callable[..., dict]


def parse_eml(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        sent = parsedate_to_datetime(str(msg.get("Date"))).isoformat() if msg.get("Date") else None
    except (TypeError, ValueError):
        sent = None
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            body = re.sub(r"<[^>]+>", " ", body)
    attachments = []
    for att in msg.iter_attachments():
        data = att.get_payload(decode=True) or b""
        fname = att.get_filename() or "attachment"
        is_pdf = att.get_content_type() == "application/pdf" or fname.lower().endswith(".pdf") or data[:4] == b"%PDF"
        attachments.append({"filename": fname, "content_type": att.get_content_type(), "size": len(data),
                            "is_pdf": is_pdf, "data": data})
    return {"message_id": str(msg.get("Message-ID", "")).strip() or None, "from_addr": addr.lower() or None,
            "from_name": name or None, "to_addr": str(msg.get("To", "")) or None, "subject": str(msg.get("Subject", "")),
            "sent_at": sent, "body": re.sub(r"\s+", " ", body).strip(), "attachments": attachments}


def ingest(raw: bytes, source: str, start: StartFn) -> dict:
    """Record the email and start one run per PDF attachment (multi-invoice PDFs split further)."""
    e = parse_eml(raw)
    eid = uuid.uuid4().hex
    meta = [{k: a[k] for k in ("filename", "content_type", "size", "is_pdf")} for a in e["attachments"]]
    row = {"id": eid, "received_at": db.now_iso(), "source": source, "message_id": e["message_id"],
           "from_addr": e["from_addr"], "from_name": e["from_name"], "to_addr": e["to_addr"],
           "subject": e["subject"], "sent_at": e["sent_at"], "body_excerpt": e["body"][:1200],
           "attachments": meta, "run_ids": [], "status": "processing", "note": None}

    if e["message_id"] and db.email_by_message_id(e["message_id"]):
        row.update(status="duplicate_email", note="Same Message-ID already received; attachments not re-processed")
        db.create_email(row)
        db.audit(None, "inbox", "duplicate_email", f"{e['from_addr']}: {e['subject']}")
        return db.get_email(eid)

    db.create_email(row)
    pdfs = [a for a in e["attachments"] if a["is_pdf"]]
    if not pdfs:
        db.update_email(eid, status="no_pdf", note="No PDF attached; drafted a reply asking for the invoice")
        notify.vendor_draft(
            e["from_addr"], f"Re: {e['subject']}",
            f"Hello{(' ' + e['from_name']) if e['from_name'] else ''},\n\nThank you for your email. We could not find an "
            f"invoice PDF attached. Please resend the invoice as a PDF attachment to {config.load_reference()['company']['ap_email']}."
            f"\n\nRegards,\nAccounts Payable", email_id=eid)
        return db.get_email(eid)

    run_ids: list[str] = []
    for a in pdfs:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", a["filename"])
        dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe}"
        dest.write_bytes(a["data"])
        res = start(dest, a["filename"], f"email:{source}", email_id=eid)
        run_ids.extend(res.get("ids") or [res["id"]])
    skipped = len(e["attachments"]) - len(pdfs)
    db.update_email(eid, status="processed", run_ids=run_ids,
                    note=f"{len(run_ids)} invoice run(s)" + (f"; {skipped} non-PDF attachment(s) kept for reference" if skipped else ""))
    db.audit(None, "inbox", "email_received", f"{e['from_addr']}: {e['subject']} → {len(run_ids)} run(s)")
    return db.get_email(eid)


# --------------------------------------------------------------------------- watched folder
def scan_drop_folder(start: StartFn) -> int:
    done = config.INBOX_DROP_DIR / "processed"
    done.mkdir(exist_ok=True)
    n = 0
    for p in sorted(config.INBOX_DROP_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".eml", ".pdf"):
            continue
        try:
            age = time.time() - p.stat().st_mtime
            if age < 1.5:  # still being written
                continue
            if p.suffix.lower() == ".eml":
                ingest(p.read_bytes(), "folder", start)
            else:
                dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{p.name}"
                shutil.copyfile(p, dest)
                start(dest, p.name, "folder")
            shutil.move(str(p), str(done / f"{int(time.time())}_{p.name}"))
            n += 1
        except Exception as ex:
            db.audit(None, "inbox", "folder_error", f"{p.name}: {ex}")
            shutil.move(str(p), str(done / f"failed_{p.name}"))
    return n


# --------------------------------------------------------------------------- IMAP
def imap_enabled() -> bool:
    return bool(config.IMAP_HOST and config.IMAP_USER and config.IMAP_PASSWORD)


def poll_imap(start: StartFn) -> int:
    """Fetch UNSEEN messages, ingest them, mark them seen."""
    if not imap_enabled():
        return 0
    n = 0
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as m:
        m.login(config.IMAP_USER, config.IMAP_PASSWORD)
        m.select(config.IMAP_FOLDER)
        typ, data = m.search(None, "UNSEEN")
        for num in (data[0].split() if typ == "OK" and data and data[0] else []):
            typ, msg = m.fetch(num, "(RFC822)")
            if typ == "OK" and msg and isinstance(msg[0], tuple):
                ingest(msg[0][1], "imap", start)
                m.store(num, "+FLAGS", "\\Seen")
                n += 1
    return n


_started = False
STATUS = {"folder_last_scan": None, "imap_last_poll": None, "imap_error": None}


def start_watchers(start: StartFn) -> None:
    global _started
    if _started:
        return
    _started = True

    def folder_loop():
        while True:
            try:
                scan_drop_folder(start)
                STATUS["folder_last_scan"] = db.now_iso()
            except Exception:
                pass
            time.sleep(4)

    def imap_loop():
        while True:
            try:
                poll_imap(start)
                STATUS["imap_last_poll"], STATUS["imap_error"] = db.now_iso(), None
            except Exception as e:
                STATUS["imap_error"] = f"{type(e).__name__}: {e}"
            time.sleep(config.IMAP_POLL_SECONDS)

    threading.Thread(target=folder_loop, daemon=True, name="inbox-folder").start()
    if imap_enabled():
        threading.Thread(target=imap_loop, daemon=True, name="inbox-imap").start()


def sample_emails() -> list[Path]:
    d = config.SAMPLES_DIR / "emails"
    return sorted(d.glob("*.eml")) if d.exists() else []
