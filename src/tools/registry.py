"""Discovers tools and serves them to the engine and the API.

Every module in this package that defines a module-level ``TOOL`` (a
:class:`~src.tools.base.Tool`) or a ``TOOLS`` list is registered automatically
at startup. The registry is the single place the rest of the app asks: what
tools exist, which categories are there, give me the schemas for the enabled
ones, run this call.

Why a registry instead of importing tools directly: it keeps "add a tool" down
to "drop a file", and it's where category filtering and (later) retrieval live,
so callers never need to know how the set is assembled.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import re
from typing import Any

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Lightweight relevance signals mapping a user message to a tool category. This
# is the "send fewer tools" lever: the verbose tool schemas dominate decision
# latency (on CPU, ~2.4s of prompt processing per tool), so for a clearly-scoped
# request we offer only the relevant category and skip the rest. It is
# deliberately conservative - when nothing matches we fall back to every enabled
# category, so a tool is never hidden when we are unsure; we only speed up the
# obvious cases. (This is the lightweight first cut of tool retrieval.)
_ARITHMETIC = re.compile(r"\d\s*[-+*/x^%]\s*\d")  # e.g. "8*7", "12 + 3"
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_CATEGORY_HINTS: dict[str, list[str]] = {
    "math": ["calculate", "calculator", "compute", "convert", "conversion",
             "plus", "minus", "times", "multiply", "divide", "sum", "percent",
             "percentage", "equation", "math", "arithmetic"],
    "time": ["time", "date", "today", "now", "day", "year", "month", "clock",
             "timezone", "tomorrow", "yesterday", "hour"],
    "files": ["file", "files", "read", "document", "documents", "folder",
              "notes", "txt", ".md"],
    "web": ["search", "google", "web", "online", "internet", "latest", "news",
            "look up", "website", "url", "link", "github", "repo", "repository",
            "repositories", "browse", "fetch", "wiki"],
}

# Modules in this package that are infrastructure, not tools.
_SKIP = {"base", "registry"}

# name -> Tool, filled by discover().
_TOOLS: dict[str, Tool] = {}


def _register(tool: Any, source: str) -> None:
    """Add one Tool to the registry, warning (not failing) on a bad entry."""
    if not isinstance(tool, Tool):
        log.warning("Module %s exported a non-Tool value; skipped", source)
        return
    if tool.name in _TOOLS:
        log.warning("Duplicate tool name '%s' from %s; overwriting", tool.name, source)
    _TOOLS[tool.name] = tool
    log.debug("Registered tool %s (category=%s, local=%s)",
              tool.name, tool.category, tool.is_local)


def discover() -> None:
    """Import every tool module in this package and register its tool(s).

    Called once at startup. Idempotent: clears and re-imports each time. A module
    may export either a single ``TOOL`` or a ``TOOLS`` list.
    """
    _TOOLS.clear()
    package = importlib.import_module(__package__)
    for info in pkgutil.iter_modules(package.__path__):
        if info.name in _SKIP:
            continue
        module = importlib.import_module(f"{__package__}.{info.name}")
        if hasattr(module, "TOOLS"):
            for tool in module.TOOLS:
                _register(tool, info.name)
        elif hasattr(module, "TOOL"):
            _register(module.TOOL, info.name)
        else:
            log.warning("Module %s has no TOOL/TOOLS; skipped", info.name)
    log.info("Discovered %d tool(s) across %d categories",
             len(_TOOLS), len(categories()))


def all_tools() -> list[Tool]:
    """Every registered tool."""
    return list(_TOOLS.values())


def categories() -> list[str]:
    """Sorted, de-duplicated list of tool categories."""
    return sorted({t.category for t in _TOOLS.values()})


def get(name: str) -> Tool | None:
    """Look up a tool by name, or None if it doesn't exist."""
    return _TOOLS.get(name)


def enabled_tools(enabled_categories: list[str]) -> list[Tool]:
    """Tools whose category the user has switched on."""
    cats = set(enabled_categories)
    return [t for t in _TOOLS.values() if t.category in cats]


def schemas(enabled_categories: list[str]) -> list[dict[str, Any]]:
    """OpenAI-format schemas for the enabled tools (what the engine advertises)."""
    return [t.schema() for t in enabled_tools(enabled_categories)]


def relevant_categories(text: str, enabled_categories: list[str]) -> list[str]:
    """Pick the enabled categories the message actually signals a need for.

    Returns only the categories whose signals match (keywords, or structural cues
    like a bare arithmetic expression or a URL). When nothing matches it returns
    an empty list, meaning "offer no tools this turn" - so ordinary conversation
    (greetings, opinions, chit-chat) stays fast and a tool-happy small model isn't
    tempted to call tools it doesn't need. Tools still fire whenever the request
    clearly calls for one (a calculation, a URL, a search, a date, a file).
    """
    enabled = set(c for c in categories() if c in set(enabled_categories))
    if not enabled:
        return []

    low = text.lower()
    selected: set[str] = set()
    for cat in enabled:
        if any(hint in low for hint in _CATEGORY_HINTS.get(cat, [])):
            selected.add(cat)
    # Structural signals catch what keywords miss (a bare expression, a URL).
    if "math" in enabled and _ARITHMETIC.search(text):
        selected.add("math")
    if "web" in enabled and _URL.search(text):
        selected.add("web")

    return sorted(selected)


async def execute(name: str, args: dict[str, Any]) -> str:
    """Run a tool by name with parsed args, returning its text result.

    Raises :class:`ToolError` for an unknown tool, and lets a tool's own
    ToolError propagate so the loop can feed a useful message back to the model.
    """
    tool = _TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool: {name}")
    log.debug("Executing tool %s args=%s", name, args)
    result = tool.run(args)
    if inspect.isawaitable(result):
        result = await result
    return str(result)
