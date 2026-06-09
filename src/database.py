"""SQLite-backed storage for chat sessions and their messages.

Why SQLite: it's in the Python standard library (no extra dependency),
single-file, and trivially portable across Windows, Linux, and macOS - which
matches Mocca's "simple, local, no setup" goal. The schema is five tables:

    folders(id, name, created_at, updated_at)
    sessions(id, title, model, folder_id, favorite, created_at, updated_at)
    messages(id, session_id, role, content, created_at)
    memories(id, content, created_at)
    documents(id, session_id, filename, content, source, created_at, updated_at)

``documents`` are the text files a chat works with: the user uploads them (or the
AI authors one), they show as tabs in the side panel, and the model reads them on
demand through the ``read_document`` tool. They are per-session (FK with cascade,
so deleting a chat drops its documents) and stored only in the database - never on
disk - so one chat can never read another's files.

``memories`` is Mocca's long-term, cross-chat memory: short, important facts
about the user (their name, preferences, ...) that the AI saves via the
``remember`` tool and that get injected into every conversation so the model
"knows" them. It is global (not tied to a session) and small by design.

``sessions.folder_id`` is NULL for chats at the root, or a folders.id when the
chat has been dragged into a folder. ``favorite`` (0/1) floats a chat to the
top of its container. All timestamps are ISO-8601 UTC strings. A thin
function-based API keeps callers from writing SQL directly.

``init_db()`` also performs a tiny in-place migration (ADD COLUMN) so existing
databases gain the folder_id/favorite columns without losing any data.
"""

from __future__ import annotations

import logging
import re
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


# Cap on a stored document filename, so the UI tab/label and download name stay
# sane and a pathological upload can't bloat a row's display.
_MAX_FILENAME = 200


def safe_filename(name: str) -> str:
    """Reduce an uploaded name to a safe, bare filename.

    Document content lives only in the database, so this never touches the
    filesystem - but the name is shown as a tab, used to match the model's
    edits back to a document, and used as a download name, so it must be clean.
    We strip any directory components (defeating ``../`` and both separators on
    either OS), drop control characters, bound the length, and fall back to a
    default when nothing usable remains. Mirrors ``models.safe_filename``'s
    "trust no user-supplied name" stance.
    """
    # Take the last path component regardless of which separator was used.
    base = re.split(r"[\\/]", name or "")[-1]
    # Drop control chars and leading/trailing dots+spaces (".." -> "", " x " -> "x").
    base = "".join(ch for ch in base if ch.isprintable()).strip(" .")
    base = base[:_MAX_FILENAME].strip()
    return base or "document.txt"


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

            CREATE TABLE IF NOT EXISTS memories (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'fact',
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                filename    TEXT NOT NULL,
                content     TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'upload',  -- 'upload' | 'assistant'
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_documents_session
                ON documents(session_id, created_at);
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

    # Memories gained a category (identity/preference/fact/...) after first ship.
    mem_cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
    if mem_cols and "category" not in mem_cols:
        conn.execute("ALTER TABLE memories ADD COLUMN category TEXT NOT NULL DEFAULT 'fact'")
        log.info("Migrated memories: added category column")


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


def get_messages_page(
    session_id: str, before_seq: int | None = None, limit: int = 15
) -> dict[str, Any]:
    """Return one page of *displayable* messages for the infinite scroller.

    The chat UI shows the most recent ``limit`` messages and loads older pages
    as the user scrolls up. A page is the ``limit`` newest messages whose
    ``seq`` is below ``before_seq`` (or the very newest when ``before_seq`` is
    None), returned oldest-first so they can be prepended/appended directly.

    ``seq`` is the row's monotonic rowid: it increases with insertion order
    (which matches chronological order, since messages are only ever appended),
    so it doubles as a stable scroll cursor. Tool rows are display-only and are
    excluded here, so a page is always ``limit`` *visible* bubbles.

    Returns ``{"messages": [...], "has_more": bool}`` where ``has_more`` says
    whether still-older pages exist.
    """
    limit = max(1, limit)
    params: list[Any] = [session_id]
    cursor = ""
    if before_seq is not None:
        cursor = "AND rowid < ? "
        params.append(before_seq)
    params.append(limit + 1)  # One extra row tells us if more pages remain.
    with _connect() as conn:
        rows = conn.execute(
            "SELECT rowid AS seq, role, content, created_at FROM messages "
            f"WHERE session_id = ? AND role != 'tool' {cursor}"
            "ORDER BY rowid DESC LIMIT ?",
            params,
        ).fetchall()
    has_more = len(rows) > limit
    page = [dict(r) for r in rows[:limit]]
    page.reverse()  # Oldest-first for the UI.
    return {"messages": page, "has_more": has_more}


# --------------------------------------------------------------------------- #
# Memories (long-term, cross-chat facts about the user)
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric word tokens (punctuation stripped).

    Splitting on whitespace alone left punctuation attached ("python." !=
    "python"), which broke near-duplicate detection; this normalises both.
    """
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: str, b: str) -> float:
    """Word-overlap similarity (0..1) of two strings.

    Cheap, dependency-free near-duplicate detection so rephrasings like "User
    likes Python" and "The user likes Python." don't both get stored.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Above this word-overlap, a new memory is treated as a duplicate of an existing
# one and skipped. 0.6 catches rephrasings without merging genuinely distinct
# facts (e.g. "likes Python" vs "uses Python at work" share few words).
_DEDUP_THRESHOLD = 0.6


def add_memory(content: str, category: str = "fact") -> dict[str, Any] | None:
    """Store one durable fact about the user, returning it (or None if blank).

    Deduplicated two ways so re-saving the same fact (across turns, or rephrased)
    doesn't pile up rows: an exact case-insensitive match, and a fuzzy
    word-overlap check (:func:`_jaccard` >= ``_DEDUP_THRESHOLD``). When a
    duplicate is found, the existing row is returned unchanged.
    """
    text = (content or "").strip()
    if not text:
        return None
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM memories").fetchall()
        for row in rows:
            if row["content"].lower() == text.lower() or _jaccard(row["content"], text) >= _DEDUP_THRESHOLD:
                return dict(row)
        mid = _new_id()
        ts = _now()
        conn.execute(
            "INSERT INTO memories (id, content, category, created_at) VALUES (?, ?, ?, ?)",
            (mid, text, category, ts),
        )
    log.debug("Stored memory %s (category=%s)", mid, category)
    return {"id": mid, "content": text, "category": category, "created_at": ts}


def list_memories() -> list[dict[str, Any]]:
    """Return all stored memories, oldest first (stable display order)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY created_at, rowid"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_memory(memory_id: str) -> bool:
    """Delete one memory. Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    log.debug("Deleted memory %s (existed=%s)", memory_id, cur.rowcount > 0)
    return cur.rowcount > 0


def clear_memories() -> int:
    """Delete every memory, returning how many were removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM memories")
    log.info("Cleared %d memories", cur.rowcount)
    return cur.rowcount


# --------------------------------------------------------------------------- #
# Documents (per-session text files the chat works with)
# --------------------------------------------------------------------------- #

def create_document(
    session_id: str, filename: str, content: str, source: str = "upload"
) -> dict[str, Any]:
    """Store a document for a session and return it (content included).

    ``source`` records where it came from: ``'upload'`` for a user upload,
    ``'assistant'`` for a file the model authored. The filename is sanitised
    here as a second line of defence (the route also does it). Creating a
    document bumps the session's updated_at so it floats up the sidebar.
    """
    did = _new_id()
    ts = _now()
    name = safe_filename(filename)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO documents (id, session_id, filename, content, source, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (did, session_id, name, content, source, ts, ts),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id)
        )
    log.debug("Created document %s (%s) in session %s", did, name, session_id)
    return {
        "id": did, "session_id": session_id, "filename": name, "content": content,
        "source": source, "created_at": ts, "updated_at": ts,
    }


def list_documents(session_id: str) -> list[dict[str, Any]]:
    """Return a session's documents (with content), oldest first (stable tabs)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    """Return one document by id, or None if it doesn't exist."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    return dict(row) if row else None


def get_document_by_filename(session_id: str, filename: str) -> dict[str, Any] | None:
    """Find a session document by filename (case-insensitive), newest wins.

    Used by the read tool and by edit write-back to map a name the model used
    back to a stored document. Newest-wins so a re-uploaded name resolves to the
    most recent copy.
    """
    name = safe_filename(filename)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE session_id = ? "
            "AND filename = ? COLLATE NOCASE ORDER BY created_at DESC, rowid DESC",
            (session_id, name),
        ).fetchone()
    return dict(row) if row else None


def update_document(document_id: str, content: str) -> bool:
    """Replace a document's content. Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE documents SET content = ?, updated_at = ? WHERE id = ?",
            (content, _now(), document_id),
        )
    return cur.rowcount > 0


def rename_document(document_id: str, filename: str) -> bool:
    """Rename a document (sanitised). Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE documents SET filename = ?, updated_at = ? WHERE id = ?",
            (safe_filename(filename), _now(), document_id),
        )
    return cur.rowcount > 0


def delete_document(document_id: str) -> bool:
    """Delete one document. Returns True if it existed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    log.debug("Deleted document %s (existed=%s)", document_id, cur.rowcount > 0)
    return cur.rowcount > 0
