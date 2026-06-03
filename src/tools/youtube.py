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

import httpx

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# YouTube's oEmbed endpoint: a no-auth, no-dependency way to get a video's title
# and channel (richer context than the transcript alone). Best-effort only.
_OEMBED = "https://www.youtube.com/oembed"

# Cap the transcript so a long video can't blow past a small local context.
_MAX_CHARS = 8_000
# Transcript fetches are flaky (YouTube rate-limits / transient errors); a couple
# of quick retries noticeably improves the hit rate.
_MAX_ATTEMPTS = 2
# Emit a [MM:SS] marker roughly every this-many seconds of speech, grouping the
# text between markers onto one line. Gives the model timestamps to cite
# ("around 3:45 they discuss X") without a noisy stamp on every short phrase.
_STAMP_EVERY = 20.0

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


def _fetch_segments(video_id: str) -> list[tuple[float, str]]:
    """Fetch a transcript as (start_seconds, text) segments (blocking; off-thread).

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
        return [(float(s.start), s.text.strip()) for s in fetched if s.text.strip()]

    # Legacy static API (<= 0.6): returns a list of {"text", "start", ...}.
    snippets = YouTubeTranscriptApi.get_transcript(video_id)
    return [(float(s.get("start", 0)), s["text"].strip())
            for s in snippets if s.get("text", "").strip()]


async def _fetch_metadata(video_id: str) -> tuple[str, str]:
    """Best-effort video title + channel via YouTube's oEmbed endpoint.

    Returns ``("", "")`` on any failure - metadata just enriches the context, so
    it must never break transcript reading (no key, no extra dependency).
    """
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(_OEMBED, params={
                "format": "json",
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })
            resp.raise_for_status()
            data = resp.json()
        return str(data.get("title", "")).strip(), str(data.get("author_name", "")).strip()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("YouTube oEmbed lookup failed for %s: %s", video_id, exc)
        return "", ""


def _format_timestamp(seconds: float) -> str:
    """Seconds -> 'M:SS' (or 'H:MM:SS' for long videos)."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _build_transcript(segments: list[tuple[float, str]]) -> str:
    """Render segments into timestamped lines, one per ~``_STAMP_EVERY`` seconds.

    Grouping keeps a short, citeable marker (e.g. ``[3:45] ...``) without a
    timestamp on every two-word phrase, which would bloat the text and the
    model's context.
    """
    lines: list[str] = []
    bucket_start: float | None = None
    bucket: list[str] = []
    for start, text in segments:
        if bucket_start is None or start - bucket_start >= _STAMP_EVERY:
            if bucket:
                lines.append(f"[{_format_timestamp(bucket_start)}] {' '.join(bucket)}")
            bucket_start, bucket = start, [text]
        else:
            bucket.append(text)
    if bucket and bucket_start is not None:
        lines.append(f"[{_format_timestamp(bucket_start)}] {' '.join(bucket)}")
    return "\n".join(lines)


async def _run(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        raise ToolError("Provide the YouTube 'url' whose transcript to read.")
    video_id = _video_id(url)
    if not video_id:
        raise ToolError("That doesn't look like a YouTube video URL.")

    # A couple of attempts with a short backoff - transcript fetches flake out.
    segments: list[tuple[float, str]] | None = None
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            segments = await asyncio.to_thread(_fetch_segments, video_id)
            break
        except ImportError as exc:
            raise ToolError("The youtube-transcript-api package is not installed.") from exc
        except Exception as exc:  # noqa: BLE001 - surface the library's own reason
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(1.0 * (attempt + 1))
    if segments is None:
        # Covers disabled/unavailable transcripts, private/removed videos, etc.
        raise ToolError(f"Could not get a transcript for this video: {last_exc}")
    if not segments:
        return "(the video has no transcript text)"

    text = _build_transcript(segments)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [transcript truncated at {_MAX_CHARS} characters]"

    # Prepend the title/channel when we can get them (best-effort, never fatal).
    title, channel = await _fetch_metadata(video_id)
    header = ""
    if title:
        header += f"Title: {title}\n"
    if channel:
        header += f"Channel: {channel}\n"
    return f"{header}\n{text}" if header else text


TOOL = Tool(
    name="youtube_transcript",
    description=(
        "Read the transcript (spoken captions) of a YouTube video. Use this "
        "whenever the user gives a YouTube link (youtube.com or youtu.be) and "
        "wants to know about, summarise, or ask questions about the video. The "
        "transcript includes [M:SS] timestamps you can cite. Pass the video URL "
        "as 'url'."
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
