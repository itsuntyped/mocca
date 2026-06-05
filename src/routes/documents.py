"""Document routes: per-session text files the chat works with.

A document is a text file attached to one chat - uploaded by the user or authored
by the AI. It is stored in the database (content and all) and surfaced to the
model on demand via the read_document tool, not injected into the prompt. These
endpoints are the CRUD the frontend uses to upload, list, edit, and remove them;
the side panel renders them as tabs.

Text only: we reject content that looks binary (a NUL byte is the cheap, reliable
tell) and cap the size, so the panel and the model only ever deal with text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import database

router = APIRouter()

# Reject anything larger than this (characters). Documents are text the user reads
# and the model reads on demand; multi-megabyte files don't belong here and would
# bloat the database and the tool result.
_MAX_CONTENT = 1_000_000


class CreateDocumentRequest(BaseModel):
    filename: str
    content: str = ""
    source: str = "upload"


class UpdateDocumentRequest(BaseModel):
    content: str | None = None
    filename: str | None = None


def _reject_non_text(content: str) -> None:
    """Raise 400/413 if the content isn't acceptable text."""
    if "\x00" in content:
        raise HTTPException(status_code=400, detail="File does not look like text.")
    if len(content) > _MAX_CONTENT:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large (limit {_MAX_CONTENT} characters).",
        )


def _public(doc: dict[str, Any], *, with_content: bool) -> dict[str, Any]:
    """Shape a document row for the API, optionally omitting the (large) content."""
    fields = ["id", "session_id", "filename", "source", "created_at", "updated_at"]
    out = {k: doc[k] for k in fields}
    if with_content:
        out["content"] = doc["content"]
    return out


@router.get("/api/sessions/{session_id}/documents")
async def list_documents(session_id: str) -> dict[str, Any]:
    """List a session's documents (metadata only - no content, to keep it light)."""
    docs = database.list_documents(session_id)
    return {"documents": [_public(d, with_content=False) for d in docs]}


@router.post("/api/sessions/{session_id}/documents")
async def create_document(session_id: str, req: CreateDocumentRequest) -> dict[str, Any]:
    """Attach a new document to a session."""
    if database.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _reject_non_text(req.content)
    source = req.source if req.source in ("upload", "assistant") else "upload"
    doc = database.create_document(session_id, req.filename, req.content, source=source)
    return _public(doc, with_content=True)


@router.get("/api/sessions/{session_id}/documents/{document_id}")
async def get_document(session_id: str, document_id: str) -> dict[str, Any]:
    """Fetch one document including its content (used when opening a tab)."""
    doc = database.get_document(document_id)
    if doc is None or doc["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return _public(doc, with_content=True)


@router.patch("/api/documents/{document_id}")
async def update_document(document_id: str, req: UpdateDocumentRequest) -> dict[str, str]:
    """Update a document's content and/or filename (hand edits in the panel)."""
    if database.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if req.content is not None:
        _reject_non_text(req.content)
        database.update_document(document_id, req.content)
    if req.filename is not None:
        database.rename_document(document_id, req.filename)
    return {"status": "ok"}


@router.delete("/api/documents/{document_id}")
async def delete_document(document_id: str) -> dict[str, str]:
    """Remove a document from its chat."""
    if not database.delete_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted"}
