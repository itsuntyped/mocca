"""Session document tools: let the AI read the files attached to this chat.

Documents are the text files a chat works with - the user uploads them (or the
AI authors one), they appear as tabs in the side panel, and they are stored in
the database per session (see ``database`` / ``routes/documents``). Their content
is deliberately NOT injected into every prompt; instead the system prompt lists
the filenames and the model calls ``read_document`` to read one on demand. That
keeps the context small even with several files, and scales to large files.

Scoping is by the current session: the tool reads the session id from
``tool_context`` (set by ``tool_loop.run`` for the turn), so one chat can only
read its own documents - never another chat's. Because content lives only in the
database, there is no shared on-disk folder to leak across sessions.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import database, tool_context
from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Match the read_file cap: don't pour an enormous document into the context window.
_MAX_CHARS = 20_000


def _session_or_error() -> str:
    """Return the current session id, or raise a clear ToolError if there is none."""
    sid = tool_context.current_session_id()
    if not sid:
        # Should not happen during a normal chat turn; surface it rather than
        # silently reading nothing.
        raise ToolError("No chat is in scope, so there are no documents to read.")
    return sid


def _stem(name: str) -> str:
    """Lowercase filename without its extension (for loose matching)."""
    return name.lower().rsplit(".", 1)[0]


def _resolve(sid: str, filename: str) -> dict | None:
    """Find an attached document by name, tolerating a loose reference.

    Tries an exact (case-insensitive) match first, then a stem match (so
    "README" or "readme.txt" still finds readme.md) when it is unambiguous. This
    is a safety net for when the model passes a slightly different name than the
    stored one; an ambiguous match falls through to None so we ask rather than
    guess.
    """
    exact = database.get_document_by_filename(sid, filename)
    if exact:
        return exact
    target = _stem(filename)
    if target:
        matches = [d for d in database.list_documents(sid) if _stem(d["filename"]) == target]
        if len(matches) == 1:
            return matches[0]
    return None


def _read_run(args: dict[str, Any]) -> str:
    """Read one document attached to the current chat, by filename."""
    sid = _session_or_error()
    filename = str(args.get("filename", "")).strip()
    if not filename:
        raise ToolError("Provide the 'filename' of the attached document to read.")
    doc = _resolve(sid, filename)
    if doc is None:
        names = ", ".join(d["filename"] for d in database.list_documents(sid)) or "none"
        raise ToolError(
            f"No attached document named '{filename}'. Attached documents are: {names}."
        )
    text = doc["content"]
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [truncated at {_MAX_CHARS} characters]"
    return text


_READ_TOOL = Tool(
    name="read_document",
    description=(
        "Read the full text of a document attached to this chat. Pass its "
        "filename (the system prompt lists what is attached). Read a document "
        "before answering questions about it or editing it - never guess its "
        "contents."
    ),
    category="documents",
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "The attached document's filename, e.g. 'notes.md'.",
            },
        },
        "required": ["filename"],
    },
    run=_read_run,
)

# A single tool: the model reads a document on demand. The system prompt's
# manifest already lists which documents are attached, so no separate "list" tool
# is needed (and avoiding it keeps the single tool round to one direct read).
TOOL = _READ_TOOL
