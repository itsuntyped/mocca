"""Tests for memory pruning (memory_extractor.prune_stale_memories).

Pruning is the mirror of capture: when a turn contradicts, replaces, or asks us
to drop a stored fact, the fact should be forgotten. The LLM decides *which*
facts (by number); these tests pin the surrounding machinery - the number->id
mapping, the conservative guards, and the "never crash the chat" contract -
without depending on a real model. The LLM call is replaced with a fake that
returns a canned JSON array, so we test our handling of its output in depth:
valid picks, out-of-range/duplicate/garbage numbers, the empty case, the
no-memories short-circuit, and an LLM failure.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from src import database, memory_extractor


def _fake_complete(reply: str):
    """An async stand-in for engine.complete that always returns ``reply``."""

    async def _complete(model, messages, options=None):  # noqa: ANN001
        return reply

    return _complete


def _convo(*turns: str) -> list[dict[str, str]]:
    """Build a user/assistant message list from alternating texts."""
    roles = ("user", "assistant")
    return [{"role": roles[i % 2], "content": t} for i, t in enumerate(turns)]


def _prune(reply: str, convo: list[dict[str, str]]) -> int:
    with mock.patch.object(memory_extractor.engine, "complete", _fake_complete(reply)):
        return asyncio.run(memory_extractor.prune_stale_memories("m", convo))


def _extract(reply: str, convo: list[dict[str, str]]) -> int:
    with mock.patch.object(memory_extractor.engine, "complete", _fake_complete(reply)):
        return asyncio.run(memory_extractor.extract_and_store("m", convo))


class TestPruneStaleMemories(unittest.TestCase):
    def setUp(self):
        database.init_db()
        database.clear_memories()

    def _contents(self) -> list[str]:
        return [m["content"] for m in database.list_memories()]

    def test_removes_the_picked_fact(self):
        database.add_memory("The user loves the Elixir programming language.", "preference")
        database.add_memory("The user's name is Sam.", "identity")
        removed = _prune("[1]", _convo("im not into elixir anymore, ive moved to react"))
        self.assertEqual(removed, 1)
        contents = self._contents()
        self.assertFalse(any("Elixir" in c for c in contents))
        self.assertTrue(any("Sam" in c for c in contents))

    def test_empty_pick_keeps_everything(self):
        database.add_memory("The user likes hiking.", "preference")
        removed = _prune("[]", _convo("i also enjoy bouldering"))
        self.assertEqual(removed, 0)
        self.assertTrue(any("hiking" in c for c in self._contents()))

    def test_no_memories_short_circuits_without_llm(self):
        # With nothing stored there is nothing to prune; the LLM must not be
        # consulted at all (an unexpected call would blow up this fake).
        async def _boom(*a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("engine.complete should not be called")

        with mock.patch.object(memory_extractor.engine, "complete", _boom):
            removed = asyncio.run(
                memory_extractor.prune_stale_memories("m", _convo("forget everything"))
            )
        self.assertEqual(removed, 0)

    def test_out_of_range_and_garbage_numbers_are_ignored(self):
        database.add_memory("The user lives in Berlin.", "location")
        # 0 and 9 are out of range, "x" is non-numeric, the duplicate 1s count once.
        removed = _prune("[0, 9, 1, 1, \"x\"]", _convo("forget where i live"))
        self.assertEqual(removed, 1)
        self.assertEqual(self._contents(), [])

    def test_llm_failure_removes_nothing(self):
        database.add_memory("The user lives in Berlin.", "location")

        async def _fail(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("model unavailable")

        with mock.patch.object(memory_extractor.engine, "complete", _fail):
            removed = asyncio.run(
                memory_extractor.prune_stale_memories("m", _convo("forget berlin"))
            )
        self.assertEqual(removed, 0)
        self.assertTrue(any("Berlin" in c for c in self._contents()))

    def test_tolerates_markdown_fenced_json(self):
        database.add_memory("The user lives in Berlin.", "location")
        removed = _prune("```json\n[1]\n```", _convo("im not in berlin anymore"))
        self.assertEqual(removed, 1)


class TestExtractGrounding(unittest.TestCase):
    """The identity-grounding guard: a name the user never typed is dropped.

    Small models sometimes copy a name out of the extractor's own few-shot
    example (the original bug stored "The user's name is Sam." for a turn that
    never mentioned Sam). extract_and_store rejects an identity fact whose
    distinctive word isn't present in the user's text.
    """

    def setUp(self):
        database.init_db()
        database.clear_memories()

    def _contents(self) -> list[str]:
        return [m["content"] for m in database.list_memories()]

    def test_hallucinated_name_is_dropped(self):
        # The user never says "Sam"; the model copies it from its example.
        convo = _convo("I'm impressed, you really checked my website? it doesn't look like it.")
        added = _extract('[{"text": "The user\'s name is Sam.", "category": "identity"}]', convo)
        self.assertEqual(added, 0)
        self.assertEqual(self._contents(), [])

    def test_real_name_is_kept(self):
        convo = _convo("yo im sam, nice to be here")
        added = _extract('[{"text": "The user\'s name is Sam.", "category": "identity"}]', convo)
        self.assertEqual(added, 1)
        self.assertTrue(any("Sam" in c for c in self._contents()))

    def test_non_identity_fact_is_not_grounding_checked(self):
        # Grounding is scoped to identity facts; other categories pass through
        # (they get normalised/paraphrased, so token-matching would misfire).
        convo = _convo("tell me about rust")
        added = _extract('[{"text": "The user enjoys systems programming.", "category": "preference"}]', convo)
        self.assertEqual(added, 1)


if __name__ == "__main__":
    unittest.main()
