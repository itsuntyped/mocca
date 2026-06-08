"""Background extraction (and pruning) of durable user facts from a chat turn.

Why this exists: capturing memory by asking the chat model to call a ``remember``
tool mid-reply is unreliable - it depends on the model choosing to, and the
choice swings with phrasing ("my name is X" saved, "hey im X" didn't). Instead,
once a turn has finished, we run a *separate, focused* extraction pass whose only
job is to pull durable personal facts. Because it runs as a background task it
never delays the user's reply, and a small regex ``_fallback_candidates`` catches
the obvious facts (name, location, preference) even if the model returns nothing.

A mirror pass, :func:`prune_stale_memories`, runs on the same gate to *forget*
facts a turn made obsolete - ones the user contradicted, replaced, or explicitly
asked us to drop (loved Elixir last week, prefers React now). It is deliberately
conservative: forgetting is irreversible, so "when unsure, keep it".

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
# question-answering turns don't trigger a background LLM call. Includes the
# apostrophe-less casual contractions ("im", "ive") Mocca deliberately supports -
# without "ive", a casual "ive worked with Rust" would skip extraction entirely.
_PERSONAL = re.compile(r"\b(i|im|ive|my|me|mine|myself)\b", re.IGNORECASE)

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
    "Example 1 (durable facts):\n"
    "Conversation:\n"
    "User: yo im sam, ive used rust for years\n"
    "Assistant: Nice to meet you, Sam!\n"
    "Output: [{\"text\": \"The user's name is Sam.\", \"category\": \"identity\"}, "
    "{\"text\": \"The user has used Rust for years.\", \"category\": \"fact\"}]\n\n"
    "Example 2 (a one-off task or edit request reveals nothing durable - return "
    "[], do NOT record what they asked you to do):\n"
    "Conversation:\n"
    "User: I changed the introduction, can you add a quick start section to the README?\n"
    "Assistant: Sure! Here is the updated README...\n"
    "Output: []"
)


_PRUNE_SYSTEM_PROMPT = (
    "You maintain a list of remembered facts about a USER. Given the recent "
    "conversation and the current list of facts, decide which facts are now "
    "WRONG and should be forgotten.\n\n"
    "Forget a fact when the user:\n"
    "  - explicitly asks to forget or delete it ('forget that I like X', "
    "'you can drop my name').\n"
    "  - says it is no longer true (moved city, changed jobs, stopped doing X).\n"
    "  - states a preference that REPLACES it ('im not into Elixir anymore, "
    "ive switched to React' contradicts 'the user loves Elixir' - forget it).\n\n"
    "Do NOT forget a fact just because it wasn't mentioned this turn, and do NOT "
    "forget one merely because the user added a new, compatible fact - liking "
    "React does not by itself contradict liking Elixir; only an explicit 'not "
    "anymore' / 'instead of' / 'used to but now' does. When unsure, keep it.\n\n"
    "The facts are a numbered list. Output a JSON array of the NUMBERS to forget "
    "(e.g. [2]). If none should be forgotten, return []. Output ONLY the JSON, no "
    "markdown or commentary.\n\n"
    "Example 1 (a preference was replaced):\n"
    "Remembered facts:\n"
    "1. The user loves the Elixir programming language.\n"
    "2. The user's name is Sam.\n"
    "Conversation:\n"
    "User: honestly im not into elixir anymore, ive switched to react and prefer it now\n"
    "Assistant: Got it - React it is!\n"
    "Output: [1]\n\n"
    "Example 2 (a new, compatible fact - forget nothing):\n"
    "Remembered facts:\n"
    "1. The user likes hiking.\n"
    "Conversation:\n"
    "User: i also really enjoy bouldering\n"
    "Assistant: Nice!\n"
    "Output: []"
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
    (re.compile(r"\bi(?:'?m| am)\s+from\s+([A-Za-z][\w' \-]{1,40})", re.I), "The user is from {}.", "location"),
    (re.compile(r"\bi live in\s+([A-Za-z][\w' \-]{1,40})", re.I), "The user lives in {}.", "location"),
    (re.compile(r"\bi work (?:as|at|in)\s+([A-Za-z][\w' \-]{2,50})", re.I), "The user works {}.", "job"),
    # Accept the apostrophe-less casual forms too ("ive used", "im from"): Mocca's
    # memory deliberately targets casual phrasing, and the LLM extractor is
    # inconsistent on it, so the deterministic net must catch it.
    (re.compile(r"\bi(?:'?ve| have)? ?(?:used|been using|worked with)\s+([A-Za-z][\w'+#. \-]{1,40})", re.I), "The user has experience with {}.", "fact"),
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


def _render(recent: list[dict[str, str]]) -> str:
    """Render recent turns as plain "Role: text" lines for an analysis prompt.

    Plain text beats replaying the turns as roles: a small model tends to
    *continue* a role-shaped conversation rather than analyze it.
    """
    return "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)


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

        # Render the recent turns as plain text inside one analysis prompt (see
        # _render for why plain text beats replaying roles to a small model).
        rendered = _render(recent)
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


async def prune_stale_memories(model_name: str, messages: list[dict[str, Any]]) -> int:
    """Forget stored facts the latest turn made obsolete. Returns count removed.

    The mirror image of :func:`extract_and_store`: a separate, focused pass that
    looks for facts the user has contradicted, replaced, or asked us to drop -
    e.g. they once "loved Elixir" but now say they've moved to React and aren't
    interested anymore, so the Elixir fact should go. Runs as a background task
    on the same gate as extraction and swallows all errors, so a failure never
    touches the chat. Conservative by design (the prompt says "when unsure,
    keep it"): forgetting a fact is irreversible, so a false positive is worse
    than a missed prune, which the next contradicting turn can still catch.
    """
    try:
        memories = database.list_memories()
        if not memories:
            return 0  # Nothing to forget - skip the LLM call entirely.
        recent = _recent_pairs(messages)
        if not recent:
            return 0

        listing = "\n".join(f"{i}. {m['content']}" for i, m in enumerate(memories, 1))
        user_msg = (
            f"Remembered facts:\n{listing}\n\n"
            f"Conversation:\n{_render(recent)}\n\n"
            "Which fact numbers should be forgotten? JSON array (or [] if none)."
        )

        try:
            raw = await engine.complete(
                model_name,
                [
                    {"role": "system", "content": _PRUNE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                options={"temperature": 0.1, "max_tokens": 64},
            )
        except Exception as exc:  # noqa: BLE001 - pruning is best-effort
            log.warning("Memory prune LLM call failed: %s", exc)
            return 0

        # Reuse the JSON-array parser; here the elements are 1-based fact numbers.
        numbers = _parse_facts(raw)
        removed = 0
        seen: set[int] = set()
        for n in numbers:
            # Guard against bools (a subclass of int) and stray non-numeric junk.
            if isinstance(n, bool):
                continue
            try:
                idx = int(n) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(memories) and idx not in seen:
                seen.add(idx)
                if database.delete_memory(memories[idx]["id"]):
                    removed += 1

        if removed:
            log.info("Memory prune removed %d stale fact(s)", removed)
        return removed
    except Exception as exc:  # noqa: BLE001 - never let a background task crash loudly
        log.error("Memory prune failed: %s", exc)
        return 0
