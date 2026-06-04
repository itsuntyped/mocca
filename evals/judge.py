"""Soft LLM-as-judge: score whether an answer reads sensibly for a scenario.

The hard checks in harness.py verify *facts* (a tool ran, a memory landed). They
can't easily judge "does this answer actually make sense for a landing-page
carrier" - that's a quality call. So, when a scenario provides a one-line
``judge`` rubric, we ask the *same local model* to grade the final answer 1-5
against it.

This is deliberately a **soft, reported-only** signal: the judge is itself a
fuzzy model, so a low score is a flag to look at, not a build failure. Keeping it
non-gating avoids trading deterministic flakiness for judge flakiness. Runs at
temperature 0 for as much stability as a local model allows.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src import engine

log = logging.getLogger("mocca.eval")

_JUDGE_SYSTEM = (
    "You are a strict evaluator of an AI assistant's reply. You are given the "
    "user's message, the assistant's reply, and a rubric describing what a good "
    "reply must do. Score how well the reply satisfies the rubric from 1 (fails "
    "completely) to 5 (fully satisfies it). Judge only against the rubric, not "
    "your own preferences. Respond with ONLY a JSON object: "
    '{"score": <1-5>, "reason": "<one short sentence>"}.'
)


def _parse(raw: str) -> tuple[float | None, str]:
    """Pull a {score, reason} out of the judge's reply, tolerating stray prose."""
    text = (raw or "").strip()
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        text = text[a : b + 1]
    try:
        data = json.loads(text)
        score = float(data.get("score"))
        reason = str(data.get("reason", "")).strip()
        # Clamp to the 1-5 band in case the model overshoots.
        score = max(1.0, min(5.0, score))
        return score, reason
    except (json.JSONDecodeError, ValueError, TypeError):
        # Last resort: a bare number somewhere in the text.
        m = re.search(r"[1-5]", text)
        return (float(m.group()), "") if m else (None, "")


async def judge_answer(model: str, user: str, answer: str, rubric: str) -> tuple[float | None, str]:
    """Score one answer against a rubric. Returns (score 1-5 or None, reason)."""
    if not answer.strip():
        return None, "no answer to judge"
    user_msg = (
        f"User message:\n{user}\n\n"
        f"Assistant reply:\n{answer}\n\n"
        f"Rubric (what a good reply must do):\n{rubric}\n\n"
        "Score the reply against the rubric."
    )
    try:
        raw = await engine.complete(
            model,
            [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            options={"temperature": 0.0, "max_tokens": 128},
        )
    except Exception as exc:  # noqa: BLE001 - judging is best-effort, never fatal
        log.warning("Judge call failed: %s", exc)
        return None, f"judge error: {exc}"
    return _parse(raw)
