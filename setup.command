#!/usr/bin/env bash
cd "$(dirname "$0")"
set -e

echo "=== instaclaw setup ==="

# Check python3
if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "ERROR: python3 is not installed."
    echo "Install Python 3.10+ from https://python.org/downloads"
    echo ""
    read -p "Press Enter to close..." dummy
    exit 1
fi

# Check version >= 3.10
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PYMAJOR=${PYVER%.*}
PYMINOR=${PYVER#*.}
if [ "$PYMAJOR" -lt 3 ] || { [ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 10 ]; }; then
    echo ""
    echo "ERROR: Python $PYVER is too old. Need 3.10+."
    echo "Install a newer Python from https://python.org/downloads"
    echo ""
    read -p "Press Enter to close..." dummy
    exit 1
fi
echo "Found Python $PYVER"

# Create venv if missing
if [ ! -f ".venv/bin/activate" ]; then
    echo "Creating virtualenv at .venv ..."
    python3 -m venv .venv
else
    echo "Virtualenv .venv already exists, reusing."
fi

source .venv/bin/activate

echo "Upgrading pip ..."
python -m pip install --upgrade pip

echo "Installing requirements ..."
pip install -r requirements.txt

echo "Installing playwright chromium ..."
playwright install chromium || echo "WARNING: playwright install chromium failed. You may need to run it manually later."

# Prompt for OPENROUTER_API_KEY if .env missing
if [ ! -f ".env" ]; then
    echo ""
    echo "No .env file found. Please paste your OpenRouter API key."
    echo "(Grab one at https://openrouter.ai — single key routes to DeepSeek + Qwen-VL.)"
    echo "It will be saved to .env in this folder."
    read -p "OPENROUTER_API_KEY: " APIKEY
    if [ -z "$APIKEY" ]; then
        echo "No key entered, skipping .env creation. You can create it later from .env.example."
    else
        echo "OPENROUTER_API_KEY=$APIKEY" > .env
        echo "Wrote .env"
    fi
else
    echo ".env already exists, leaving it alone."
fi

echo ""
echo "Setup complete. Double-click instaclaw.command to start."
echo ""
read -p "Press Enter to close..." dummy
