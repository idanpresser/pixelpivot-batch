# PixelPivot Wheel Downloader
# RUN THIS ON YOUR HOST MACHINE (WITH INTERNET) BEFORE STARTING THE SANDBOX

$ProjectRoot = Get-Item .
$WheelsDir = Join-Path $ProjectRoot "vendor\wheels"

if (-not (Test-Path $WheelsDir)) {
    New-Item -ItemType Directory -Path $WheelsDir
}

Write-Host "--- DOWNLOADING PIXELPIVOT DEPENDENCIES ---" -ForegroundColor Cyan

$PythonExe = "python.exe" # Assumes python is in host PATH

# Task 026: read the canonical dep list from scripts/air_gap_deps.txt so
# this script and sandbox_init.ps1 can never drift.
$DepListFile = Join-Path $PSScriptRoot "air_gap_deps.txt"
if (-not (Test-Path $DepListFile)) {
    Write-Host "CRITICAL ERROR: $DepListFile not found." -ForegroundColor Red
    Exit 1
}
$deps = Get-Content $DepListFile | Where-Object {
    $line = $_.Trim()
    $line -and -not $line.StartsWith("#")
} | ForEach-Object { $_.Trim() }

Write-Host "Downloading wheels to $WheelsDir..."
# Pin target: the sandbox runs CPython 3.14 (win_amd64). Make the closure
# reproducible regardless of host Python version -- if you bump the target,
# update PythonVersion / Abi / Platform here AND `requires-python` in
# pyproject.toml AND the embedded distro path in sandbox_init.ps1.
$PythonVersion = "314"
$Abi = "cp314"
$Platform = "win_amd64"

# Try to get binary wheels first to avoid build-time issues
& $PythonExe -m pip download $deps --dest $WheelsDir `
    --only-binary=:all: `
    --python-version=$PythonVersion --platform=$Platform --abi=$Abi --implementation=cp
# Fallback to allow SDists for those that don't have wheels (but we have setuptools now)
& $PythonExe -m pip download $deps --dest $WheelsDir `
    --python-version=$PythonVersion --platform=$Platform --abi=$Abi --implementation=cp

Write-Host "--- DOWNLOAD COMPLETE ---" -ForegroundColor Green
Write-Host "You can now start the air-gapped sandbox using PixelPivot.wsb"
