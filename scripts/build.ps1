# Build the Windows Mocca.exe with PyInstaller.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#
# Output: packaging\windows\dist\Mocca\Mocca.exe (a one-folder app). Zip that
# Mocca folder to hand to a friend. Build config: packaging\windows\mocca.spec.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # Project root (scripts\ is one level down).
$pkg = Join-Path $root "packaging\windows"
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    throw "Could not find the project venv at $py. Create it and install requirements first."
}

Write-Host "Installing build dependencies (pyinstaller, pystray, pillow)..."
& $py -m pip install -q pyinstaller pystray pillow

Write-Host "Running PyInstaller..."
& $py -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $pkg "dist") `
    --workpath (Join-Path $pkg "build") `
    (Join-Path $pkg "mocca.spec")

$exe = Join-Path $pkg "dist\Mocca\Mocca.exe"
if (Test-Path $exe) {
    Write-Host "`nBuilt: $exe"
    Write-Host "Zip the folder 'packaging\windows\dist\Mocca' and send it to your friends."
} else {
    throw "Build finished but $exe was not found - check the PyInstaller output above."
}
