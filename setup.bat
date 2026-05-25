@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === instaclaw setup ===

REM Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not installed or not on PATH.
    echo Please install Python 3.10+ from https://python.org/downloads
    echo During install, check "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM Check Python version >= 3.10 (uses python itself for portability)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: Python is older than 3.10. Install a newer Python from https://python.org/downloads
    echo.
    pause
    exit /b 1
)

REM Create venv if missing
if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtualenv at .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: failed to create virtualenv.
        pause
        exit /b 1
    )
) else (
    echo Virtualenv .venv already exists, reusing.
)

call .venv\Scripts\activate.bat

echo Upgrading pip ...
python -m pip install --upgrade pip

echo Installing requirements ...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

echo Installing playwright chromium ...
playwright install chromium
if errorlevel 1 (
    echo WARNING: playwright install chromium failed. You may need to run it manually later.
)

REM Prompt for ANTHROPIC_API_KEY if .env missing
if not exist ".env" (
    echo.
    echo No .env file found. Please paste your Anthropic API key.
    echo It will be saved to .env in this folder.
    set /p APIKEY=ANTHROPIC_API_KEY:
    if "!APIKEY!"=="" (
        echo No key entered, skipping .env creation. You can create it later from .env.example.
    ) else (
        > .env echo ANTHROPIC_API_KEY=!APIKEY!
        echo Wrote .env
    )
) else (
    echo .env already exists, leaving it alone.
)

echo.
echo Setup complete. Double-click instaclaw.bat to start.
echo.
pause
endlocal
