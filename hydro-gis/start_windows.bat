@echo off
cd /d "%~dp0"

echo Подготовка приложения и сборка HydroGIS.exe.
echo Этот этап требует Python 3.11+ и Интернет для загрузки зависимостей.
call build_windows.bat
if errorlevel 1 exit /b 1
if not exist "dist\HydroGIS\HydroGIS.exe" (
    echo Не найден готовый исполняемый файл.
    pause
    exit /b 1
)
start "" "%~dp0dist\HydroGIS\HydroGIS.exe"
