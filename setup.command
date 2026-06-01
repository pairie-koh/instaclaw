#!/usr/bin/env bash
cd "$(dirname "$0")"
set -e

echo "=== instaclaw setup (codegraff SDK) ==="

# codegraff ships native wheels for macOS arm64 / CPython 3.12, 3.13, 3.14t only.
if [ "$(uname -s)" != "Darwin" ]; then
    echo "ERROR: setup.command is for macOS. On Linux/Windows, install codegraff per its docs, then run: python register_mcp.py && uvicorn server:app."
    read -p "Press Enter to close..." dummy
    exit 1
fi

# Find a compatible interpreter (3.13 preferred, then 3.12).
PY=""
for c in python3.13 python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "ERROR: need Python 3.12 or 3.13 (codegraff has no 3.14 wheel)."
    echo "Install one with:  brew install python@3.13"
    read -p "Press Enter to close..." dummy
    exit 1
fi
echo "Using $PY ($($PY -V 2>&1))"

if [ ! -f ".venv-cg/bin/activate" ]; then
    echo "Creating virtualenv at .venv-cg ..."
    "$PY" -m venv .venv-cg
else
    echo "Virtualenv .venv-cg already exists, reusing."
fi
source .venv-cg/bin/activate

echo "Upgrading pip ..."
python -m pip install --upgrade pip

echo "Installing requirements (codegraff, mcp, kuri client deps) ..."
pip install -r requirements.txt

echo "Installing playwright chromium (for share-card PNGs) ..."
playwright install chromium || echo "WARNING: playwright install chromium failed; card PNGs won't render."

echo "Registering the kuri MCP server in forge ..."
python register_mcp.py

# Prompt for CODEGRAFF_API_KEY if .env missing
if [ ! -f ".env" ]; then
    echo ""
    echo "No .env file found. Please paste your codegraff API key."
    echo "(Grab one at https://codegraff.com/dashboard/keys — a cg_sk_ key.)"
    echo "It will be saved to .env in this folder."
    read -p "CODEGRAFF_API_KEY: " APIKEY
    if [ -z "$APIKEY" ]; then
        echo "No key entered, skipping .env creation. You can create it later from .env.example."
    else
        echo "CODEGRAFF_API_KEY=$APIKEY" > .env
        echo "Wrote .env"
    fi
else
    echo ".env already exists, leaving it alone."
fi

echo ""
echo "Setup complete. Make sure kuri is running, then double-click instaclaw.command."
echo ""
read -p "Press Enter to close..." dummy
