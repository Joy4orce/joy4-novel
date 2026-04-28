@echo off
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python is not installed.
    echo Please install from: https://www.python.org/downloads/
    pause
    exit /b 1
)

pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo Package install failed.
    pause
    exit /b 1
)

start "" pythonw "%~dp0main.py"
