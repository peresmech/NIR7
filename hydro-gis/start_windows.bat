@echo off
cd /d "%~dp0"
if exist "dist\HydroGIS\HydroGIS.exe" (
    start "" "%~dp0dist\HydroGIS\HydroGIS.exe"
    exit /b 0
)

echo Первый запуск: подготовка локального приложения.
echo Нужны Python 3.11+ и подключение к Интернету только для сборки.
call build_windows.bat
if errorlevel 1 exit /b 1
if not exist "dist\HydroGIS\HydroGIS.exe" (
    echo Не найден готовый исполняемый файл.
    pause
    exit /b 1
)
start "" "%~dp0dist\HydroGIS\HydroGIS.exe"
