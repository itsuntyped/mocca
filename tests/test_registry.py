"""In-depth tests for the tool registry.

The registry is what the rest of the app asks "what tools exist, which are
active, which does this message need, run this one". We test discovery (every
expected tool registers), the local/network category split, the web-search
gating, the signal-based relevance routing (the "send fewer tools" lever), and
execute() for both a real call and an unknown tool.
"""

from __future__ import annotations

import asyncio
import unittest

from src.tools import registry
from src.tools.base import ToolError

# The tools we ship; discovery must find all of them.
_EXPECTED = {
    "calculator", "convert_units", "current_datetime", "list_files", "read_file",
    "web_search", "fetch_url", "youtube_transcript", "track_shipment", "get_weather",
}


class TestRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.discover()

    def test_discovers_all_tools(self):
        names = {t.name for t in registry.all_tools()}
        self.assertTrue(_EXPECTED.issubset(names), f"missing: {_EXPECTED - names}")

    def test_network_categories(self):
        self.assertEqual(set(registry.network_categories()), {"web", "youtube", "shipping", "weather"})

    def test_active_excludes_network_when_off(self):
        active = set(registry.active_categories(enable_web_search=False))
        self.assertNotIn("web", active)
        self.assertNotIn("youtube", active)
        self.assertNotIn("shipping", active)
        # Local categories stay on.
        self.assertIn("math", active)
        self.assertIn("time", active)
        self.assertIn("files", active)

    def test_active_includes_network_when_on(self):
        active = set(registry.active_categories(enable_web_search=True))
        self.assertIn("web", active)
        self.assertIn("youtube", active)
        self.assertIn("shipping", active)

    def test_get_known_and_unknown(self):
        self.assertIsNotNone(registry.get("calculator"))
        self.assertIsNone(registry.get("does_not_exist"))

    def test_schemas_shape(self):
        schemas = registry.schemas(["math"])
        self.assertTrue(schemas)
        for s in schemas:
            self.assertEqual(s["type"], "function")
            self.assertIn("name", s["function"])

    # Relevance routing: only the signalled categories are offered.
    def _relevant(self, text):
        return set(registry.relevant_categories(text, registry.active_categories(True)))

    def test_relevance_arithmetic(self):
        self.assertIn("math", self._relevant("what is 12 * 8"))

    def test_relevance_time_keyword(self):
        self.assertIn("time", self._relevant("what time is it right now"))

    def test_relevance_files_keyword(self):
        self.assertIn("files", self._relevant("please read my notes file"))

    def test_relevance_weather_keyword(self):
        self.assertIn("weather", self._relevant("what's the weather in Paris"))

    def test_relevance_search_keyword(self):
        self.assertIn("web", self._relevant("search for the latest news"))

    def test_relevance_plain_url_routes_web(self):
        self.assertEqual(self._relevant("https://example.com/article"), {"web"})

    def test_relevance_youtube_link_routes_youtube_not_web(self):
        rel = self._relevant("summarise https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("youtube", rel)
        self.assertNotIn("web", rel)

    def test_relevance_tracking_number(self):
        self.assertIn("shipping", self._relevant("track INTLCMI306283305 please"))

    def test_relevance_greeting_has_no_tools(self):
        # Ordinary chat must offer no tools, so a tool-happy model stays quiet.
        self.assertEqual(self._relevant("hello there, nice to meet you"), set())

    # Category menu (what the model-based router sees instead of full schemas).
    def test_category_descriptions_all(self):
        desc = registry.category_descriptions()
        # Every category gets a non-empty, one-line blurb built from its tools.
        self.assertEqual(set(desc), set(registry.categories()))
        for blurb in desc.values():
            self.assertTrue(blurb.strip())

    def test_category_descriptions_subset(self):
        desc = registry.category_descriptions(["math"])
        self.assertEqual(set(desc), {"math"})

    # Execution.
    def test_execute_calculator(self):
        out = asyncio.run(registry.execute("calculator", {"expression": "6 * 7"}))
        self.assertEqual(out, "42")

    def test_execute_unknown_tool(self):
        with self.assertRaises(ToolError):
            asyncio.run(registry.execute("nope", {}))


if __name__ == "__main__":
    unittest.main()
