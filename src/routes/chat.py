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


def _memory_block(memories: list[dict]) -> str:
    """Render the long-term-memory section of the system prompt.

    This is the "recall" half of Mocca's memory: the ``remember`` tool saves
    facts; here we surface them so the model carries that knowledge into every
    conversation without it living in the chat history.

    It's included whenever memory is enabled - **even with nothing saved** - to
    ground the model against fabrication. Without it, a model asked something it
    has no memory for tends to invent an answer (e.g. "last time you said your
    name was [previous name]"). So we list the real facts as the *only* personal
    details it knows, and tell it to admit ignorance rather than guess; and when
    there are none, we say so explicitly.
    """
    if memories:
        facts = "\n".join(f"- {m['content']}" for m in memories)
        return (
            "Long-term memory - the things you actually know about the user from "
            "past conversations:\n"
            f"{facts}\n"
            "Use these facts naturally when relevant. They are the ONLY personal "
            "details you know about the user: if asked about something not listed "
            "here, say you don't know it yet rather than guessing or inventing it. "
            "Do not announce that you have a memory unless the user asks."
        )
    return (
        "Long-term memory: you have no saved facts about the user yet. Do not "
        "claim to remember past conversations, and do not invent personal details "
        "such as their name. If asked something personal you have not been told, "
        "say you do not know it yet and offer to remember it."
    )


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

    # Compose the system message: the user's persona prompt, plus (when memory is
    # on) the memory block - always, even when empty, so the model is grounded
    # and won't fabricate remembered details (see _memory_block). We fold both
    # into a single leading system message because many chat templates expect
    # just one.
    system_text = settings.system_prompt.strip()
    if settings.enable_memory:
        block = _memory_block(database.list_memories())
        system_text = f"{system_text}\n\n{block}" if system_text else block

    messages: list[dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
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
