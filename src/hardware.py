"""Lightweight, dependency-free hardware detection and model-fit ratings.

Mocca uses this for two UI things:

  * a short, human-readable hardware summary line, and
  * a per-model "fit" rating computed from the model's on-disk size.

Detection is deliberately self-contained (no external service, no third-party
package): RAM comes from the OS (Windows ``GlobalMemoryStatusEx`` via ctypes,
Linux ``/proc/meminfo``), the CPU name from the registry / ``/proc/cpuinfo``, and
the GPU from ``nvidia-smi`` (NVIDIA, exact name + VRAM) with a vendor-neutral
fallback - the Windows display-adapter registry, or Linux ``/sys/class/drm`` -
that also picks up **AMD and Intel** GPUs. Anything we can't determine is simply
omitted, and the whole thing degrades to "hardware unknown" rather than ever
failing.

``detect_system()`` returns a dict shaped like::

    {
      "total_ram_gb": 31.9, "available_ram_gb": 17.8,
      "cpu_name": "AMD Ryzen 5 5600X 6-Core Processor", "cpu_cores": 12,
      "has_gpu": True, "gpu_name": "NVIDIA GeForce RTX 3070", "gpu_vram_gb": 8.0,
      "gpu_vendor": "nvidia",  # or "amd" / "intel" / None
    }

or ``None`` if we couldn't even read system RAM. The result is cached for the
process lifetime (hardware doesn't change while Mocca runs).

``gpu_vendors()`` is a lighter, build-time helper (used by ``scripts/setup.py``)
that just reports which GPU vendors are present, so the installer can pick the
right ``llama-cpp-python`` build (CUDA for NVIDIA, the universal Vulkan wheel for
AMD/Intel).
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


# Priority when several GPUs are present: a discrete NVIDIA/AMD card beats an
# Intel integrated GPU; ties break on VRAM. Also the value space for "gpu_vendor".
_VENDOR_PRIORITY = {"nvidia": 3, "amd": 2, "intel": 1}


def _vendor_from_name(name: str | None) -> str | None:
    """Classify a GPU's marketing name into a vendor id, or None if unrecognised."""
    n = (name or "").lower()
    if any(k in n for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(k in n for k in ("amd", "radeon", "instinct", "firepro")) or "ati " in n:
        return "amd"
    if any(k in n for k in ("intel", "arc ", "iris", "uhd graphics", "hd graphics")):
        return "intel"
    return None


def _vendor_from_pci(devid: str | None) -> str | None:
    """Vendor from a PCI id - a Windows MatchingDeviceId (``...VEN_10DE...``) or a
    Linux sysfs vendor file (``0x10de``)."""
    d = (devid or "").lower()
    if "10de" in d:
        return "nvidia"
    if "1002" in d:
        return "amd"
    if "8086" in d:
        return "intel"
    return None


def _nvidia_smi() -> tuple[bool, str | None, float | None]:
    """(has_gpu, name, vram_gb) for an NVIDIA GPU via nvidia-smi, else (False, ...).

    nvidia-smi gives an exact marketing name and accurate VRAM, so it's the
    preferred source for NVIDIA; the registry/sysfs fallback covers everything
    else (and NVIDIA cards on a box without the CLI installed).
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


def _windows_gpus() -> list[tuple[str, float | None, str | None]]:
    """(name, vram_gb, vendor) for each display adapter, from the registry.

    Reads the display-adapter class key; each numbered subkey is one adapter with
    a ``DriverDesc`` (name) and ``HardwareInformation.qwMemorySize`` (VRAM in
    bytes - the reliable VRAM source on Windows, unlike WMI's 4 GB-capped field).
    Adapters whose vendor we can't identify (e.g. "Microsoft Basic Display") are
    dropped. Vendor-neutral, so it sees AMD and Intel GPUs too.
    """
    if os.name != "nt":
        return []
    import winreg

    key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

    def _get(handle, name):
        try:
            value, _ = winreg.QueryValueEx(handle, name)
            return value
        except OSError:
            return None

    gpus: list[tuple[str, float | None, str | None]] = []
    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
    except OSError:
        return []
    with base:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():  # Skip "Properties" and other non-adapter keys.
                continue
            try:
                with winreg.OpenKey(base, sub) as k:
                    name = _get(k, "DriverDesc")
                    if not name:
                        continue
                    vendor = _vendor_from_name(name) or _vendor_from_pci(_get(k, "MatchingDeviceId"))
                    if not vendor:
                        continue
                    mem = _get(k, "HardwareInformation.qwMemorySize")
                    if mem is None:
                        raw = _get(k, "HardwareInformation.MemorySize")
                        mem = int.from_bytes(raw, "little") if isinstance(raw, bytes) else raw
                    vram_gb = round(int(mem) / 1024 ** 3, 1) if mem else None
                    gpus.append((str(name), vram_gb, vendor))
            except OSError:
                continue
    return gpus


def _linux_gpus() -> list[tuple[str, float | None, str | None]]:
    """(name, vram_gb, vendor) for each DRM GPU, from /sys/class/drm.

    The PCI ``vendor`` file identifies AMD/Intel/NVIDIA; AMD also exposes
    ``mem_info_vram_total`` (bytes). sysfs has no marketing name, so we label by
    vendor (NVIDIA itself is normally named precisely by nvidia-smi instead).
    """
    import glob

    def _read(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return None

    gpus: list[tuple[str, float | None, str | None]] = []
    for dev in sorted(glob.glob("/sys/class/drm/card[0-9]/device")):
        vendor = _vendor_from_pci(_read(os.path.join(dev, "vendor")))
        if not vendor:
            continue
        raw = _read(os.path.join(dev, "mem_info_vram_total"))
        vram_gb = round(int(raw) / 1024 ** 3, 1) if raw and raw.isdigit() else None
        name = {"nvidia": "NVIDIA GPU", "amd": "AMD GPU", "intel": "Intel GPU"}[vendor]
        gpus.append((name, vram_gb, vendor))
    return gpus


def _enumerate_gpus() -> list[tuple[str, float | None, str | None]]:
    """All detectable GPUs as (name, vram_gb, vendor), via the OS-specific source."""
    return _windows_gpus() if os.name == "nt" else _linux_gpus()


def _detect_gpu() -> tuple[bool, str | None, float | None, str | None]:
    """Return (has_gpu, name, vram_gb, vendor) for the best GPU present.

    NVIDIA is read from nvidia-smi first (exact name + VRAM); otherwise we fall
    back to the OS enumeration, which also covers AMD and Intel. Returns
    ``(False, None, None, None)`` when no GPU can be detected.
    """
    ok, name, vram = _nvidia_smi()
    if ok:
        return True, name, vram, "nvidia"
    gpus = _enumerate_gpus()
    if gpus:
        # Prefer a discrete card over an integrated one; break ties on VRAM.
        name, vram, vendor = max(
            gpus, key=lambda g: (_VENDOR_PRIORITY.get(g[2], 0), g[1] or 0)
        )
        return True, name, vram, vendor
    return False, None, None, None


def gpu_vendors() -> set[str]:
    """Which GPU vendors are present: a subset of ``{'nvidia', 'amd', 'intel'}``.

    A lightweight signal for ``scripts/setup.py`` to choose the engine build,
    independent of the cached ``detect_system`` result. Empty when no GPU is found.
    """
    vendors: set[str] = set()
    if _nvidia_smi()[0]:
        vendors.add("nvidia")
    for _, _, vendor in _enumerate_gpus():
        if vendor:
            vendors.add(vendor)
    return vendors


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

    has_gpu, gpu_name, gpu_vram, gpu_vendor = _detect_gpu()
    _system = {
        "total_ram_gb": round(total_ram, 2),
        "available_ram_gb": round(avail_ram, 2) if avail_ram else None,
        "cpu_name": _cpu_name(),
        "cpu_cores": os.cpu_count(),
        "has_gpu": has_gpu,
        "gpu_name": gpu_name,
        "gpu_vram_gb": gpu_vram,
        "gpu_vendor": gpu_vendor,
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
