"""Test that the tool loop binds the session for session-scoped tools.

The document tools read the current session from ``tool_context``, which
``tool_loop.run`` must set for the turn and reset afterwards. We stub the engine
and router so the loop runs offline, drive a turn that calls read_document, and
assert the tool actually saw this session's document (proving the contextvar is
live during ``registry.execute``) and that it is cleared once the turn ends.
"""

from __future__ import annotations

import asyncio
import unittest

from src import database, engine, tool_context, tool_loop, tool_router
from src.tools import registry


class TestToolLoopSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.discover()

    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]
        database.create_document(self.sid, "notes.md", "SECRET-XYZ")
        # Save the real engine/router hooks so we can stub and restore them.
        self._saved = {
            "is_available": engine.is_available,
            "decide": engine.decide,
            "chat": engine.chat,
            "choose": tool_router.choose_categories,
        }

    def tearDown(self):
        engine.is_available = self._saved["is_available"]
        engine.decide = self._saved["decide"]
        engine.chat = self._saved["chat"]
        tool_router.choose_categories = self._saved["choose"]

    def _drive(self, session_id):
        async def choose(model, text, active):
            return ["documents"]

        async def decide(model, messages, schemas, *, options=None):
            return engine.Decision(tool_calls=[
                engine.ToolCall(name="read_document", arguments={"filename": "notes.md"})
            ])

        async def chat(model, messages, *, options=None):
            yield "done"

        engine.is_available = lambda: True
        tool_router.choose_categories = choose
        engine.decide = decide
        engine.chat = chat

        events = []

        async def run():
            async for ev in tool_loop.run(
                "m.gguf", [{"role": "user", "content": "read notes.md"}], session_id=session_id
            ):
                events.append(ev)

        asyncio.run(run())
        return events

    def test_tool_sees_session_during_execute(self):
        events = self._drive(self.sid)
        results = [e["tool_result"] for e in events if "tool_result" in e]
        self.assertTrue(results, "read_document should have run")
        self.assertIn("SECRET-XYZ", results[0]["result"])

    def test_context_reset_after_turn(self):
        self._drive(self.sid)
        self.assertIsNone(tool_context.current_session_id())


if __name__ == "__main__":
    unittest.main()
