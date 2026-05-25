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
    echo WARNING: .env not found. Server will start but Anthropic calls will fail.
)

start "" http://localhost:8000
python -m uvicorn server:app --host 127.0.0.1 --port 8000
