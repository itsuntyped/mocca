"""Orchestrates a single chat turn that may use tools.

The loop, for one user turn:
  1. Ask the model to decide: call a tool, or answer (``engine.decide``).
  2. If it called tools, run them locally, feed the results back into the
     conversation, and ask again.
  3. Repeat up to a hard cap, then stream the final answer (``engine.chat``).

Tool calls and results are recorded in standard OpenAI message shapes on a
working copy of the conversation that lives only for this turn; the engine
adapts those shapes per model (native pass-through, or flattened for the grammar
path). We cap the number of tool rounds so a confused model can't spin forever.

If no tools are enabled, or the engine isn't installed, this collapses to a
plain streamed answer - byte-for-byte Mocca's pre-tools behaviour, which is what
keeps tool support a non-breaking addition.

This is an async generator of event dicts; the chat route turns each into an SSE
frame and handles persistence:
    {"tool_call":   {"id", "name", "arguments"}}   a tool is about to run
    {"tool_result": {"id", "name", "result"}}      its result
    {"chunk":       "<text>"}                       a piece of the final answer
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

from . import config, database, engine, tool_context, tool_router
from .tools import registry

log = logging.getLogger("mocca.toolloop")

# Appended before the final answer when any tool ran, so the model actually
# synthesises from what it gathered instead of ignoring it (a weak model
# otherwise sometimes calls more tools or claims it has no information). Phrased
# to cover both cases: a question to answer AND a statement to acknowledge - so
# that, e.g., saving a fact to memory ("my name is Martin") gets a warm reply
# rather than the model insisting no question was asked.
_SYNTHESIZE = (
    "Now respond to my message above, using any information gathered. If I asked "
    "something, answer it directly. If I shared something about myself, "
    "acknowledge it warmly and naturally. Do not call any more tools."
)

# A short nudge so the model knows tools exist and when to reach for them. The
# native path only sees the tool *schemas*; the grammar path also gets a manifest
# describing the JSON protocol - this complements both by covering *when* to use
# a tool. Added only when tools are active, and never written to the user's
# saved system prompt.
_USE_TOOLS = (
    "You have tools available. Use a tool only when it directly helps answer the "
    "user's current request. If the user gives a YouTube link, use "
    "youtube_transcript to read the video's transcript. For any other web URL, "
    "use fetch_url to read that page; use web_search only to find information "
    "when you do not have a URL. If the user refers to an attached document or "
    "asks you to change, edit, or add to a file, call read_document with that "
    "filename FIRST to get its current contents - never edit a file you have not "
    "read. Call at most one tool at a time, and as soon as you have the "
    "information you need, answer the user directly - do not call additional, "
    "repeated, or unrelated tools."
)

# Guards against the model fabricating tool use - claiming it searched or visited
# a site when no tool was actually called (a common small-model failure).
_NO_FABRICATION = (
    "Never claim to have used a tool, visited a website, or retrieved information "
    "unless a tool actually returned it in this conversation. If you cannot do "
    "something, say so plainly instead of inventing an answer."
)

def _build_awareness(enabled_categories: list[str]) -> str:
    """Compose the tool-awareness system text for this turn.

    Always covers when to use tools and the no-fabrication rule. If some tools
    exist but their category is disabled (only web search can be turned off), it
    also names them so the model can point the user to Settings instead of
    refusing flatly or making something up (the misleading-refusal problem when
    web access is turned off).
    """
    parts = [_USE_TOOLS, _NO_FABRICATION]

    enabled = set(enabled_categories)
    disabled = [t for t in registry.all_tools() if t.category not in enabled]
    if disabled:
        by_category: dict[str, list[str]] = {}
        for tool in disabled:
            by_category.setdefault(tool.category, []).append(tool.name)
        listed = "; ".join(
            f"{cat} ({', '.join(names)})" for cat, names in sorted(by_category.items())
        )
        parts.append(
            "Some capabilities exist but are currently turned off: " + listed + ". "
            "If the user asks for one of these, do not attempt it or invent a result - "
            "tell them to enable web search in Settings."
        )
    return "\n\n".join(parts)


def _decision_context(
    messages: list[dict[str, Any]], awareness: str
) -> list[dict[str, Any]]:
    """Build the system context for the tool-decision step.

    The decision is a tool-selection classification, so we lead with the tool
    instructions (``awareness``) and deliberately DROP the chat persona. The
    warm, "talk like a thoughtful person, not a search box" persona biases the
    model toward a conversational reply instead of making the tool call - e.g.
    greeting "Nice to meet you, Martin" instead of calling ``remember``. The
    persona/memory recall return for the streamed answer (see ``final_convo``).
    Prior turns are kept (minus their system message) so the model still has
    conversational context for its choice.
    """
    history = [dict(m) for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": awareness}, *history]


def _call_signature(call: engine.ToolCall) -> str:
    """A stable key for a tool call (name + normalised args) to detect repeats.

    String argument values have their whitespace collapsed so that, e.g.,
    ``8*7`` and ``8 * 7`` count as the same call - small models often re-issue a
    call with trivially different spacing, which would otherwise dodge the guard.
    """
    norm = {
        k: (re.sub(r"\s+", "", v) if isinstance(v, str) else v)
        for k, v in call.arguments.items()
    }
    return call.name + ":" + json.dumps(norm, sort_keys=True)


def _assistant_call_message(calls: list[engine.ToolCall]) -> dict[str, Any]:
    """Build the OpenAI-shaped assistant message recording the tool calls made."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in calls
        ],
    }


async def run(
    model_name: str,
    messages: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one chat turn, yielding tool_call / tool_result / chunk events.

    ``session_id`` is bound into ``tool_context`` for the whole turn so
    session-scoped tools (the document tools) read the right chat's files. The
    binding brackets the entire generator body, so it is live across every
    ``yield`` and the ``await registry.execute`` tool call, and is reset when the
    turn ends (or the generator is closed early).
    """
    token = tool_context.set_session(session_id)
    try:
        async for event in _run(model_name, messages, options=options):
            yield event
    finally:
        tool_context.reset_session(token)


async def _run(
    model_name: str,
    messages: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """The actual turn logic; ``run`` wraps it with the session context binding."""
    settings = config.get()
    # Offer only the tool categories relevant to the user's latest message. The
    # verbose tool schemas dominate decision latency on CPU, so narrowing them for
    # a clearly-scoped request (e.g. a bare calculation) is a big speed-up. The
    # router asks the model which categories the message needs (a cheap pass over
    # a compact menu, not the full schemas); it falls back to keyword routing when
    # the engine is unavailable or its reply can't be parsed (see tool_router).
    user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    # All local tools are always available; web search is the one toggle. (Memory
    # is no longer a tool - durable facts are captured by background extraction
    # in memory_extractor; see the chat route.)
    active = registry.active_categories(settings.enable_web_search)
    categories = await tool_router.choose_categories(model_name, user_text, active)
    # If this chat has attached documents, always keep the document tools in scope,
    # even if the router didn't pick them. Small models often read "edit
    # settings.json" as "write a new settings.json" and skip the documents
    # category, then generate from scratch and lose the user's content. Forcing
    # read_document into scope lets the decision step read the file first (the
    # _USE_TOOLS nudge tells it to). The cost is one small extra schema, and only
    # in chats that actually have documents.
    sid = tool_context.current_session_id()
    session_docs = database.list_documents(sid) if sid else []
    if session_docs and "documents" in active and "documents" not in categories:
        categories = sorted({*categories, "documents"})
    schemas = registry.schemas(categories)

    # No tools to offer (none enabled, or engine missing) -> plain streamed turn.
    if not schemas or not engine.is_available():
        async for chunk in engine.chat(model_name, messages, options=options):
            yield {"chunk": chunk}
        return

    # Decision context: tool instructions + history, persona dropped (see
    # _decision_context). Never persisted; the route saves the answer.
    awareness = _build_awareness(active)
    # The decision step does NOT see the system prompt (and so not the document
    # manifest), so name the attached documents here. Without this, the model
    # guesses a filename from the user's words ("the readme") and read_document
    # misses when the stored name differs (e.g. an earlier generated file saved as
    # document.md). Listing the exact names lets it pick the right one.
    if session_docs:
        names = ", ".join(d["filename"] for d in session_docs)
        awareness += (
            "\n\nThis chat has attached documents: " + names + ". To answer "
            "about one or to edit it, call read_document with its EXACT filename "
            "from this list first. Choose the one the user means (for example, a "
            "request about \"the readme\" or \"the doc you wrote\" refers to the "
            "matching document in the list)."
        )
    decision_convo = _decision_context(messages, awareness)
    # This turn's tool call/result messages, kept apart so we can build a focused
    # final-answer context from them (see below).
    tool_msgs: list[dict[str, Any]] = []

    # A single tool round: the model either calls tool(s) now or answers. We
    # deliberately do not loop - small local models wander into extra, irrelevant
    # calls when looped, and one round handles the common cases (a calculation, a
    # URL, a lookup) reliably. Multi-step needs can be driven by follow-up turns.
    decision = await engine.decide(model_name, decision_convo, schemas, options=options)
    if decision.tool_calls:
        # De-duplicate calls within the decision; a model sometimes repeats one.
        new_calls: list[engine.ToolCall] = []
        sigs: set[str] = set()
        for c in decision.tool_calls:
            sig = _call_signature(c)
            if sig not in sigs:
                new_calls.append(c)
                sigs.add(sig)

        tool_msgs.append(_assistant_call_message(new_calls))
        for call in new_calls:
            yield {"tool_call": {"id": call.id, "name": call.name, "arguments": call.arguments}}
            try:
                result = await registry.execute(call.name, call.arguments)
            except Exception as exc:  # noqa: BLE001 - any tool failure becomes feedback
                # Feed the error back so the model can explain it, not abort.
                result = f"Error: {exc}"
                log.warning("Tool %s failed: %s", call.name, exc)
            yield {"tool_result": {"id": call.id, "name": call.name, "result": result}}
            tool_msgs.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": result,
            })

    # Build the context for the streamed final answer.
    if tool_msgs:
        # Focused context: the persona, the user's *current* question, and only
        # this turn's tool results - then an instruction to answer. Dropping the
        # earlier history stops a small model from drifting back to a previous
        # topic (e.g. answering "8*7" with an earlier time-in-India reply) and
        # shrinks the prompt, which also speeds the answer up.
        final_convo: list[dict[str, Any]] = []
        if messages and messages[0].get("role") == "system":
            final_convo.append({"role": "system", "content": messages[0]["content"]})
        final_convo.append({"role": "user", "content": user_text})
        final_convo.extend(tool_msgs)
        final_convo.append({"role": "user", "content": _SYNTHESIZE})
    else:
        # No tools ran: answer normally with the full conversation in view.
        final_convo = messages

    async for chunk in engine.chat(model_name, final_convo, options=options):
        yield {"chunk": chunk}
