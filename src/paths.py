"""Filesystem locations used across Mocca.

Everything Mocca writes (database, logs, config) lives under a single ``data/``
directory at the project root. Keeping the paths in one module means the rest
of the codebase never hard-codes a location.

Set the ``MOCCA_DATA_DIR`` environment variable to store everything somewhere
else (handy for keeping data off the project tree, or for tests that need a
throwaway directory without touching the real one).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root = the directory that contains the ``src`` package.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Where the bundled assets (static/, templates/) live. In a normal run that's
# the project root; in a PyInstaller build they're extracted under sys._MEIPASS.
_ASSET_ROOT: Path = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))

# Static assets and HTML templates shipped with the app.
STATIC_DIR: Path = _ASSET_ROOT / "static"
TEMPLATES_DIR: Path = _ASSET_ROOT / "templates"

# Writable runtime data. Created on startup if missing (see ensure_dirs()).
# Honour MOCCA_DATA_DIR if set, else default to <project root>/data.
DATA_DIR: Path = (
    Path(os.environ["MOCCA_DATA_DIR"]).expanduser().resolve()
    if os.environ.get("MOCCA_DATA_DIR")
    else PROJECT_ROOT / "data"
)
LOG_DIR: Path = DATA_DIR / "logs"
MODELS_DIR: Path = DATA_DIR / "models"  # Downloaded .gguf model files live here.
# Drop folder for documents the user wants the AI to read via the file tools.
# Kept separate from the rest of data/ so those tools can never reach the
# database, config, logs, or model files.
FILES_DIR: Path = DATA_DIR / "files"

CONFIG_FILE: Path = DATA_DIR / "config.json"
DB_FILE: Path = DATA_DIR / "mocca.db"
LOG_FILE: Path = LOG_DIR / "mocca.log"


def ensure_dirs() -> None:
    """Create the writable directories Mocca needs, if they don't exist yet.

    Safe to call repeatedly; ``mkdir(exist_ok=True)`` is idempotent.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
