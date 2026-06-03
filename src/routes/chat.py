"""Chat route: stream an assistant reply (possibly using tools) over SSE."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config, database, engine, tool_loop
from ..sse import sse

log = logging.getLogger("mocca.chat")
router = APIRouter()

# Message roles that form the conversation the engine sees. Tool rows are stored
# for display only and are rebuilt fresh each turn by the tool loop, so they're
# excluded here to avoid feeding stale/unpaired tool messages back to the model.
_ENGINE_ROLES = {"system", "user", "assistant"}


class ChatRequest(BaseModel):
    session_id: str
    model: str
    message: str


@router.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream an assistant reply for a user message.

    Flow:
      1. Persist the user's message.
      2. Build the prompt: optional system prompt + prior conversation.
      3. Run the tool loop, streaming tool activity and the final answer as SSE.
      4. Persist tool interactions (for display) and the complete answer.
    """
    session = database.get_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    settings = config.get()
    database.add_message(req.session_id, "user", req.message)
    database.set_session_model(req.session_id, req.model)

    # Assemble the conversation the engine sees (history minus display-only rows).
    history = [m for m in database.get_messages(req.session_id) if m["role"] in _ENGINE_ROLES]
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
        # Tool call arguments stashed by id so we can store them with the result.
        pending: dict[str, dict] = {}
        try:
            async for event in tool_loop.run(req.model, messages, options=options):
                if "chunk" in event:
                    collected.append(event["chunk"])
                    yield sse({"chunk": event["chunk"]})
                elif "tool_call" in event:
                    call = event["tool_call"]
                    pending[call["id"]] = call
                    yield sse(event)
                elif "tool_result" in event:
                    result = event["tool_result"]
                    call = pending.get(result["id"], {})
                    # Persist a display-only record of this tool interaction.
                    database.add_message(req.session_id, "tool", json.dumps({
                        "name": result["name"],
                        "arguments": call.get("arguments", {}),
                        "result": result["result"],
                    }))
                    yield sse(event)
        except engine.EngineError as exc:
            log.error("Chat failed: %s", exc)
            yield sse({"error": str(exc)})
        finally:
            # Always save whatever final answer we managed to generate.
            reply = "".join(collected)
            if reply:
                database.add_message(req.session_id, "assistant", reply)
        yield sse({"done": True})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
