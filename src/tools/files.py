"""Read-only file tools: let the AI list and read documents the user shares.

To stay safe and local, these tools can ONLY see inside ``data/files/`` - a
dedicated drop folder for documents the user wants the AI to read. Every path is
resolved and checked to be inside that folder, so a crafted name like
``../mocca.db`` or an absolute path cannot escape it. The database, config,
logs, and model files are therefore never reachable through a tool.

There is no write tool by design (batch one is read-only); adding one later
should set ``confirm=True`` so the UI asks before changing anything on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..paths import FILES_DIR, ensure_dirs
from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Don't pour an enormous file into the model's context window; cap what we return.
_MAX_CHARS = 20_000


def _safe_path(rel: str) -> Path:
    """Resolve ``rel`` under the files folder, rejecting anything that escapes it."""
    root = FILES_DIR.resolve()
    target = (root / rel).resolve()
    # is_relative_to() (Python 3.9+) is the clean traversal check: a path built
    # with ".." or an absolute prefix won't be under root, and is rejected.
    if target != root and not target.is_relative_to(root):
        raise ToolError("Path is outside the readable files folder.")
    return target


def _list_run(args: dict[str, Any]) -> str:
    """List the files available under data/files/ (recursively)."""
    ensure_dirs()
    root = FILES_DIR.resolve()
    files = [p for p in root.rglob("*") if p.is_file()]
    if not files:
        return "No files available. Place documents in the data/files folder for me to read."
    # Show paths relative to the folder, sorted, so the model can pick one to read.
    rels = sorted(str(p.relative_to(root)).replace("\\", "/") for p in files)
    return "Available files:\n" + "\n".join(rels)


def _read_run(args: dict[str, Any]) -> str:
    """Read one text file from under data/files/."""
    ensure_dirs()
    rel = str(args.get("path", "")).strip()
    if not rel:
        raise ToolError("Provide a 'path' relative to the data/files folder.")
    path = _safe_path(rel)
    if not path.exists() or not path.is_file():
        raise ToolError(f"No such file: {rel} (looked under data/files/).")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"Could not read {rel}: {exc}") from exc
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [truncated at {_MAX_CHARS} characters]"
    return text


_LIST_TOOL = Tool(
    name="list_files",
    description=(
        "List the text documents the user has made available (in the data/files "
        "folder) so you can choose one to read."
    ),
    category="files",
    parameters={"type": "object", "properties": {}},
    run=_list_run,
)

_READ_TOOL = Tool(
    name="read_file",
    description=(
        "Read a text file the user has shared. Pass a path relative to the "
        "data/files folder (use list_files first to see what's available)."
    ),
    category="files",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to data/files, e.g. 'notes.txt'.",
            },
        },
        "required": ["path"],
    },
    run=_read_run,
)

# This module exposes two related tools, so it exports a TOOLS list.
TOOLS = [_LIST_TOOL, _READ_TOOL]
