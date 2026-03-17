import sqlite3
from typing import Optional

from nutrition_bot.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            gender TEXT,
            age INTEGER,
            height_cm REAL,
            weight_kg REAL,
            activity_level TEXT DEFAULT 'moderate',
            goal TEXT DEFAULT 'maintain',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            activity_name TEXT NOT NULL,
            intensity TEXT DEFAULT 'medium',
            calories_per_hour REAL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            meal_type TEXT NOT NULL,
            description TEXT,
            calories REAL DEFAULT 0,
            protein REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def save_user(
    user_id: int,
    username: str,
    gender: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity_level: str,
    goal: str,
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (user_id, username, gender, age, height_cm, weight_kg, activity_level, goal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            gender=excluded.gender,
            age=excluded.age,
            height_cm=excluded.height_cm,
            weight_kg=excluded.weight_kg,
            activity_level=excluded.activity_level,
            goal=excluded.goal,
            updated_at=CURRENT_TIMESTAMP
        """,
        (user_id, username, gender, age, height_cm, weight_kg, activity_level, goal),
    )
    conn.commit()
    conn.close()


def update_user_field(user_id: int, field: str, value: object) -> None:
    allowed_fields = {
        "gender", "age", "height_cm", "weight_kg",
        "activity_level", "goal", "username",
    }
    if field not in allowed_fields:
        raise ValueError(f"Field {field} is not allowed")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE users SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (value, user_id),
    )
    conn.commit()
    conn.close()


def get_schedule(user_id: int, day_of_week: Optional[int] = None) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    if day_of_week is not None:
        cursor.execute(
            "SELECT * FROM schedule WHERE user_id = ? AND day_of_week = ? ORDER BY time_start",
            (user_id, day_of_week),
        )
    else:
        cursor.execute(
            "SELECT * FROM schedule WHERE user_id = ? ORDER BY day_of_week, time_start",
            (user_id,),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_schedule_entry(
    user_id: int,
    day_of_week: int,
    time_start: str,
    time_end: str,
    activity_type: str,
    activity_name: str,
    intensity: str,
    calories_per_hour: float,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO schedule (user_id, day_of_week, time_start, time_end,
                              activity_type, activity_name, intensity, calories_per_hour)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, day_of_week, time_start, time_end,
         activity_type, activity_name, intensity, calories_per_hour),
    )
    conn.commit()
    entry_id = cursor.lastrowid
    conn.close()
    return entry_id


def delete_schedule_entry(entry_id: int, user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM schedule WHERE id = ? AND user_id = ?",
        (entry_id, user_id),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def clear_schedule(user_id: int, day_of_week: Optional[int] = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    if day_of_week is not None:
        cursor.execute(
            "DELETE FROM schedule WHERE user_id = ? AND day_of_week = ?",
            (user_id, day_of_week),
        )
    else:
        cursor.execute("DELETE FROM schedule WHERE user_id = ?", (user_id,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def add_meal(
    user_id: int,
    date: str,
    meal_type: str,
    description: str,
    calories: float,
    protein: float,
    fat: float,
    carbs: float,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO meal_log (user_id, date, meal_type, description, calories, protein, fat, carbs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, date, meal_type, description, calories, protein, fat, carbs),
    )
    conn.commit()
    meal_id = cursor.lastrowid
    conn.close()
    return meal_id


def get_meals_for_date(user_id: int, date: str) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM meal_log WHERE user_id = ? AND date = ? ORDER BY created_at",
        (user_id, date),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_meal(meal_id: int, user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM meal_log WHERE id = ? AND user_id = ?",
        (meal_id, user_id),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
