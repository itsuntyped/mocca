"""Local model management: list, download, and delete GGUF model files.

Mocca stores models as plain ``.gguf`` files under ``data/models/``. There is no
external service - downloads come straight from Hugging Face over HTTPS, which
keeps the app fully standalone. We stream the download ourselves (rather than
pulling in a heavyweight client library) so the UI can show an accurate,
byte-level progress bar.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from .paths import MODELS_DIR, ensure_dirs

log = logging.getLogger("mocca.models")

# Template for resolving a file inside a Hugging Face repo to a direct download.
_HF_URL = "https://huggingface.co/{repo}/resolve/{revision}/{filename}"


class ModelError(RuntimeError):
    """Raised for download/delete problems, carrying a UI-friendly message."""


def safe_filename(filename: str) -> str:
    """Return just the final path component, rejecting traversal attempts.

    Guards against a malicious/typo'd filename like ``../../etc/passwd`` by
    collapsing to the bare name and requiring a ``.gguf`` extension.
    """
    name = Path(filename).name
    if not name.endswith(".gguf"):
        raise ModelError("Model filename must end in '.gguf'.")
    return name


def model_path(filename: str) -> Path:
    """Absolute path to a (possibly not-yet-downloaded) model file."""
    return MODELS_DIR / safe_filename(filename)


def list_local_models() -> list[dict[str, Any]]:
    """Return the downloaded models (name + size), newest first."""
    ensure_dirs()
    out: list[dict[str, Any]] = []
    for p in MODELS_DIR.glob("*.gguf"):
        stat = p.stat()
        out.append({"name": p.name, "size": stat.st_size, "modified": stat.st_mtime})
    out.sort(key=lambda m: m["modified"], reverse=True)
    log.debug("%d local model(s) found", len(out))
    return out


def has_any_model() -> bool:
    """True if at least one model is downloaded."""
    ensure_dirs()
    return any(MODELS_DIR.glob("*.gguf"))


def delete_model(filename: str) -> bool:
    """Delete a downloaded model. Returns True if the file existed."""
    path = model_path(filename)
    if path.exists():
        path.unlink()
        log.info("Deleted model %s", path.name)
        return True
    return False


async def download_model(
    repo: str, filename: str, revision: str = "main"
) -> AsyncIterator[dict[str, Any]]:
    """Stream a GGUF download from Hugging Face, yielding progress events.

    Yields dicts shaped like ``{"status", "completed", "total"}`` (bytes), and
    a final ``{"done": True}``. The file is written to a ``.part`` temp path and
    atomically renamed on success, so a cancelled/failed download never leaves a
    corrupt model that looks complete.
    """
    ensure_dirs()
    name = safe_filename(filename)
    dest = MODELS_DIR / name
    part = dest.with_name(name + ".part")

    if dest.exists():
        yield {"status": "Already downloaded.", "completed": 1, "total": 1, "done": True}
        return

    url = _HF_URL.format(repo=repo.strip("/"), revision=revision, filename=name)
    log.info("Downloading %s from %s", name, url)

    try:
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise ModelError(
                        f"Not found: {repo}/{name}. Check the repo and filename "
                        "on huggingface.co."
                    )
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                done = 0
                # 1 MiB chunks: smooth progress without spamming the UI.
                with open(part, "wb") as fh:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        fh.write(chunk)
                        done += len(chunk)
                        yield {"status": f"Downloading {name}", "completed": done, "total": total}
        part.replace(dest)
        log.info("Downloaded %s (%d bytes)", name, dest.stat().st_size)
        yield {"status": "Done", "completed": dest.stat().st_size, "total": dest.stat().st_size, "done": True}
    except httpx.HTTPError as exc:
        raise ModelError(f"Download failed: {exc}") from exc
    finally:
        # Remove the partial file if it's still around. This covers a failed
        # download AND a user cancellation: when the client disconnects, the
        # streaming generator is closed (GeneratorExit/CancelledError at a yield)
        # and this finally runs, so a cancelled download never leaves a stray
        # ``.part``. On success ``part`` was already renamed to ``dest``, so this
        # is a harmless no-op.
        part.unlink(missing_ok=True)
