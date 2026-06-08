"""Model-based tool routing: pick which tool categories a message needs.

This is the *primary* router for a chat turn - it decides which tool categories
to hand the decision step (``engine.decide``), so the model only wades through
the schemas it might actually need. That narrowing matters because the verbose
tool schemas dominate prompt-processing latency on CPU (~2.4s each), so offering
fewer is a real speed-up.

It replaces the older keyword/regex signal-gating
(``registry.relevant_categories``) as the default. The job is the same; the
method is smarter. Instead of matching specific words - which misses any phrasing
without a signal word (e.g. "who runs France now?" needs the web but contains no
"search"/"look up") - we ask the model itself, given a compact menu of category
names plus one-line descriptions. Crucially the menu is *not* the full schemas:
it is a fraction of their size, so this extra routing pass stays cheap.

Graceful degradation, as everywhere in Mocca: when the engine isn't available or
the router's reply can't be parsed, we fall back to the keyword router, so a tool
is never hidden because one LLM call hiccuped. An explicit empty selection (the
model deciding "no tools") is honoured, not treated as a failure - that is how a
greeting correctly gets no tools.
"""

from __future__ import annotations

import json
import logging
import re

from . import engine
from .tools import registry

log = logging.getLogger("mocca.toolrouter")

# Routing is a classification ("which categories, if any?"), not a creative task,
# so we run it near-deterministically and cap the output hard - a JSON array of a
# few short names needs very few tokens. (engine.complete forwards max_tokens only
# when > 0, and temperature only when not None, so 0.0 is passed through.)
_ROUTER_TEMPERATURE = 0.0
_ROUTER_MAX_TOKENS = 64

# The router system prompt. It leads with the menu (built per turn from the
# enabled categories) and demands a bare JSON array back, so parsing is trivial
# and the model can't wander into a chatty reply. {menu} is filled by _menu().
#
# The critical instruction is to route by the *nature* of the request, NOT by
# whether the model thinks it already knows the answer. An earlier version said
# "reply [] for anything you can answer yourself"; a small model is overconfident
# about stale facts, so it returned [] for "who is the current UN secretary-
# general?" - believing it knew - and never searched, defeating web search. We
# instead tell it its built-in knowledge may be out of date and to prefer web for
# any current/real-world fact, while still keeping greetings and chit-chat tool-
# free.
_ROUTER_INSTRUCTIONS = (
    "You route a user's message to the capabilities needed to answer it well.\n\n"
    "Available categories:\n{menu}\n\n"
    "How to choose:\n"
    "- Your own built-in knowledge may be OUT OF DATE. For any question about "
    "real-world facts - a current office-holder or leader, the latest or newest "
    "of something, recent events, prices, specific people, companies, products, "
    "or places - choose web, EVEN IF you think you already know the answer.\n"
    "- Choose web whenever the message contains a link or URL, or asks you to "
    "open, visit, read, or check a page (the page must actually be fetched).\n"
    "- Choose math for arithmetic or unit conversions, time for the current date "
    "or time, files to read the user's saved files, youtube for a YouTube video, "
    "weather for weather, shipping to track a parcel.\n"
    "- Choose nothing (reply []) only for greetings, small talk, opinions, "
    "creative writing, or timeless general concepts you can explain without "
    "looking anything up.\n\n"
    "Reply with ONLY a JSON array of category names from the list above, for "
    'example ["web"], ["math"], or []. Output nothing before or after the array.'
)


def _menu(categories: list[str]) -> str:
    """Build the '- name: description' menu lines for the router prompt."""
    desc = registry.category_descriptions(categories)
    return "\n".join(f"- {cat}: {desc[cat]}" for cat in sorted(desc))


# Matches the first JSON array in the model's output, so a stray leading or
# trailing sentence (some models add one despite the instruction) doesn't break
# parsing. Categories never contain "]", so a non-greedy character class is safe.
_ARRAY_RE = re.compile(r"\[[^\]]*\]")


def _parse(text: str, valid: set[str]) -> list[str] | None:
    """Parse the router's reply into the selected, valid category names.

    Returns the selected categories (de-duplicated, intersected with ``valid``),
    an empty list when the model explicitly chose none, or ``None`` when the
    output couldn't be parsed at all. The distinction matters: ``[]`` is a real
    "no tools" decision and must be honoured, while ``None`` is the signal to fall
    back to keyword routing.
    """
    match = _ARRAY_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    # Keep only known, enabled categories; compare case-insensitively and dedupe
    # (a small model may echo "Web" or repeat a name).
    by_lower = {c.lower(): c for c in valid}
    selected = {
        by_lower[item.lower()]
        for item in data
        if isinstance(item, str) and item.lower() in by_lower
    }
    return sorted(selected)


def _ensure_url_web(categories: list[str], user_text: str, active: list[str]) -> list[str]:
    """Guarantee a pasted URL gets the web category, whatever the model decided.

    A bare link ("look at this: <url>", or even "visit the url <url>") is an
    unambiguous "read this page" request, but a small model routing only on the
    message text often fails to pick web - so fetch_url was never offered and the
    model answered the page from imagination. The keyword router has always forced
    web for a URL (registry.url_needs_web); the model router lost that guarantee
    when it became the primary path, so we reinstate it deterministically here.
    Only applies when web is actually enabled.
    """
    if "web" in active and "web" not in categories and registry.url_needs_web(user_text):
        log.debug("Message contains a URL; forcing 'web' category into scope")
        return sorted({*categories, "web"})
    return categories


async def choose_categories(
    model_name: str,
    user_text: str,
    active_categories: list[str],
) -> list[str]:
    """Pick the active categories the user's message needs (model-routed).

    ``active_categories`` is the set the user has enabled (all local categories,
    plus the network ones when web search is on). We ask the model which of those
    the latest message needs and return that subset. Falls back to the keyword
    router (``registry.relevant_categories``) when the engine is unavailable or
    the reply can't be parsed. A pasted URL always forces web in (see
    ``_ensure_url_web``), regardless of the model's choice.
    """
    active = list(active_categories)
    if not active or not engine.is_available():
        # Nothing to route, or no engine to ask - the keyword router handles both
        # (and returns [] for an empty/greeting case), so behaviour is unchanged
        # from the pre-router build when the engine isn't installed.
        return _ensure_url_web(registry.relevant_categories(user_text, active), user_text, active)

    convo = [
        {"role": "system", "content": _ROUTER_INSTRUCTIONS.format(menu=_menu(active))},
        {"role": "user", "content": user_text},
    ]
    opts = {"temperature": _ROUTER_TEMPERATURE, "max_tokens": _ROUTER_MAX_TOKENS}
    try:
        reply = await engine.complete(model_name, convo, options=opts)
    except Exception as exc:  # noqa: BLE001 - any engine failure -> keyword fallback
        log.warning("Tool router failed (%s); falling back to keyword routing", exc)
        return _ensure_url_web(registry.relevant_categories(user_text, active), user_text, active)

    selected = _parse(reply, set(active))
    if selected is None:
        log.debug("Router reply unparseable (%r); falling back to keyword routing", reply)
        return _ensure_url_web(registry.relevant_categories(user_text, active), user_text, active)
    log.debug("Router selected categories: %s", selected)
    return _ensure_url_web(selected, user_text, active)
