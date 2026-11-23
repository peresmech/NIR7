@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconsole --onedir --name HydroGIS ^
  --add-data "templates;templates" ^
  --add-data "..\first\data\hydro.db;data_seed" ^
  --add-data "..\first\data\stations.csv;." ^
  app.py
echo.
echo Готово: dist\HydroGIS\HydroGIS.exe
pause
