"""Tests for the session document tools (read_document / list_documents).

These tools are session-scoped: they read the current session id from
``tool_context`` (set per turn by the tool loop) and only ever see that chat's
documents. We verify the happy path, the no-session guard, truncation, the
missing-file error, the tool metadata, and - most importantly - cross-session
isolation, since that scoping is the whole safety story.
"""

from __future__ import annotations

import asyncio
import unittest

from src import database, tool_context
from src.tools import registry
from src.tools.base import ToolError


class TestDocumentTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.discover()

    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]
        self._token = None

    def tearDown(self):
        if self._token is not None:
            tool_context.reset_session(self._token)

    def _scope(self, sid):
        self._token = tool_context.set_session(sid)

    def _run(self, name, args):
        return asyncio.run(registry.execute(name, args))

    def test_read_returns_content(self):
        database.create_document(self.sid, "notes.md", "the answer is 42")
        self._scope(self.sid)
        out = self._run("read_document", {"filename": "notes.md"})
        self.assertIn("the answer is 42", out)

    def test_no_session_is_clear_error(self):
        # Default context (no session set) -> a friendly message, never a read.
        with self.assertRaises(ToolError):
            self._run("read_document", {"filename": "notes.md"})

    def test_missing_document_raises(self):
        self._scope(self.sid)
        with self.assertRaises(ToolError):
            self._run("read_document", {"filename": "ghost.md"})

    def test_loose_name_resolves_by_stem(self):
        # The model may pass "README" for a stored "readme.md".
        database.create_document(self.sid, "readme.md", "STEM-MATCH")
        self._scope(self.sid)
        self.assertIn("STEM-MATCH", self._run("read_document", {"filename": "README"}))
        self.assertIn("STEM-MATCH", self._run("read_document", {"filename": "readme.txt"}))

    def test_ambiguous_stem_does_not_guess(self):
        database.create_document(self.sid, "a.md", "MD")
        database.create_document(self.sid, "a.txt", "TXT")
        self._scope(self.sid)
        with self.assertRaises(ToolError):
            self._run("read_document", {"filename": "a"})

    def test_truncates_long_document(self):
        database.create_document(self.sid, "big.txt", "a" * 25_000)
        self._scope(self.sid)
        out = self._run("read_document", {"filename": "big.txt"})
        self.assertIn("truncated", out)

    def test_cross_session_isolation(self):
        other = database.create_session(title="o")["id"]
        database.create_document(self.sid, "notes.md", "MINE")
        database.create_document(other, "notes.md", "THEIRS")
        self._scope(self.sid)
        out = self._run("read_document", {"filename": "notes.md"})
        self.assertIn("MINE", out)
        self.assertNotIn("THEIRS", out)

    def test_tool_metadata(self):
        read = registry.get("read_document")
        self.assertEqual(read.category, "documents")
        self.assertTrue(read.is_local)


if __name__ == "__main__":
    unittest.main()
