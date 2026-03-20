import json
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
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS event_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                typical_start_hour INTEGER,
                typical_duration_minutes INTEGER,
                category TEXT,
                color TEXT,
                use_count INTEGER DEFAULT 1,
                last_used TEXT DEFAULT (datetime('now'))
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

    async def track_event_template(
        self, title: str, start_hour: int | None = None,
        duration_minutes: int | None = None, category: str | None = None,
        color: str | None = None,
    ) -> None:
        assert self.db is not None
        # Check if similar template exists
        cursor = await self.db.execute(
            "SELECT id, use_count FROM event_templates WHERE title = ?",
            (title,),
        )
        row = await cursor.fetchone()
        if row:
            await self.db.execute(
                "UPDATE event_templates SET use_count = use_count + 1, last_used = datetime('now') WHERE id = ?",
                (row[0],),
            )
        else:
            await self.db.execute(
                "INSERT INTO event_templates (title, typical_start_hour, typical_duration_minutes, category, color) VALUES (?, ?, ?, ?, ?)",
                (title, start_hour, duration_minutes, category, color),
            )
        await self.db.commit()

    async def get_frequent_templates(self, min_count: int = 2, limit: int = 5) -> list[dict]:
        assert self.db is not None
        cursor = await self.db.execute(
            "SELECT title, typical_start_hour, typical_duration_minutes, category, color, use_count "
            "FROM event_templates WHERE use_count >= ? ORDER BY use_count DESC LIMIT ?",
            (min_count, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "title": r[0],
                "typical_start_hour": r[1],
                "typical_duration_minutes": r[2],
                "category": r[3],
                "color": r[4],
                "use_count": r[5],
            }
            for r in rows
        ]
