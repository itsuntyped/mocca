"""Folder routes: create, list, rename, and delete sidebar folders."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import database

router = APIRouter()


class FolderRequest(BaseModel):
    name: str


@router.get("/api/folders")
async def list_folders() -> dict[str, Any]:
    return {"folders": database.list_folders()}


@router.post("/api/folders")
async def create_folder(req: FolderRequest) -> dict[str, Any]:
    return database.create_folder(req.name.strip() or "New folder")


@router.patch("/api/folders/{folder_id}")
async def rename_folder(folder_id: str, req: FolderRequest) -> dict[str, str]:
    if not database.rename_folder(folder_id, req.name.strip() or "New folder"):
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"status": "ok"}


@router.delete("/api/folders/{folder_id}")
async def delete_folder(folder_id: str) -> dict[str, str]:
    """Delete a folder; its chats move back to the root (never deleted)."""
    if not database.delete_folder(folder_id):
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"status": "deleted"}
