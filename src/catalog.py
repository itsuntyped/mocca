"""A small curated catalog of recommended models.

Mocca downloads GGUF model files straight from Hugging Face. Picking a good
GGUF by hand is intimidating for newcomers, so we ship a short, opinionated
list of small, capable, instruction-tuned models that run on ordinary
hardware. The web UI shows these as one-click downloads; advanced users can
still paste any Hugging Face repo + filename manually.

Each entry points at a specific repo/file. ``size_gb`` is approximate and only
used to set expectations in the UI - the real size comes from the download's
Content-Length header. If a filename ever changes upstream, update it here;
nothing else depends on these exact strings.
"""

from __future__ import annotations

# NOTE: these are 4-bit (Q4_K_M) quantizations - a good balance of quality and
# size. The "bartowski" account is a well-known publisher of GGUF conversions.
CATALOG: list[dict] = [
    {
        "name": "Llama 3.2 3B Instruct",
        "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size_gb": 2.0,
        "description": "Meta's compact all-rounder. Great default for most laptops.",
    },
    {
        "name": "Llama 3.2 1B Instruct",
        "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF",
        "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size_gb": 0.8,
        "description": "Tiny and fast. Runs comfortably on low-RAM machines.",
    },
    {
        "name": "Qwen2.5 3B Instruct",
        "repo": "bartowski/Qwen2.5-3B-Instruct-GGUF",
        "filename": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "size_gb": 2.0,
        "description": "Strong at reasoning, code, and multilingual chat.",
    },
    {
        "name": "Gemma 2 2B Instruct",
        "repo": "bartowski/gemma-2-2b-it-GGUF",
        "filename": "gemma-2-2b-it-Q4_K_M.gguf",
        "size_gb": 1.7,
        "description": "Google's small instruct model. Friendly and concise.",
    },
    {
        "name": "Phi 3.5 Mini Instruct",
        "repo": "bartowski/Phi-3.5-mini-instruct-GGUF",
        "filename": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "size_gb": 2.4,
        "description": "Microsoft's capable 3.8B model; good at structured tasks.",
    },
]
