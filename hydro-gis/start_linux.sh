#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip cache purge >/dev/null 2>&1 || true
.venv/bin/python -m pip install --upgrade pip --no-cache-dir
.venv/bin/python -m pip install --no-cache-dir -r requirements.txt
.venv/bin/python app.py
