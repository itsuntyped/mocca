# PyInstaller spec for the Windows build of Mocca.
#
# Build it with packaging\windows\build.ps1 (which installs the build-only
# dependencies first). Produces a one-folder app at packaging\windows\dist\Mocca
# whose entry point is Mocca.exe.

import os

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# SPECPATH is the directory holding this spec (packaging/windows); the project
# root is two levels up.
PROJECT_ROOT = os.path.dirname(os.path.dirname(SPECPATH))
LAUNCHER = os.path.join(SPECPATH, "launcher.py")

# Bundle the web assets at the archive root so src/paths.py finds them under
# sys._MEIPASS, plus any data files llama.cpp ships.
datas = [
    (os.path.join(PROJECT_ROOT, "templates"), "templates"),
    (os.path.join(PROJECT_ROOT, "static"), "static"),
]
datas += collect_data_files("llama_cpp")

# Bundle the standalone llmfit binary at the archive root so the app can run it
# for hardware detection. If llmfit isn't installed, the build still works and
# the app degrades gracefully (no hardware hints).
try:
    from llmfit import find_llmfit_bin
    datas += [(str(find_llmfit_bin()), ".")]
except Exception as exc:  # noqa: BLE001
    print(f"[mocca.spec] llmfit binary not bundled: {exc}")

# llama.cpp's native shared libraries (the actual inference engine).
binaries = collect_dynamic_libs("llama_cpp")

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
    name="Mocca",
)
