"""Hardware-fit helpers built on llmfit's system info.

The :mod:`src.llmfit_service` module owns the llmfit server process and fetches
its raw ``system`` payload. This module turns that payload into the two things
the UI needs:

  * a short, human-readable hardware summary line, and
  * a per-model "fit" rating computed from the model's on-disk size.

We compute the fit ourselves (rather than mapping our GGUF filenames onto
llmfit's catalog) so the rating works for *any* model the user installs. llmfit
supplies the trustworthy part - cross-platform RAM/GPU/VRAM detection.

llmfit's ``system`` schema (API v1) looks like::

    {
      "total_ram_gb": 31.93, "available_ram_gb": 19.53,
      "cpu_name": "...", "cpu_cores": 12,
      "has_gpu": true, "gpu_name": "NVIDIA GeForce RTX 3070",
      "gpu_vram_gb": 8.0, "backend": "CUDA",
      "gpus": [ {"name": ..., "vram_gb": 8.0, ...} ]
    }
"""

from __future__ import annotations

from typing import Any

# Conservative multiplier over the GGUF file size to allow for the KV cache and
# runtime overhead once a model is loaded.
_OVERHEAD = 1.2


def summarise(system: dict[str, Any] | None) -> str:
    """Build a one-line hardware summary from llmfit's system payload."""
    if not system:
        return "Hardware unknown"
    parts: list[str] = []

    ram = system.get("total_ram_gb")
    if ram:
        parts.append(f"{ram:g} GB RAM")

    cpu = system.get("cpu_name")
    cores = system.get("cpu_cores")
    if cpu:
        parts.append(f"{cpu} ({cores} cores)" if cores else cpu)

    if system.get("has_gpu") and system.get("gpu_name"):
        vram = system.get("gpu_vram_gb")
        gpu = system["gpu_name"]
        parts.append(f"{gpu} ({vram:g} GB VRAM)" if vram else gpu)
    else:
        parts.append("no GPU detected")

    return " - ".join(parts)


def fit_for_size(size_gb: float | None, system: dict[str, Any] | None) -> dict[str, str] | None:
    """Rate how well a model of ``size_gb`` fits the detected hardware.

    Returns ``{"level", "label"}`` (level: ``perfect`` / ``good`` / ``tight`` /
    ``too_big``) or ``None`` when we lack hardware info or a size to judge by.
    """
    if not system or not size_gb:
        return None

    required = size_gb * _OVERHEAD
    ram = system.get("total_ram_gb") or 0
    vram = system.get("gpu_vram_gb") or 0
    has_gpu = bool(system.get("has_gpu"))

    if has_gpu and vram and required <= vram:
        return {"level": "perfect", "label": "Runs on GPU"}
    if ram and required <= ram * 0.75:
        return {"level": "good", "label": "Fits in RAM"}
    if ram and required <= ram:
        return {"level": "tight", "label": "Tight fit"}
    return {"level": "too_big", "label": "Too large"}
