"""Tests for the Tool dataclass and its schema emission.

Tool.schema() is what the engine advertises to the model, so its shape matters:
it must be the OpenAI function-calling spec with the tool's name, description,
and parameters. We also confirm the metadata defaults (local, no confirm).
"""

from __future__ import annotations

import unittest

from src.tools.base import Tool, ToolError


def _noop(_args):
    return "ok"


class TestTool(unittest.TestCase):
    def _tool(self, **overrides):
        defaults = dict(
            name="demo",
            description="A demo tool.",
            category="test",
            parameters={"type": "object", "properties": {}},
            run=_noop,
        )
        defaults.update(overrides)
        return Tool(**defaults)

    def test_schema_shape(self):
        schema = self._tool().schema()
        self.assertEqual(schema["type"], "function")
        fn = schema["function"]
        self.assertEqual(fn["name"], "demo")
        self.assertEqual(fn["description"], "A demo tool.")
        self.assertEqual(fn["parameters"], {"type": "object", "properties": {}})

    def test_schema_excludes_internal_metadata(self):
        # category / is_local / confirm are Mocca-internal, not part of the spec.
        fn = self._tool().schema()["function"]
        self.assertNotIn("category", fn)
        self.assertNotIn("is_local", fn)
        self.assertNotIn("confirm", fn)

    def test_defaults(self):
        t = self._tool()
        self.assertTrue(t.is_local)
        self.assertFalse(t.confirm)

    def test_frozen(self):
        # The dataclass is frozen; tools are immutable once defined.
        t = self._tool()
        with self.assertRaises(Exception):
            t.name = "changed"  # type: ignore[misc]

    def test_toolerror_is_runtimeerror(self):
        self.assertTrue(issubclass(ToolError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
