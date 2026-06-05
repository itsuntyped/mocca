"""Tests for the engine's context-window auto-grow on overflow.

Editing a document can push a turn past a small n_ctx. Rather than fail, the
engine grows the context to fit and retries. We test the pure helpers and the
floor-growing decision with a fake model (no real GGUF needed).
"""

from __future__ import annotations

import time
import unittest

from src import engine
from src.config import Settings


class _FakeLLM:
    """Stand-in for a loaded llama.cpp model: just a context size + metadata."""

    def __init__(self, ctx: int, train: int | None = 32768):
        self._ctx = ctx
        self.metadata = {"qwen2.context_length": str(train)} if train else {}

    def n_ctx(self) -> int:
        return self._ctx


class TestRoundUpCtx(unittest.TestCase):
    def test_rounds_to_power_of_two(self):
        self.assertEqual(engine._round_up_ctx(5757), 8192)
        self.assertEqual(engine._round_up_ctx(4097), 8192)
        self.assertEqual(engine._round_up_ctx(2048), 2048)
        self.assertEqual(engine._round_up_ctx(100), 2048)  # floor of 2048


class TestFriendlyError(unittest.TestCase):
    def test_overflow_is_translated(self):
        msg = engine._friendly_generation_error(
            ValueError("Requested tokens (5245) exceed context window of 4096")
        )
        self.assertNotIn("Requested tokens", msg)
        self.assertIn("context window", msg.lower())

    def test_other_errors_passthrough(self):
        self.assertIn("boom", engine._friendly_generation_error(RuntimeError("boom")))


class TestGrowCtxFloor(unittest.TestCase):
    def setUp(self):
        self.worker = engine._Worker()

    def _overflow(self, requested: int) -> ValueError:
        return ValueError(f"Requested tokens ({requested}) exceed context window of 4096")

    def test_grows_to_fit(self):
        self.worker._llm = _FakeLLM(ctx=4096)
        self.assertTrue(self.worker._grow_ctx_floor(self._overflow(5245)))
        self.assertEqual(self.worker._ctx_floor, 8192)

    def test_non_overflow_does_not_grow(self):
        self.worker._llm = _FakeLLM(ctx=4096)
        self.assertFalse(self.worker._grow_ctx_floor(RuntimeError("unrelated")))
        self.assertEqual(self.worker._ctx_floor, 0)

    def test_capped(self):
        # A huge request is capped at _CTX_GROW_CAP (and the model's train size).
        self.worker._llm = _FakeLLM(ctx=4096)
        self.worker._grow_ctx_floor(self._overflow(999_999))
        self.assertLessEqual(self.worker._ctx_floor, engine._CTX_GROW_CAP)

    def test_cannot_grow_past_current(self):
        # Already at the cap: nothing more to do, so don't loop.
        self.worker._llm = _FakeLLM(ctx=engine._CTX_GROW_CAP)
        self.worker._ctx_floor = engine._CTX_GROW_CAP
        self.assertFalse(self.worker._grow_ctx_floor(self._overflow(999_999)))

    def test_respects_model_train_ctx(self):
        # A model trained for only 4096 can't be grown beyond that.
        self.worker._llm = _FakeLLM(ctx=4096, train=4096)
        self.assertFalse(self.worker._grow_ctx_floor(self._overflow(8000)))


class TestUnloadAndIdle(unittest.TestCase):
    def setUp(self):
        self.worker = engine._Worker()

    def test_unload_resets_ctx_floor(self):
        # The grown context must NOT survive an unload: the next load starts at base.
        self.worker._llm = _FakeLLM(ctx=4096)
        self.worker._grow_ctx_floor(
            ValueError("Requested tokens (5245) exceed context window of 4096")
        )
        self.assertEqual(self.worker._ctx_floor, 8192)
        self.assertTrue(self.worker.unload())
        self.assertEqual(self.worker._ctx_floor, 0)
        self.assertIsNone(self.worker._llm)

    def test_idle_seconds_none_when_unloaded(self):
        self.assertIsNone(self.worker.idle_seconds())

    def test_idle_seconds_after_touch(self):
        self.worker._llm = _FakeLLM(ctx=4096)
        self.worker._touch()
        self.assertLess(self.worker.idle_seconds(), 1.0)

    def test_unload_if_idle_keeps_recent(self):
        self.worker._llm = _FakeLLM(ctx=4096)
        self.worker._last_used = time.monotonic()  # just used
        self.assertFalse(self.worker.unload_if_idle(60))
        self.assertIsNotNone(self.worker._llm)

    def test_unload_if_idle_drops_stale(self):
        self.worker._llm = _FakeLLM(ctx=4096)
        self.worker._last_used = time.monotonic() - 1000  # idle 1000s
        self.assertTrue(self.worker.unload_if_idle(60))
        self.assertIsNone(self.worker._llm)

    def test_unload_if_idle_noop_when_unloaded(self):
        self.assertFalse(self.worker.unload_if_idle(0))


class TestConfigDefault(unittest.TestCase):
    def test_idle_unload_enabled_by_default(self):
        # Shipped default: on, at 15 minutes.
        self.assertEqual(Settings().unload_idle_minutes, 15)


if __name__ == "__main__":
    unittest.main()
