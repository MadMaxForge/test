"""Bot configuration — loads from environment variables (with dotenv fallback)."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (parent of nutrition_bot/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
# Also try working directory
load_dotenv(override=False)

BOT_TOKEN: str = os.environ.get("NUTRITION_BOT_TOKEN", "")
OPENROUTER_API_KEY: str = os.environ.get("NUTRITION_OPENROUTER_KEY", "")
OPENROUTER_MODEL: str = "google/gemini-2.0-flash-001"
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1/chat/completions"

DB_PATH: str = os.environ.get("NUTRITION_DB_PATH", "nutrition_bot.db")

# Owner restriction — only this Telegram user ID can use the bot.
# Set to 0 to allow everyone.
OWNER_ID: int = int(os.environ.get("NUTRITION_OWNER_ID", "444288673"))

# Google Calendar (optional)
GOOGLE_CREDENTIALS_JSON: str = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
