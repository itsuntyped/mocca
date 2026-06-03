"""One-step dependency setup for Mocca, with automatic GPU detection.

This is the recommended way to install Mocca's dependencies. It installs the
right ``llama-cpp-python`` build for the machine, then the rest of the
requirements:

  * an **NVIDIA GPU** is detected  -> the prebuilt **CUDA** wheel (plus the CUDA
    runtime wheels it needs), so the model runs on the GPU; or
  * otherwise                      -> the prebuilt **CPU** wheel.

Installing the engine wheel first means the ``requirements.txt`` step never has
to build ``llama-cpp-python`` from source. This keeps Mocca cross-platform - a
CPU-only or non-NVIDIA machine still works - while giving NVIDIA users the large
GPU speed-up by default.

Usage (from the project root, inside your virtualenv):

    python scripts/setup.py            # auto-detect
    python scripts/setup.py --cuda     # force the CUDA build
    python scripts/setup.py --cpu      # force the CPU build

Notes:
  * Prebuilt wheels exist for specific Python versions; on Windows that means
    **Python 3.11 or 3.12**. On a newer Python (3.13/3.14) there is no prebuilt
    CUDA wheel, so the CUDA path won't work there (use 3.11/3.12, or build from
    source with the CUDA Toolkit). See the readme.
  * The CUDA wheel needs no system CUDA Toolkit: the runtime comes from the
    ``nvidia-*-cu12`` pip wheels, and ``src/engine.py`` adds them to the DLL
    search path at startup. You only need an up-to-date NVIDIA driver.
  * After a CUDA install, set **GPU layers** to **99** in Settings to offload the
    whole model (the field caps to the model's real layer count).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# This file lives in scripts/, so put the project root on sys.path - the final
# verification step imports ``src.engine`` to mirror the app's DLL setup.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Prebuilt wheel indexes. The CUDA index hosts CUDA 12.4 builds (compatible with
# current NVIDIA drivers and Ampere/Ada GPUs); the CPU index hosts plain builds.
_CUDA_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu124"
_CPU_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"

# Pinned to the newest prebuilt wheel available for Windows / CPython 3.12 on
# each index (Windows CUDA prebuilts lag well behind the CPU ones). Pinning a
# version that PyPI only ships as an sdist makes pip prefer the index's wheel, so
# no source build happens. Bump these as newer Windows wheels are published.
_CUDA_WHEEL = "llama-cpp-python==0.3.4"
_CPU_WHEEL = "llama-cpp-python==0.3.19"

# The CUDA runtime libraries the prebuilt CUDA wheel loads at run time. Installed
# from PyPI so no system CUDA Toolkit is required.
_CUDA_RUNTIME_PKGS = ["nvidia-cuda-runtime-cu12", "nvidia-cublas-cu12"]

_IS_WINDOWS = sys.platform == "win32"


def _run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.check_call(args)


def _pip(*args: str) -> None:
    _run([sys.executable, "-m", "pip", *args])


def _has_nvidia_gpu() -> bool:
    """True if an NVIDIA GPU looks present (nvidia-smi exists and runs)."""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def _gpu_offload_supported() -> bool | None:
    """Whether the installed llama-cpp-python build can use the GPU (None if it
    can't be imported / its DLLs won't load)."""
    try:
        from src import engine  # noqa: F401  (runs _prepare_cuda_dll_path)
        import llama_cpp
        return bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:  # noqa: BLE001 - any import/DLL failure means "unknown"
        return None


def _install_engine(use_cuda: bool) -> None:
    """Install the prebuilt engine wheel (and CUDA runtime, for the GPU build).

    Windows uses the pinned prebuilt wheels above; other platforms fall back to a
    normal install via requirements.txt (handled by the caller).
    """
    index = _CUDA_INDEX if use_cuda else _CPU_INDEX
    wheel = _CUDA_WHEEL if use_cuda else _CPU_WHEEL
    _pip("install", wheel, "--force-reinstall", "--no-cache-dir",
         "--extra-index-url", index)
    if use_cuda:
        _pip("install", *_CUDA_RUNTIME_PKGS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Mocca's dependencies.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cpu", action="store_true", help="force the CPU build")
    group.add_argument("--cuda", action="store_true", help="force the CUDA build")
    args = parser.parse_args()

    if args.cuda:
        use_cuda = True
    elif args.cpu:
        use_cuda = False
    else:
        use_cuda = _has_nvidia_gpu()
        print(f"NVIDIA GPU detected: {use_cuda}")

    # Install the prebuilt engine first (Windows) so requirements.txt won't build
    # llama-cpp-python from source. Other platforms skip this and let pip resolve
    # the engine normally during the requirements step.
    if _IS_WINDOWS:
        try:
            _install_engine(use_cuda)
        except subprocess.CalledProcessError:
            if use_cuda:
                print(
                    "\nCould not install a prebuilt CUDA wheel - there may be "
                    f"none for Python {sys.version_info.major}."
                    f"{sys.version_info.minor}. Use Python 3.11/3.12, or build "
                    "from source with the CUDA Toolkit. Falling back to CPU."
                )
                use_cuda = False
                _install_engine(False)
            else:
                raise

    # The rest of the dependencies. With the engine already satisfied above, pip
    # leaves it alone here (no source build).
    _pip("install", "-r", "requirements.txt")

    # Report the result.
    supported = _gpu_offload_supported()
    print()
    print(f"llama-cpp-python GPU offload supported: {supported}")
    if use_cuda and supported:
        print("Done. Set GPU layers to 99 in Settings to run the model on your GPU.")
    elif use_cuda and not supported:
        print("Warning: CUDA build installed but GPU offload reports unavailable; "
              "check your NVIDIA driver.")
    else:
        print("Done (CPU build).")


if __name__ == "__main__":
    main()
