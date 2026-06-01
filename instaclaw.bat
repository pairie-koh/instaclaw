@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do set %%a=%%b
) else (
    echo WARNING: .env not found. Server will start but codegraff calls will fail.
)

if "%KURI_API_TOKEN%"=="" (
    echo ERROR: KURI_API_TOKEN missing from .env. Re-run setup.bat.
    pause
    exit /b 1
)

REM Start kuri in WSL if not already up. Non-headless so the user can log in
REM to Instagram once in the WSLg-rendered Chrome window; the profile in
REM /root/.kuri/ persists the session.
curl -s -o NUL -w "%%{http_code}" -H "Authorization: Bearer %KURI_API_TOKEN%" http://127.0.0.1:8080/health > %TEMP%\kuri_hc.txt 2>NUL
set /p HC=<%TEMP%\kuri_hc.txt
del %TEMP%\kuri_hc.txt 2>NUL

if not "%HC%"=="200" (
    echo Starting kuri in WSL ...
    start "kuri" /MIN wsl -d Ubuntu -- bash -lc "KURI_API_TOKEN=%KURI_API_TOKEN% HEADLESS=false /root/.local/bin/kuri"
    timeout /t 6 /nobreak >nul
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
start "" http://localhost:8000
python -m uvicorn server:app --host 127.0.0.1 --port 8000
