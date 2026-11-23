@echo off
cd /d "%~dp0"
python -m pip install --no-cache-dir -r requirements.txt
python -m pip install --no-cache-dir pyinstaller
python -m PyInstaller --noconsole --onedir --name HydroGIS ^
  --add-data "templates;templates" ^
  --add-data "resources;resources" ^
  app.py
echo.
echo Готово: dist\HydroGIS\HydroGIS.exe
pause
