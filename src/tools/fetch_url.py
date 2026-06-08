"""Network tool: fetch a web page and return its readable text.

This tool reaches the internet, so it is marked ``is_local=False`` and lives in
the "web" category, which is OFF by default - Mocca is local-only unless the user
opts in (see CLAUDE.md goal #2).

We deliberately avoid adding an HTML-parsing dependency: a small regex pass
strips tags and scripts to give the model plain-ish text. It's good enough for
reading an article and keeps Mocca dependency-light. ``httpx`` is already a
project dependency (used for model downloads).
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

import httpx

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Cap the returned text so a huge page can't blow up the model's context. Local
# models often run with a small context (e.g. 4096), and the result is fed back
# in alongside tool schemas, so keep this conservative.
_MAX_CHARS = 6_000
# A short timeout: a tool call should not hang a chat turn for long.
_TIMEOUT = 15.0

# Whole non-content blocks to drop entirely (with their inner text). Removing
# nav/header/footer/svg chrome is what makes the actual page content dominate -
# on a real GitHub page this cuts the readable text from ~5600 to ~2300 chars,
# so the part the user cares about fits a small context instead of being buried.
_BLOCKS = re.compile(
    r"<(script|style|svg|head|nav|header|footer|form|button|noscript)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\n\s*\n\s*\n+")

# Head metadata: the page <title> and social meta tags. JS-rendered pages (a
# Credly badge, a LinkedIn post, many SPAs) serve a near-empty <body> and render
# their real content in the browser - but the <head> still carries the page
# title and an Open Graph title/description naming what the page is. We strip the
# <head> for the body text (it's chrome), so we mine it separately here; without
# this, such a page reads back as "(no readable text)" and the model is left to
# guess (the bug that had it inventing a "Python" certificate for an ICAgile badge).
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
# Attribute parsing that doesn't care about order (content= can precede property=)
# or quote style.
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# meta name/property -> the label we surface it under. og:* is usually the
# fullest, plain description a good fallback.
_META_KEYS = {
    "og:title": "Title",
    "twitter:title": "Title",
    "og:description": "Description",
    "twitter:description": "Description",
    "description": "Description",
}


def _extract_metadata(html: str) -> str:
    """Pull the page <title> and key social meta tags from the <head>.

    Returns labelled "Title:" / "Description:" lines (HTML-unescaped, whitespace
    collapsed), keeping the most informative value per label, or "" when the head
    carries nothing useful. This is what lets a JavaScript-rendered page still
    tell the model what it is instead of coming back blank.
    """
    found: dict[str, str] = {}

    m = _TITLE.search(html)
    if m:
        title = unescape(re.sub(r"\s+", " ", m.group(1)).strip())
        if title:
            found["Title"] = title

    for tag in _META_TAG.findall(html):
        attrs = {k.lower(): (v1 or v2) for k, v1, v2 in _ATTR.findall(tag)}
        label = _META_KEYS.get((attrs.get("property") or attrs.get("name") or "").lower())
        content = unescape(re.sub(r"\s+", " ", attrs.get("content", "")).strip())
        if not label or not content:
            continue
        # Prefer the longest value per label (og:title beats a terse <title>).
        if label not in found or len(content) > len(found[label]):
            found[label] = content

    return "\n".join(f"{lbl}: {found[lbl]}" for lbl in ("Title", "Description") if lbl in found)


def _strip_html(html: str) -> str:
    """Turn HTML into rough plain text without a parser dependency.

    Drops boilerplate blocks (scripts, styles, and navigation chrome) before
    removing the remaining tags, so the readable content isn't drowned out by
    menus and icons. Good enough for reading a page; not a full HTML parser.
    """
    text = _BLOCKS.sub(" ", html)
    text = _COMMENTS.sub(" ", text)
    text = _TAGS.sub(" ", text)
    # Collapse the worst of the whitespace the tag removal leaves behind.
    text = re.sub(r"[ \t]+", " ", text)
    text = _WHITESPACE.sub("\n\n", text)
    return text.strip()


async def _run(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        raise ToolError("Provide a 'url' to fetch.")
    if not url.startswith(("http://", "https://")):
        raise ToolError("URL must start with http:// or https://.")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mocca/0.1 (local AI)"})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not fetch {url}: {exc}") from exc

    content_type = resp.headers.get("content-type", "")
    if "html" in content_type:
        # Lead with the head metadata (title/Open Graph): it orients the model on
        # any page and, on a JS-rendered one whose <body> strips to nothing, it's
        # the only readable signal there is.
        meta = _extract_metadata(resp.text)
        body = _strip_html(resp.text)
        body = "\n\n".join(p for p in (meta, body) if p)
    else:
        body = resp.text
    if len(body) > _MAX_CHARS:
        body = body[:_MAX_CHARS] + f"\n... [truncated at {_MAX_CHARS} characters]"
    # An empty result almost always means a page that renders its content with
    # JavaScript, which this tool can't run; say so plainly so the model reports
    # that it couldn't read the page rather than inventing its contents.
    return body or (
        "(the page returned no readable text - it likely renders its content with "
        "JavaScript, which this tool cannot run. Do not guess what the page says.)"
    )


TOOL = Tool(
    name="fetch_url",
    description=(
        "Fetch a web page by URL and return its readable text. Use to read a "
        "specific page the user names or that web_search returned."
    ),
    category="web",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full http(s) URL to fetch.",
            },
        },
        "required": ["url"],
    },
    is_local=False,
    run=_run,
)
