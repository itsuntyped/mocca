"""Mocca model-eval runner: exercise tool/memory/answer behaviour with a real LLM.

This is the *opt-in, slow* companion to ``scripts/test.py``. Where that suite is
fast, deterministic, and must always pass, this one loads a real GGUF model and
drives full chat turns through the real pipeline to catch model-driven
regressions - a tool not selected, a fact not captured ("im Martin"), a tool
result relayed nonsensically - that unit tests cannot see. It is never part of
the default test run.

Usage (from the project root):

    python scripts/eval.py                 # run all scenarios once (needs the model)
    python scripts/eval.py --download      # download the eval model first if missing
    python scripts/eval.py --runs 3        # k-of-n: run each scenario 3x (steadier)
    python scripts/eval.py memory          # only scenarios whose name/area matches
    python scripts/eval.py --list          # list scenarios, run nothing
    python scripts/eval.py --no-judge      # skip the soft LLM-as-judge scoring

The eval is fully sandboxed: MOCCA_DATA_DIR is pointed at a git-ignored
``evals/.sandbox`` (its own config, database, and a dedicated, git-ignored copy
of the model) so it never touches the user's real ``data/`` - their chats,
settings, and downloaded models are untouched.

Exit codes: 0 = all scenarios passed, 1 = at least one hard failure,
2 = could not run (engine not installed, or the model isn't present and
``--download`` wasn't given).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Project root on sys.path (the dir holding ``src`` and ``evals``).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Sandbox everything BEFORE importing src, so paths.py routes the database,
# config, and models into evals/.sandbox and the real data/ is never touched.
_SANDBOX = PROJECT_ROOT / "evals" / ".sandbox"
os.environ["MOCCA_DATA_DIR"] = str(_SANDBOX)

# The eval model: a dedicated, git-ignored copy lives under the sandbox's
# models/ dir. Qwen2.5-7B-Instruct is a solid, tool-capable local default.
_MODEL_FILENAME = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
_MODEL_REPO = "bartowski/Qwen2.5-7B-Instruct-GGUF"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mocca model-eval runner")
    p.add_argument("filters", nargs="*",
                   help="Only run scenarios whose name or area contains one of these.")
    p.add_argument("--runs", type=int, default=1,
                   help="Run each scenario N times (k-of-n majority). Default 1.")
    p.add_argument("--temp", type=float, default=0.3,
                   help="Answer sampling temperature (lower = steadier). Default 0.3.")
    p.add_argument("--gpu-layers", type=int, default=None,
                   help="Override n_gpu_layers (e.g. 99 to fully offload on a CUDA build).")
    p.add_argument("--ctx", type=int, default=None, help="Override n_ctx (context window).")
    p.add_argument("--model", default=_MODEL_FILENAME, help="Model filename (.gguf) to use.")
    p.add_argument("--repo", default=_MODEL_REPO, help="Hugging Face repo to download from.")
    p.add_argument("--download", action="store_true",
                   help="Download the model into the sandbox if it isn't present.")
    p.add_argument("--no-judge", dest="judge", action="store_false",
                   help="Skip the soft LLM-as-judge quality scoring.")
    p.add_argument("--list", action="store_true", help="List scenarios and exit.")
    return p.parse_args()


def _select(scenarios, filters):
    """Keep scenarios whose name or area contains any filter term (or all)."""
    if not filters:
        return list(scenarios)
    low = [f.lower() for f in filters]
    return [s for s in scenarios
            if any(f in s.name.lower() or f in s.area.lower() for f in low)]


async def _ensure_model(args) -> bool:
    """Make sure the model file is present, downloading it if asked. False = skip."""
    from src.models import download_model, model_path

    path = model_path(args.model)
    if path.exists():
        return True
    if not args.download:
        print(f"\nModel not found: {path}")
        print("This eval needs a real model. Re-run with --download to fetch it")
        print(f"(~4.7 GB from {args.repo}), or place the .gguf there yourself.\n")
        return False

    print(f"Downloading {args.model} from {args.repo} (this is a few GB)...")
    last_pct = -1
    async for ev in download_model(args.repo, args.model):
        total, done = ev.get("total") or 0, ev.get("completed") or 0
        if total:
            pct = int(done * 100 / total)
            if pct != last_pct and pct % 5 == 0:  # Log every 5% to avoid spam.
                print(f"  {pct}% ({done // (1024*1024)} / {total // (1024*1024)} MiB)")
                last_pct = pct
        if ev.get("done"):
            print("  download complete.")
    return path.exists()


def _print_report(results) -> int:
    """Print the per-scenario report and return the process exit code."""
    print("\n" + "=" * 70)
    print("EVAL RESULTS")
    print("=" * 70)

    hard_failures = 0
    by_area: dict[str, list] = {}
    for r in results:
        by_area.setdefault(r.scenario.area, []).append(r)

    for area in sorted(by_area):
        print(f"\n[{area}]")
        for r in by_area[area]:
            status = "PASS" if r.passed else "FAIL"
            line = f"  {status}  {r.scenario.name}  ({r.passed_runs}/{r.runs} runs)"
            if r.judge_score is not None:
                line += f"  judge={r.judge_score:.0f}/5"
            print(line)
            if not r.passed:
                hard_failures += 1
            # Show which hard checks failed, and the judge's reason when weak.
            for label, count in sorted(r.failures.items()):
                print(f"         - failed: {label}  ({count}/{r.runs} run(s))")
            if r.judge_score is not None and r.judge_score <= 3 and r.judge_reason:
                print(f"         - judge: {r.judge_reason}")

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print("\n" + "-" * 70)
    print(f"{passed}/{total} scenarios passed"
          + (f", {hard_failures} failed" if hard_failures else ""))
    print("-" * 70)
    return 1 if hard_failures else 0


async def _run(args) -> int:
    from src import config, database, engine
    from src.paths import ensure_dirs
    from src.tools import registry
    from evals import judge as judge_mod
    from evals.harness import run_scenario
    from evals.scenarios import SCENARIOS

    selected = _select(SCENARIOS, args.filters)
    if args.list:
        print("Scenarios:")
        for s in selected:
            print(f"  [{s.area:7}] {s.name}  ({len(s.messages)} turn(s))")
        return 0
    if not selected:
        print("No scenarios match the given filter(s).")
        return 2

    # The engine is required - this whole runner is about real-model behaviour.
    if not engine.is_available():
        print("\nThe inference engine (llama-cpp-python) is not installed, so the")
        print("model eval cannot run. Install it (see requirements.txt) and retry.\n")
        return 2

    ensure_dirs()
    database.init_db()  # Create the sandbox DB schema (the server does this at startup).
    config.load()  # Creates the sandbox config (picks GPU layers per build).
    patch = {"default_model": args.model, "temperature": args.temp,
             "enable_web_search": True, "enable_memory": True}
    if args.gpu_layers is not None:
        patch["n_gpu_layers"] = args.gpu_layers
    if args.ctx is not None:
        patch["n_ctx"] = args.ctx
    config.update(patch)

    if not await _ensure_model(args):
        return 2

    registry.discover()
    print(f"\nRunning {len(selected)} scenario(s) x {args.runs} run(s) with {args.model}")
    print("(first turn loads the model into memory; that can take a moment)\n")

    results = []
    for scenario in selected:
        print(f"-> {scenario.name} ...", flush=True)
        result = await run_scenario(args.model, scenario, runs=args.runs, temperature=args.temp)
        # Soft judge on the last run's final answer, when a rubric is set. The
        # score is reported, never gating (see judge.py).
        if args.judge and scenario.judge and result.last is not None:
            score, reason = await judge_mod.judge_answer(
                args.model, scenario.messages[-1], result.last.last.answer, scenario.judge,
            )
            result.judge_score, result.judge_reason = score, reason
        results.append(result)

    # Release the model so its file isn't left locked (Windows mmaps the GGUF).
    engine.unload()
    return _print_report(results)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("MOCCA_LOG_LEVEL", "WARNING"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
