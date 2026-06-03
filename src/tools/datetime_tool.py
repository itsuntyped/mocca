"""Datetime tool: report the current date and time.

A local model has no clock - its sense of "now" is frozen at training time. This
tool lets the AI fetch the real current time (UTC and the machine's local zone)
so it can answer "what's today's date" or reason about relative time correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import Tool


def _run(_args: dict[str, Any]) -> str:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone()  # The machine's configured local zone.
    tzname = now_local.tzname() or "local"
    return (
        f"UTC: {now_utc.isoformat(timespec='seconds')}\n"
        f"Local ({tzname}): {now_local.isoformat(timespec='seconds')}"
    )


TOOL = Tool(
    name="current_datetime",
    description=(
        "Get the current date and time (UTC and the machine's local time zone). "
        "Use whenever the user asks about today, now, or the current time."
    ),
    category="time",
    # No arguments: the tool always reports the current moment.
    parameters={"type": "object", "properties": {}},
    run=_run,
)
