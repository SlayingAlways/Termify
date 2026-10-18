@echo off
setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo === Termify first-time setup ===
    echo Creating virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo Could not create a virtual environment.
        echo Install Python 3.10+ from https://www.python.org/downloads/ and try again.
        pause
        exit /b 1
    )
    echo Installing dependencies ^(this can take a minute^)...
    "%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Dependency install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo Setup complete.
    echo.
)

"%PY%" -m termify %*
if errorlevel 1 pause
