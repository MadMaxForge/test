import os

BOT_TOKEN = os.environ.get("NUTRITION_BOT_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("NUTRITION_OPENROUTER_KEY", "")
OPENROUTER_MODEL = "google/gemini-2.0-flash-001"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

DB_PATH = os.environ.get("NUTRITION_DB_PATH", "nutrition_bot.db")
