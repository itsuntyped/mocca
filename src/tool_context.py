"""Per-turn context for tools that need to know which chat they run in.

Most tools are pure (calculator, unit convert) and need nothing but their
arguments. A few - notably the document tools - need to know the *current
session* so they read the right chat's files. Tools are registered globally and
``Tool.run`` only receives its arguments, so rather than thread a session id
through every tool's signature we expose it here as a ``contextvars.ContextVar``.

``tool_loop.run`` sets the session for the duration of a turn (and resets it
afterwards); a session-scoped tool reads it via :func:`current_session_id`. A
``ContextVar`` is the right tool: it is bound to the running task/context, so the
value a tool sees is exactly the one set for its turn even with many chats in
flight, and ``asyncio.to_thread`` copies the context so a tool would still see it
if it ever ran on a worker thread.
"""

from __future__ import annotations

import contextvars

# Default None means "no session in scope" - tools must handle that (e.g. a
# document tool called outside a chat turn returns a clear message instead of
# reading some arbitrary session).
_current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mocca_current_session_id", default=None
)


def set_session(session_id: str | None) -> contextvars.Token:
    """Bind the current session id; returns a token to pass to :func:`reset_session`."""
    return _current_session_id.set(session_id)


def reset_session(token: contextvars.Token) -> None:
    """Restore the session id to what it was before the matching :func:`set_session`."""
    _current_session_id.reset(token)


def current_session_id() -> str | None:
    """The session id bound for the current turn, or None if none is in scope."""
    return _current_session_id.get()
