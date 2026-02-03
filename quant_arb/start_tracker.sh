#!/bin/bash
# Start the Polymarket Whale Tracker

cd "$(dirname "$0")"

# Load environment variables
if [ -f ../.env ]; then
    export $(cat ../.env | grep -v '^#' | xargs)
fi

if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check for required env vars
if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "ERROR: Missing Telegram configuration"
    echo "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env file"
    exit 1
fi

echo "Starting Whale Tracker..."
python3 whale_tracker.py
