"""Tests for the current-datetime tool.

The tool reads the real clock, so we can't assert an exact value; instead we
check the *shape* of the output (a UTC line and a local line, both ISO-ish with
seconds precision) and that it ignores its arguments.
"""

from __future__ import annotations

import re
import unittest
from datetime import datetime

from src.tools import datetime_tool


class TestDatetime(unittest.TestCase):
    def test_has_utc_and_local_lines(self):
        out = datetime_tool._run({})
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("UTC: "))
        self.assertTrue(lines[1].startswith("Local ("))

    def test_utc_is_parseable_iso(self):
        out = datetime_tool._run({})
        utc_value = out.splitlines()[0][len("UTC: "):]
        # Should round-trip through fromisoformat without raising.
        parsed = datetime.fromisoformat(utc_value)
        self.assertIsNotNone(parsed.tzinfo)

    def test_seconds_precision_no_microseconds(self):
        # timespec='seconds' means no fractional seconds in the stamp.
        out = datetime_tool._run({})
        self.assertNotRegex(out, re.compile(r"\d\.\d{6}"))

    def test_ignores_arguments(self):
        # The tool takes no parameters; extra args must not change behaviour.
        a = datetime_tool._run({})
        b = datetime_tool._run({"anything": "ignored"})
        # Both are well-formed (timestamps may differ by a second; just check shape).
        for out in (a, b):
            self.assertIn("UTC: ", out)

    def test_tool_metadata(self):
        self.assertEqual(datetime_tool.TOOL.name, "current_datetime")
        self.assertEqual(datetime_tool.TOOL.category, "time")
        self.assertTrue(datetime_tool.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
