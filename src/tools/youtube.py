"""Network tool: read the transcript (captions) of a YouTube video.

When the user shares a YouTube link, this fetches the video's transcript via the
``youtube-transcript-api`` package so the AI can summarise it or answer questions
about its contents. It reaches the internet, so it is ``is_local=False`` and
lives in the "youtube" category - gated behind the web-search toggle like the
other network tools (Mocca stays local-only unless the user opts in).

The library is imported lazily inside the run function, so a missing install
never breaks tool discovery or app startup; the tool just reports it's
unavailable. The library is synchronous (it uses ``requests`` under the hood),
so we run it off the event loop with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# Cap the transcript so a long video can't blow past a small local context.
_MAX_CHARS = 8_000

# Pull an 11-char YouTube video id out of the common URL shapes: watch?v=,
# youtu.be/, /shorts/, /embed/, /live/.
_ID_RE = re.compile(r"(?:v=|/(?:shorts|embed|live)/|youtu\.be/)([A-Za-z0-9_-]{11})")
# A bare id pasted on its own (no URL).
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _video_id(url: str) -> str | None:
    """Extract the YouTube video id from a URL (or a bare id), else None."""
    url = url.strip()
    match = _ID_RE.search(url)
    if match:
        return match.group(1)
    return url if _BARE_ID_RE.match(url) else None


def _fetch_transcript(video_id: str) -> str:
    """Fetch and flatten a transcript to plain text (blocking; run off-thread).

    Supports both the modern instance API (youtube-transcript-api >= 1.0) and the
    older static ``get_transcript`` (<= 0.6). For non-English videos where the
    default fetch finds nothing, we fall back to any available transcript.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    if hasattr(YouTubeTranscriptApi, "fetch"):  # modern instance API (>= 1.0)
        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id)
        except Exception:  # noqa: BLE001 - e.g. no English track; try any language
            fetched = next(iter(api.list(video_id))).fetch()
        return " ".join(snippet.text for snippet in fetched)

    # Legacy static API (<= 0.6): returns a list of {"text", "start", ...}.
    snippets = YouTubeTranscriptApi.get_transcript(video_id)
    return " ".join(s["text"] for s in snippets)


async def _run(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        raise ToolError("Provide the YouTube 'url' whose transcript to read.")
    video_id = _video_id(url)
    if not video_id:
        raise ToolError("That doesn't look like a YouTube video URL.")

    try:
        text = await asyncio.to_thread(_fetch_transcript, video_id)
    except ImportError as exc:
        raise ToolError("The youtube-transcript-api package is not installed.") from exc
    except Exception as exc:  # noqa: BLE001 - surface the library's own reason
        # Covers disabled/unavailable transcripts, private/removed videos, etc.
        raise ToolError(f"Could not get a transcript for this video: {exc}") from exc

    text = text.strip()
    if not text:
        return "(the video has no transcript text)"
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [transcript truncated at {_MAX_CHARS} characters]"
    return text


TOOL = Tool(
    name="youtube_transcript",
    description=(
        "Read the transcript (spoken captions) of a YouTube video. Use this "
        "whenever the user gives a YouTube link (youtube.com or youtu.be) and "
        "wants to know about, summarise, or ask questions about the video. Pass "
        "the video URL as 'url'."
    ),
    category="youtube",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The YouTube video URL (e.g. https://youtu.be/... or https://www.youtube.com/watch?v=...).",
            },
        },
        "required": ["url"],
    },
    is_local=False,
    run=_run,
)
