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

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    project_type TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT,
    budget_ghs REAL,
    package TEXT,
    payment_arrangement TEXT,
    status TEXT NOT NULL DEFAULT 'planning',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Dated progress notes ("milestone entries") for a project.
-- author_id records who wrote the note (owner now, team later).
CREATE TABLE IF NOT EXISTS project_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    author_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    stage TEXT,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Uploaded site photos. Files themselves live on disk under
-- uploads/ (never commit binary media or the temp webroot).
CREATE TABLE IF NOT EXISTS project_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    caption TEXT,
    created_at TEXT NOT NULL
);

-- Expense entries (the spending ledger) for a project, in Ghana cedis.
-- entered_by mirrors project_updates.author_id: the owner records
-- entries for now; the team interface will take over in a later phase.
CREATE TABLE IF NOT EXISTS project_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    entered_by INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    amount_ghs REAL NOT NULL,
    category TEXT NOT NULL,
    spent_on TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Private message thread between the project owner and the
-- HorizonView team. Owner posts now; team replies arrive later.
CREATE TABLE IF NOT EXISTS project_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    sender_id INTEGER NOT NULL REFERENCES users(id),
    sender_role TEXT NOT NULL DEFAULT 'owner',
    body TEXT NOT NULL,
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


def create_project(
    owner_id: int,
    name: str,
    project_type: str,
    location: str,
    description: str | None,
    budget_ghs: float | None,
    package: str | None,
    payment_arrangement: str | None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO projects (owner_id, name, project_type, location, "
            "description, budget_ghs, package, payment_arrangement, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (owner_id, name, project_type, location, description,
             budget_ghs, package, payment_arrangement, now),
        )
        return cursor.lastrowid


def get_projects_for_user(owner_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_project_by_id(project_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None


def set_project_progress(project_id: int, progress_percent: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE projects SET progress_percent = ? WHERE id = ?",
            (progress_percent, project_id),
        )


def create_project_update(
    project_id: int,
    author_id: int,
    title: str,
    body: str,
    stage: str | None,
    progress_percent: int,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO project_updates (project_id, author_id, title, body, "
            "stage, progress_percent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, author_id, title, body, stage, progress_percent, now),
        )
        return cursor.lastrowid


def get_updates_for_project(project_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM project_updates WHERE project_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def create_photo(
    project_id: int,
    uploader_id: int,
    original_filename: str,
    stored_path: str,
    extension: str,
    caption: str | None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO project_photos (project_id, uploader_id, "
            "original_filename, stored_path, extension, caption, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, uploader_id, original_filename, stored_path,
             extension, caption, now),
        )
        return cursor.lastrowid


def get_photos_for_project(project_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM project_photos WHERE project_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_photo_by_id(photo_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM project_photos WHERE id = ?", (photo_id,)
        ).fetchone()
        return dict(row) if row else None


def create_expense(
    project_id: int,
    entered_by: int,
    title: str,
    amount_ghs: float,
    category: str,
    spent_on: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO project_expenses (project_id, entered_by, title, "
            "amount_ghs, category, spent_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, entered_by, title, amount_ghs, category,
             spent_on, now),
        )
        return cursor.lastrowid


def get_expenses_for_project(project_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM project_expenses WHERE project_id = ? "
            "ORDER BY spent_on DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def create_message(
    project_id: int,
    sender_id: int,
    sender_role: str,
    body: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO project_messages (project_id, sender_id, "
            "sender_role, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (project_id, sender_id, sender_role, body, now),
        )
        return cursor.lastrowid


def get_messages_for_project(project_id: int) -> list[dict]:
    # Chat order: oldest first, so the thread reads like a conversation.
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM project_messages WHERE project_id = ? "
            "ORDER BY created_at ASC, id ASC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]
