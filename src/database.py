"""SQLite-backed storage for chat sessions and their messages.

Why SQLite: it's in the Python standard library (no extra dependency),
single-file, and trivially portable between Windows and Arch - which matches
Mocca's "simple, local, no setup" goal. The schema is three tables:

    folders(id, name, created_at, updated_at)
    sessions(id, title, model, folder_id, favorite, created_at, updated_at)
    messages(id, session_id, role, content, created_at)

``sessions.folder_id`` is NULL for chats at the root, or a folders.id when the
chat has been dragged into a folder. ``favorite`` (0/1) floats a chat to the
top of its container. All timestamps are ISO-8601 UTC strings. A thin
function-based API keeps callers from writing SQL directly.

``init_db()`` also performs a tiny in-place migration (ADD COLUMN) so existing
databases gain the folder_id/favorite columns without losing any data.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .paths import DB_FILE, ensure_dirs

log = logging.getLogger("mocca.db")


def _now() -> str:
    """Current time as an ISO-8601 UTC string (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    """Short, URL-safe unique id for a row."""
    return uuid.uuid4().hex


def _connect() -> sqlite3.Connection:
    """Open a connection with sane defaults.

    ``check_same_thread=False`` because uvicorn handles requests across a
    thread pool; we open a fresh connection per call so this is safe. Row
    factory gives us dict-like access to columns.
    """
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enforce the foreign-key cascade so deleting a session drops its messages.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist, then migrate. Called once at startup."""
    ensure_dirs()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                model       TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);
            """
        )
        _migrate(conn)
    log.info("Database ready at %s", DB_FILE)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release, preserving all data.

    SQLite's ``ALTER TABLE ADD COLUMN`` is cheap and non-destructive; we only
    add a column when it's missing, so this is safe to run on every startup.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "folder_id" not in cols:
        # NULL folder_id == the chat lives at the sidebar root.
        conn.execute("ALTER TABLE sessions ADD COLUMN folder_id TEXT")
        log.info("Migrated sessions: added folder_id column")
    if "favorite" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
        log.info("Migrated sessions: added favorite column")


# --------------------------------------------------------------------------- #
# Folders
# --------------------------------------------------------------------------- #

def create_folder(name: str) -> dict[str, Any]:
    """Insert a new (root-level) folder and return it."""
    fid = _new_id()
    ts = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO folders (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (fid, name, ts, ts),
        )
    log.debug("Created folder %s (%s)", fid, name)
    return {"id": fid, "name": name, "created_at": ts, "updated_at": ts}


def list_folders() -> list[dict[str, Any]]:
    """Return all folders, alphabetically by name (case-insensitive)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM folders ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def rename_folder(folder_id: str, name: str) -> bool:
    """Rename a folder. Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE folders SET name = ?, updated_at = ? WHERE id = ?",
            (name, _now(), folder_id),
        )
    return cur.rowcount > 0


def delete_folder(folder_id: str) -> bool:
    """Delete a folder, moving any chats it held back to the root.

    We never delete the chats themselves - only the folder. Returns True if
    the folder existed.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
        )
        cur = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    log.debug("Deleted folder %s (existed=%s)", folder_id, cur.rowcount > 0)
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

def create_session(title: str = "New chat", model: str = "") -> dict[str, Any]:
    """Insert a new session and return it as a dict."""
    sid = _new_id()
    ts = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, title, model, ts, ts),
        )
    log.debug("Created session %s (model=%s)", sid, model)
    return {"id": sid, "title": title, "model": model, "created_at": ts, "updated_at": ts}


def list_sessions() -> list[dict[str, Any]]:
    """Return all sessions, most recently updated first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> dict[str, Any] | None:
    """Return a single session with its messages, or None if not found."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        msgs = conn.execute(
            # rowid breaks ties when several messages share a (seconds-precision)
            # timestamp, e.g. tool rows and the answer saved within one turn.
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        ).fetchall()
    session = dict(row)
    session["messages"] = [dict(m) for m in msgs]
    return session


def rename_session(session_id: str, title: str) -> bool:
    """Update a session's title. Returns True if a row was changed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
    return cur.rowcount > 0


def set_session_model(session_id: str, model: str) -> None:
    """Remember which model a session is using."""
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET model = ?, updated_at = ? WHERE id = ?",
            (model, _now(), session_id),
        )


def move_session(session_id: str, folder_id: str | None) -> bool:
    """Move a chat into a folder, or to the root when ``folder_id`` is None.

    Note: we intentionally do NOT touch updated_at here - moving a chat
    shouldn't bump it above more recently used chats.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET folder_id = ? WHERE id = ?", (folder_id, session_id)
        )
    return cur.rowcount > 0


def set_favorite(session_id: str, favorite: bool) -> bool:
    """Flag/unflag a chat as a favorite. Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET favorite = ? WHERE id = ?",
            (1 if favorite else 0, session_id),
        )
    return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    """Delete a session (its messages cascade). Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    log.debug("Deleted session %s (existed=%s)", session_id, cur.rowcount > 0)
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #

def add_message(session_id: str, role: str, content: str) -> dict[str, Any]:
    """Append a message to a session and bump the session's updated_at."""
    mid = _new_id()
    ts = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, session_id, role, content, ts),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id)
        )
    return {"id": mid, "session_id": session_id, "role": role, "content": content, "created_at": ts}


def get_messages(session_id: str) -> list[dict[str, Any]]:
    """Return all messages for a session in chronological order."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? "
            "ORDER BY created_at, rowid",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]
