# PyInstaller spec for the Windows build of Mocca.
#
# Build it with packaging\windows\build.ps1 (which installs the build-only
# dependencies first). Produces a one-folder app at packaging\windows\dist\Mocca
# whose entry point is Mocca.exe.

import glob
import importlib.util
import os

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is the directory holding this spec (packaging/windows); the project
# root is two levels up.
PROJECT_ROOT = os.path.dirname(os.path.dirname(SPECPATH))
LAUNCHER = os.path.join(SPECPATH, "launcher.py")


def _package_dir(name):
    """Locate an installed package's directory WITHOUT importing it.

    We use find_spec rather than collect_dynamic_libs/import so the spec never
    loads llama_cpp - importing the CUDA build on a machine with no NVIDIA driver
    (e.g. a CI runner) fails, which would break the build. find_spec just reads
    metadata.
    """
    spec = importlib.util.find_spec(name)
    if spec and spec.submodule_search_locations:
        return spec.submodule_search_locations[0]
    return None

# Build variant, set by build.ps1: "cpu" (default), "cuda", or "vulkan". Whichever
# llama-cpp-python build is installed in the build venv is what gets packaged (the
# binaries glob below grabs its ggml-*.dll automatically). This flag only controls
# the extra CUDA runtime DLLs (CUDA only) and the output folder name. The Vulkan
# variant needs no extra DLLs - its loader (vulkan-1.dll) ships with the GPU driver.
VARIANT = os.environ.get("MOCCA_BUILD_VARIANT", "cpu").lower()
CUDA = VARIANT == "cuda"
APP_DIR_NAME = {"cuda": "Mocca-CUDA", "vulkan": "Mocca-Vulkan"}.get(VARIANT, "Mocca")

# Bundle the web assets at the archive root so src/paths.py finds them under
# sys._MEIPASS, plus the VERSION file so src/version.py reports the right version.
datas = [
    (os.path.join(PROJECT_ROOT, "templates"), "templates"),
    (os.path.join(PROJECT_ROOT, "static"), "static"),
    (os.path.join(PROJECT_ROOT, "VERSION"), "."),
]

# llama.cpp's native shared libraries (the actual inference engine), globbed from
# the installed package's lib/ folder. This grabs whatever build is installed -
# ggml-cpu.dll for the CPU build, or the much larger ggml-cuda.dll for the CUDA
# build - without importing llama_cpp (see _package_dir).
binaries = []
_llama_dir = _package_dir("llama_cpp")
if _llama_dir:
    for dll in glob.glob(os.path.join(_llama_dir, "lib", "*.dll")):
        binaries.append((dll, os.path.join("llama_cpp", "lib")))
else:
    print("[mocca.spec] WARNING: llama_cpp not installed; the build can't run models")

# For the CUDA variant, also bundle the CUDA runtime DLLs (from the nvidia-*-cu12
# wheels) right next to ggml-cuda.dll. llama-cpp-python's loader puts its lib/
# folder on PATH, so placing cudart/cublas there lets ggml-cuda.dll resolve them
# without a system CUDA Toolkit. (Without this, the CUDA build fails to load.)
if CUDA:
    nvidia_spec = importlib.util.find_spec("nvidia")
    locations = list(nvidia_spec.submodule_search_locations) if nvidia_spec else []
    cuda_dlls = []
    for root in locations:
        cuda_dlls += glob.glob(os.path.join(root, "*", "bin", "*.dll"))
    if not cuda_dlls:
        raise SystemExit(
            "[mocca.spec] CUDA variant requested but no nvidia-*-cu12 runtime "
            "DLLs found. Run 'python scripts/setup.py --cuda' in the build venv."
        )
    binaries += [(dll, os.path.join("llama_cpp", "lib")) for dll in cuda_dlls]
    print(f"[mocca.spec] CUDA variant: bundling {len(cuda_dlls)} CUDA runtime DLL(s)")

# uvicorn, pystray, and our own package pull in submodules dynamically, so
# collect them explicitly rather than relying on static-import analysis.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("pystray")
    + collect_submodules("src")
)

a = Analysis(
    [LAUNCHER],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mocca",
    console=False,  # Windowed app; the tray icon provides Open / Quit.
    icon=os.path.join(PROJECT_ROOT, "static", "images", "favicon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name=APP_DIR_NAME,  # "Mocca" (CPU) or "Mocca-CUDA" - keeps both side by side.
)
