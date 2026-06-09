"""One-step dependency setup for Mocca, with automatic GPU detection.

This is the recommended way to install Mocca's dependencies. It installs the
right ``llama-cpp-python`` build for the machine, then the rest of the
requirements. The engine build is chosen smart-auto:

  * an **NVIDIA GPU** + a Python with a prebuilt CUDA wheel (CPython 3.10-3.12)
    -> the **CUDA** wheel (plus the CUDA runtime wheels it needs) - fastest; or
  * **any GPU** (NVIDIA on a newer Python, or an **AMD / Intel** GPU)
    -> the vendor-neutral **Vulkan** wheel, which runs on all three vendors and,
    being a ``py3-none`` wheel, installs on any Python 3.x; or
  * **no GPU**                     -> the prebuilt **CPU** wheel.

Installing the engine wheel first means the ``requirements.txt`` step never has
to build ``llama-cpp-python`` from source. This keeps Mocca cross-platform while
giving GPU users the large speed-up by default - on NVIDIA, AMD, and Intel.

Usage (from the project root, inside your virtualenv):

    python scripts/setup.py            # auto-detect the best build
    python scripts/setup.py --cuda     # force the CUDA (NVIDIA) build
    python scripts/setup.py --vulkan   # force the Vulkan (NVIDIA/AMD/Intel) build
    python scripts/setup.py --cpu      # force the CPU build

Notes:
  * The Vulkan build needs only an up-to-date GPU driver (the Vulkan loader,
    ``vulkan-1.dll``, ships with NVIDIA/AMD/Intel drivers) - no SDK, no toolkit.
  * The CUDA build needs no system CUDA Toolkit: the runtime comes from the
    ``nvidia-*-cu12`` pip wheels, and ``src/engine.py`` adds them to the DLL
    search path at startup. You only need an up-to-date NVIDIA driver. Its
    prebuilt wheels exist for CPython 3.10-3.12 only; on a newer Python the auto
    pick uses Vulkan instead.
  * Either GPU build defaults **GPU layers to 99** on first run (offloads the
    whole model); change it in Settings if needed.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

# This file lives in scripts/, so put the project root on sys.path - we import
# ``src.hardware`` (GPU vendor detection) and, at the end, ``src.engine`` to
# mirror the app's DLL setup.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Prebuilt wheel indexes. CUDA hosts CUDA 12.4 builds (current NVIDIA drivers,
# Ampere/Ada); Vulkan hosts the vendor-neutral GPU build (NVIDIA/AMD/Intel); CPU
# hosts plain builds.
_CUDA_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu124"
_VULKAN_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/vulkan"
_CPU_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"

# Pinned to the newest prebuilt wheel on each index. Pinning a version that PyPI
# only ships as an sdist makes pip prefer the index's wheel, so no source build
# happens. The CUDA prebuilts lag well behind; the Vulkan/CPU ones track latest.
# Bump these as newer wheels are published.
_CUDA_WHEEL = "llama-cpp-python==0.3.4"      # CPython 3.10-3.12 only.
_VULKAN_WHEEL = "llama-cpp-python==0.3.28"   # py3-none: installs on any Python 3.
_CPU_WHEEL = "llama-cpp-python==0.3.19"

# The CUDA runtime libraries the prebuilt CUDA wheel loads at run time. Installed
# from PyPI so no system CUDA Toolkit is required. (Vulkan needs no such wheels -
# its loader ships with the GPU driver.)
_CUDA_RUNTIME_PKGS = ["nvidia-cuda-runtime-cu12", "nvidia-cublas-cu12"]

_BACKENDS = {
    "cuda": (_CUDA_INDEX, _CUDA_WHEEL),
    "vulkan": (_VULKAN_INDEX, _VULKAN_WHEEL),
    "cpu": (_CPU_INDEX, _CPU_WHEEL),
}

_IS_WINDOWS = sys.platform == "win32"
_IS_LINUX_X64 = sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64")
# Platforms the abetlen indexes publish prebuilt wheels for. Elsewhere (macOS,
# arm) we leave the engine to the normal requirements.txt resolution, exactly as
# before - so this change never regresses those setups.
_HAS_PREBUILT = _IS_WINDOWS or _IS_LINUX_X64


def _run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.check_call(args)


def _pip(*args: str) -> None:
    _run([sys.executable, "-m", "pip", *args])


def _cuda_wheel_available(py: tuple[int, int] = sys.version_info[:2]) -> bool:
    """Whether a prebuilt CUDA wheel exists for this interpreter.

    The CUDA index ships CPython 3.10-3.12 wheels only (no 3.13/3.14 yet), on the
    prebuilt platforms. Pure so the selection logic is testable.
    """
    return _HAS_PREBUILT and py in {(3, 10), (3, 11), (3, 12)}


def _choose_backend(vendors: set[str], cuda_ok: bool) -> str:
    """Smart-auto engine build for the detected GPU vendor(s).

    NVIDIA gets CUDA when a wheel is installable (fastest); any GPU otherwise -
    an AMD/Intel card, or an NVIDIA one on a Python without a CUDA wheel - gets
    the vendor-neutral Vulkan build; no GPU gets CPU. Pure and side-effect-free
    so the matrix is unit-tested.
    """
    if "nvidia" in vendors and cuda_ok:
        return "cuda"
    if vendors:
        return "vulkan"
    return "cpu"


def _gpu_offload_supported() -> bool | None:
    """Whether the installed llama-cpp-python build can use the GPU (None if it
    can't be imported / its libraries won't load)."""
    try:
        from src import engine  # noqa: F401  (runs _prepare_cuda_dll_path)
        import llama_cpp
        return bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:  # noqa: BLE001 - any import/DLL failure means "unknown"
        return None


def _install_engine(backend: str) -> None:
    """Install the prebuilt engine wheel for ``backend`` (and CUDA runtime, for CUDA)."""
    index, wheel = _BACKENDS[backend]
    _pip("install", wheel, "--force-reinstall", "--no-cache-dir",
         "--extra-index-url", index)
    if backend == "cuda":
        _pip("install", *_CUDA_RUNTIME_PKGS)


# When a GPU build can't be installed (e.g. no prebuilt wheel for this Python),
# step down: CUDA -> Vulkan -> CPU, Vulkan -> CPU. So a GPU machine still ends up
# with the best engine that will actually install, never a hard failure.
_FALLBACK = {"cuda": ["cuda", "vulkan", "cpu"], "vulkan": ["vulkan", "cpu"], "cpu": ["cpu"]}


def _install_engine_with_fallback(backend: str) -> str:
    """Install ``backend``, stepping down the fallback chain on failure. Returns
    the backend actually installed; re-raises if even CPU won't install."""
    last_exc: subprocess.CalledProcessError | None = None
    for candidate in _FALLBACK[backend]:
        try:
            _install_engine(candidate)
            return candidate
        except subprocess.CalledProcessError as exc:
            last_exc = exc
            print(f"\nCould not install the {candidate} engine wheel "
                  f"(no prebuilt wheel for Python {sys.version_info.major}."
                  f"{sys.version_info.minor}?); trying the next option...")
    assert last_exc is not None
    raise last_exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Mocca's dependencies.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cpu", action="store_true", help="force the CPU build")
    group.add_argument("--cuda", action="store_true", help="force the CUDA (NVIDIA) build")
    group.add_argument("--vulkan", action="store_true",
                       help="force the Vulkan (NVIDIA/AMD/Intel) build")
    args = parser.parse_args()

    forced = "cuda" if args.cuda else "vulkan" if args.vulkan else "cpu" if args.cpu else None
    if forced:
        backend = forced
    elif _HAS_PREBUILT:
        from src import hardware
        vendors = hardware.gpu_vendors()
        backend = _choose_backend(vendors, _cuda_wheel_available())
        print(f"Detected GPU vendor(s): {', '.join(sorted(vendors)) or 'none'} "
              f"-> {backend} build")
    else:
        # macOS / non-x86 Linux: no prebuilt index, let requirements.txt resolve.
        backend = "cpu"

    # Install the prebuilt engine first so requirements.txt won't build
    # llama-cpp-python from source. On a non-prebuilt platform with no forced
    # backend we skip this and let pip resolve the engine in the requirements step
    # (unchanged behaviour for macOS/arm).
    installed = backend
    if _HAS_PREBUILT or forced:
        installed = _install_engine_with_fallback(backend)

    # The rest of the dependencies. With the engine already satisfied above, pip
    # leaves it alone here (no source build).
    _pip("install", "-r", "requirements.txt")

    # Report the result.
    supported = _gpu_offload_supported()
    print()
    print(f"Engine build installed: {installed}")
    print(f"llama-cpp-python GPU offload supported: {supported}")
    if installed in ("cuda", "vulkan") and supported:
        print("Done. The model offloads to your GPU out of the box "
              "(GPU layers default to 99; adjust in Settings).")
    elif installed in ("cuda", "vulkan") and not supported:
        driver = "NVIDIA driver" if installed == "cuda" else "GPU driver (Vulkan loader)"
        print(f"Warning: {installed} build installed but GPU offload reports "
              f"unavailable; check your {driver}.")
    else:
        print("Done (CPU build).")


if __name__ == "__main__":
    main()
