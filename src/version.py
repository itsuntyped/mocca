"""Single source of truth for Mocca's version.

The canonical version string lives in the top-level ``VERSION`` file, so the app,
the Windows build, and CI all read the same value. Bump it with
``scripts/bump_version.py`` (which can also tag the release).

We read the file with a safe fallback, and look inside the PyInstaller bundle
directory when frozen (the spec ships ``VERSION`` there).
"""

from __future__ import annotations

import sys
from pathlib import Path

# In a normal run this is the project root; in a PyInstaller build it's the
# extracted bundle dir (sys._MEIPASS), where the spec places VERSION.
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
_VERSION_FILE = _BASE / "VERSION"


def get_version() -> str:
    """Return the version string from the VERSION file, or a safe fallback."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = get_version()
