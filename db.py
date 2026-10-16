"""
SQLite database helpers for HorizonView.

Why SQLite? It is a real relational database that lives in a single
file -- no server to install. When HorizonView grows, we can move to
PostgreSQL without changing this file's function names.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# The database file is created next to this file. It is listed in
# .gitignore and must never be committed (it contains user records).
DB_PATH = Path(__file__).resolve().parent / "horizonview.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def create_user(full_name: str, email: str, phone: str, password_hash: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (full_name, email, phone, password_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (full_name, email, phone, password_hash, now),
        )
        return cursor.lastrowid


def get_user_by_email(email: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
