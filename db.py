"""
SQLite database helpers for HorizonView.

Why SQLite? It is a real relational database that lives in a single
file -- no server to install. When HorizonView grows, we can move to
PostgreSQL without changing this file's function names.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
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
    -- Administrator identity. 0/1 flag, DEFAULT OFF for everyone.
    -- IMPORTANT: no web route grants admin powers from this flag yet.
    -- It exists so a future (locally-bound or strongly protected)
    -- admin interface can have identity + access control to build on,
    -- decided in advance rather than improvised. Promotion happens
    -- only through the local CLI tool (admin_tool.py) by the person
    -- operating the server.
    is_admin INTEGER NOT NULL DEFAULT 0,
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

-- Uploaded project documents (contracts, drawings, receipts).
-- Files live on disk under uploads/, like photos.
CREATE TABLE IF NOT EXISTS project_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

-- Payment attempts (payments scaffold, TEST MODE only). A row is
-- created the moment the owner starts a checkout. Only SERVER-side
-- verification (the provider's own verify response, or a webhook
-- with a valid signature) can move status past 'attempted' — a
-- client's "I paid" is a claim, never a fact.
-- Statuses: attempted | paid | mismatch (amount differed; never
-- unlocks anything).
CREATE TABLE IF NOT EXISTS payment_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL DEFAULT 'paystack',
    provider_reference TEXT NOT NULL UNIQUE,
    amount_ghs REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'attempted',
    test_mode INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    verified_at TEXT
);
-- Reset requests from the "Forgot your password?" page. In this
-- stage there is no email service: the administrator sees the pending
-- requests here (create_reset_link.py) and verifies the person's
-- identity out of band before minting a link.
CREATE TABLE IF NOT EXISTS password_reset_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    requested_ip TEXT
);

-- Server-side session registry. The signed browser cookie stays, but
-- it now carries a session id (sid) that must match a live row here.
-- This is what makes revocation possible:
--   - logging out deletes the row  -> that cookie stops working
--   - a password reset deletes ALL rows for the user -> every device
--     (stolen laptop, ex-partner's phone, ...) is signed out at once.
-- Cookie maximum age is one week; rows live until revoked.
CREATE TABLE IF NOT EXISTS user_sessions (
    sid TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

-- Password reset requests. A single-use token is stored ONLY as a
-- SHA-256 hash: even a stolen database backup cannot be replayed as
-- a reset link. expires_at bounds the window in which the link works.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    requested_ip TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
    ensure_admin_column()


def ensure_admin_column() -> None:
    """Upgrade older databases: add users.is_admin if missing.

    CREATE TABLE IF NOT EXISTS never changes an existing table, so
    databases created before the admin flag need this one-time ALTER.
    """
    with get_connection() as conn:
        columns = [
            row["name"] for row in
            conn.execute("PRAGMA table_info(users)").fetchall()
        ]
        if "is_admin" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )


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


def set_admin_flag(user_id: int, is_admin: bool) -> None:
    """Grant or remove the administrator identity. Local CLI only:
    no HTTP endpoint ever flips this flag."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?",
            (1 if is_admin else 0, user_id),
        )


def list_users() -> list[dict]:
    """Account overview for the local admin tool, one row per user,
    with live counts so the operator sees real workload at a glance."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT u.id, u.full_name, u.email, u.phone, u.is_admin, "
            "u.created_at, "
            "(SELECT COUNT(*) FROM projects p WHERE p.owner_id = u.id) "
            "  AS project_count, "
            "(SELECT COUNT(*) FROM user_sessions s "
            "  WHERE s.user_id = u.id) AS active_sessions "
            "FROM users u ORDER BY u.created_at DESC, u.id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


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


def create_document(
    project_id: int,
    uploader_id: int,
    original_filename: str,
    stored_path: str,
    extension: str,
    description: str | None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO project_documents (project_id, uploader_id, "
            "original_filename, stored_path, extension, description, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, uploader_id, original_filename, stored_path,
             extension, description, now),
        )
        return cursor.lastrowid


def get_documents_for_project(project_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM project_documents WHERE project_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_document_by_id(document_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM project_documents WHERE id = ?", (document_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------- payments (scaffold, test mode) ----------

def create_payment_attempt(project_id: int, owner_id: int,
                           provider: str, provider_reference: str,
                           amount_ghs: float, test_mode: bool) -> dict:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO payment_attempts (project_id, owner_id, "
            "provider, provider_reference, amount_ghs, status, "
            "test_mode, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'attempted', ?, ?)",
            (project_id, owner_id, provider, provider_reference,
             amount_ghs, 1 if test_mode else 0, _now_iso()),
        )
    return get_payment_attempt_by_reference(provider_reference)


def get_payment_attempt_by_reference(reference: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM payment_attempts "
            "WHERE provider_reference = ?", (reference,),
        ).fetchone()
        return dict(row) if row else None


def get_payment_attempts_for_project(project_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_attempts WHERE project_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def set_payment_attempt_status(reference: str, status: str,
                               verified: bool) -> dict | None:
    """Only reached through server-side verification code paths."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE payment_attempts SET status = ?, verified_at = ? "
            "WHERE provider_reference = ?",
            (status, _now_iso() if verified else None, reference),
        )
    return get_payment_attempt_by_reference(reference)


# ---------- Correction helpers (owner can delete their own records) ----------

def get_expense_by_id(expense_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM project_expenses WHERE id = ?", (expense_id,)
        ).fetchone()
        return dict(row) if row else None


def get_update_by_id(update_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM project_updates WHERE id = ?", (update_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_expense(expense_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM project_expenses WHERE id = ?", (expense_id,)
        )


def delete_update(update_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM project_updates WHERE id = ?", (update_id,)
        )


def delete_photo(photo_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM project_photos WHERE id = ?", (photo_id,)
        )


def delete_document(document_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM project_documents WHERE id = ?", (document_id,)
        )


# ---------- Password reset (single-use hashed tokens) ----------

def record_reset_request(email: str, requested_ip: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO password_reset_requests "
            "(email, created_at, requested_ip) VALUES (?, ?, ?)",
            (email, now, requested_ip),
        )
        return cursor.lastrowid


def list_reset_requests(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM password_reset_requests "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def create_password_reset(
    user_id: int, token_hash: str, expires_at: str, requested_ip: str
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO password_reset_tokens "
            "(user_id, token_hash, expires_at, created_at, requested_ip) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, token_hash, expires_at, now, requested_ip),
        )
        return cursor.lastrowid


def get_password_reset_by_hash(token_hash: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None


def count_unused_resets_for_user(user_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM password_reset_tokens "
            "WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        ).fetchone()
        return row["n"]


def mark_password_reset_used(reset_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (now, reset_id),
        )


def set_user_password(user_id: int, password_hash: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


# ---------- Server-side session registry (revocation) ----------

def create_session(sid: str, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_sessions (sid, user_id, created_at) "
            "VALUES (?, ?, ?)",
            (sid, user_id, now),
        )


def session_is_active(sid: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_sessions WHERE sid = ?", (sid,)
        ).fetchone()
        return row is not None


def sessions_for_user(user_id: int) -> list[dict]:
    """Active session registry rows for one account (admin tool)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT sid, created_at FROM user_sessions "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def revoke_session(sid: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM user_sessions WHERE sid = ?", (sid,))


def revoke_all_sessions(user_id: int) -> None:
    """Sign EVERY device out of this account (used on password reset)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM user_sessions WHERE user_id = ?", (user_id,)
        )


def prune_expired_sessions(max_age_days: int = 8) -> int:
    """Delete registry rows whose browser cookie must have expired.

    Session cookies carry max_age = 7 days, so a row older than that
    can never again match a working cookie. A day of slack absorbs
    clock differences. Called on each login: one cheap DELETE,
    without needing a background scheduler in this stage.
    Returns the number of rows removed.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM user_sessions WHERE created_at <= ?", (cutoff,)
        )
        return cursor.rowcount
