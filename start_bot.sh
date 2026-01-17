#!/bin/bash

# ===========================================
# Polymarket Speed Trading Bot - Start Script
# ===========================================

echo "=============================================="
echo "  POLYMARKET SPEED TRADING BOT"
echo "=============================================="
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found!"
    echo ""
    echo "Quick fix:"
    echo "  cp autobot/.env.example .env"
    echo "  nano .env  # edit your settings"
    echo ""
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
REQUIRED_VERSION="3.9"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "WARNING: Python $PYTHON_VERSION detected. Python 3.9+ recommended."
fi

# Install dependencies if needed
if ! python3 -c "import httpx" 2>/dev/null; then
    echo "Installing dependencies..."
    pip3 install -r autobot/requirements.txt
fi

echo ""
echo "Starting bot..."
echo "Press Ctrl+C to stop"
echo ""

# Run the bot
python3 -m autobot.main
