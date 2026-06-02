"""System routes: health, settings, hardware detection, and recommendations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import config, engine, hardware, models
from ..llmfit_service import service as llmfit

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
    """Report detected hardware (via the bundled llmfit server), for fit hints.

    ``available`` is False only if llmfit failed to start; the UI then shows a
    neutral message instead of a hardware summary.
    """
    system = await llmfit.get_system()
    return {
        "available": system is not None,
        "summary": hardware.summarise(system),
        "system": system,
    }


@router.get("/api/recommendations")
async def get_recommendations(limit: int = 8, min_fit: str = "good") -> dict[str, Any]:
    """Return llmfit's top models for this machine (best fit first).

    Powers the optional 'recommended for your hardware' list. Each entry keeps
    llmfit's useful fields (name, params, fit_level, estimated speed, GGUF
    sources) so the UI can show them and offer a download where available.
    """
    raw = await llmfit.get_top_models(limit=limit, min_fit=min_fit)
    return {
        "recommendations": [
            {
                "name": m.get("name"),
                "provider": m.get("provider"),
                "params": m.get("parameter_count"),
                "fit_level": m.get("fit_level"),
                "fit_label": m.get("fit_label"),
                "estimated_tps": m.get("estimated_tps"),
                "best_quant": m.get("best_quant"),
                "use_case": m.get("use_case"),
                "gguf_sources": m.get("gguf_sources", []),
            }
            for m in raw
        ]
    }
