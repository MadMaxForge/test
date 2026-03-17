#!/bin/bash
# Start the Calendar Bot
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Run the bot
exec python -m calendar_bot.main
