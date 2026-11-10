"""Paths, environment and default business rules."""
from __future__ import annotations

import json
import os

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
SAMPLES_DIR = ROOT / "samples"
STORAGE_DIR = ROOT / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
DB_PATH = STORAGE_DIR / "zamp_ap.db"
REFERENCE_PATH = DATA_DIR / "reference.json"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# LLM providers (all optional). Same stack as ai24c Invoice_Processing: OpenAI SDK + instructor,
# gpt-5-mini vision; OPENAI_BASE_URL=https://openrouter.ai/api/v1 routes it through OpenRouter.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
# An empty OPENAI_BASE_URL in .env would otherwise be picked up by the OpenAI SDK as the URL itself
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")
# openai | anthropic | "" (auto: whichever key is set, OpenAI first)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
# Google Cloud Vision OCR for scans (as in ai24c). Unset -> local RapidOCR.
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
# auto  -> Claude when a key is configured, otherwise local text-layer / OCR parsing
# llm   -> always Claude (fails the stage if no key)
# local -> never call an LLM
EXTRACTION_MODE = os.getenv("EXTRACTION_MODE", "auto").lower()

# Notifications to review teams. Without these, messages land in the in-app outbox ("simulated").
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "ap-autopilot@lumenretail.example").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8765").rstrip("/")

# Email intake. A watched folder always works; IMAP polling turns on when IMAP_HOST is set.
INBOX_DROP_DIR = STORAGE_DIR / "inbox_drop"
INBOX_DROP_DIR.mkdir(parents=True, exist_ok=True)
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()
IMAP_USER = os.getenv("IMAP_USER", "").strip()
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "").strip()
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX").strip()
IMAP_POLL_SECONDS = int(os.getenv("IMAP_POLL_SECONDS", "60") or 60)

# "Today" for date checks. Pinned so the demo invoices stay valid; override with AP_TODAY.
AP_TODAY = os.getenv("AP_TODAY", "")

# Business rules. Editable live from the Settings page; persisted in SQLite.
DEFAULT_SETTINGS: dict = {
    "price_tolerance_pct": 2.0,          # unit-price drift allowed per line vs PO
    "amount_tolerance_abs": 25.0,        # absolute slack on totals (rounding, freight cents)
    "po_overrun_tolerance_pct": 1.0,     # cumulative billing allowed above PO value
    "approval_limit": 25000.0,           # invoices above this need a manager even if clean
    "duplicate_window_days": 60,         # same vendor + same amount within N days = suspicious
    "max_invoice_age_days": 180,
    "min_field_confidence": 0.80,        # below this, a human checks the extraction
    "po_inference_min_confidence": 0.80, # below this, the PO guess is not trusted
    "stage_min_ms": 450,                 # demo pacing so each stage is visible in the live view
    "sla_treasury_hours": 4.0,           # fraud signals must be looked at fast
    "sla_procurement_hours": 48.0,
    "sla_default_hours": 24.0,           # Receiving, AP Specialist, Finance Manager
    "max_prepayment_pct": 30.0,          # deposit invoices may bill up to this % of the PO (unless the PO says otherwise)
    "non_po_limit": 5000.0,              # non-PO invoices above this (USD) go to the Finance Manager
    "non_po_variance_pct": 25.0,         # recurring non-PO bills outside +/- this % of the usual amount get reviewed
}


# Reference data (vendors, POs, goods receipts) starts from the seed file and becomes editable:
# imports, live spreadsheet syncs and in-app edits write a live copy; Reset demo deletes it.
LIVE_REFERENCE_PATH = STORAGE_DIR / "reference_live.json"
_ref_cache: dict = {"mtime": None, "path": None, "data": None}


def load_reference() -> dict:
    path = LIVE_REFERENCE_PATH if LIVE_REFERENCE_PATH.exists() else REFERENCE_PATH
    mtime = path.stat().st_mtime_ns
    if _ref_cache["data"] is None or _ref_cache["path"] != path or _ref_cache["mtime"] != mtime:
        _ref_cache.update(mtime=mtime, path=path, data=json.loads(path.read_text(encoding="utf-8")))
    return _ref_cache["data"]


def save_reference(data: dict) -> None:
    tmp = LIVE_REFERENCE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(LIVE_REFERENCE_PATH)
    _ref_cache["data"] = None


def reset_reference() -> None:
    LIVE_REFERENCE_PATH.unlink(missing_ok=True)
    _ref_cache["data"] = None
