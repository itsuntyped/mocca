"""Tools routes: list the AI's available tools and toggle which categories are on.

The UI uses these to show what the assistant can do and to let the user switch
tool categories on or off (notably the network "web" category, which is off by
default to keep Mocca local-only). Enabling/disabling is per-category rather than
per-tool: it's simpler to reason about and scales as the tool count grows.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..tools import registry

router = APIRouter()


class ToolsUpdate(BaseModel):
    # The full set of category names the user wants enabled.
    enabled_categories: list[str]


def _payload() -> dict[str, Any]:
    """Current tools view: all tools, all categories, and which are enabled."""
    settings = config.get()
    return {
        "categories": registry.categories(),
        "enabled": settings.enabled_tool_categories,
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "is_local": t.is_local,
            }
            for t in registry.all_tools()
        ],
    }


@router.get("/api/tools")
async def list_tools() -> dict[str, Any]:
    """List every registered tool plus the enabled-category state."""
    return _payload()


@router.put("/api/tools")
async def update_tools(update: ToolsUpdate) -> dict[str, Any]:
    """Set which tool categories are enabled, then return the updated view.

    Unknown category names are dropped so a stale client can't enable something
    that doesn't exist.
    """
    known = set(registry.categories())
    enabled = [c for c in update.enabled_categories if c in known]
    config.update({"enabled_tool_categories": enabled})
    return _payload()
