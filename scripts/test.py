"""Mocca test runner: run the offline tool/unit test suite.

Run from the project root with:  python scripts/test.py
Pass -v / -q to change verbosity, or a name to run a subset:
    python scripts/test.py                 # everything
    python scripts/test.py calculator      # only tests whose module/name matches
    python scripts/test.py -q              # quieter output

Like scripts/run.py, this first puts the project root on sys.path so the ``src``
package imports cleanly no matter where it's launched from. Crucially it also
points ``MOCCA_DATA_DIR`` at a throwaway temp directory *before* any ``src``
import, so the suite (the file tools especially) never touches the user's real
``data/`` - paths.py reads that env var at import time (see paths.py).

The suite is deliberately **offline and deterministic**: network tools are
exercised against fake HTTP clients (see tests/helpers.py), not the live
internet, so the tests are fast, repeatable, and safe to run anywhere. The goal
is to test each tool's logic in depth across many inputs - happy paths, edge
cases, and the errors a confused model can trigger.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable (the dir that contains ``src`` and ``tests``).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Redirect all runtime data to a fresh temp dir BEFORE importing anything from
# ``src`` - paths.py captures MOCCA_DATA_DIR at import time, so this guarantees
# the file tools read/write a sandbox and the real data/ is left untouched.
_TMP_DATA = tempfile.mkdtemp(prefix="mocca-test-data-")
os.environ["MOCCA_DATA_DIR"] = _TMP_DATA


def main() -> None:
    # Anything after the script name that isn't a flag is treated as a name
    # filter (substring match against the dotted test id), so you can run just
    # one tool's tests: ``python scripts/test.py shipping``.
    argv = sys.argv[1:]
    verbosity = 2
    names: list[str] = []
    for arg in argv:
        if arg in ("-q", "--quiet"):
            verbosity = 1
        elif arg in ("-v", "--verbose"):
            verbosity = 2
        elif not arg.startswith("-"):
            names.append(arg.lower())

    tests_dir = PROJECT_ROOT / "tests"
    loader = unittest.TestLoader()
    # top_level_dir == start_dir keeps the test modules importable by their bare
    # filename (so tests/helpers.py is just ``import helpers``) without needing an
    # __init__.py - matching the project's no-__init__ namespace-package style.
    suite = loader.discover(str(tests_dir), pattern="test_*.py", top_level_dir=str(tests_dir))

    if names:
        # Keep only tests whose dotted id contains one of the requested names.
        def matches(test: unittest.TestCase) -> bool:
            tid = test.id().lower()
            return any(name in tid for name in names)

        filtered = unittest.TestSuite(t for t in _iter_tests(suite) if matches(t))
        suite = filtered

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    # Clean up the throwaway data dir; never leave temp folders behind.
    shutil.rmtree(_TMP_DATA, ignore_errors=True)
    sys.exit(0 if result.wasSuccessful() else 1)


def _iter_tests(suite: unittest.TestSuite):
    """Flatten a (possibly nested) TestSuite into individual test cases."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


if __name__ == "__main__":
    main()
