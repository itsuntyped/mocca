"""In-depth tests for the get_weather tool.

The formatting helper carries the tool's logic - assembling a concise summary
from a forecast object whose fields may be missing - so it's tested directly with
fake forecast objects (no network, no python-weather install needed). _run is
tested with the package faked via sys.modules, covering unit selection and input
validation. Routing is checked against the registry so a weather question reaches
the tool.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from src.tools import registry, weather
from src.tools.base import ToolError


def _fake_forecast(**overrides):
    """A forecast object with sensible defaults; override any field per test."""
    base = dict(
        location="Tokyo",
        temperature=20,
        feels_like=19,
        description="Partly cloudy",
        humidity=60,
        wind_speed=10,
        wind_direction="NE",
        daily_forecasts=[
            SimpleNamespace(date=date(2026, 6, 4), lowest_temperature=15, highest_temperature=24),
            SimpleNamespace(date=date(2026, 6, 5), lowest_temperature=16, highest_temperature=26),
            SimpleNamespace(date=date(2026, 6, 6), lowest_temperature=14, highest_temperature=22),
            SimpleNamespace(date=date(2026, 6, 7), lowest_temperature=13, highest_temperature=21),
        ],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestTemp(unittest.TestCase):
    def test_rounds_with_label(self):
        self.assertEqual(weather._temp(20.4, "°C"), "20°C")
        self.assertEqual(weather._temp(20.6, "°C"), "21°C")

    def test_missing_value(self):
        self.assertEqual(weather._temp(None, "°C"), "?")

    def test_no_label(self):
        self.assertEqual(weather._temp(10, ""), "10")


class TestFormatWeather(unittest.TestCase):
    def test_full_summary(self):
        out = weather._format_weather(_fake_forecast(), "Tokyo", "°C", "km/h")
        self.assertIn("Weather for Tokyo:", out)
        self.assertIn("Now: 20°C, Partly cloudy (feels like 19°C)", out)
        self.assertIn("Humidity 60%", out)
        self.assertIn("wind 10 km/h NE", out)
        self.assertIn("Forecast:", out)
        self.assertIn("15°C to 24°C", out)

    def test_caps_forecast_days(self):
        # Only today + the next two days are shown (four supplied above).
        out = weather._format_weather(_fake_forecast(), "Tokyo", "°C", "km/h")
        self.assertIn("Jun 04", out)
        self.assertIn("Jun 06", out)
        self.assertNotIn("Jun 07", out)

    def test_missing_fields_degrade(self):
        # A forecast exposing only a temperature must not crash; other lines drop.
        sparse = SimpleNamespace(temperature=18)
        out = weather._format_weather(sparse, "Reykjavik", "°C", "km/h")
        self.assertIn("Weather for Reykjavik:", out)  # falls back to the passed name
        self.assertIn("Now: 18°C", out)
        self.assertNotIn("Humidity", out)
        self.assertNotIn("Forecast:", out)

    def test_feels_like_omitted_when_equal(self):
        out = weather._format_weather(_fake_forecast(feels_like=20), "Tokyo", "°C", "km/h")
        self.assertNotIn("feels like", out)


class TestWeatherRun(unittest.IsolatedAsyncioTestCase):
    async def test_empty_location_rejected(self):
        with self.assertRaises(ToolError):
            await weather._run({"location": "  "})

    async def _run_with_fake(self, args):
        """Run the tool with python_weather faked, capturing the unit passed."""
        captured = {}

        class FakeClient:
            def __init__(self, unit):
                captured["unit"] = unit

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, location):
                captured["location"] = location
                return _fake_forecast()

        fake = SimpleNamespace(IMPERIAL="IMP", METRIC="MET", Client=FakeClient)
        with mock.patch.dict(sys.modules, {"python_weather": fake}):
            out = await weather._run(args)
        return out, captured

    async def test_metric_default(self):
        out, captured = await self._run_with_fake({"location": "Tokyo"})
        self.assertEqual(captured["unit"], "MET")
        self.assertEqual(captured["location"], "Tokyo")
        self.assertIn("°C", out)
        self.assertNotIn("°F", out)

    async def test_imperial_unit(self):
        out, captured = await self._run_with_fake({"location": "Austin", "unit": "fahrenheit"})
        self.assertEqual(captured["unit"], "IMP")
        self.assertIn("°F", out)
        self.assertIn("mph", out)

    async def test_lookup_failure_is_tool_error(self):
        class BoomClient:
            def __init__(self, unit):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, location):
                raise RuntimeError("network down")

        fake = SimpleNamespace(IMPERIAL="IMP", METRIC="MET", Client=BoomClient)
        with mock.patch.dict(sys.modules, {"python_weather": fake}):
            with self.assertRaises(ToolError):
                await weather._run({"location": "Tokyo"})


class TestWeatherMetadata(unittest.TestCase):
    def test_is_network_tool(self):
        self.assertEqual(weather.TOOL.category, "weather")
        self.assertFalse(weather.TOOL.is_local)

    def test_routes_on_weather_question(self):
        registry.discover()
        cats = registry.relevant_categories("what's the weather in Tokyo?", ["weather"])
        self.assertIn("weather", cats)


if __name__ == "__main__":
    unittest.main()
