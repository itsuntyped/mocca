"""Chat route: stream an assistant reply over Server-Sent Events."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config, database, engine
from ..sse import sse

log = logging.getLogger("mocca.chat")
router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    model: str
    message: str


@router.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream an assistant reply for a user message.

    Flow:
      1. Persist the user's message.
      2. Build the prompt: optional system prompt + full session history.
      3. Stream tokens from the engine back to the browser as SSE.
      4. Persist the complete assistant reply once streaming finishes.
    """
    session = database.get_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    settings = config.get()
    database.add_message(req.session_id, "user", req.message)
    database.set_session_model(req.session_id, req.model)

    # Assemble the message list the engine expects.
    history = database.get_messages(req.session_id)
    messages: list[dict[str, str]] = []
    if settings.system_prompt.strip():
        messages.append({"role": "system", "content": settings.system_prompt})
    messages.extend(history)

    options = {
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "max_tokens": settings.max_tokens,
    }

    async def event_stream() -> AsyncIterator[str]:
        collected: list[str] = []
        try:
            async for chunk in engine.chat(req.model, messages, options=options):
                collected.append(chunk)
                yield sse({"chunk": chunk})
        except engine.EngineError as exc:
            log.error("Chat failed: %s", exc)
            yield sse({"error": str(exc)})
        finally:
            # Always save whatever we managed to generate.
            reply = "".join(collected)
            if reply:
                database.add_message(req.session_id, "assistant", reply)
        yield sse({"done": True})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
