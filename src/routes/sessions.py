"""Session routes: CRUD plus moving a chat between folders and favoriting it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, database

router = APIRouter()


class CreateSessionRequest(BaseModel):
    title: str = "New chat"
    model: str = ""


class RenameSessionRequest(BaseModel):
    title: str


class MoveSessionRequest(BaseModel):
    folder_id: str | None = None  # None = move to root.


class FavoriteSessionRequest(BaseModel):
    favorite: bool


@router.get("/api/sessions")
async def list_sessions() -> dict[str, Any]:
    return {"sessions": database.list_sessions()}


@router.post("/api/sessions")
async def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    return database.create_session(title=req.title, model=req.model)


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    session = database.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/api/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str, before: int | None = None
) -> dict[str, Any]:
    """Return one page of displayable messages for the infinite scroller.

    Without ``before`` this is the most recent page; pass the ``seq`` of the
    oldest message currently shown to fetch the page just before it. The page
    size is the user's ``messages_per_page`` setting.
    """
    if database.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    limit = config.get().messages_per_page
    return database.get_messages_page(session_id, before_seq=before, limit=limit)


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameSessionRequest) -> dict[str, str]:
    if not database.rename_session(session_id, req.title):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok", "title": req.title}


@router.put("/api/sessions/{session_id}/folder")
async def move_session(session_id: str, req: MoveSessionRequest) -> dict[str, str]:
    """Move a chat into a folder (or to the root when folder_id is null)."""
    if not database.move_session(session_id, req.folder_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.put("/api/sessions/{session_id}/favorite")
async def favorite_session(session_id: str, req: FavoriteSessionRequest) -> dict[str, str]:
    """Mark or unmark a chat as a favorite."""
    if not database.set_favorite(session_id, req.favorite):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, str]:
    if not database.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}
