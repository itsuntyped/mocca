"""Tests for the chat message pagination helper (database.get_messages_page).

The chat UI shows the most recent page of messages and loads older pages as the
user scrolls up. We cover the page size and ordering, the ``before`` scroll
cursor walking backwards through history, the ``has_more`` flag at the boundary,
and the exclusion of display-only ``tool`` rows so a page is always N *visible*
bubbles.
"""

from __future__ import annotations

import unittest

from src import database


class TestGetMessagesPage(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]

    def _seed(self, n: int) -> None:
        for i in range(n):
            database.add_message(self.sid, "user", f"m{i}")

    def test_latest_page_is_newest_oldest_first(self):
        self._seed(20)
        page = database.get_messages_page(self.sid, limit=15)
        self.assertEqual(len(page["messages"]), 15)
        self.assertTrue(page["has_more"])
        # Oldest-first within the page, holding the 5 newest..19 (i.e. m5..m19).
        contents = [m["content"] for m in page["messages"]]
        self.assertEqual(contents[0], "m5")
        self.assertEqual(contents[-1], "m19")

    def test_before_cursor_walks_backwards(self):
        self._seed(20)
        first = database.get_messages_page(self.sid, limit=15)
        cursor = first["messages"][0]["seq"]  # seq of m5, the oldest shown.
        older = database.get_messages_page(self.sid, before_seq=cursor, limit=15)
        contents = [m["content"] for m in older["messages"]]
        self.assertEqual(contents, [f"m{i}" for i in range(5)])  # m0..m4.
        self.assertFalse(older["has_more"])  # Nothing older than m0.

    def test_has_more_false_when_everything_fits(self):
        self._seed(10)
        page = database.get_messages_page(self.sid, limit=15)
        self.assertEqual(len(page["messages"]), 10)
        self.assertFalse(page["has_more"])

    def test_empty_session(self):
        page = database.get_messages_page(self.sid, limit=15)
        self.assertEqual(page["messages"], [])
        self.assertFalse(page["has_more"])

    def test_tool_rows_are_excluded(self):
        database.add_message(self.sid, "user", "hi")
        database.add_message(self.sid, "tool", '{"name": "x"}')
        database.add_message(self.sid, "assistant", "hello")
        page = database.get_messages_page(self.sid, limit=15)
        roles = [m["role"] for m in page["messages"]]
        self.assertEqual(roles, ["user", "assistant"])  # tool row dropped.


if __name__ == "__main__":
    unittest.main()
