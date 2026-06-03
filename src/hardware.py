"""Lightweight, dependency-free hardware detection and model-fit ratings.

Mocca uses this for two UI things:

  * a short, human-readable hardware summary line, and
  * a per-model "fit" rating computed from the model's on-disk size.

Detection is deliberately self-contained (no external service, no third-party
package): RAM comes from the OS (Windows ``GlobalMemoryStatusEx`` via ctypes,
Linux ``/proc/meminfo``), the CPU name from the registry / ``/proc/cpuinfo``, and
GPU/VRAM from ``nvidia-smi`` when an NVIDIA driver is present. Anything we can't
determine is simply omitted - non-NVIDIA GPUs aren't detected, and the whole
thing degrades to "hardware unknown" rather than ever failing.

``detect_system()`` returns a dict shaped like::

    {
      "total_ram_gb": 31.9, "available_ram_gb": 17.8,
      "cpu_name": "AMD Ryzen 5 5600X 6-Core Processor", "cpu_cores": 12,
      "has_gpu": True, "gpu_name": "NVIDIA GeForce RTX 3070", "gpu_vram_gb": 8.0,
    }

or ``None`` if we couldn't even read system RAM. The result is cached for the
process lifetime (hardware doesn't change while Mocca runs).
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from typing import Any

log = logging.getLogger("mocca.hardware")

# Conservative multiplier over the GGUF file size to allow for the KV cache and
# runtime overhead once a model is loaded.
_OVERHEAD = 1.2

# Cached detection result. The sentinel distinguishes "not detected yet" from a
# genuine ``None`` ("detection failed").
_UNSET = object()
_system: Any = _UNSET


def _ram_gb() -> tuple[float | None, float | None]:
    """Return (total_gb, available_gb), each None if it can't be read."""
    if os.name == "nt":
        # Windows: GlobalMemoryStatusEx fills a struct with byte counts.
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                gb = 1024 ** 3
                return stat.ullTotalPhys / gb, stat.ullAvailPhys / gb
        except (OSError, AttributeError) as exc:
            log.debug("GlobalMemoryStatusEx failed: %s", exc)
        return None, None

    # Linux (and anything with /proc/meminfo): values are in kB.
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0])
        total = info.get("MemTotal")
        avail = info.get("MemAvailable", info.get("MemFree"))
        mb = 1024 * 1024
        return (total / mb if total else None, avail / mb if avail else None)
    except (OSError, ValueError, IndexError) as exc:
        log.debug("/proc/meminfo read failed: %s", exc)
        return None, None


def _cpu_name() -> str | None:
    """Best-effort marketing name of the CPU (e.g. 'AMD Ryzen 5 5600X ...')."""
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            with key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return str(value).strip() or None
        except OSError:
            pass
        return platform.processor() or None

    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        pass
    return platform.processor() or None


def _detect_gpu() -> tuple[bool, str | None, float | None]:
    """Return (has_gpu, name, vram_gb) for an NVIDIA GPU, via nvidia-smi.

    Returns ``(False, None, None)`` when nvidia-smi isn't installed (no NVIDIA
    driver) or anything goes wrong. AMD/Intel GPUs aren't detected.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, None, None
    # Keep a console window from flashing up in the packaged (windowed) app.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=creationflags,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return False, None, None
        # First GPU only; fields: "NVIDIA GeForce RTX 3070, 8192" (MiB).
        name, mem = (p.strip() for p in out.stdout.strip().splitlines()[0].split(","))
        return True, name, round(float(mem) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.debug("nvidia-smi query failed: %s", exc)
        return False, None, None


def detect_system(refresh: bool = False) -> dict[str, Any] | None:
    """Detect RAM/CPU/GPU, cached for the process. None if RAM is unreadable."""
    global _system
    if _system is not _UNSET and not refresh:
        return _system

    total_ram, avail_ram = _ram_gb()
    if total_ram is None:
        # Without even a RAM figure we can't say anything useful; treat as
        # unknown so the UI hides the hardware line (graceful degradation).
        _system = None
        return None

    has_gpu, gpu_name, gpu_vram = _detect_gpu()
    _system = {
        "total_ram_gb": round(total_ram, 2),
        "available_ram_gb": round(avail_ram, 2) if avail_ram else None,
        "cpu_name": _cpu_name(),
        "cpu_cores": os.cpu_count(),
        "has_gpu": has_gpu,
        "gpu_name": gpu_name,
        "gpu_vram_gb": gpu_vram,
    }
    log.info("Detected hardware: %s", summarise(_system))
    return _system


def summarise(system: dict[str, Any] | None) -> str:
    """Build a one-line hardware summary from a detected system dict."""
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
