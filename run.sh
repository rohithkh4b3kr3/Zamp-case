#!/usr/bin/env sh
# One-command local start (macOS / Linux / Git Bash). Serves API + UI on http://localhost:8765
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
PY=.venv/bin/python; [ -f .venv/Scripts/python.exe ] && PY=.venv/Scripts/python.exe
$PY -m pip install -q -r requirements.txt
if [ ! -d "$ROOT/frontend/dist" ]; then (cd "$ROOT/frontend" && npm install && npm run build); fi
exec $PY -m uvicorn app.main:app --host 127.0.0.1 --port 8765
