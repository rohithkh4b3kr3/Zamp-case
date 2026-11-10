"""SQLite persistence: runs, invoice ledger, audit trail, settings."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH, DEFAULT_SETTINGS, load_reference

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    route_to TEXT,
    vendor_id TEXT,
    vendor_name TEXT,
    invoice_number TEXT,
    po_number TEXT,
    total REAL,
    currency TEXT,
    extraction_method TEXT,
    duration_ms INTEGER,
    stages_json TEXT NOT NULL DEFAULT '[]',
    extracted_json TEXT,
    decision_json TEXT,
    review_status TEXT,
    reviewer TEXT,
    review_note TEXT,
    reviewed_at TEXT,
    ledger_posted INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_hash ON runs(file_hash);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    invoice_number_norm TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    po_number TEXT,
    invoice_date TEXT,
    subtotal REAL,
    tax REAL,
    total REAL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    run_id TEXT,
    lines_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_vendor ON ledger(vendor_id);
CREATE INDEX IF NOT EXISTS idx_ledger_po ON ledger(po_number);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit(run_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def norm_invoice_number(s: str | None) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN batch_id TEXT",          # multi-invoice PDF: runs split from one upload
    "ALTER TABLE runs ADD COLUMN batch_label TEXT",       # e.g. 'invoice 2 of 3 · pages 2-3'
    "ALTER TABLE runs ADD COLUMN parent_run_id TEXT",     # re-run with reviewer corrections
    "ALTER TABLE runs ADD COLUMN overrides_json TEXT",    # the corrected field values
    "ALTER TABLE runs ADD COLUMN superseded_by TEXT",
    "ALTER TABLE runs ADD COLUMN email_id TEXT",          # arrived by email
    "ALTER TABLE runs ADD COLUMN base_total REAL",        # total converted to base currency (USD)
    "ALTER TABLE runs ADD COLUMN fx_rate REAL",
    "ALTER TABLE runs ADD COLUMN sla_due_at TEXT",        # review deadline for the routed team
    "ALTER TABLE runs ADD COLUMN escalated_at TEXT",
]

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,              -- upload | folder | imap | sample
    message_id TEXT,
    from_addr TEXT,
    from_name TEXT,
    to_addr TEXT,
    subject TEXT,
    sent_at TEXT,
    body_excerpt TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    run_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,              -- processed | no_pdf | duplicate_email | error
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT,
    email_id TEXT,
    kind TEXT NOT NULL,                -- routed | escalation | vendor_draft | reviewed
    team TEXT,
    channel TEXT NOT NULL,             -- slack | email | outbox
    recipient TEXT,
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL,              -- sent | simulated | draft | failed
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_run ON notifications(run_id);

CREATE TABLE IF NOT EXISTS vendor_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- alias | date_format | correction
    value TEXT NOT NULL,
    detail TEXT,
    source_run TEXT,
    created_at TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_vendor ON vendor_memory(vendor_id);
"""


def init_db() -> None:
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        c.executescript(EXTRA_SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
        for stmt in MIGRATIONS:
            if stmt.split("ADD COLUMN ")[1].split()[0] not in cols:
                c.execute(stmt)
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id)")
        if c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0:
            seed_ledger()
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
        # a server restart while a run was mid-flight leaves it orphaned
        c.execute("UPDATE runs SET status='failed', error='Interrupted by server restart' WHERE status IN ('queued','running')")
        c.commit()


def seed_ledger() -> None:
    ref = load_reference()
    c = conn()
    for inv in ref["invoice_ledger"]:
        c.execute(
            """INSERT INTO ledger(invoice_number, invoice_number_norm, vendor_id, po_number, invoice_date,
               subtotal, tax, total, status, source, run_id, lines_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (inv["invoice_number"], norm_invoice_number(inv["invoice_number"]), inv["vendor_id"], inv["po_number"],
             inv["invoice_date"], inv["subtotal"], inv["tax"], inv["total"], inv["status"], "seed", None,
             json.dumps(inv.get("lines", [])), now_iso()),
        )


def reset_demo() -> None:
    from .config import reset_reference
    reset_reference()  # back to the seed vendors / POs / goods receipts
    with _lock:
        c = conn()
        for t in ("runs", "ledger", "audit", "emails", "notifications", "vendor_memory"):
            c.execute(f"DELETE FROM {t}")
        seed_ledger()
        c.commit()


# ---------------------------------------------------------------- settings
def get_settings() -> dict[str, Any]:
    with _lock:
        rows = conn().execute("SELECT key, value FROM settings").fetchall()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: json.loads(r["value"]) for r in rows})
    return out


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        c = conn()
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
        c.commit()
    return get_settings()


# ---------------------------------------------------------------- runs
RUN_JSON_COLS = ("stages_json", "extracted_json", "decision_json", "overrides_json")


def create_run(run_id: str, filename: str, file_path: str, source: str, batch_id: str | None = None,
               batch_label: str | None = None, parent_run_id: str | None = None,
               overrides: dict | None = None, email_id: str | None = None) -> None:
    ts = now_iso()
    with _lock:
        conn().execute(
            """INSERT INTO runs(id, created_at, updated_at, filename, file_path, source, status,
                                batch_id, batch_label, parent_run_id, overrides_json, email_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, ts, ts, filename, file_path, source, "queued", batch_id, batch_label, parent_run_id,
             json.dumps(overrides) if overrides else None, email_id),
        )
        conn().commit()


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    for k in RUN_JSON_COLS:
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], default=str)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        conn().execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        conn().commit()


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["stages"] = json.loads(d.pop("stages_json") or "[]")
    d["extracted"] = json.loads(d.pop("extracted_json") or "null")
    d["decision_detail"] = json.loads(d.pop("decision_json") or "null")
    d["overrides"] = json.loads(d.pop("overrides_json") or "null")
    return d


def batch_runs(batch_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT id, batch_label, status, decision, invoice_number, total FROM runs WHERE batch_id=? ORDER BY created_at",
            (batch_id,)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        row = conn().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _run_row(row) if row else None


def list_runs(limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            """SELECT id, created_at, updated_at, filename, source, status, decision, route_to, vendor_id, vendor_name,
                      invoice_number, po_number, total, currency, extraction_method, duration_ms, review_status,
                      reviewer, ledger_posted, error, batch_id, batch_label, parent_run_id, superseded_by,
                      email_id, base_total, fx_rate, sla_due_at, escalated_at, reviewed_at
               FROM runs ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def runs_with_hash(file_hash: str, exclude_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT id, created_at, decision, ledger_posted, invoice_number FROM runs WHERE file_hash=? AND id<>? ORDER BY created_at",
            (file_hash, exclude_id),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- ledger
def ledger_entries(vendor_id: str | None = None, po_number: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM ledger WHERE 1=1", []
    if vendor_id:
        q += " AND vendor_id=?"
        args.append(vendor_id)
    if po_number:
        q += " AND po_number=?"
        args.append(po_number)
    q += " ORDER BY invoice_date"
    with _lock:
        rows = conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["lines"] = json.loads(d.pop("lines_json") or "[]")
        out.append(d)
    return out


def post_to_ledger(run_id: str, entry: dict[str, Any]) -> None:
    with _lock:
        c = conn()
        c.execute(
            """INSERT INTO ledger(invoice_number, invoice_number_norm, vendor_id, po_number, invoice_date,
               subtotal, tax, total, status, source, run_id, lines_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (entry["invoice_number"], norm_invoice_number(entry["invoice_number"]), entry["vendor_id"],
             entry.get("po_number"), entry.get("invoice_date"), entry.get("subtotal"), entry.get("tax"),
             entry.get("total"), entry.get("status", "approved"), "pipeline", run_id,
             json.dumps(entry.get("lines", [])), now_iso()),
        )
        c.execute("UPDATE runs SET ledger_posted=1 WHERE id=?", (run_id,))
        c.commit()


# ---------------------------------------------------------------- audit
def audit(run_id: str | None, actor: str, action: str, detail: str = "") -> None:
    with _lock:
        conn().execute("INSERT INTO audit(run_id, ts, actor, action, detail) VALUES (?,?,?,?,?)",
                       (run_id, now_iso(), actor, action, detail))
        conn().commit()


def audit_for(run_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute("SELECT * FROM audit WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- emails
def create_email(row: dict[str, Any]) -> None:
    cols = ["id", "received_at", "source", "message_id", "from_addr", "from_name", "to_addr", "subject", "sent_at",
            "body_excerpt", "attachments_json", "run_ids_json", "status", "note"]
    vals = [row.get(c) if not c.endswith("_json") else json.dumps(row.get(c[:-5], [])) for c in cols]
    with _lock:
        conn().execute(f"INSERT INTO emails({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        conn().commit()


def update_email(email_id: str, **fields: Any) -> None:
    for k in ("attachments", "run_ids"):  # accept plain names for the JSON columns
        if k in fields:
            fields[f"{k}_json"] = fields.pop(k)
    for k in ("attachments_json", "run_ids_json"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        conn().execute(f"UPDATE emails SET {cols} WHERE id=?", (*fields.values(), email_id))
        conn().commit()


def _email_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["attachments"] = json.loads(d.pop("attachments_json") or "[]")
    d["run_ids"] = json.loads(d.pop("run_ids_json") or "[]")
    return d


def list_emails(limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute("SELECT * FROM emails ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
    return [_email_row(r) for r in rows]


def get_email(email_id: str) -> dict[str, Any] | None:
    with _lock:
        r = conn().execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
    return _email_row(r) if r else None


def email_by_message_id(message_id: str) -> dict[str, Any] | None:
    with _lock:
        r = conn().execute("SELECT * FROM emails WHERE message_id=? AND status<>'duplicate_email' LIMIT 1",
                           (message_id,)).fetchone()
    return _email_row(r) if r else None


# ---------------------------------------------------------------- notifications
def add_notification(**n: Any) -> int:
    n.setdefault("ts", now_iso())
    cols = list(n)
    with _lock:
        cur = conn().execute(f"INSERT INTO notifications({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [n[c] for c in cols])
        conn().commit()
        return cur.lastrowid


def list_notifications(run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM notifications", []
    if run_id:
        q += " WHERE run_id=?"
        args.append(run_id)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock:
        return [dict(r) for r in conn().execute(q, args).fetchall()]


def overdue_reviews(now: str) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            """SELECT id, route_to, vendor_name, invoice_number, total, currency, sla_due_at, created_at FROM runs
               WHERE review_status='pending' AND sla_due_at IS NOT NULL AND sla_due_at < ? AND escalated_at IS NULL""",
            (now,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- vendor memory (learning from corrections)
def add_memory(vendor_id: str, kind: str, value: str, detail: str = "", source_run: str | None = None) -> bool:
    """Insert a learned rule; returns False if the same rule already exists."""
    with _lock:
        c = conn()
        if kind != "correction" and c.execute("SELECT 1 FROM vendor_memory WHERE vendor_id=? AND kind=? AND lower(value)=lower(?)",
                                              (vendor_id, kind, value)).fetchone():
            return False
        if kind == "date_format":  # one active date format per vendor
            c.execute("DELETE FROM vendor_memory WHERE vendor_id=? AND kind='date_format'", (vendor_id,))
        c.execute("INSERT INTO vendor_memory(vendor_id, kind, value, detail, source_run, created_at) VALUES (?,?,?,?,?,?)",
                  (vendor_id, kind, value, detail, source_run, now_iso()))
        c.commit()
    return True


def memory_for(vendor_id: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM vendor_memory", []
    if vendor_id:
        q += " WHERE vendor_id=?"
        args.append(vendor_id)
    q += " ORDER BY id DESC"
    with _lock:
        return [dict(r) for r in conn().execute(q, args).fetchall()]


def memory_hit(mem_id: int) -> None:
    with _lock:
        conn().execute("UPDATE vendor_memory SET hits=hits+1 WHERE id=?", (mem_id,))
        conn().commit()


def delete_memory(mem_id: int) -> bool:
    with _lock:
        cur = conn().execute("DELETE FROM vendor_memory WHERE id=?", (mem_id,))
        conn().commit()
        return cur.rowcount > 0
