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

from . import config, engine
from .tools import registry

log = logging.getLogger("mocca.toolloop")

# Appended before the final answer when any tool ran, so the model actually
# synthesises from what it gathered instead of ignoring it (a weak model
# otherwise sometimes calls more tools or claims it has no information).
_SYNTHESIZE = (
    "Now answer my original question directly using the information gathered "
    "above. Do not call any more tools."
)

# A short nudge so the model knows tools exist and when to reach for them. The
# native path only sees the tool *schemas*; the grammar path also gets a manifest
# describing the JSON protocol - this complements both by covering *when* to use
# a tool. Added only when tools are active, and never written to the user's
# saved system prompt.
_USE_TOOLS = (
    "You have tools available. Use a tool only when it directly helps answer the "
    "user's current request. If the user gives a web URL, use fetch_url to read "
    "that page; use web_search only to find information when you do not have a "
    "URL. Call at most one tool at a time, and as soon as you have the "
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
    exist but their category is disabled, it also names them so the model can
    point the user to Settings instead of refusing flatly or making something up
    (the misleading-refusal problem when, e.g., web access is turned off).
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
            "tell them to enable that category in Settings -> Tools."
        )
    return "\n\n".join(parts)


def _with_tool_awareness(
    messages: list[dict[str, Any]], awareness: str
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with the awareness text folded in.

    Merges into a leading system message when there is one (many chat templates
    expect a single system message), otherwise inserts one at the front.
    """
    msgs = [dict(m) for m in messages]
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = f"{(msgs[0].get('content') or '').rstrip()}\n\n{awareness}"
    else:
        msgs.insert(0, {"role": "system", "content": awareness})
    return msgs


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
) -> AsyncIterator[dict[str, Any]]:
    """Run one chat turn, yielding tool_call / tool_result / chunk events."""
    settings = config.get()
    # Offer only the tool categories relevant to the user's latest message. The
    # verbose tool schemas dominate decision latency on CPU, so narrowing them for
    # a clearly-scoped request (e.g. a bare calculation) is a big speed-up;
    # ambiguous requests fall back to all enabled categories (see registry).
    user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    categories = registry.relevant_categories(user_text, settings.enabled_tool_categories)
    schemas = registry.schemas(categories)

    # No tools to offer (none enabled, or engine missing) -> plain streamed turn.
    if not schemas or not engine.is_available():
        async for chunk in engine.chat(model_name, messages, options=options):
            yield {"chunk": chunk}
        return

    # Decision context: the full history plus the tool-awareness text. Never
    # persisted as-is; the route saves the user message and final answer.
    awareness = _build_awareness(settings.enabled_tool_categories)
    decision_convo = _with_tool_awareness(messages, awareness)
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
