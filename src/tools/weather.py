"""Network tool: current weather and a short forecast for a place.

Backed by the ``python-weather`` package (a thin async client over wttr.in). It
reaches the internet, so it is ``is_local=False`` and lives in its own "weather"
category - gated behind the web-search toggle like the other network tools, so
Mocca stays local-only unless the user opts in.

The package is imported lazily inside the run function, so a missing install
never breaks tool discovery or app startup; the tool just reports it's
unavailable. The formatting is split into a pure ``_format_weather`` helper that
reads attributes defensively (the library's shape has shifted across versions),
which keeps the assembled summary easy to unit-test without any network.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Tool, ToolError

log = logging.getLogger("mocca.tools")

# How many days of the daily forecast to include (today + the next two).
_MAX_DAYS = 3

# Aliases the model might pass for the imperial unit.
_IMPERIAL_NAMES = {"f", "fahrenheit", "imperial", "us"}


def _temp(value: Any, label: str) -> str:
    """Render a temperature with its unit label, tolerating a missing value."""
    if value is None:
        return "?"
    try:
        return f"{round(float(value))}{label}"
    except (TypeError, ValueError):
        return f"{value}{label}"


def _format_weather(weather: Any, location: str, temp_label: str, wind_label: str) -> str:
    """Build a concise, model-friendly summary from a python-weather forecast.

    Reads everything via ``getattr`` so a renamed/absent field degrades to "?"
    rather than raising - the library's attribute set has changed between major
    versions, and a partial summary is far better than a failed tool call.
    """
    name = getattr(weather, "location", None) or location
    current = getattr(weather, "temperature", None)
    feels = getattr(weather, "feels_like", None)
    desc = str(getattr(weather, "description", "") or "").strip()
    humidity = getattr(weather, "humidity", None)
    wind = getattr(weather, "wind_speed", None)
    wind_dir = getattr(weather, "wind_direction", None)

    lines = [f"Weather for {name}:"]

    now = f"Now: {_temp(current, temp_label)}"
    if desc:
        now += f", {desc}"
    if feels is not None and feels != current:
        now += f" (feels like {_temp(feels, temp_label)})"
    lines.append(now)

    details: list[str] = []
    if humidity is not None:
        details.append(f"Humidity {humidity}%")
    if wind is not None:
        direction = f" {wind_dir}" if wind_dir is not None else ""
        details.append(f"wind {_temp(wind, '')} {wind_label}{direction}".strip())
    if details:
        lines.append(", ".join(details))

    # daily_forecasts is the documented list; fall back to iterating the forecast
    # object itself (older versions yielded the daily forecasts on iteration).
    dailies = list(getattr(weather, "daily_forecasts", None) or [])
    if not dailies:
        try:
            dailies = list(weather)
        except TypeError:
            dailies = []
    if dailies:
        lines.append("Forecast:")
        for daily in dailies[:_MAX_DAYS]:
            when = getattr(daily, "date", None)
            when_str = when.strftime("%a %b %d") if hasattr(when, "strftime") else str(when)
            low = getattr(daily, "lowest_temperature", None)
            high = getattr(daily, "highest_temperature", None)
            lines.append(f"  {when_str}: {_temp(low, temp_label)} to {_temp(high, temp_label)}")

    return "\n".join(lines)


async def _run(args: dict[str, Any]) -> str:
    location = str(args.get("location", "")).strip()
    if not location:
        raise ToolError("Provide a 'location', e.g. 'Paris' or 'Austin, Texas'.")

    imperial = str(args.get("unit", "")).strip().lower() in _IMPERIAL_NAMES
    temp_label = "°F" if imperial else "°C"
    wind_label = "mph" if imperial else "km/h"

    try:
        import python_weather
    except ImportError as exc:
        raise ToolError(
            "The weather feature needs the 'python-weather' package "
            "(pip install python-weather)."
        ) from exc

    unit = python_weather.IMPERIAL if imperial else python_weather.METRIC
    try:
        async with python_weather.Client(unit=unit) as client:
            weather = await client.get(location)
    except Exception as exc:  # noqa: BLE001 - surface a friendly reason, not a stack trace
        log.debug("Weather lookup failed for %r", location, exc_info=True)
        raise ToolError(f"Could not get the weather for {location!r}.") from exc

    return _format_weather(weather, location, temp_label, wind_label)


TOOL = Tool(
    name="get_weather",
    description=(
        "Get the current weather and a short multi-day forecast for a city or "
        "place. Use whenever the user asks about the weather, temperature, or "
        "forecast somewhere. Pass the place as 'location'; set 'unit' to "
        "'fahrenheit' for US-style units (defaults to celsius)."
    ),
    category="weather",
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or place, e.g. 'Paris' or 'Austin, Texas'.",
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "Temperature unit. Defaults to celsius.",
            },
        },
        "required": ["location"],
    },
    is_local=False,
    run=_run,
)
