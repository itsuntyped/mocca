# Build the Windows Mocca.exe with PyInstaller, in a CPU / CUDA / Vulkan variant.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1            # cpu + cuda
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cpu
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cuda
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant vulkan
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant all     # all three
#
# Output (one-folder apps under packaging\windows\dist):
#   Mocca\Mocca.exe          - CPU build, runs on any Windows PC.
#   Mocca-CUDA\Mocca.exe     - NVIDIA GPU build (bundles the CUDA runtime, much larger).
#   Mocca-Vulkan\Mocca.exe   - NVIDIA/AMD/Intel GPU build via Vulkan; needs only an
#                              up-to-date GPU driver (the Vulkan loader ships with it).
# Zip the folder you want and hand it over. Build config: packaging\windows\mocca.spec.
#
# Each variant builds in its own throwaway venv (.venv-build-<variant>) so your
# development .venv is never touched. Requires Python 3.12 (the prebuilt CUDA
# wheel targets 3.11/3.12; the Vulkan/CPU wheels work on any 3.x).

param(
    [ValidateSet("cpu", "cuda", "vulkan", "both", "all")]
    [string]$Variant = "both"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # Project root (scripts\ is one level down).
$pkg = Join-Path $root "packaging\windows"

$variants = switch ($Variant) {
    "both" { @("cpu", "cuda") }
    "all"  { @("cpu", "cuda", "vulkan") }
    default { @($Variant) }
}

function Get-DirName($v) {
    switch ($v) { "cuda" { "Mocca-CUDA" } "vulkan" { "Mocca-Vulkan" } default { "Mocca" } }
}

foreach ($v in $variants) {
    Write-Host "`n=== Building Mocca ($v) ===" -ForegroundColor Cyan
    $venv = Join-Path $root ".venv-build-$v"
    $py = Join-Path $venv "Scripts\python.exe"

    if (-not (Test-Path $py)) {
        Write-Host "Creating build venv (.venv-build-$v) with Python 3.12..."
        & py -3.12 -m venv $venv
        if (-not (Test-Path $py)) {
            throw "Failed to create $venv. Is Python 3.12 installed? (py -3.12 --version)"
        }
    }

    Write-Host "Installing dependencies ($v build)..."
    & $py (Join-Path $root "scripts\setup.py") "--$v"
    if ($LASTEXITCODE -ne 0) { throw "Dependency setup failed for the $v build." }

    Write-Host "Installing build tools (pyinstaller, pystray, pillow)..."
    & $py -m pip install -q pyinstaller pystray pillow
    if ($LASTEXITCODE -ne 0) { throw "Failed to install build tools for the $v build." }

    # The spec keys off this to bundle CUDA runtime DLLs and name the output dir.
    $env:MOCCA_BUILD_VARIANT = $v
    try {
        Write-Host "Running PyInstaller ($v)..."
        & $py -m PyInstaller --noconfirm --clean `
            --distpath (Join-Path $pkg "dist") `
            --workpath (Join-Path $pkg "build") `
            (Join-Path $pkg "mocca.spec")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for the $v build." }
    }
    finally {
        Remove-Item Env:\MOCCA_BUILD_VARIANT -ErrorAction SilentlyContinue
    }

    $exe = Join-Path $pkg ("dist\" + (Get-DirName $v) + "\Mocca.exe")
    if (Test-Path $exe) {
        Write-Host "Built: $exe" -ForegroundColor Green
    } else {
        throw "Build finished but $exe was not found - check the PyInstaller output above."
    }
}

Write-Host "`nDone. Output under packaging\windows\dist:" -ForegroundColor Cyan
foreach ($v in $variants) { Write-Host ("  " + (Get-DirName $v) + "  ($v)") }
Write-Host "Zip a folder to share. CPU build runs anywhere; CUDA needs an NVIDIA GPU + driver; Vulkan runs on NVIDIA/AMD/Intel with an up-to-date GPU driver."
