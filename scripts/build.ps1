# Build the Windows Mocca.exe with PyInstaller, in a CPU and/or CUDA variant.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1            # both
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cpu
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cuda
#
# Output (one-folder apps under packaging\windows\dist):
#   Mocca\Mocca.exe        - CPU build, runs on any Windows PC.
#   Mocca-CUDA\Mocca.exe   - GPU build, needs an NVIDIA GPU + driver (much larger,
#                            it bundles the CUDA runtime).
# Zip the folder you want and hand it over. Build config: packaging\windows\mocca.spec.
#
# Each variant builds in its own throwaway venv (.venv-build-cpu / .venv-build-cuda)
# so your development .venv is never touched. Requires Python 3.12 (the prebuilt
# engine wheels - including CUDA - target 3.11/3.12).

param(
    [ValidateSet("cpu", "cuda", "both")]
    [string]$Variant = "both"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # Project root (scripts\ is one level down).
$pkg = Join-Path $root "packaging\windows"

$variants = if ($Variant -eq "both") { @("cpu", "cuda") } else { @($Variant) }

function Get-DirName($v) { if ($v -eq "cuda") { "Mocca-CUDA" } else { "Mocca" } }

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
Write-Host "Zip a folder to share. CPU build runs anywhere; the CUDA build needs an NVIDIA GPU + driver."
