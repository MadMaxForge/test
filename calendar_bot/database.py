import aiosqlite
from datetime import datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS sent_reminders (
                event_id TEXT NOT NULL,
                reminder_type TEXT NOT NULL,
                sent_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (event_id, reminder_type)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    async def is_reminder_sent(self, event_id: str, reminder_type: str) -> bool:
        assert self.db is not None
        cursor = await self.db.execute(
            "SELECT 1 FROM sent_reminders WHERE event_id = ? AND reminder_type = ?",
            (event_id, reminder_type),
        )
        row = await cursor.fetchone()
        return row is not None

    async def mark_reminder_sent(self, event_id: str, reminder_type: str) -> None:
        assert self.db is not None
        await self.db.execute(
            "INSERT OR IGNORE INTO sent_reminders (event_id, reminder_type) VALUES (?, ?)",
            (event_id, reminder_type),
        )
        await self.db.commit()

    async def cleanup_old_reminders(self, days: int = 7) -> None:
        assert self.db is not None
        await self.db.execute(
            "DELETE FROM sent_reminders WHERE sent_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await self.db.commit()

    async def get_setting(self, key: str) -> str | None:
        assert self.db is not None
        cursor = await self.db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        assert self.db is not None
        await self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self.db.commit()

    async def save_message(self, chat_id: int, role: str, content: str | None) -> None:
        assert self.db is not None
        if not content:
            content = ""
        await self.db.execute(
            "INSERT INTO conversation_history (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
        # Keep only last 20 messages per chat
        await self.db.execute("""
            DELETE FROM conversation_history
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM conversation_history
                WHERE chat_id = ?
                ORDER BY id DESC LIMIT 20
            )
        """, (chat_id, chat_id))
        await self.db.commit()

    async def get_history(self, chat_id: int, limit: int = 10) -> list[dict[str, str]]:
        assert self.db is not None
        cursor = await self.db.execute(
            "SELECT role, content FROM conversation_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]
