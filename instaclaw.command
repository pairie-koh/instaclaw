#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: .venv not found. Run setup.command first."
    read -p "Press Enter to close..." dummy
    exit 1
fi

source .venv/bin/activate

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
else
    echo "WARNING: .env not found. Server will start but Anthropic calls will fail."
fi

(sleep 2 && open http://localhost:8000) &
exec python -m uvicorn server:app --host 127.0.0.1 --port 8000
