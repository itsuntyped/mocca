"""Chat route: stream an assistant reply (possibly using tools) over SSE."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config, database, engine, memory_extractor, tool_loop
from ..sse import sse

log = logging.getLogger("mocca.chat")
router = APIRouter()

# Holds references to in-flight background memory-extraction tasks so the event
# loop doesn't garbage-collect them mid-run; each removes itself when done.
_bg_tasks: set[asyncio.Task] = set()

# Message roles that form the conversation the engine sees. Tool rows are stored
# for display only and are rebuilt fresh each turn by the tool loop, so they're
# excluded here to avoid feeding stale/unpaired tool messages back to the model.
_ENGINE_ROLES = {"system", "user", "assistant"}

# A short formatting instruction folded into every system prompt. The UI renders
# the reply as Markdown, and the one case Markdown genuinely cannot express is a
# fenced code block that itself contains fenced code: a bare ``` opening a nested
# block is indistinguishable from a ``` closing the outer one. The fix is in the
# spec - use a *longer* outer fence - so we ask the model to do exactly that.
# Appended here (not baked into the editable persona) so it applies even to
# users who have customised their system prompt.
_FORMATTING_NOTE = (
    "Format replies in Markdown. When you show a fenced code block that itself "
    "contains a fenced code block (for example, a README that includes code), "
    "wrap the OUTER block in four backticks (````) so the inner triple-backtick "
    "fences render as literal text."
)


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


def _open_file_block(title: str, content: str) -> str:
    """Render the "file currently open in the editor" section of the prompt.

    This is what gives the side-panel editor continuity with the chat: when the
    user has an artifact open and asks for a change, we hand the model the file's
    *current* contents (including any edits they typed by hand) and tell it to
    return the whole updated file, so the next reply builds on what they're
    actually looking at rather than on whatever it generated last.
    """
    named = f" named {title}" if title.strip() else ""
    return (
        f"The user currently has a file open in the editor{named}. It may include "
        "edits they made by hand, so the version between the markers below is the "
        "ONLY current, authoritative version. Any earlier copy of this file that "
        "appears earlier in the conversation is outdated - ignore it.\n"
        "When the user asks you to change, add to, or fix this file: start from "
        "the version below and keep every part they did not ask you to change "
        "exactly as it is, word for word. Then reply with the COMPLETE updated "
        "file in a single fenced code block (not a diff, not only the changed "
        "lines), so it opens in their editor.\n"
        "But if the user's latest message is NOT a request to change the file - "
        "for example small talk, a thank-you, or a general question - just reply "
        "normally in a sentence or two and do NOT output the file again. Only "
        "produce the file when they actually ask for a change to it.\n"
        "--- BEGIN CURRENT FILE ---\n"
        f"{content}\n"
        "--- END CURRENT FILE ---"
    )


def _collapse_code_blocks(text: str, min_lines: int = 4) -> str:
    """Replace large fenced code blocks with a short placeholder.

    Used only when the user has a file open. The file's current contents are
    supplied separately (see _open_file_block), so the full copy that also lives
    in earlier assistant turns is a stale duplicate. Left in, a small model tends
    to copy its own previous message verbatim and lose the user's hand edits, so
    we strip those big blocks from the history the engine sees. The display
    history stored in the database is left untouched.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(`{3,}|~{3,})(.*)$", lines[i])
        if m:
            marker = m.group(2)[0]
            close = re.compile(r"^\s*" + re.escape(marker) + "{%d,}\\s*$" % len(m.group(2)))
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            # lines[j] is the closing fence (or we ran off the end, unterminated).
            if (j - i - 1) >= min_lines:
                out.append("[earlier version of a file omitted; the current version is provided separately]")
            else:
                out.extend(lines[i:j + 1])
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


class OpenFile(BaseModel):
    """The artifact the user has open in the side panel, sent with a message."""

    title: str = ""
    content: str = ""


class ChatRequest(BaseModel):
    session_id: str
    model: str
    message: str
    # Present only when the user has a file open in the editor; per-turn context,
    # never persisted to history.
    open_file: OpenFile | None = None


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
    # Always include the Markdown-formatting guidance (see _FORMATTING_NOTE).
    system_text = f"{system_text}\n\n{_FORMATTING_NOTE}" if system_text else _FORMATTING_NOTE
    # If the user has a file open in the editor, fold its current contents in so
    # follow-up edits build on the real, possibly hand-edited state (see
    # _open_file_block). This is per-turn context only - never stored in history.
    editing_file = bool(req.open_file and req.open_file.content.strip())
    if editing_file:
        log.info("open_file context: title=%r (%d chars)", req.open_file.title, len(req.open_file.content))
        system_text += "\n\n" + _open_file_block(req.open_file.title, req.open_file.content)

    messages: list[dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    if editing_file:
        # The open file is the single source of truth this turn, so strip the
        # stale full-file copies out of the history we feed the engine - otherwise
        # the model copies its own earlier message and drops the user's edits.
        for m in history:
            if m["role"] == "assistant":
                messages.append({**m, "content": _collapse_code_blocks(m["content"])})
            else:
                messages.append(m)
    else:
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
                # Capture durable facts in the background (after the reply, while
                # the model is idle) - never blocks the response. Gated on the
                # memory toggle and a quick "is this about the user?" check so
                # ordinary Q&A turns don't trigger an extra LLM call.
                if settings.enable_memory and memory_extractor.looks_personal(req.message):
                    convo = database.get_messages(req.session_id)
                    task = asyncio.create_task(
                        memory_extractor.extract_and_store(req.model, convo)
                    )
                    _bg_tasks.add(task)
                    task.add_done_callback(_bg_tasks.discard)
        yield sse({"done": True})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
