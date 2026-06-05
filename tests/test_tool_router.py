"""In-depth tests for the model-based tool router.

The router (``src/tool_router.py``) is the primary "which tool categories does
this message need" decision: it asks the model over a compact menu and falls back
to keyword routing on any failure. We cannot run a real model in the offline
suite, so we exercise two layers:

  * ``_parse`` directly - the bulk of the logic - across valid output, prose-
    wrapped output, unknown/duplicate names, the explicit empty selection, and
    the unparseable cases that must signal a fallback.
  * ``choose_categories`` with a fake ``engine.complete`` / ``is_available`` so
    the routing decision, the empty-selection honouring, and every fallback path
    are covered without the network or a GGUF.
"""

from __future__ import annotations

import unittest

from src import engine, tool_router
from src.tools import registry


class TestParse(unittest.TestCase):
    """The reply parser: turn the model's text into valid category names."""

    VALID = {"web", "time", "math"}

    def test_plain_array(self):
        self.assertEqual(tool_router._parse('["web", "time"]', self.VALID), ["time", "web"])

    def test_filters_unknown_names(self):
        # A name the model invented (not enabled) is dropped, not passed through.
        self.assertEqual(tool_router._parse('["web", "banana"]', self.VALID), ["web"])

    def test_case_insensitive(self):
        self.assertEqual(tool_router._parse('["WEB"]', self.VALID), ["web"])

    def test_dedupes(self):
        self.assertEqual(tool_router._parse('["web", "web"]', self.VALID), ["web"])

    def test_extracts_from_prose(self):
        # Some models add a sentence despite the instruction; we take the array.
        self.assertEqual(tool_router._parse('Sure, here you go: ["web"].', self.VALID), ["web"])

    def test_empty_array_is_honoured(self):
        # An explicit "no tools" is a real decision (returns []), not a failure.
        self.assertEqual(tool_router._parse("[]", self.VALID), [])

    def test_no_array_returns_none(self):
        # None is the signal to fall back to keyword routing.
        self.assertIsNone(tool_router._parse("I think you want the web.", self.VALID))

    def test_malformed_json_returns_none(self):
        self.assertIsNone(tool_router._parse('["web", ', self.VALID))

    def test_non_list_returns_none(self):
        self.assertIsNone(tool_router._parse('{"category": "web"}', self.VALID))

    def test_non_string_items_ignored(self):
        self.assertEqual(tool_router._parse('["web", 5, null]', self.VALID), ["web"])


class TestChooseCategories(unittest.IsolatedAsyncioTestCase):
    """The async entry point, with the engine faked so no model is loaded."""

    @classmethod
    def setUpClass(cls):
        # The menu and the keyword fallback both need the tools registered.
        registry.discover()

    def setUp(self):
        # Save the real engine hooks so each test can swap them and restore.
        self._real_available = engine.is_available
        self._real_complete = engine.complete

    def tearDown(self):
        engine.is_available = self._real_available
        engine.complete = self._real_complete

    def _fake_complete(self, reply):
        """Build an async stand-in for engine.complete that returns ``reply``."""
        async def fake(model_name, messages, *, options=None):
            self._last_call = {"messages": messages, "options": options}
            return reply
        return fake

    async def test_uses_model_selection(self):
        engine.is_available = lambda: True
        engine.complete = self._fake_complete('["web"]')
        active = registry.active_categories(enable_web_search=True)
        # A keyword-less phrasing the old router would miss - the model picks web.
        out = await tool_router.choose_categories("m.gguf", "who runs France now?", active)
        self.assertEqual(out, ["web"])

    async def test_empty_selection_honoured_not_overridden(self):
        # The model says "no tools" for a message keyword routing WOULD route
        # (a bare arithmetic expression). The empty decision must win.
        engine.is_available = lambda: True
        engine.complete = self._fake_complete("[]")
        active = registry.active_categories(enable_web_search=True)
        out = await tool_router.choose_categories("m.gguf", "what is 2 * 2", active)
        self.assertEqual(out, [])

    async def test_unparseable_falls_back_to_keywords(self):
        # Garbage reply -> keyword router, which routes "2 * 2" to math.
        engine.is_available = lambda: True
        engine.complete = self._fake_complete("hmm, not sure")
        active = registry.active_categories(enable_web_search=True)
        out = await tool_router.choose_categories("m.gguf", "what is 2 * 2", active)
        self.assertIn("math", out)

    async def test_engine_failure_falls_back_to_keywords(self):
        engine.is_available = lambda: True

        async def boom(model_name, messages, *, options=None):
            raise RuntimeError("engine exploded")

        engine.complete = boom
        active = registry.active_categories(enable_web_search=True)
        out = await tool_router.choose_categories("m.gguf", "what is 2 * 2", active)
        self.assertIn("math", out)

    async def test_engine_unavailable_uses_keywords_without_calling_model(self):
        engine.is_available = lambda: False
        called = {"hit": False}

        async def should_not_run(model_name, messages, *, options=None):
            called["hit"] = True
            return "[]"

        engine.complete = should_not_run
        active = registry.active_categories(enable_web_search=True)
        out = await tool_router.choose_categories("m.gguf", "what is 2 * 2", active)
        self.assertIn("math", out)
        self.assertFalse(called["hit"], "engine.complete must not run when unavailable")

    async def test_no_active_categories_short_circuits(self):
        # With nothing enabled, route without touching the engine.
        called = {"hit": False}

        async def should_not_run(model_name, messages, *, options=None):
            called["hit"] = True
            return "[]"

        engine.is_available = lambda: True
        engine.complete = should_not_run
        out = await tool_router.choose_categories("m.gguf", "what is 2 * 2", [])
        self.assertEqual(out, [])
        self.assertFalse(called["hit"])

    async def test_routing_is_deterministic_and_capped(self):
        # The router must pin temperature to 0 and cap tokens, regardless of the
        # user's chat settings, so tool choice doesn't swing with temperature.
        engine.is_available = lambda: True
        engine.complete = self._fake_complete('["time"]')
        active = registry.active_categories(enable_web_search=True)
        await tool_router.choose_categories("m.gguf", "what's the date today", active)
        self.assertEqual(self._last_call["options"]["temperature"], 0.0)
        self.assertEqual(self._last_call["options"]["max_tokens"], tool_router._ROUTER_MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
