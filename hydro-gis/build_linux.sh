#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --no-cache-dir -r requirements.txt
.venv/bin/python -m pip install --no-cache-dir pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --onedir --name HydroGIS \
  --collect-all PyQt5.QtWebEngineWidgets \
  --collect-all PyQt5.QtWebEngineCore \
  --hidden-import PyQt5.QtWebEngineWidgets \
  --hidden-import PyQt5.QtWebEngineCore \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --add-data "resources:resources" \
  app.py
echo "Сборка готова: dist/HydroGIS (скопируйте всю папку)"
