@echo off
cd /d "%~dp0"

set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON (
    where python >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
    echo Python не найден. Установите Python 3.11+ с сайта python.org.
    echo При установке включите флажок "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Создание локального окружения Python...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo Не удалось создать виртуальное окружение.
        pause
        exit /b 1
    )
)

echo Установка зависимостей...
".venv\Scripts\python.exe" -m pip cache purge >nul 2>nul
".venv\Scripts\python.exe" -m pip install --upgrade pip --no-cache-dir
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 (
    echo Не удалось установить зависимости. Проверьте подключение к Интернету.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" app.py
pause
