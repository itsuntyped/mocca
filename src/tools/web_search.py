"""Network tool: search the web and return the top results.

Reaches the internet, so it is ``is_local=False`` and in the "web" category,
which is OFF by default (Mocca is local-only unless the user opts in).

Backend: DuckDuckGo's HTML endpoint, scraped with a small regex. This keeps
Mocca dependency-light (no search-API client, no API key) at the cost of being
best-effort - if DuckDuckGo changes its markup, parsing may return fewer
results, which we degrade to gracefully rather than failing the chat. ``httpx``
is already a project dependency.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import unquote

import httpx

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

_ENDPOINT = "https://html.duckduckgo.com/html/"
_TIMEOUT = 15.0
# How many results to hand back to the model by default.
_DEFAULT_MAX = 5
_HARD_MAX = 10

# DuckDuckGo HTML results: an <a class="result__a" href="...">title</a> for the
# link/title, and a <a class="result__snippet">...</a> for the snippet.
_RESULT_LINK = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET = re.compile(
    r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Strip tags and decode HTML entities from a snippet of result markup."""
    return html.unescape(_TAGS.sub("", text)).strip()


def _real_url(href: str) -> str:
    """Unwrap DuckDuckGo's redirect links (…/l/?uddg=<encoded real url>)."""
    match = re.search(r"uddg=([^&]+)", href)
    return unquote(match.group(1)) if match else href


async def _run(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("Provide a 'query' to search for.")
    max_results = min(int(args.get("max_results", _DEFAULT_MAX) or _DEFAULT_MAX), _HARD_MAX)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(
                _ENDPOINT,
                data={"q": query},
                headers={"User-Agent": "Mocca/0.1 (local AI)"},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(f"Search failed: {exc}") from exc

    links = list(_RESULT_LINK.finditer(resp.text))
    snippets = [_clean(m.group("snippet")) for m in _SNIPPET.finditer(resp.text)]
    if not links:
        return f"No results found for '{query}'."

    lines: list[str] = []
    for i, link in enumerate(links[:max_results]):
        title = _clean(link.group("title"))
        url = _real_url(link.group("href"))
        snippet = snippets[i] if i < len(snippets) else ""
        lines.append(f"{i + 1}. {title}\n   {url}\n   {snippet}".rstrip())
    return "\n".join(lines)


TOOL = Tool(
    name="web_search",
    description=(
        "Search the web and return the top results (title, URL, snippet). Use to "
        "find current information; follow up with fetch_url to read a result."
    ),
    category="web",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {
                "type": "integer",
                "description": f"How many results to return (1-{_HARD_MAX}). Default {_DEFAULT_MAX}.",
            },
        },
        "required": ["query"],
    },
    is_local=False,
    run=_run,
)
