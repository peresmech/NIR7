@echo off
cd /d "%~dp0"
setlocal

if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto failed
)

".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-cache-dir pyinstaller
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name HydroGIS ^
  --icon "resources\hydrogis.ico" ^
  --collect-all PyQt5.QtWebEngineWidgets ^
  --collect-all PyQt5.QtWebEngineCore ^
  --hidden-import PyQt5.QtWebEngineWidgets ^
  --hidden-import PyQt5.QtWebEngineCore ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "resources;resources" ^
  app.py
if errorlevel 1 goto failed

echo.
echo Сборка готова: dist\HydroGIS\HydroGIS.exe
echo Для переноса скопируйте всю папку dist\HydroGIS.
exit /b 0

:failed
echo.
echo Сборка не завершена. Проверьте сообщение об ошибке выше.
pause
exit /b 1
