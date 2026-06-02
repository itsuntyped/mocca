"""Logging setup for Mocca.

Goal: make debugging painless. Every run writes a rotating log file under
``data/logs/mocca.log`` *and* echoes to the console. The log level is taken
from the user's config (default INFO; flip to DEBUG in Settings or via the
``MOCCA_LOG_LEVEL`` environment variable to see request/Ollama traffic).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .paths import LOG_FILE, ensure_dirs

# A compact but informative line format: time, level, logger name, message.
_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Keep ~5 MB per file, 3 backups - plenty for local debugging, never unbounded.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def setup_logging(level: str | int = "INFO") -> None:
    """Configure the root logger with console + rotating-file handlers.

    Called once at startup. The ``MOCCA_LOG_LEVEL`` env var, if set, overrides
    the passed-in level so you can crank up verbosity without editing config.
    """
    ensure_dirs()

    level = os.environ.get("MOCCA_LOG_LEVEL", level)
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid stacking duplicate handlers if this is somehow called twice
    # (e.g. uvicorn's reloader importing the module again).
    if root.handlers:
        root.handlers.clear()

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Uvicorn's access log is noisy at INFO; nudge it to WARNING unless we're
    # actively debugging, so the file stays readable.
    if level > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.getLogger("mocca").info("Logging initialised at level %s", logging.getLevelName(level))
