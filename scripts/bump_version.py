"""Bump Mocca's version and (optionally) tag the release.

The version lives in the top-level ``VERSION`` file - the single source of truth
the app and the build read. Releases are tag-driven: pushing a ``vX.Y.Z`` tag
triggers the GitHub release workflow, which builds the Windows CPU and CUDA apps
and publishes them as a GitHub Release.

Usage (from the project root):

    python scripts/bump_version.py patch          # 0.0.1 -> 0.0.2
    python scripts/bump_version.py minor          # 0.0.1 -> 0.1.0
    python scripts/bump_version.py major          # 0.0.1 -> 1.0.0
    python scripts/bump_version.py 1.2.3          # set an exact version
    python scripts/bump_version.py patch --tag    # also: git commit + tag vX.Y.Z
    python scripts/bump_version.py patch --tag --push   # ...and push (starts the release)

Without ``--tag`` it only edits the VERSION file. ``--push`` is the only step
that talks to the remote, and it's what kicks off a public release.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _ROOT / "VERSION"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _read_version() -> tuple[int, int, int]:
    raw = _VERSION_FILE.read_text(encoding="utf-8").strip()
    if not _SEMVER.match(raw):
        sys.exit(f"VERSION holds an unexpected value: {raw!r} (want X.Y.Z)")
    major, minor, patch = (int(p) for p in raw.split("."))
    return major, minor, patch


def _next_version(current: tuple[int, int, int], bump: str) -> tuple[int, int, int]:
    major, minor, patch = current
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    if bump == "patch":
        return major, minor, patch + 1
    if _SEMVER.match(bump):
        a, b, c = (int(p) for p in bump.split("."))
        return a, b, c
    sys.exit(f"Unknown bump '{bump}' - use major | minor | patch | X.Y.Z")


def _git(*args: str) -> None:
    print("+ git", *args)
    subprocess.check_call(["git", *args], cwd=_ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump Mocca's version.")
    parser.add_argument("bump", help="major | minor | patch | X.Y.Z")
    parser.add_argument("--tag", action="store_true",
                        help="git commit the bump and create tag vX.Y.Z")
    parser.add_argument("--push", action="store_true",
                        help="push the commit and tag (triggers the release workflow)")
    args = parser.parse_args()

    new_version = ".".join(str(p) for p in _next_version(_read_version(), args.bump))
    _VERSION_FILE.write_text(new_version + "\n", encoding="utf-8")
    print(f"VERSION -> {new_version}")

    tag = f"v{new_version}"
    if args.tag or args.push:
        _git("add", "VERSION")
        _git("commit", "-m", f"Release {tag}")
        _git("tag", "-a", tag, "-m", f"Mocca {tag}")
        print(f"Committed and tagged {tag}.")

    if args.push:
        _git("push", "--follow-tags")
        print(f"Pushed {tag}. The release workflow will build and publish it.")
    elif args.tag:
        print("Next: git push --follow-tags   (to start the release)")
    else:
        print(f"VERSION updated only. Re-run with --tag to commit and tag {tag}.")


if __name__ == "__main__":
    main()
