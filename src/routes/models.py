"""Model routes: recommended catalog, installed list, download, and delete."""

from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import catalog, engine, hardware, models
from ..sse import sse

router = APIRouter()


class DownloadRequest(BaseModel):
    repo: str       # Hugging Face repo id, e.g. "bartowski/Llama-3.2-3B-Instruct-GGUF".
    filename: str   # GGUF filename within that repo.


@router.get("/api/catalog")
async def get_catalog(refresh: bool = False) -> dict[str, Any]:
    """Return the downloadable models (from HF), each with a hardware-fit rating.

    ``source`` is ``"unavailable"`` (with no models) when Hugging Face couldn't
    be reached, so the UI can show an offline message. ``refresh=true`` rebuilds
    the list instead of using the cached one (the UI's Refresh button).
    """
    system = hardware.detect_system()
    result = await catalog.get_catalog(refresh=refresh)
    items = [
        {**entry, "fit": hardware.fit_for_size(entry.get("size_gb"), system)}
        for entry in result["models"]
    ]
    return {"catalog": items, "source": result["source"]}


@router.get("/api/models")
async def get_models() -> dict[str, Any]:
    """List downloaded models, each annotated with a hardware-fit rating."""
    system = hardware.detect_system()
    items = [
        {**m, "fit": hardware.fit_for_size(m["size"] / (1024 ** 3), system)}
        for m in models.list_local_models()
    ]
    return {"models": items}


@router.delete("/api/models/{filename:path}")
async def remove_model(filename: str) -> dict[str, str]:
    """Delete a downloaded model. (``:path`` tolerates dotted filenames.)

    If the model is the one currently loaded in the engine, we unload it first so
    the file is no longer memory-mapped - otherwise Windows refuses to delete it.
    """
    try:
        name = models.safe_filename(filename)
    except models.ModelError:
        name = filename
    engine.unload(name)  # No-op unless this exact model is loaded.

    try:
        existed = models.delete_model(filename)
    except models.ModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not existed:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"status": "deleted", "name": filename}


@router.post("/api/models/download")
async def download_model(req: DownloadRequest) -> StreamingResponse:
    """Download a model from Hugging Face, streaming progress via SSE."""

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for progress in models.download_model(req.repo, req.filename):
                yield sse(progress)
        except models.ModelError as exc:
            yield sse({"error": str(exc), "done": True})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
