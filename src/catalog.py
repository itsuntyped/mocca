"""The downloadable model catalog, sourced live from Hugging Face.

Mocca downloads GGUF model files straight from Hugging Face. Rather than ship a
hand-maintained list that goes stale, we build the catalog at runtime from the
Hugging Face API (no API key needed): the most-downloaded GGUF repos from
**bartowski** - the canonical, high-quality GGUF publisher - resolved to a
single, root-level, non-sharded ``Q4_K_M`` file Mocca can one-click install.
Popularity is a good quality proxy, and querying one trusted publisher keeps the
list clean (no random fine-tunes, roleplay forks, or non-GGUF formats).

Crucially, we also check each candidate's **architecture** (read from its GGUF
header) against what the bundled llama.cpp supports, so the catalog never offers
a model that would fail to load - e.g. a brand-new family the installed engine
doesn't recognize yet. See ``_SUPPORTED_ARCHITECTURES``.

We deliberately keep only models small enough to be reasonable on ordinary
hardware (see ``_MAX_SIZE_GB``); advanced users can still paste any repo +
filename in the Advanced tab.

If Hugging Face is unreachable, ``get_catalog()`` falls back to a small built-in
list so the UI always has something to offer (graceful degradation). Results are
cached for the process lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
from typing import Any

import httpx

log = logging.getLogger("mocca.catalog")

# Hugging Face API (anonymous, public): list a publisher's models, inspect one
# repo's files (with sizes via ?blobs=true), and range-fetch a file's header.
_HF_LIST = "https://huggingface.co/api/models"
_HF_MODEL = "https://huggingface.co/api/models/{repo}"
_HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{filename}"

# Model architectures the bundled llama.cpp can actually load. We check each
# candidate's GGUF header against this so the catalog never offers a model that
# fails to load (e.g. a brand-new 'qwen35' the installed engine doesn't know).
# Deliberately conservative - these are long-supported, broadly-available
# families; extend it as the bundled llama-cpp-python advances.
_SUPPORTED_ARCHITECTURES = {
    "llama",       # Llama 2/3.x, Mistral, Mixtral, SmolLM, TinyLlama, ...
    "qwen2", "qwen2moe",
    "gemma", "gemma2",
    "phi2", "phi3",
    "stablelm", "starcoder2", "gptneox", "falcon",
}

# How many GGUF header bytes to range-fetch. general.architecture is one of the
# very first metadata keys, so this is plenty and avoids pulling the big file.
_HEADER_BYTES = 65535

# The trusted GGUF publisher we build the catalog from.
_PUBLISHER = "bartowski"
# The quantization Mocca offers: a good quality/size balance, widely available.
_QUANT = "Q4_K_M"
# Skip anything bigger than this (GGUF on-disk GB); keeps the list laptop-friendly.
_MAX_SIZE_GB = 9.0
# How many to show, and how many repos to consider before filtering.
_LIMIT = 12
_CANDIDATE_POOL = 40
_RESOLVE_BATCH = 8

# Matches a sharded GGUF part ("...-00001-of-00009.gguf"); Mocca's downloader
# fetches a single file, so these can't be installed in one click.
_SHARD_RE = re.compile(r"\d{4,5}-of-\d{4,5}")

# Built-in fallback used only when the Hugging Face API can't be reached. These
# are known-good, single-file Q4_K_M GGUFs; filenames are pinned and may drift
# upstream over time, but they exist purely as a safety net.
_FALLBACK: list[dict[str, Any]] = [
    {"name": "Llama 3.2 3B Instruct", "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
     "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf", "size_gb": 2.0,
     "description": "Meta's compact all-rounder. Great default for most laptops."},
    {"name": "Llama 3.2 1B Instruct", "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF",
     "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf", "size_gb": 0.8,
     "description": "Tiny and fast. Runs comfortably on low-RAM machines."},
    {"name": "Qwen2.5 3B Instruct", "repo": "bartowski/Qwen2.5-3B-Instruct-GGUF",
     "filename": "Qwen2.5-3B-Instruct-Q4_K_M.gguf", "size_gb": 2.0,
     "description": "Strong at reasoning, code, and multilingual chat."},
    {"name": "Gemma 2 2B Instruct", "repo": "bartowski/gemma-2-2b-it-GGUF",
     "filename": "gemma-2-2b-it-Q4_K_M.gguf", "size_gb": 1.7,
     "description": "Google's small instruct model. Friendly and concise."},
    {"name": "Phi 3.5 Mini Instruct", "repo": "bartowski/Phi-3.5-mini-instruct-GGUF",
     "filename": "Phi-3.5-mini-instruct-Q4_K_M.gguf", "size_gb": 2.4,
     "description": "Microsoft's capable 3.8B model; good at structured tasks."},
]

# Cached catalog for the process lifetime.
_cache: list[dict[str, Any]] | None = None


def _pretty(repo_id: str) -> str:
    """Turn a repo id into a friendly display name.

    'bartowski/Meta-Llama-3.1-8B-Instruct-GGUF' -> 'Meta Llama 3.1 8B Instruct'.
    bartowski sometimes prefixes the source owner ('Qwen_Qwen2.5-7B-...'); we
    drop that prefix too.
    """
    base = repo_id.split("/")[-1]
    for suffix in ("-GGUF", "-gguf"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    if "_" in base:  # Drop a leading source-owner prefix like "Qwen_".
        base = base.split("_", 1)[1]
    return base.replace("-", " ").strip() or repo_id


def _human_downloads(n: int) -> str:
    """Compact download count: 358120 -> '358K', 3085069 -> '3.1M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def _read_gguf_architecture(buf: bytes) -> str | None:
    """Parse ``general.architecture`` from the start of a GGUF file.

    GGUF layout: magic 'GGUF', version (u32), tensor_count (u64), kv_count
    (u64), then key/value metadata pairs. ``general.architecture`` is one of the
    first keys, so a small header slice is enough. We parse sequentially,
    skipping values by type, and bail (return None) on anything we can't read -
    callers treat None as "unknown / unsupported".
    """
    if len(buf) < 24 or buf[:4] != b"GGUF":
        return None
    if struct.unpack_from("<I", buf, 4)[0] not in (2, 3):  # GGUF version
        return None
    off = 16  # skip magic(4) + version(4) + tensor_count(8)
    try:
        kv_count = struct.unpack_from("<Q", buf, off)[0]
        off += 8

        def read_str(o: int) -> tuple[str, int]:
            (n,) = struct.unpack_from("<Q", buf, o)
            o += 8
            return buf[o : o + n].decode("utf-8", "replace"), o + n

        # Byte sizes of the fixed-width scalar value types (GGUF type enum).
        scalar = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
        for _ in range(min(kv_count, 64)):
            key, off = read_str(off)
            (vtype,) = struct.unpack_from("<I", buf, off)
            off += 4
            if key == "general.architecture":
                return read_str(off)[0] if vtype == 8 else None
            if vtype in scalar:
                off += scalar[vtype]
            elif vtype == 8:  # string
                _, off = read_str(off)
            elif vtype == 9:  # array: elem_type (u32), count (u64), elements
                (etype,) = struct.unpack_from("<I", buf, off)
                off += 4
                (count,) = struct.unpack_from("<Q", buf, off)
                off += 8
                if etype in scalar:
                    off += scalar[etype] * count
                elif etype == 8:
                    for _ in range(count):
                        _, off = read_str(off)
                else:
                    return None  # nested/unknown array element
            else:
                return None
    except (struct.error, IndexError):
        return None
    return None


async def _architecture(client: httpx.AsyncClient, repo: str, filename: str) -> str | None:
    """Fetch just the GGUF header (HTTP range) and return its architecture."""
    url = _HF_RESOLVE.format(repo=repo, filename=filename)
    try:
        resp = await client.get(url, headers={"Range": f"bytes=0-{_HEADER_BYTES}"})
        if resp.status_code not in (200, 206):
            return None
        return _read_gguf_architecture(resp.content)
    except httpx.HTTPError:
        return None


async def _resolve(client: httpx.AsyncClient, repo: str, downloads: int) -> dict[str, Any] | None:
    """Resolve one repo to a catalog entry, or None if unsuitable/unavailable.

    Keeps only a single, root-level, non-sharded ``Q4_K_M`` GGUF within
    ``_MAX_SIZE_GB`` whose architecture the engine supports, reading the exact
    size from the Hugging Face blob info and the architecture from the file
    header.
    """
    try:
        resp = await client.get(_HF_MODEL.format(repo=repo), params={"blobs": "true"})
        resp.raise_for_status()
        siblings = resp.json().get("siblings", [])
    except (httpx.HTTPError, ValueError):
        return None

    for s in siblings:
        fn = s.get("rfilename", "")
        if (
            fn.endswith(".gguf") and "/" not in fn and not _SHARD_RE.search(fn)
            and _QUANT.lower() in fn.lower()
        ):
            size = s.get("size")
            if not size:
                continue
            size_gb = size / (1024 ** 3)
            if size_gb > _MAX_SIZE_GB:
                return None  # Too big for the "small, capable" catalog.
            # Only offer models the installed engine can actually load.
            arch = await _architecture(client, repo, fn)
            if arch not in _SUPPORTED_ARCHITECTURES:
                log.debug("Skipping %s: architecture %r not supported", repo, arch)
                return None
            return {
                "name": _pretty(repo),
                "repo": repo,
                "filename": fn,
                "size_gb": round(size_gb, 1),
                "description": f"{_human_downloads(downloads)} downloads on Hugging Face",
            }
    return None


async def _fetch_from_hf() -> list[dict[str, Any]]:
    """Build the catalog from bartowski's most-downloaded GGUF repos."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(_HF_LIST, params={
            "author": _PUBLISHER, "sort": "downloads", "direction": -1,
            "limit": _CANDIDATE_POOL,
        })
        resp.raise_for_status()
        repos = [(m.get("id"), m.get("downloads", 0)) for m in resp.json() if m.get("id")]

        # Resolve in small concurrent batches, stopping once we have enough.
        out: list[dict[str, Any]] = []
        for i in range(0, len(repos), _RESOLVE_BATCH):
            batch = repos[i : i + _RESOLVE_BATCH]
            resolved = await asyncio.gather(*(_resolve(client, r, d) for r, d in batch))
            out.extend(e for e in resolved if e)
            if len(out) >= _LIMIT:
                break
    return out[:_LIMIT]


async def get_catalog(refresh: bool = False) -> list[dict[str, Any]]:
    """Return the downloadable catalog (HF-sourced, cached; fallback on failure)."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    try:
        catalog = await _fetch_from_hf()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Catalog fetch from Hugging Face failed: %s", exc)
        catalog = []
    if not catalog:
        log.info("Using built-in fallback catalog (%d entries)", len(_FALLBACK))
        catalog = _FALLBACK
    else:
        log.info("Built catalog from Hugging Face (%d entries)", len(catalog))
    _cache = catalog
    return _cache
