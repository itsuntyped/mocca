"""The eval harness: drive real chat turns through a real model and check them.

Unlike the deterministic unit suite under ``tests/`` (exact assertions on pure
code), this exercises the *model-driven* behaviour that only shows up with a real
LLM in the loop: did it pick the right tool, did it capture a durable fact, did
it relay a tool result sensibly. Those are the regressions a prompt/persona/
grammar change can silently introduce - the "im Martin wasn't saved" class of bug
- which no unit test can see.

Because a real model is non-deterministic and slow, the rules are different from
the unit suite:

  * **Assert on signals, not prose.** We check structured facts - which tool was
    called and with what args, whether a memory row landed, lenient keyword/regex
    on the answer - never an exact string, which would flake on every re-run.
  * **Run k-of-n.** Each scenario runs a few times; it passes if a majority of
    runs pass its hard checks, so a single unlucky sample doesn't fail the build.
  * **Hard vs soft.** Hard checks (tool called, memory saved) gate pass/fail. An
    optional LLM-as-judge score (see judge.py) is *reported only*, never gating,
    because the judge is itself fuzzy.

To stay faithful to production, a turn is run through the **real pipeline**: we
reuse the chat route's own ``_memory_block`` and engine roles, the real
``tool_loop.run``, and the real ``memory_extractor`` - the only difference from
the live route is that extraction is awaited (so we can assert on the result)
rather than fired into the background.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from src import config, database, memory_extractor, tool_loop

# Reuse the chat route's real recall block, engine-role filter, and open-file
# handling so the eval tests exactly what production does, with no drift.
from src.routes.chat import (
    _ENGINE_ROLES,
    _collapse_code_blocks,
    _memory_block,
    _open_file_block,
)

log = logging.getLogger("mocca.eval")


# --- Captured results -------------------------------------------------------

@dataclass
class Turn:
    """What one user turn produced: the answer plus any tool activity."""

    user: str
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)   # {"name", "arguments"}
    tool_results: list[dict[str, Any]] = field(default_factory=list)  # {"name", "result"}


@dataclass
class RunResult:
    """One full run of a scenario: every turn, plus the memories left behind."""

    turns: list[Turn]
    memories: list[dict[str, Any]]

    @property
    def last(self) -> Turn:
        return self.turns[-1]


# --- Checks (hard signals) --------------------------------------------------
# A Check is a labelled predicate over a RunResult. Its label is what the report
# prints when it fails, so it should read as the expectation ("calls calculator").

@dataclass
class Check:
    label: str
    fn: Callable[[RunResult], bool]

    def __call__(self, result: RunResult) -> bool:
        try:
            return bool(self.fn(result))
        except Exception:  # noqa: BLE001 - a check must never crash the run
            log.debug("Check %r raised", self.label, exc_info=True)
            return False


def _all_calls(result: RunResult) -> list[dict[str, Any]]:
    return [c for turn in result.turns for c in turn.tool_calls]


def called_tool(name: str) -> Check:
    """Some turn called the named tool."""
    return Check(f"calls {name}", lambda r: any(c["name"] == name for c in _all_calls(r)))


def no_tools() -> Check:
    """No tool was called in the whole conversation (routing restraint)."""
    return Check("calls no tools", lambda r: not _all_calls(r))


def tool_arg_contains(name: str, needle: str) -> Check:
    """A call to ``name`` passed an argument whose value contains ``needle``."""
    low = needle.lower()

    def fn(r: RunResult) -> bool:
        for c in _all_calls(r):
            if c["name"] != name:
                continue
            for value in (c.get("arguments") or {}).values():
                if low in str(value).lower():
                    return True
        return False

    return Check(f"{name} arg contains '{needle}'", fn)


def answer_contains(needle: str) -> Check:
    low = needle.lower()
    return Check(f"answer contains '{needle}'", lambda r: low in r.last.answer.lower())


def answer_lacks(needle: str) -> Check:
    low = needle.lower()
    return Check(f"answer omits '{needle}'", lambda r: low not in r.last.answer.lower())


def answer_matches(pattern: str) -> Check:
    rx = re.compile(pattern, re.IGNORECASE)
    return Check(f"answer matches /{pattern}/", lambda r: bool(rx.search(r.last.answer)))


def answer_not_matches(pattern: str) -> Check:
    """The answer must NOT match - for asserting the absence of a behaviour.

    Useful when the property under test is "it did not do X" (e.g. did not
    fabricate a name), which is far more robust than trying to enumerate every
    acceptable phrasing of the correct behaviour.
    """
    rx = re.compile(pattern, re.IGNORECASE)
    return Check(f"answer does not match /{pattern}/", lambda r: not rx.search(r.last.answer))


def memory_contains(needle: str) -> Check:
    """A memory row was stored whose text contains ``needle``."""
    low = needle.lower()
    return Check(
        f"memory contains '{needle}'",
        lambda r: any(low in m["content"].lower() for m in r.memories),
    )


def memory_category(cat: str) -> Check:
    return Check(
        f"memory of category '{cat}'",
        lambda r: any(m.get("category") == cat for m in r.memories),
    )


def memory_count_at_least(n: int) -> Check:
    return Check(f">= {n} memory row(s)", lambda r: len(r.memories) >= n)


# --- Scenario ---------------------------------------------------------------

@dataclass
class Scenario:
    """One eval case: user turns, hard checks, and an optional judge rubric.

    ``area`` groups scenarios ("memory" / "tools" / "answer") so a run can be
    filtered. ``seed_memories`` pre-populates the (sandboxed) memory table before
    the conversation, for recall tests. ``judge`` is a one-line rubric handed to
    the soft LLM judge (see judge.py); None skips judging for this scenario.
    """

    name: str
    area: str
    messages: list[str]
    checks: list[Check]
    judge: str | None = None
    seed_memories: list[tuple[str, str]] = field(default_factory=list)
    # Parallel to ``messages``: the file the user has open in the editor for that
    # turn, as ``(title, content)`` - or None (the default for any turn without an
    # entry). Drives the same open-file context the live route injects, so the
    # artifact-editing behaviour can be evaluated end to end.
    open_files: list[tuple[str, str] | None] = field(default_factory=list)


# --- Running a scenario -----------------------------------------------------

async def run_once(model: str, scenario: Scenario, *, temperature: float) -> RunResult:
    """Run a scenario one time through the real pipeline and capture the result.

    Mirrors ``routes/chat.py`` turn-for-turn: persist the user message, build
    ``[system(+memory block), ...history]``, run the tool loop, save the reply,
    then run memory extraction (awaited here, not backgrounded). Each run starts
    from a clean memory table with only ``seed_memories`` present, so memory
    assertions are isolated from other scenarios.
    """
    settings = config.get()

    # Fresh memory state for this run.
    database.clear_memories()
    for text, category in scenario.seed_memories:
        database.add_memory(text, category=category)

    session = database.create_session(title=f"eval:{scenario.name}", model=model)
    sid = session["id"]
    turns: list[Turn] = []

    for idx, user_text in enumerate(scenario.messages):
        database.add_message(sid, "user", user_text)
        database.set_session_model(sid, model)

        history = [m for m in database.get_messages(sid) if m["role"] in _ENGINE_ROLES]
        system_text = settings.system_prompt.strip()
        if settings.enable_memory:
            block = _memory_block(database.list_memories())
            system_text = f"{system_text}\n\n{block}" if system_text else block

        # Open-file context for this turn, mirroring routes/chat.py exactly: inject
        # the file as authoritative and strip stale full-file copies from history.
        open_file = scenario.open_files[idx] if idx < len(scenario.open_files) else None
        editing_file = bool(open_file and open_file[1].strip())
        if editing_file:
            system_text += "\n\n" + _open_file_block(open_file[0], open_file[1])

        messages: list[dict[str, str]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        if editing_file:
            for m in history:
                if m["role"] == "assistant":
                    messages.append({**m, "content": _collapse_code_blocks(m["content"])})
                else:
                    messages.append(m)
        else:
            messages.extend(history)

        options = {
            "temperature": temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_tokens,
        }

        collected: list[str] = []
        calls: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        pending: dict[str, dict] = {}
        async for event in tool_loop.run(model, messages, options=options):
            if "chunk" in event:
                collected.append(event["chunk"])
            elif "tool_call" in event:
                call = event["tool_call"]
                pending[call["id"]] = call
                calls.append({"name": call["name"], "arguments": call.get("arguments", {})})
            elif "tool_result" in event:
                res = event["tool_result"]
                results.append({"name": res["name"], "result": res["result"]})

        reply = "".join(collected)
        if reply:
            database.add_message(sid, "assistant", reply)

        # Extraction: same gate as production, but awaited so we can assert on it.
        if settings.enable_memory and memory_extractor.looks_personal(user_text):
            convo = database.get_messages(sid)
            await memory_extractor.extract_and_store(model, convo)

        turns.append(Turn(user=user_text, answer=reply, tool_calls=calls, tool_results=results))

    return RunResult(turns=turns, memories=database.list_memories())


@dataclass
class ScenarioResult:
    """The aggregate verdict for a scenario across its runs."""

    scenario: Scenario
    runs: int
    passed_runs: int
    # The hard checks that failed at least once, with how many runs each failed.
    failures: dict[str, int]
    judge_score: float | None = None
    judge_reason: str = ""
    last: RunResult | None = None

    @property
    def passed(self) -> bool:
        # Majority rule: a scenario passes if most runs passed all hard checks.
        return self.passed_runs >= math.ceil(self.runs * _PASS_RATIO)


# A scenario passes if at least this fraction of its runs pass every hard check.
_PASS_RATIO = 0.6


async def run_scenario(
    model: str, scenario: Scenario, *, runs: int, temperature: float
) -> ScenarioResult:
    """Run a scenario ``runs`` times and aggregate hard-check results."""
    passed_runs = 0
    failures: dict[str, int] = {}
    last: RunResult | None = None

    for i in range(runs):
        result = await run_once(model, scenario, temperature=temperature)
        last = result
        run_ok = True
        for check in scenario.checks:
            if not check(result):
                run_ok = False
                failures[check.label] = failures.get(check.label, 0) + 1
        if run_ok:
            passed_runs += 1
        log.info("Scenario %s run %d/%d: %s", scenario.name, i + 1, runs,
                 "pass" if run_ok else "fail")

    return ScenarioResult(
        scenario=scenario, runs=runs, passed_runs=passed_runs,
        failures=failures, last=last,
    )
