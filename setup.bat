@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === instaclaw setup ===

REM --- Python check ---
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not installed or not on PATH.
    echo Install Python 3.10+ from https://python.org/downloads ^(check "Add Python to PATH"^).
    echo.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo ERROR: Python ^< 3.10. Install a newer one from https://python.org/downloads
    pause
    exit /b 1
)

REM --- WSL check (kuri only ships Linux binaries) ---
wsl -l -v >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: WSL is not installed. instaclaw drives the browser through kuri,
    echo which only ships Linux binaries.
    echo.
    echo Open an Administrator PowerShell and run:
    echo     wsl --install -d Ubuntu
    echo Reboot, then re-run setup.bat.
    pause
    exit /b 1
)

wsl -d Ubuntu -- bash -c "true" >nul 2>&1
if errorlevel 1 (
    echo ERROR: WSL distro "Ubuntu" not found.
    echo Run:   wsl --install -d Ubuntu
    pause
    exit /b 1
)

REM --- venv + python deps ---
if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtualenv at .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: failed to create virtualenv.
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

REM --- google-chrome inside WSL (for kuri-managed browser) ---
echo Checking google-chrome in WSL ...
wsl -d Ubuntu -- bash -c "command -v google-chrome >/dev/null"
if errorlevel 1 (
    echo Installing google-chrome inside WSL ...
    wsl -d Ubuntu -- bash -c "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wget gnupg ca-certificates >/dev/null 2>&1; mkdir -p /etc/apt/keyrings; wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-linux.gpg; echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/google-linux.gpg] https://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list; DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq google-chrome-stable >/dev/null 2>&1"
)

REM --- kuri install inside WSL (idempotent) ---
echo Checking kuri in WSL ...
wsl -d Ubuntu -- bash -c "test -x /root/.local/bin/kuri || curl -fsSL https://raw.githubusercontent.com/justrach/kuri/main/install.sh | sh"
if errorlevel 1 (
    echo ERROR: failed to install kuri inside WSL.
    pause
    exit /b 1
)

echo.
echo NOTE: The kuri install script currently ships v0.4.4. The CDP-detach fix
echo for Instagram landed in v0.4.5 ^(commit 648fe344, issue #172^) but is not
echo yet published as a release binary. If you see CDP errors after navigating
echo to instagram.com, you need v0.4.5 — build from source inside WSL:
echo   cd /root ^&^& git clone --branch v0.4.5 https://github.com/justrach/kuri.git kuri-src
echo   cd kuri-src ^&^& zig build -Doptimize=ReleaseFast
echo   cp zig-out/bin/kuri /root/.local/bin/kuri
echo.

REM --- .env: CODEGRAFF_API_KEY + KURI_API_TOKEN ---
if not exist ".env" (
    echo. > .env
)

findstr /B /C:"CODEGRAFF_API_KEY=" .env >nul 2>&1
if errorlevel 1 (
    echo.
    set /p APIKEY=CODEGRAFF_API_KEY:
    if "!APIKEY!" NEQ "" (
        echo CODEGRAFF_API_KEY=!APIKEY!>> .env
    )
)

findstr /B /C:"KURI_API_TOKEN=" .env >nul 2>&1
if errorlevel 1 (
    for /f %%t in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"') do set TOK=%%t
    echo KURI_API_TOKEN=!TOK!>> .env
    echo Generated KURI_API_TOKEN.
)

findstr /B /C:"PYTHONIOENCODING=" .env >nul 2>&1
if errorlevel 1 (
    echo PYTHONIOENCODING=utf-8>> .env
)

echo.
echo Setup complete. Double-click instaclaw.bat to start.
echo.
pause
endlocal
