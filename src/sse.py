"""Server-Sent Events helper shared by the streaming routes."""

from __future__ import annotations

import json
from typing import Any


def sse(data: dict[str, Any]) -> str:
    """Format a dict as a single Server-Sent Event frame."""
    return f"data: {json.dumps(data)}\n\n"
