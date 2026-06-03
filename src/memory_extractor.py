"""Background extraction of durable user facts from a finished chat turn.

Why this exists: capturing memory by asking the chat model to call a ``remember``
tool mid-reply is unreliable - it depends on the model choosing to, and the
choice swings with phrasing ("my name is X" saved, "hey im X" didn't). Instead,
once a turn has finished, we run a *separate, focused* extraction pass whose only
job is to pull durable personal facts. Because it runs as a background task it
never delays the user's reply, and a small regex ``_fallback_candidates`` catches
the obvious facts (name, location, preference) even if the model returns nothing.

Design notes for Mocca:
  * **Reuses the loaded model** via ``engine.complete`` - no extra dependency and
    no second model. It serialises behind chat generation on the engine lock, so
    it runs while the model is otherwise idle (right after a reply).
  * **Gated** to run only when memory is enabled and the user's message looks
    personal (:func:`looks_personal`), to bound cost on CPU.
  * **Graceful degradation:** every failure is logged, never raised; the chat is
    unaffected. Dedup lives in ``database.add_memory`` (exact + fuzzy), so
    re-extracting the same fact is harmless.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import database, engine

log = logging.getLogger("mocca.memory")

# Any first-person reference -> the message is *about the user*, so it may carry
# a fact worth keeping. We only bother extracting when this matches, so ordinary
# question-answering turns don't trigger a background LLM call.
_PERSONAL = re.compile(r"\b(i|im|my|me|mine|myself)\b", re.IGNORECASE)

# Recent messages to feed the extractor: enough for context (e.g. "what's your
# name?" then "Martin"), few enough to stay cheap.
_CONTEXT_WINDOW = 6
# Allowed fact categories; anything else is normalised to "fact".
_CATEGORIES = {"identity", "preference", "fact", "location", "job", "goal"}

_EXTRACT_SYSTEM_PROMPT = (
    "You read a conversation and extract durable facts the USER revealed about "
    "themselves - things worth remembering in future, unrelated chats.\n\n"
    "Capture: the user's name (however casually they introduce it - 'hey im "
    "martin' means their name is Martin), where they live, their job, their "
    "skills or what they work with, family, long-term goals or projects, and "
    "standing preferences (what they like, dislike, or usually do).\n"
    "Ignore: what they asked about today, temporary moods or states ('I'm "
    "tired', 'I'm bored'), one-off tasks or requests, opinions on the current "
    "topic, and anything the ASSISTANT said.\n\n"
    "Output a JSON array of objects with \"text\" (one short third-person "
    "sentence) and \"category\" (one of: identity, preference, fact, location, "
    "job, goal). Return at most 2 - the most important. If nothing durable was "
    "revealed, return []. Output ONLY the JSON, no markdown or commentary.\n\n"
    "Example:\n"
    "Conversation:\n"
    "User: yo im sam, ive used rust for years\n"
    "Assistant: Nice to meet you, Sam!\n"
    "Output: [{\"text\": \"The user's name is Sam.\", \"category\": \"identity\"}, "
    "{\"text\": \"The user has used Rust for years.\", \"category\": \"fact\"}]"
)


def looks_personal(text: str) -> bool:
    """Whether a message is worth running extraction on (mentions the user)."""
    return bool(_PERSONAL.search(text or ""))


def _clean(value: str, max_len: int = 200) -> str:
    """Tidy an extracted fact; return "" to reject it."""
    value = re.sub(r"\s+", " ", value or "").strip()
    if len(value) < 4 or len(value) > max_len:
        return ""
    return value


def _parse_facts(raw: str) -> list[dict[str, str]]:
    """Parse the model's JSON array of facts, tolerating markdown fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        # Drop the opening fence line and the closing fence.
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    # Fall back to the outermost [...] if there's leading/trailing prose.
    if not text.startswith("["):
        a, b = text.find("["), text.rfind("]")
        if a >= 0 and b > a:
            text = text[a : b + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


# High-precision patterns so obvious facts are never lost when the model
# extraction misfires or returns []. The model is the primary extractor; these
# are a deterministic safety net for explicit statements. We avoid bare "i'm X"
# name patterns on purpose - they'd misfire on "i'm tired" - so casual-name
# capture is left to the model (with its example), and these cover the
# unambiguous phrasings.
_FALLBACK = [
    (re.compile(r"\bmy name is\s+([A-Za-z][\w' \-]{1,40})", re.I), "The user's name is {}.", "identity"),
    (re.compile(r"\bcall me\s+([A-Za-z][\w' \-]{1,40})", re.I), "The user likes to be called {}.", "identity"),
    (re.compile(r"\bi(?:'m| am)\s+from\s+([A-Za-z][\w' \-]{1,40})", re.I), "The user is from {}.", "location"),
    (re.compile(r"\bi live in\s+([A-Za-z][\w' \-]{1,40})", re.I), "The user lives in {}.", "location"),
    (re.compile(r"\bi work (?:as|at|in)\s+([A-Za-z][\w' \-]{2,50})", re.I), "The user works {}.", "job"),
    (re.compile(r"\bi(?:'ve| have)? ?(?:used|been using|worked with)\s+([A-Za-z][\w'+#. \-]{1,40})", re.I), "The user has experience with {}.", "fact"),
    (re.compile(r"\bi (?:really |absolutely |;)?(?:like|love|prefer|enjoy)\s+([A-Za-z][\w' \-]{2,50})", re.I), "The user likes {}.", "preference"),
]


def _fallback_candidates(user_text: str) -> list[dict[str, str]]:
    """Best-effort extraction of explicit facts via regex (no LLM)."""
    out: list[dict[str, str]] = []
    for pattern, template, category in _FALLBACK:
        m = pattern.search(user_text)
        if m:
            value = _clean(m.group(1).rstrip(" .,!?"), 50)
            if value:
                out.append({"text": template.format(value), "category": category})
    return out


def _recent_pairs(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The last few user/assistant turns (system rows dropped) for extraction."""
    convo = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return convo[-_CONTEXT_WINDOW:]


async def extract_and_store(model_name: str, messages: list[dict[str, Any]]) -> int:
    """Extract durable facts from recent messages and save them. Returns count.

    Built to run as a background task (``asyncio.create_task``); it swallows all
    errors so a failure never affects the chat.
    """
    try:
        recent = _recent_pairs(messages)
        if not recent:
            return 0
        last_user = next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")

        # Render the recent turns as plain text inside one analysis prompt. With
        # a small model this is far more reliable than replaying the messages as
        # roles (which it tends to "continue" rather than analyze, returning []).
        rendered = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
        user_msg = (
            f"Conversation:\n{rendered}\n\n"
            "Extract durable facts about the user as a JSON array (or [] if none)."
        )

        facts: list[dict[str, str]] = []
        try:
            raw = await engine.complete(
                model_name,
                [
                    {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                options={"temperature": 0.1, "max_tokens": 256},
            )
            facts = _parse_facts(raw)
        except Exception as exc:  # noqa: BLE001 - extraction is best-effort
            log.warning("Memory extraction LLM call failed: %s", exc)

        # Always fold in regex fallbacks for explicit statements, so a confused
        # extraction model can't drop an obvious "my name is ..." fact.
        facts = list(facts) + _fallback_candidates(last_user)
        if not facts:
            log.debug("Memory extraction: no candidates")
            return 0

        added = 0
        for fact in facts:
            if isinstance(fact, str):
                text, category = fact, "fact"
            elif isinstance(fact, dict):
                text, category = fact.get("text", ""), fact.get("category", "fact")
            else:
                continue
            text = _clean(text)
            if not text:
                continue
            category = category if category in _CATEGORIES else "fact"
            # add_memory dedups (exact + fuzzy); a returned existing row that
            # we didn't just create doesn't count as newly added.
            before = len(database.list_memories())
            stored = database.add_memory(text, category=category)
            if stored and len(database.list_memories()) > before:
                added += 1

        if added:
            log.info("Memory extraction stored %d fact(s)", added)
        return added
    except Exception as exc:  # noqa: BLE001 - never let a background task crash loudly
        log.error("Memory extraction failed: %s", exc)
        return 0
