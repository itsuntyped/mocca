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
    "fences render as literal text. When you write out the full contents of a "
    "file (rather than a short snippet), put a clear filename on the opening "
    "fence, for example ```readme.md or ```app.py, so it can be saved and edited "
    "later."
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


# Cap how many filenames we list in the manifest, so a chat with a huge number of
# documents can't bloat every system prompt. The contents are never injected
# (the model reads them on demand via read_document), so this is just the list.
_MANIFEST_MAX = 50


def _documents_manifest(docs: list[dict]) -> str:
    """Render the "documents attached to this chat" section of the prompt.

    Crucially this lists only the filenames, never the contents: the model reads a
    document on demand with the read_document tool, so the prompt stays small even
    with several large files. It also carries the editing protocol - read first,
    then return the COMPLETE updated file labelled with its filename - so the
    side-panel editor updates with a faithful, whole-file edit.
    """
    shown = docs[:_MANIFEST_MAX]
    names = "\n".join(f"- {d['filename']}" for d in shown)
    extra = "" if len(docs) <= _MANIFEST_MAX else f"\n- ...and {len(docs) - _MANIFEST_MAX} more"
    return (
        "Documents attached to this chat (the user uploaded them, or you created "
        "them). You do NOT have their contents here - only their names:\n"
        f"{names}{extra}\n"
        "When the user's message is about one of these documents, call "
        "read_document with its exact filename to read it before answering - never "
        "guess or invent a document's contents. When the user asks you to change a "
        "document: read it first, then reply with the COMPLETE updated file in a "
        "single fenced code block whose info line is the filename (for example "
        "```notes.md) - not a diff and not only the changed lines - so it updates "
        "in their editor. You can edit several documents by returning one such "
        "block per file. For an ordinary question or small talk, just reply "
        "normally and do not output a file."
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


# File-block detection for document write-back. Kept in sync with the frontend's
# artifacts.js (FILE_LANGS / MIN_LINES / filename rule) so what the server
# persists as a document matches what the panel would show as a file. A block
# becomes a document when it is long enough AND either names a file in its info
# line or uses a file-ish language.
_FILE_LANGS = {
    "markdown", "md", "html", "htm", "xml", "css", "scss", "sass", "json",
    "jsonc", "yaml", "yml", "toml", "ini", "conf", "csv", "tsv", "sql", "js",
    "javascript", "jsx", "ts", "typescript", "tsx", "py", "python", "sh",
    "bash", "zsh", "c", "cpp", "h", "hpp", "java", "go", "rust", "rs", "rb",
    "ruby", "php", "dockerfile", "makefile", "txt", "text", "env",
}
_LANG_EXT = {
    "markdown": "md", "md": "md", "html": "html", "htm": "html", "xml": "xml",
    "css": "css", "json": "json", "jsonc": "json", "yaml": "yml", "yml": "yml",
    "toml": "toml", "ini": "ini", "conf": "conf", "csv": "csv", "tsv": "tsv",
    "sql": "sql", "js": "js", "javascript": "js", "ts": "ts", "typescript": "ts",
    "py": "py", "python": "py", "sh": "sh", "bash": "sh", "txt": "txt", "text": "txt",
}
_MIN_FILE_LINES = 6
_FILENAME_TOKEN = re.compile(r"^[\w.\-/]+\.[A-Za-z0-9]+$")


def _extract_file_blocks(text: str) -> list[tuple[str | None, str, str]]:
    """Pull file-worthy fenced code blocks from a reply as (filename, content, ext).

    ``filename`` is the token from the fence info line when present (the manifest
    asks the model to label an edited file's block with its name), else None - in
    which case a name is derived from ``ext`` (the language's extension) at
    write-back. Mirrors artifacts.js: a Markdown block closes at its LAST bare
    fence (it may contain its own fences); any other language closes at the first
    matching fence.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[tuple[str | None, str, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(`{3,})(.*)$", lines[i])
        if not m:
            i += 1
            continue
        fence_len = len(m.group(2))
        info = m.group(3).strip()
        parts = info.split()
        first = parts[0] if parts else ""
        lang = first.lower()
        # The info line names a file when its first token looks like one.
        filename = first if (parts and _FILENAME_TOKEN.match(first)) else None
        if lang in ("markdown", "md"):
            close = -1
            for k in range(i + 1, len(lines)):
                if re.match(r"^\s*`{3,}\s*$", lines[k]):
                    close = k
        else:
            close_re = re.compile(r"^\s*`{%d,}\s*$" % fence_len)
            close = i + 1
            while close < len(lines) and not close_re.match(lines[close]):
                close += 1
        end = close if close > i else len(lines)
        content = "\n".join(lines[i + 1:end])
        n_lines = len(content.split("\n")) if content else 0
        if (lang in _FILE_LANGS or filename) and n_lines >= _MIN_FILE_LINES:
            blocks.append((filename, content, _LANG_EXT.get(lang, "txt")))
        i = end + 1 if end < len(lines) else len(lines)
    return blocks


def _slug_from_content(content: str) -> str:
    """A short filename stem from a document's title, or "" if none is obvious.

    Looks for a Markdown H1 (``# Title``) first, else the first non-empty line if
    it is short and prose-like. Keeps unlabelled generated files referable (an
    "Acme" readme becomes acme.md, not document.md) so the user and the model can
    name it later.
    """
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        elif len(line) > 60 or any(c in line for c in "{}<>=;"):
            return ""  # First line looks like code/data, not a title.
        slug = re.sub(r"[^a-z0-9]+", "-", line.lower()).strip("-")
        return slug[:40].strip("-")
    return ""


def _derive_filename(session_id: str, ext: str, content: str = "") -> str:
    """A unique default name for an unlabelled generated file.

    Prefers a name derived from the content's title (see _slug_from_content),
    falling back to document.<ext>; de-duplicates against the session's existing
    document names.
    """
    existing = {d["filename"].lower() for d in database.list_documents(session_id)}
    stem = _slug_from_content(content) or "document"
    base = f"{stem}.{ext}"
    if base.lower() not in existing:
        return base
    n = 2
    while f"{stem}-{n}.{ext}".lower() in existing:
        n += 1
    return f"{stem}-{n}.{ext}"


def _write_back_documents(session_id: str, reply: str) -> bool:
    """Persist file blocks from a reply as session documents. Returns True if any.

    A labelled block is matched to an existing document by filename and updated in
    place (so the read tool and the panel tab see the edit), else creates a new
    ``source='assistant'`` document. An unlabelled block updates the session's
    single document when there is exactly one (an obvious edit target); when the
    session has no documents it creates a new one with a derived name (the
    "generate a file from scratch" flow); when several exist it is skipped rather
    than guessed which to overwrite.
    """
    blocks = _extract_file_blocks(reply)
    if not blocks:
        return False
    docs = database.list_documents(session_id)
    wrote = False
    for filename, content, ext in blocks:
        if not filename:
            if len(docs) == 1:
                database.update_document(docs[0]["id"], content)
                wrote = True
            elif not docs:
                database.create_document(
                    session_id, _derive_filename(session_id, ext, content), content,
                    source="assistant",
                )
                wrote = True
            # Several documents and no filename: skip rather than guess.
            continue
        existing = database.get_document_by_filename(session_id, filename)
        if existing:
            database.update_document(existing["id"], content)
        else:
            database.create_document(session_id, filename, content, source="assistant")
        wrote = True
    return wrote


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
    # Always include the Markdown-formatting guidance (see _FORMATTING_NOTE).
    system_text = f"{system_text}\n\n{_FORMATTING_NOTE}" if system_text else _FORMATTING_NOTE
    # List any documents attached to this chat (filenames only) so the model knows
    # what it can read with read_document and how to return an edit. Contents are
    # never injected - they are read on demand (see _documents_manifest).
    docs = database.list_documents(req.session_id)
    has_documents = bool(docs)
    if has_documents:
        log.info("documents in scope: %d (%s)", len(docs), ", ".join(d["filename"] for d in docs[:5]))
        system_text += "\n\n" + _documents_manifest(docs)

    messages: list[dict[str, str]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    if has_documents:
        # The freshly-read document is the source of truth, so strip stale
        # full-file copies out of the history we feed the engine - otherwise a
        # small model copies its own earlier message instead of the current file.
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
            async for event in tool_loop.run(
                req.model, messages, options=options, session_id=req.session_id
            ):
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
                # Persist any file the model returned as a session document: an
                # edit to an attached file updates it in place; a new file becomes
                # a new tab. Done here so the read tool and the panel see the
                # change on the next turn / refresh.
                edited_document = _write_back_documents(req.session_id, reply)
                # Capture durable facts in the background (after the reply, while
                # the model is idle) - never blocks the response. Gated on the
                # memory toggle and a quick "is this about the user?" check so
                # ordinary Q&A turns don't trigger an extra LLM call. We also skip
                # turns that edited a document: those are one-off task actions
                # ("add a section", "fix the intro"), not durable facts about the
                # user - capturing them just pollutes memory.
                if (settings.enable_memory and not edited_document
                        and memory_extractor.looks_personal(req.message)):
                    convo = database.get_messages(req.session_id)
                    # Two focused passes on the same gate: one captures new durable
                    # facts, the other forgets ones this turn contradicted or the
                    # user asked us to drop (e.g. "not into Elixir anymore").
                    for coro in (
                        memory_extractor.extract_and_store(req.model, convo),
                        memory_extractor.prune_stale_memories(req.model, convo),
                    ):
                        task = asyncio.create_task(coro)
                        _bg_tasks.add(task)
                        task.add_done_callback(_bg_tasks.discard)
        yield sse({"done": True})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
