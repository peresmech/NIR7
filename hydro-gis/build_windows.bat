@echo off
cd /d "%~dp0"
python -m pip install --no-cache-dir -r requirements.txt
python -m pip install --no-cache-dir pyinstaller
python -m PyInstaller --noconsole --onedir --name GidroRezhim ^
  --icon "resources\hydro-regime.ico" ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "resources;resources" ^
  app.py
echo.
echo Готово: dist\GidroRezhim\GidroRezhim.exe
echo Ярлык с иконкой: создайте его на GidroRezhim.exe в проводнике Windows.
pause
