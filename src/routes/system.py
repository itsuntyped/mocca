"""System routes: health, settings, and hardware detection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import config, engine, hardware, models

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, Any]:
    """Report engine availability and whether any model is downloaded.

    The UI uses this to show clear setup guidance instead of failing silently.
    """
    return {
        "engine_available": engine.is_available(),
        "has_models": models.has_any_model(),
    }


@router.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    """Return the current settings."""
    return config.get().to_dict()


@router.put("/api/settings")
async def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial settings update and return the new settings.

    If the log level changed, reconfigure logging on the fly so it takes effect
    without a restart.
    """
    before = config.get().log_level
    new = config.update(patch)
    if new.log_level != before:
        from ..logging_config import setup_logging
        setup_logging(new.log_level)
    return new.to_dict()


@router.get("/api/hardware")
async def get_hardware() -> dict[str, Any]:
    """Report detected hardware (RAM/CPU/GPU) for the model-fit hints.

    ``available`` is False only if detection failed (e.g. RAM unreadable); the
    UI then shows a neutral message instead of a hardware summary.
    """
    system = hardware.detect_system()
    return {
        "available": system is not None,
        "summary": hardware.summarise(system),
        "system": system,
    }
