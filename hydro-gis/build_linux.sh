#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pip install --no-cache-dir -r requirements.txt
python3 -m pip install --no-cache-dir pyinstaller
python3 -m PyInstaller --noconsole --onedir --name GidroRezhim \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --add-data "resources:resources" \
  app.py
echo "Готово: dist/GidroRezhim/GidroRezhim"
