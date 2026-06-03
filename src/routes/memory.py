"""Memory routes: view and manage what the AI has learned about the user.

Mocca's long-term memory is meant to be transparent and under the user's
control: these endpoints let the Settings UI list every stored fact and delete
any of them (or clear all). The facts themselves are *created* by the AI through
the ``remember`` tool during chat (see ``src/tools/memory.py``), not here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import database

router = APIRouter()


@router.get("/api/memories")
async def list_memories() -> dict[str, Any]:
    """Return every stored memory (oldest first)."""
    return {"memories": database.list_memories()}


@router.delete("/api/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: str) -> None:
    """Delete a single memory by id."""
    if not database.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")


@router.delete("/api/memories")
async def clear_memories() -> dict[str, Any]:
    """Delete all memories, returning how many were removed."""
    return {"removed": database.clear_memories()}
