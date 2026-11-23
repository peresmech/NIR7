#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 -m pip install pyinstaller
python3 -m PyInstaller --noconsole --onedir --name HydroGIS \
  --add-data "templates:templates" \
  --add-data "resources:resources" \
  app.py
echo "Готово: dist/HydroGIS/HydroGIS"
