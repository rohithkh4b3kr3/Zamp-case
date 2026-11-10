"""Intake connectors, triggers and run reports.

  * Connectors pull invoice files on a schedule: IMAP mailbox, Google Drive folder, Amazon S3 prefix, SFTP directory.
    Each one remembers the files it has already taken, so a file is processed once.
  * Inbound webhook: other systems POST a PDF (multipart, base64 JSON or a URL) to a secret URL to start a run.
  * Event actions: when a run finishes with a matching outcome, send email, Slack or a JSON webhook.
  * Run report: after each run, email a summary (PDF + JSON attached) to the AP mailbox.

Secrets are stored in the local SQLite database and never returned by the API (masked as "••••").
"""
from __future__ import annotations

import base64
import fnmatch
import imaplib
import io
import json
import re
import secrets
import smtplib
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from . import config, db

StartFn = Callable[..., dict]
_start: StartFn | None = None
MASK = "••••"

KINDS = {
    "imap": {"label": "Email inbox", "secrets": ["password"],
             "fields": ["host", "port", "username", "password", "folder", "use_ssl"]},
    "gdrive": {"label": "Google Drive", "secrets": ["service_account_json"],
               "fields": ["folder_id", "service_account_json", "pattern"]},
    "s3": {"label": "Amazon S3", "secrets": ["secret_access_key"],
           "fields": ["bucket", "prefix", "region", "access_key_id", "secret_access_key", "endpoint_url", "pattern"]},
    "sftp": {"label": "SFTP", "secrets": ["password", "private_key"],
             "fields": ["host", "port", "username", "password", "private_key", "remote_path", "pattern"]},
}
EVENTS = {
    "any": "Any decision",
    "approved": "Invoice approved",
    "needs_review": "Invoice sent to review",
    "rejected": "Invoice rejected",
    "amount_over": "Total over amount (USD)",
    "fraud": "Fraud signal detected",
}
CHANNELS = {"email": "Email", "slack": "Slack webhook", "webhook": "HTTP webhook"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS connectors (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL, interval_minutes REAL NOT NULL DEFAULT 5,
  last_run TEXT, last_status TEXT, last_error TEXT, files_ingested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_seen (
  connector_id TEXT NOT NULL, key TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY (connector_id, key)
);
CREATE TABLE IF NOT EXISTS event_actions (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, event TEXT NOT NULL,
  threshold REAL, channel TEXT NOT NULL, target TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
  last_fired TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

REPORT_DEFAULTS = {"enabled": True, "to": "", "only_exceptions": False, "attach_pdf": True, "attach_json": True}


# --------------------------------------------------------------------------- storage helpers
def init() -> None:
    with db._lock:
        db.conn().executescript(SCHEMA)
        db.conn().commit()
    if not _kv("webhook_token"):
        _kv_set("webhook_token", secrets.token_urlsafe(24))


def _kv(k: str, default: Any = None) -> Any:
    with db._lock:
        r = db.conn().execute("SELECT v FROM intake_kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _kv_set(k: str, v: Any) -> None:
    with db._lock:
        db.conn().execute("INSERT INTO intake_kv(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
        db.conn().commit()


def _row(r) -> dict:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    if "config_json" in d:
        d["config"] = json.loads(d.pop("config_json"))
    return d


def _public(c: dict) -> dict:
    """Connector as returned by the API: secrets masked."""
    out = dict(c)
    cfg = dict(c["config"])
    for s in KINDS[c["kind"]]["secrets"]:
        if cfg.get(s):
            cfg[s] = MASK
    out["config"] = cfg
    out["kind_label"] = KINDS[c["kind"]]["label"]
    out["next_run"] = _next_run(c)
    return out


def _next_run(c: dict) -> str | None:
    if not c["enabled"]:
        return None
    if not c.get("last_run"):
        return db.now_iso()
    t = datetime.fromisoformat(c["last_run"]).timestamp() + float(c["interval_minutes"]) * 60
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def list_connectors(kind: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM connectors", []
    if kind:
        q, a = q + " WHERE kind=?", [kind]
    with db._lock:
        return [_row(r) for r in db.conn().execute(q + " ORDER BY created_at", a).fetchall()]


def get_connector(cid: str) -> dict | None:
    with db._lock:
        r = db.conn().execute("SELECT * FROM connectors WHERE id=?", (cid,)).fetchone()
    return _row(r) if r else None


def _clean_config(kind: str, cfg: dict, old: dict | None = None) -> dict:
    if kind not in KINDS:
        raise HTTPException(400, f"Unknown connector type '{kind}'. Use one of: {', '.join(KINDS)}.")
    out = {k: cfg.get(k) for k in KINDS[kind]["fields"] if cfg.get(k) not in (None, "")}
    for s in KINDS[kind]["secrets"]:  # keep the stored secret when the UI sends the mask back
        if out.get(s) == MASK and old:
            out[s] = old.get(s)
    req = {"imap": ["host", "username", "password"], "gdrive": ["folder_id", "service_account_json"],
           "s3": ["bucket", "access_key_id", "secret_access_key"], "sftp": ["host", "username"]}[kind]
    missing = [f for f in req if not out.get(f)]
    if kind == "sftp" and not (out.get("password") or out.get("private_key")):
        missing.append("password or private_key")
    if missing:
        raise HTTPException(400, f"Missing {', '.join(missing)}. Fill in these fields and save again.")
    if kind == "gdrive":
        try:
            json.loads(out["service_account_json"])
        except Exception:
            raise HTTPException(400, "The service account key is not valid JSON. Paste the full key file downloaded from Google Cloud.")
    return out


def save_connector(body: dict, cid: str | None = None) -> dict:
    old = get_connector(cid) if cid else None
    if cid and not old:
        raise HTTPException(404, "Connector not found.")
    kind = old["kind"] if old else body.get("kind")
    cfg = _clean_config(kind, body.get("config") or {}, old["config"] if old else None)
    name = (body.get("name") or "").strip() or (old["name"] if old else KINDS[kind]["label"])
    interval = max(0.5, float(body.get("interval_minutes") or (old or {}).get("interval_minutes") or 5))
    enabled = bool(body.get("enabled", old["enabled"] if old else True))
    ts = db.now_iso()
    with db._lock:
        if old:
            db.conn().execute("UPDATE connectors SET name=?, enabled=?, config_json=?, interval_minutes=?, updated_at=? WHERE id=?",
                              (name, int(enabled), json.dumps(cfg), interval, ts, cid))
        else:
            cid = uuid.uuid4().hex[:12]
            db.conn().execute(
                "INSERT INTO connectors(id, kind, name, enabled, config_json, interval_minutes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (cid, kind, name, int(enabled), json.dumps(cfg), interval, ts, ts))
        db.conn().commit()
    db.audit(None, "user", "connector_saved" if old else "connector_added", f"{KINDS[kind]['label']}: {name}")
    return get_connector(cid)  # type: ignore[arg-type]


def _mark(cid: str, **f: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in f)
    with db._lock:
        db.conn().execute(f"UPDATE connectors SET {sets} WHERE id=?", [*f.values(), cid])
        db.conn().commit()


def _seen(cid: str, key: str) -> bool:
    with db._lock:
        return db.conn().execute("SELECT 1 FROM connector_seen WHERE connector_id=? AND key=?", (cid, key)).fetchone() is not None


def _remember(cid: str, key: str) -> None:
    with db._lock:
        db.conn().execute("INSERT OR IGNORE INTO connector_seen VALUES (?,?,?)", (cid, key, db.now_iso()))
        db.conn().commit()


# --------------------------------------------------------------------------- connectors
def _start_pdf(data: bytes, filename: str, source: str) -> list[str]:
    if _start is None:
        raise RuntimeError("Intake is not wired to the pipeline yet")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "invoice.pdf"
    dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe}"
    dest.write_bytes(data)
    res = _start(dest, filename, source)
    return res.get("ids") or [res["id"]]


def _matches(name: str, pattern: str | None) -> bool:
    return fnmatch.fnmatch(name.lower(), (pattern or "*.pdf").lower())


def _imap(c: dict, dry: bool) -> dict:
    from . import inbox  # email parsing + per-attachment runs live there
    cfg = c["config"]
    port = int(cfg.get("port") or (993 if cfg.get("use_ssl", True) else 143))
    cls = imaplib.IMAP4_SSL if cfg.get("use_ssl", True) not in (False, "false", 0) else imaplib.IMAP4
    with cls(cfg["host"], port, timeout=20) as m:
        m.login(cfg["username"], cfg["password"])
        m.select(cfg.get("folder") or "INBOX", readonly=dry)
        typ, data = m.search(None, "UNSEEN")
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        if dry:
            return {"found": len(ids), "detail": f"Signed in. {len(ids)} unread message(s) in {cfg.get('folder') or 'INBOX'}."}
        n = 0
        for i in ids:
            typ, msg = m.fetch(i, "(RFC822)")
            if typ == "OK" and msg and isinstance(msg[0], tuple):
                inbox.ingest(msg[0][1], f"imap:{c['name']}", _start)  # type: ignore[arg-type]
                n += 1
        return {"found": len(ids), "ingested": n, "detail": f"{n} email(s) received"}


def _gdrive(c: dict, dry: bool) -> dict:
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        raise RuntimeError("Google Drive support is not installed on the server. Run: pip install google-api-python-client google-auth")
    cfg = c["config"]
    creds = service_account.Credentials.from_service_account_info(
        json.loads(cfg["service_account_json"]), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    q = f"'{cfg['folder_id']}' in parents and trashed=false and mimeType='application/pdf'"
    files = svc.files().list(q=q, fields="files(id,name,md5Checksum)", pageSize=200).execute().get("files", [])
    files = [f for f in files if _matches(f["name"], cfg.get("pattern"))]
    new = [f for f in files if not _seen(c["id"], f"{f['id']}:{f.get('md5Checksum', '')}")]
    if dry:
        return {"found": len(new), "detail": f"Connected. {len(files)} PDF(s) in the folder, {len(new)} not yet processed."}
    n = 0
    for f in new:
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=f["id"]))
        done = False
        while not done:
            _, done = dl.next_chunk()
        _start_pdf(buf.getvalue(), f["name"], f"gdrive:{c['name']}")
        _remember(c["id"], f"{f['id']}:{f.get('md5Checksum', '')}")
        n += 1
    return {"found": len(new), "ingested": n, "detail": f"{n} file(s) imported"}


def _s3(c: dict, dry: bool) -> dict:
    try:
        import boto3
    except ImportError:
        raise RuntimeError("S3 support is not installed on the server. Run: pip install boto3")
    cfg = c["config"]
    s3 = boto3.client("s3", region_name=cfg.get("region") or None, endpoint_url=cfg.get("endpoint_url") or None,
                      aws_access_key_id=cfg["access_key_id"], aws_secret_access_key=cfg["secret_access_key"])
    objs, token = [], None
    while True:
        kw = {"Bucket": cfg["bucket"], "Prefix": cfg.get("prefix") or ""}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        objs += [o for o in page.get("Contents", []) if _matches(o["Key"].rsplit("/", 1)[-1], cfg.get("pattern"))]
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    new = [o for o in objs if not _seen(c["id"], f"{o['Key']}:{o.get('ETag', '')}")]
    if dry:
        return {"found": len(new), "detail": f"Connected. {len(objs)} matching file(s), {len(new)} not yet processed."}
    n = 0
    for o in new:
        data = s3.get_object(Bucket=cfg["bucket"], Key=o["Key"])["Body"].read()
        _start_pdf(data, o["Key"].rsplit("/", 1)[-1], f"s3:{c['name']}")
        _remember(c["id"], f"{o['Key']}:{o.get('ETag', '')}")
        n += 1
    return {"found": len(new), "ingested": n, "detail": f"{n} file(s) imported"}


def _sftp(c: dict, dry: bool) -> dict:
    try:
        import paramiko
    except ImportError:
        raise RuntimeError("SFTP support is not installed on the server. Run: pip install paramiko")
    cfg = c["config"]
    pkey = None
    if cfg.get("private_key"):
        for k in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                pkey = k.from_private_key(io.StringIO(cfg["private_key"]))
                break
            except Exception:
                continue
        if pkey is None:
            raise RuntimeError("The private key could not be read. Paste an unencrypted OpenSSH or PEM key.")
    t = paramiko.Transport((cfg["host"], int(cfg.get("port") or 22)))
    try:
        t.connect(username=cfg["username"], password=cfg.get("password") or None, pkey=pkey)
        sftp = paramiko.SFTPClient.from_transport(t)
        path = cfg.get("remote_path") or "."
        entries = [e for e in sftp.listdir_attr(path) if _matches(e.filename, cfg.get("pattern"))]
        new = [e for e in entries if not _seen(c["id"], f"{e.filename}:{e.st_size}:{e.st_mtime}")]
        if dry:
            return {"found": len(new), "detail": f"Connected. {len(entries)} matching file(s) in {path}, {len(new)} not yet processed."}
        n = 0
        for e in new:
            buf = io.BytesIO()
            sftp.getfo(f"{path.rstrip('/')}/{e.filename}", buf)
            _start_pdf(buf.getvalue(), e.filename, f"sftp:{c['name']}")
            _remember(c["id"], f"{e.filename}:{e.st_size}:{e.st_mtime}")
            n += 1
        return {"found": len(new), "ingested": n, "detail": f"{n} file(s) imported"}
    finally:
        t.close()


DRIVERS = {"imap": _imap, "gdrive": _gdrive, "s3": _s3, "sftp": _sftp}


def run_connector(c: dict, dry: bool = False) -> dict:
    try:
        res = DRIVERS[c["kind"]](c, dry)
        res["ok"] = True
    except Exception as e:  # surface the real reason to the user
        res = {"ok": False, "detail": f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e)}
    if not dry and c.get("id"):
        _mark(c["id"], last_run=db.now_iso(), last_status="ok" if res["ok"] else "error",
              last_error=None if res["ok"] else res["detail"])
        if res.get("ingested"):
            with db._lock:
                db.conn().execute("UPDATE connectors SET files_ingested = files_ingested + ? WHERE id=?", (res["ingested"], c["id"]))
                db.conn().commit()
            db.audit(None, "intake", "connector_pull", f"{c['name']}: {res['detail']}")
    return res


_started = False


def start_scheduler(start: StartFn) -> None:
    """Scheduled pulls: every connector runs on its own interval."""
    global _start, _started
    _start = start
    if _started:
        return
    _started = True

    def loop():
        while True:
            now = time.time()
            for c in list_connectors():
                nxt = _next_run(c)
                if nxt and datetime.fromisoformat(nxt).timestamp() <= now:
                    run_connector(c)
            time.sleep(15)

    threading.Thread(target=loop, daemon=True, name="intake-scheduler").start()


def wire(start: StartFn) -> None:
    """Let API-triggered pulls and the webhook start runs even when background workers are off (tests)."""
    global _start
    _start = start


# --------------------------------------------------------------------------- delivery (reports + event actions)
def _send_email(to: str, subject: str, body: str, attachments: list[tuple[str, bytes, str]] = ()) -> tuple[str, str | None]:
    if not config.SMTP_HOST:
        return "simulated", None
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
        msg.set_content(body)
        for name, data, mime in attachments:
            main, sub = mime.split("/", 1)
            msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return "sent", None
    except Exception as e:
        return "failed", f"{type(e).__name__}: {e}"


def _post_json(url: str, payload: dict) -> tuple[str, str | None]:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return "sent", None
    except Exception as e:
        return "failed", f"{type(e).__name__}: {e}"


def _report_pdf(run_id: str, dec: dict, inv: dict, who: str) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 60

    def line(text: str, size: float = 10, bold: bool = False, gap: float = 6, color=(0.1, 0.1, 0.12)):
        nonlocal y
        page.insert_text((50, y), text[:110], fontsize=size, fontname="hebo" if bold else "helv", color=color)
        y += size + gap

    line("AP Autopilot · Run report", 9, color=(0.45, 0.45, 0.5))
    line(f"{who}  {inv.get('invoice_number') or ''}", 16, True, 10)
    tone = {"APPROVED": (0.05, 0.5, 0.3), "NEEDS_REVIEW": (0.7, 0.45, 0.05), "REJECTED": (0.75, 0.1, 0.2)}.get(dec["decision"], (0, 0, 0))
    line(f"{dec['decision'].replace('_', ' ').title()}" + (f" · routed to {dec['route_to']}" if dec.get("route_to") else ""), 12, True, 4, tone)
    line(dec.get("headline", ""), 10, gap=14)
    for k, v in [("Invoice date", inv.get("invoice_date")), ("Due date", inv.get("due_date")), ("PO", inv.get("po_number")),
                 ("Total", f"{inv.get('currency') or 'USD'} {inv.get('total') or 0:,.2f}"), ("Run", run_id)]:
        line(f"{k}:  {v or '—'}", 10, gap=4)
    y += 10
    line("Why", 11, True)
    for r in dec.get("reasons", [])[:12]:
        line(f"•  {r.get('label', '')}: {r.get('text', '')}", 9, gap=4)
    y += 8
    line(f"Open in AP Autopilot: {config.APP_BASE_URL}/runs/{run_id}", 9, color=(0.2, 0.3, 0.7))
    return doc.tobytes()


def report_settings() -> dict:
    s = {**REPORT_DEFAULTS, **(_kv("report") or {})}
    if not s["to"]:
        try:
            s["to"] = config.load_reference()["company"]["ap_email"]
        except Exception:
            pass
    return s


def after_decision(run_id: str, dec: dict, inv: dict, vendor: dict | None) -> None:
    """Called once per finished run (from notify.on_decision). Never raises."""
    try:
        _send_report(run_id, dec, inv, vendor)
    except Exception as e:
        db.audit(run_id, "system", "report_failed", f"{type(e).__name__}: {e}")
    try:
        _fire_actions(run_id, dec, inv, vendor)
    except Exception as e:
        db.audit(run_id, "system", "event_action_failed", f"{type(e).__name__}: {e}")


def _send_report(run_id: str, dec: dict, inv: dict, vendor: dict | None) -> None:
    s = report_settings()
    if not s["enabled"] or not s["to"] or (s["only_exceptions"] and dec["decision"] == "APPROVED"):
        return
    who = vendor["name"] if vendor else (inv.get("vendor_name") or "Unknown vendor")
    amount = f"{inv.get('currency') or 'USD'} {inv.get('total') or 0:,.2f}"
    label = dec["decision"].replace("_", " ").title()
    subject = f"[AP report] {label}: {who} {inv.get('invoice_number') or ''} ({amount})"
    reasons = "\n".join(f"• {r.get('label', '')}: {r.get('text', '')}" for r in dec.get("reasons", [])[:8])
    body = (f"{dec.get('headline', '')}\n\nDecision: {label}" + (f" (routed to {dec['route_to']})" if dec.get("route_to") else "") +
            f"\nVendor: {who}\nInvoice: {inv.get('invoice_number') or '—'}\nPO: {inv.get('po_number') or '—'}\nTotal: {amount}"
            f"\n\nWhy:\n{reasons or '• All checks passed'}\n\nOpen: {config.APP_BASE_URL}/runs/{run_id}")
    att = []
    if s["attach_pdf"]:
        att.append((f"ap-report-{run_id[:8]}.pdf", _report_pdf(run_id, dec, inv, who), "application/pdf"))
    if s["attach_json"]:
        payload = {"run_id": run_id, "decision": dec.get("decision"), "route_to": dec.get("route_to"), "headline": dec.get("headline"),
                   "reasons": dec.get("reasons", []), "invoice": inv}
        att.append((f"ap-report-{run_id[:8]}.json", json.dumps(payload, indent=2, default=str).encode(), "application/json"))
    status, err = _send_email(s["to"], subject, body, att)
    db.add_notification(run_id=run_id, kind="run_report", team="AP", channel="email", recipient=s["to"],
                        subject=subject, body=body + (f"\n\nAttachments: {', '.join(a[0] for a in att)}" if att else ""),
                        status=status, error=err)


def list_actions() -> list[dict]:
    with db._lock:
        return [_row(r) for r in db.conn().execute("SELECT * FROM event_actions ORDER BY created_at").fetchall()]


def _action_matches(a: dict, dec: dict, inv: dict) -> bool:
    d = dec["decision"]
    ev = a["event"]
    if ev == "any":
        return True
    if ev in ("approved", "needs_review", "rejected"):
        return d == ev.upper()
    if ev == "amount_over":
        total = (dec.get("fx") or {}).get("base_total")  # USD equivalent, only present for non-USD invoices
        total = inv.get("total") if total is None else total
        return total is not None and a["threshold"] is not None and float(total) > float(a["threshold"])
    if ev == "fraud":
        text = " ".join(f"{r.get('label', '')} {r.get('text', '')}" for r in dec.get("reasons", [])).lower()
        return dec.get("route_to") == "Treasury & Security" or "bank" in text or "fraud" in text or "spoof" in text
    return False


def _fire_actions(run_id: str, dec: dict, inv: dict, vendor: dict | None) -> None:
    who = vendor["name"] if vendor else (inv.get("vendor_name") or "Unknown vendor")
    for a in list_actions():
        if not a["enabled"] or not _action_matches(a, dec, inv):
            continue
        subject = f"[AP] {a['name']}: {who} {inv.get('invoice_number') or ''} · {dec['decision'].replace('_', ' ').title()}"
        text = f"{subject}\n{dec.get('headline', '')}\n{config.APP_BASE_URL}/runs/{run_id}"
        payload = {"event": a["event"], "action": a["name"], "run_id": run_id, "decision": dec["decision"], "route_to": dec.get("route_to"),
                   "vendor": who, "invoice_number": inv.get("invoice_number"), "total": inv.get("total"), "currency": inv.get("currency"),
                   "headline": dec.get("headline"), "url": f"{config.APP_BASE_URL}/runs/{run_id}"}
        if a["channel"] == "email":
            status, err = _send_email(a["target"], subject, text)
        elif a["channel"] == "slack":
            status, err = _post_json(a["target"], {"text": text})
        else:
            status, err = _post_json(a["target"], payload)
        db.add_notification(run_id=run_id, kind="event_action", team=a["name"], channel=a["channel"], recipient=a["target"],
                            subject=subject, body=text, status=status, error=err)
        with db._lock:
            db.conn().execute("UPDATE event_actions SET hits = hits + 1, last_fired=? WHERE id=?", (db.now_iso(), a["id"]))
            db.conn().commit()


# --------------------------------------------------------------------------- API
router = APIRouter(prefix="/api/intake", tags=["intake"])


@router.get("/schema")
def api_schema():
    return {"kinds": {k: {"label": v["label"], "fields": v["fields"], "secrets": v["secrets"]} for k, v in KINDS.items()},
            "events": EVENTS, "channels": CHANNELS}


@router.get("/connectors")
def api_list(kind: str | None = None):
    return [_public(c) for c in list_connectors(kind)]


@router.post("/connectors")
def api_create(body: dict):
    return _public(save_connector(body))


@router.put("/connectors/{cid}")
def api_update(cid: str, body: dict):
    return _public(save_connector(body, cid))


@router.delete("/connectors/{cid}")
def api_delete(cid: str):
    c = get_connector(cid)
    if not c:
        raise HTTPException(404, "Connector not found.")
    with db._lock:
        db.conn().execute("DELETE FROM connectors WHERE id=?", (cid,))
        db.conn().execute("DELETE FROM connector_seen WHERE connector_id=?", (cid,))
        db.conn().commit()
    db.audit(None, "user", "connector_removed", f"{KINDS[c['kind']]['label']}: {c['name']}")
    return {"ok": True}


@router.post("/connectors/test")
def api_test(body: dict):
    """Check credentials without saving or importing anything. For an existing connector pass its id so masked secrets resolve."""
    old = get_connector(body["id"]) if body.get("id") else None
    kind = old["kind"] if old else body.get("kind")
    cfg = _clean_config(kind, body.get("config") or {}, old["config"] if old else None)
    return run_connector({"id": old["id"] if old else "test", "kind": kind, "name": body.get("name") or "test", "config": cfg}, dry=True)


@router.post("/connectors/{cid}/sync")
def api_sync(cid: str):
    c = get_connector(cid)
    if not c:
        raise HTTPException(404, "Connector not found.")
    return run_connector(c)


@router.get("/webhook")
def api_webhook_info():
    token = _kv("webhook_token")
    return {"url": f"{config.APP_BASE_URL}/api/intake/webhook/{token}", "token": token}


@router.post("/webhook/rotate")
def api_webhook_rotate():
    _kv_set("webhook_token", secrets.token_urlsafe(24))
    db.audit(None, "user", "webhook_rotated", "Inbound webhook URL regenerated")
    return api_webhook_info()


@router.post("/webhook/{token}")
async def api_webhook(token: str, request: Request, file: UploadFile | None = File(None)):
    if not secrets.compare_digest(token, _kv("webhook_token") or ""):
        raise HTTPException(403, "Invalid webhook URL. Copy the current URL from Intake → Triggers.")
    if file is not None:
        data, name = await file.read(), file.filename or "invoice.pdf"
    else:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Send the PDF as multipart field 'file', or JSON {filename, content_base64} or {url}.")
        if body.get("content_base64"):
            data, name = base64.b64decode(body["content_base64"]), body.get("filename") or "invoice.pdf"
        elif body.get("url"):
            with urllib.request.urlopen(body["url"], timeout=20) as r:
                data = r.read()
            name = body.get("filename") or body["url"].rsplit("/", 1)[-1].split("?")[0] or "invoice.pdf"
        else:
            raise HTTPException(400, "Send the PDF as multipart field 'file', or JSON {filename, content_base64} or {url}.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "The file is not a PDF. Send the invoice as a PDF.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "The PDF is larger than 15 MB.")
    ids = _start_pdf(data, name, "webhook")
    db.audit(None, "webhook", "received", f"{name} → {len(ids)} run(s)")
    return {"ok": True, "run_ids": ids, "urls": [f"{config.APP_BASE_URL}/runs/{i}" for i in ids]}


@router.get("/report")
def api_report_get():
    return report_settings()


@router.put("/report")
def api_report_put(body: dict):
    s = {**report_settings(), **{k: body[k] for k in REPORT_DEFAULTS if k in body}}
    if s["enabled"] and s["to"] and not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", s["to"]):
        raise HTTPException(400, "Enter a valid email address for the AP report.")
    _kv_set("report", s)
    db.audit(None, "user", "report_settings", json.dumps(s))
    return s


@router.get("/actions")
def api_actions():
    return list_actions()


def _clean_action(b: dict, old: dict | None = None) -> dict:
    a = {**(old or {}), **{k: b[k] for k in ("name", "enabled", "event", "threshold", "channel", "target") if k in b}}
    if a.get("event") not in EVENTS:
        raise HTTPException(400, f"Pick an event: {', '.join(EVENTS.values())}.")
    if a.get("channel") not in CHANNELS:
        raise HTTPException(400, "Pick a channel: email, Slack webhook or HTTP webhook.")
    t = (a.get("target") or "").strip()
    if a["channel"] == "email" and not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", t):
        raise HTTPException(400, "Enter a valid email address to notify.")
    if a["channel"] in ("slack", "webhook") and not t.startswith(("https://", "http://")):
        raise HTTPException(400, "Enter the full webhook URL, starting with https://.")
    if a["event"] == "amount_over":
        try:
            a["threshold"] = float(str(a.get("threshold")).replace(",", "").replace("$", ""))
        except Exception:
            raise HTTPException(400, "Enter the amount in USD, for example 10000.")
    else:
        a["threshold"] = None
    a["target"] = t
    a["name"] = (a.get("name") or "").strip() or EVENTS[a["event"]]
    a["enabled"] = bool(a.get("enabled", True))
    return a


@router.post("/actions")
def api_action_create(body: dict):
    a = _clean_action(body)
    aid = uuid.uuid4().hex[:12]
    with db._lock:
        db.conn().execute("INSERT INTO event_actions(id, name, enabled, event, threshold, channel, target, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (aid, a["name"], int(a["enabled"]), a["event"], a["threshold"], a["channel"], a["target"], db.now_iso()))
        db.conn().commit()
    db.audit(None, "user", "event_action_added", a["name"])
    return next(x for x in list_actions() if x["id"] == aid)


@router.put("/actions/{aid}")
def api_action_update(aid: str, body: dict):
    old = next((x for x in list_actions() if x["id"] == aid), None)
    if not old:
        raise HTTPException(404, "Action not found.")
    a = _clean_action(body, old)
    with db._lock:
        db.conn().execute("UPDATE event_actions SET name=?, enabled=?, event=?, threshold=?, channel=?, target=? WHERE id=?",
                          (a["name"], int(a["enabled"]), a["event"], a["threshold"], a["channel"], a["target"], aid))
        db.conn().commit()
    return next(x for x in list_actions() if x["id"] == aid)


@router.delete("/actions/{aid}")
def api_action_delete(aid: str):
    with db._lock:
        n = db.conn().execute("DELETE FROM event_actions WHERE id=?", (aid,)).rowcount
        db.conn().commit()
    if not n:
        raise HTTPException(404, "Action not found.")
    return {"ok": True}
