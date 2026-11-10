"""Team notifications and SLA escalation.

Every routed exception produces a message to the owning team. Delivery:
  * Slack incoming webhook  (SLACK_WEBHOOK_URL)
  * SMTP email              (SMTP_HOST ...)
  * otherwise the in-app outbox, marked "simulated" - so the demo shows exactly what would be sent.
Vendor emails are never sent automatically: they are stored as drafts for a human to send.
"""
from __future__ import annotations

import json
import smtplib
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import config, db
from .config import load_reference


def _team(team: str) -> dict:
    return load_reference().get("teams", {}).get(team, {})


def sla_hours(team: str | None) -> float | None:
    if not team or team == "Vendor":
        return None
    t = _team(team)
    return float(db.get_settings().get(t.get("sla_setting", "sla_default_hours"), 24))


def _deliver(channel: str, recipient: str, subject: str, body: str) -> tuple[str, str | None]:
    try:
        if channel == "slack" and config.SLACK_WEBHOOK_URL:
            payload = json.dumps({"text": f"*{subject}*\n{body}"}).encode()
            req = urllib.request.Request(config.SLACK_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()
            return "sent", None
        if channel == "email" and config.SMTP_HOST:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, recipient, subject
            msg.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
            return "sent", None
        return "simulated", None
    except Exception as e:  # never let a notification failure break the pipeline
        return "failed", f"{type(e).__name__}: {e}"


def notify_team(team: str, kind: str, subject: str, body: str, run_id: str | None = None) -> None:
    t = _team(team)
    channel = "slack" if config.SLACK_WEBHOOK_URL else "email" if config.SMTP_HOST else "outbox"
    recipient = t.get("slack") if channel == "slack" else t.get("email") or team
    status, err = _deliver(channel, recipient, subject, body)
    db.add_notification(run_id=run_id, kind=kind, team=team, channel=channel, recipient=recipient,
                        subject=subject, body=body, status=status, error=err)


def vendor_draft(to: str | None, subject: str, body: str, run_id: str | None = None, email_id: str | None = None) -> None:
    db.add_notification(run_id=run_id, email_id=email_id, kind="vendor_draft", team="Vendor", channel="email",
                        recipient=to, subject=subject, body=body, status="draft")


def on_decision(run_id: str, dec: dict, inv: dict, vendor: dict | None) -> str | None:
    """Called once per finished run. Returns the SLA due timestamp for routed exceptions."""
    from . import connectors  # run report + event actions (never raises)
    connectors.after_decision(run_id, dec, inv, vendor)
    link = f"{config.APP_BASE_URL}/runs/{run_id}"
    who = vendor["name"] if vendor else (inv.get("vendor_name") or "Unknown vendor")
    amount = f"{inv.get('currency') or 'USD'} {inv.get('total') or 0:,.2f}"
    if dec["decision"] == "NEEDS_REVIEW" and dec.get("route_to"):
        hours = sla_hours(dec["route_to"])
        hours = 24.0 if hours is None else hours
        due = datetime.now(timezone.utc) + timedelta(hours=hours)
        reasons = "\n".join(f"• {r['label']}: {r['text']}" for r in dec.get("reasons", [])[:4])
        notify_team(dec["route_to"], "routed",
                    f"[AP] {who} {inv.get('invoice_number') or ''} needs {dec['route_to']} review ({amount})",
                    f"{dec['headline']}\n\n{reasons}\n\nRespond within {hours:g}h (by {due:%d %b %H:%M} UTC).\nOpen: {link}",
                    run_id)
        return due.isoformat(timespec="seconds")
    if dec.get("vendor_email"):
        e = dec["vendor_email"]
        vendor_draft(e.get("to"), e.get("subject", ""), e.get("body", ""), run_id)
    return None


def check_sla() -> int:
    """Escalate pending reviews past their deadline (once each) to the team and the Finance Manager."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for r in db.overdue_reviews(now):
        late = r["sla_due_at"]
        body = (f"{r['vendor_name']} {r['invoice_number'] or ''} ({r['currency'] or 'USD'} {r['total'] or 0:,.2f}) has been "
                f"waiting on {r['route_to']} past its SLA (due {late}).\nOpen: {config.APP_BASE_URL}/runs/{r['id']}")
        notify_team(r["route_to"], "escalation", f"[AP] SLA breached — {r['route_to']}: {r['vendor_name']}", body, r["id"])
        if r["route_to"] != "Finance Manager":
            notify_team("Finance Manager", "escalation", f"[AP] Escalation: {r['route_to']} SLA breached", body, r["id"])
        db.update_run(r["id"], escalated_at=now)
        db.audit(r["id"], "system", "sla_escalated", f"{r['route_to']} past due {late}")
        n += 1
    return n


_started = False


def start_sla_watcher(interval: float = 20.0) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                check_sla()
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="sla-watcher").start()
