"""Memory tool: let the AI save important, long-term facts about the user.

This is what makes Mocca *learn* about the person it's talking to. When the user
shares something durable and personal - their name, where they live, how they
like answers formatted, a standing preference - the model calls ``remember`` to
store a one-line fact. Stored facts live in the ``memories`` table (global, not
tied to one chat) and are injected into the system prompt of every future
conversation, so the AI carries that knowledge forward without it sitting in the
chat history.

Why a tool (and not, say, fine-tuning the weights): it's local, instant,
inspectable, and reversible - the user can see and delete anything Mocca learned
in Settings. The model decides *what* is worth remembering; this tool just
records it.

Memory is local-only (``is_local=True``), so it's never gated by the web-search
toggle. It has its own switch (``config.enable_memory``); when that's off, the
tool loop simply doesn't offer this tool (see ``tool_loop``).
"""

from __future__ import annotations

import logging
from typing import Any

from .. import database
from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Keep stored facts short and focused; a memory is a one-liner, not an essay.
_MAX_CHARS = 500


def _run(args: dict[str, Any]) -> str:
    """Save one durable fact about the user to long-term memory."""
    fact = str(args.get("fact", "")).strip()
    if not fact:
        raise ToolError("Provide a short 'fact' to remember about the user.")
    if len(fact) > _MAX_CHARS:
        fact = fact[:_MAX_CHARS]
    stored = database.add_memory(fact)
    if stored is None:
        raise ToolError("Nothing to remember (the fact was empty).")
    return f"Saved to long-term memory: {stored['content']}"


TOOL = Tool(
    name="remember",
    description=(
        "Save an important, lasting fact about the user to long-term memory so "
        "you can recall it in future conversations. Use this when the user "
        "shares something durable and worth remembering - their name, where they "
        "live, their job, standing preferences, or how they like you to respond. "
        "Do NOT use it for one-off questions, trivia, or passing details. Write "
        "the fact as a short, self-contained sentence in the third person, e.g. "
        "'The user's name is Sam.' or 'The user prefers concise answers.'"
    ),
    category="memory",
    parameters={
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": (
                    "A short, self-contained fact about the user, in the third "
                    "person. e.g. 'The user lives in Berlin.'"
                ),
            },
        },
        "required": ["fact"],
    },
    run=_run,
)
