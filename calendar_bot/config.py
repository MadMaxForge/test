import os
from dataclasses import dataclass, field


@dataclass
class Config:
    telegram_token: str
    openrouter_api_key: str
    google_script_url: str
    google_script_token: str
    owner_chat_id: int
    llm_model: str
    reminder_check_interval: int
    reminder_before_minutes: list[int]
    timezone: str
    daily_digest_hour: int
    daily_digest_minute: int
    db_path: str

    @classmethod
    def from_env(cls) -> "Config":
        reminder_minutes_str = os.environ.get("REMINDER_BEFORE_MINUTES", "15,5")
        reminder_minutes = [int(x.strip()) for x in reminder_minutes_str.split(",")]

        return cls(
            telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
            openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
            google_script_url=os.environ["GOOGLE_SCRIPT_URL"],
            google_script_token=os.environ["GOOGLE_SCRIPT_TOKEN"],
            owner_chat_id=int(os.environ.get("OWNER_CHAT_ID", "0")),
            llm_model=os.environ.get("LLM_MODEL", "google/gemini-2.0-flash"),
            reminder_check_interval=int(os.environ.get("REMINDER_CHECK_INTERVAL", "120")),
            reminder_before_minutes=reminder_minutes,
            timezone=os.environ.get("TIMEZONE", "Europe/Moscow"),
            daily_digest_hour=int(os.environ.get("DAILY_DIGEST_HOUR", "8")),
            daily_digest_minute=int(os.environ.get("DAILY_DIGEST_MINUTE", "0")),
            db_path=os.environ.get("DB_PATH", "calendar_bot.db"),
        )
