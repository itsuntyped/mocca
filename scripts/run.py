"""Mocca entry point.

Run from the project root with:  python scripts/run.py
Then open: http://localhost:8000

This file lives in scripts/, so it first puts the project root on sys.path to
make the ``src`` package importable no matter where it's launched from. Boot
order matters: we set up logging and load config *before* importing the server
so the very first log lines (and any config-driven settings) are in place.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the project root importable (the dir that contains the ``src`` package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402 - must come after sys.path setup

from src import config  # noqa: E402
from src.logging_config import setup_logging  # noqa: E402
from src.paths import ensure_dirs  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Mocca - local AI chat")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    args = parser.parse_args()

    # 1. Make sure data/ exists, 2. load settings, 3. configure logging.
    ensure_dirs()
    settings = config.load()
    setup_logging(settings.log_level)

    log = logging.getLogger("mocca")
    log.info("Starting Mocca on http://%s:%d", args.host, args.port)

    # ``src.server:app`` is passed as an import string so --reload works.
    uvicorn.run(
        "src.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(PROJECT_ROOT / "src")] if args.reload else None,
        log_config=None,  # Use our logging setup, not uvicorn's default.
    )


if __name__ == "__main__":
    main()
