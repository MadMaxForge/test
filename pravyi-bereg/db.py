"""SQLite database for content queue and history."""
from __future__ import annotations

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from config import DB_PATH

log = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    """Get or create database connection."""
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db():
    """Initialize database tables."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS content_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT DEFAULT 'vk',
            post_type TEXT DEFAULT 'post',
            topic TEXT,
            format TEXT,
            hook TEXT,
            text_content TEXT,
            content_hash TEXT,
            media_ids TEXT,
            scheduled_time TEXT,
            status TEXT DEFAULT 'pending_approval',
            rejection_reason TEXT,
            created_at TEXT,
            approved_at TEXT,
            published_at TEXT
        );

        CREATE TABLE IF NOT EXISTS published_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_post_id INTEGER,
            channel TEXT DEFAULT 'vk',
            post_type TEXT DEFAULT 'post',
            topic TEXT,
            format TEXT,
            text_content TEXT,
            content_hash TEXT,
            media_ids TEXT,
            published_at TEXT,
            photo_path TEXT
        );

        CREATE TABLE IF NOT EXISTS media_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            used_at TEXT,
            post_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS parsed_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            source_url TEXT,
            title TEXT,
            body TEXT,
            topic_category TEXT,
            parsed_at TEXT,
            used_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS topics_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT UNIQUE,
            category TEXT,
            source TEXT DEFAULT 'manual',
            used_count INTEGER DEFAULT 0,
            last_used_at TEXT
        );
    """)
    conn.commit()
    log.info("Database initialized: %s", DB_PATH)


def execute(sql: str, params: tuple = ()) -> list:
    """Execute SQL and return rows."""
    conn = get_db()
    cursor = conn.execute(sql, params)
    return cursor.fetchall()


def execute_insert(sql: str, params: tuple = ()) -> int:
    """Execute INSERT and return lastrowid."""
    conn = get_db()
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor.lastrowid


def get_queue_item(queue_id: int) -> dict | None:
    """Get a queue item by ID."""
    rows = execute("SELECT * FROM content_queue WHERE id = ?", (queue_id,))
    if rows:
        return dict(rows[0])
    return None


def get_least_used_photo() -> str | None:
    """Get the least recently used photo from source photos."""
    from config import SOURCE_PHOTOS
    
    photos = list(SOURCE_PHOTOS.glob("*.png")) + list(SOURCE_PHOTOS.glob("*.jpg")) + list(SOURCE_PHOTOS.glob("*.jpeg"))
    if not photos:
        return None
    
    # Get usage counts
    usage = {}
    for row in execute("SELECT file_path, COUNT(*) as cnt FROM media_usage GROUP BY file_path"):
        usage[row["file_path"]] = row["cnt"]
    
    # Sort by usage count (least used first), then by last used time
    photos_sorted = sorted(photos, key=lambda p: usage.get(str(p), 0))
    return str(photos_sorted[0])


def record_media_usage(file_path: str, post_id: int = 0):
    """Record that a media file was used."""
    execute_insert(
        "INSERT INTO media_usage (file_path, used_at, post_id) VALUES (?, ?, ?)",
        (file_path, datetime.now().isoformat(), post_id),
    )
