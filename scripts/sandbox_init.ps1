# PixelPivot Sandbox Initialization Script
# This script sets up the environment and launches the system within Windows Sandbox.

$ProjectRoot = "C:\PixelPivot"
Set-Location $ProjectRoot

Write-Host "--- INITIALIZING PIXELPIVOT BATCH ENGINE ---" -ForegroundColor Cyan

# 0. Pre-flight Cleanup (Fix for Errno 10048)
Write-Host "[0/4] Cleaning up existing processes..."
Get-Process | Where-Object { $_.ProcessName -match "python" -or $_.ProcessName -match "node" } | Stop-Process -Force -ErrorAction SilentlyContinue

# 1. Path Configuration
$PythonDir = "$ProjectRoot\python-3.14.5-embed-amd64"
$FFmpegDir = "$ProjectRoot\bin\ffmpeg"
$MagickDir = "$ProjectRoot\bin\magick"
$NodeDir = "$ProjectRoot\vendor\node"

# Search for libvips in standard locations (vendor or bin)
$VipsDir = Get-ChildItem -Path "$ProjectRoot\bin", "$ProjectRoot\vendor" -Filter "vips" -Directory | Select-Object -First 1
if (-not $VipsDir) {
    $VipsDir = Get-ChildItem -Path "$ProjectRoot\bin", "$ProjectRoot\vendor" -Filter "vips-dev-*" -Directory | Select-Object -First 1
}

if ($VipsDir) {
    $VipsBin = Join-Path $VipsDir.FullName "bin"
    Write-Host "[1/4] Configuring Environment Paths (with libvips)..."
    $env:PATH = "$PythonDir;$FFmpegDir;$MagickDir;$VipsBin;$NodeDir;$env:PATH"
} else {
    Write-Host "[1/4] Configuring Environment Paths (libvips NOT FOUND)..." -ForegroundColor Yellow
    $env:PATH = "$PythonDir;$FFmpegDir;$MagickDir;$NodeDir;$env:PATH"
}
$env:PYTHONPATH = "$ProjectRoot"

# 2. Python Environment Setup (Embedded Distro)
Write-Host "[2/4] Preparing Python Environment..."
$PthFile = "$PythonDir\python314._pth"
if (Test-Path $PthFile) {
    Write-Host "  -> Configuring ._pth file for module resolution..."
    # Reset ._pth to a clean state that includes project root (..) and enables site-packages
    $PthContent = "python314.zip`n.`n..`nimport site"
    Set-Content $PthFile $PthContent
}

# 3. Dependency Installation (Air-Gapped)
$WheelsDir = "$ProjectRoot\vendor\wheels"
Write-Host "[3/4] Installing dependencies from local wheels (AIR-GAPPED)..."

if (Test-Path $WheelsDir) {
    # Task 026: read the canonical dep list from the shared file so this
    # script and download_wheels.ps1 cannot drift.
    $DepListFile = Join-Path $PSScriptRoot "air_gap_deps.txt"
    if (-not (Test-Path $DepListFile)) {
        Write-Host "CRITICAL ERROR: $DepListFile not found." -ForegroundColor Red
        Exit
    }
    $allDeps = Get-Content $DepListFile | Where-Object {
        $line = $_.Trim()
        $line -and -not $line.StartsWith("#")
    } | ForEach-Object { $_.Trim() }

    # Stage 1: build-time essentials must land before stage 2 (with
    # --no-build-isolation). Keep that ordering even though the shared
    # list is flat -- the stage cut is by name, not by file structure.
    $buildTools = @("setuptools", "wheel", "pip", "cffi", "pkgconfig")
    $appDeps = $allDeps | Where-Object { $buildTools -notcontains $_ }

    Write-Host "  -> Installing build-time tools ($($buildTools -join ', '))..."
    & "$PythonDir\python.exe" -m pip install --no-index --find-links="$WheelsDir" $buildTools

    Write-Host "  -> Installing application dependencies (Non-Isolated Build)..."
    & "$PythonDir\python.exe" -m pip install --no-index --find-links="$WheelsDir" --no-build-isolation $appDeps
} else {
    Write-Host "CRITICAL ERROR: Wheels directory not found at $WheelsDir!" -ForegroundColor Red
    Write-Host "Please run scripts\download_wheels.ps1 on your host machine first."
    Exit
}

# 4. Launching Services
Write-Host "[4/4] Launching Microservices..."

# Start Backend in a new minimized window
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "cd $ProjectRoot; `$env:PATH='$env:PATH'; `$env:PYTHONPATH='$env:PYTHONPATH'; & '$PythonDir\python.exe' -m uvicorn app.batch_api.main:app --host 0.0.0.0 --port 8000" -WindowStyle Minimized

# Check for Node.js (System or Portable) to start Sharp Daemon
# Air-gap rule: never run `npm install` here -- the sandbox has
# <Networking>Disable</Networking> set, so any registry hit fails. The
# vendored `node_modules\sharp` is mapped in from the host (run `npm install`
# on the host once, then ship the tree to the sandbox).
if (Get-Command node -ErrorAction SilentlyContinue) {
    $SharpModule = Join-Path $ProjectRoot "services\sharp-daemon\node_modules\sharp"
    if (Test-Path $SharpModule) {
        Write-Host "  -> Node.js + node_modules\sharp found. Starting Sharp Daemon (offline)..."
        Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "cd $ProjectRoot\services\sharp-daemon; npm start" -WindowStyle Minimized
    } else {
        Write-Host "  -> node_modules\sharp not vendored in services/sharp-daemon. Sharp converter unavailable." -ForegroundColor Yellow
        Write-Host "     Run ``npm install`` on the host inside services/sharp-daemon before launching the sandbox to fix." -ForegroundColor Yellow
    }
} else {
    Write-Host "  -> Node.js not found. Sharp converter will be unavailable." -ForegroundColor Yellow
}

# Give the backend a moment to warm up
Start-Sleep -Seconds 3

# Start Frontend in a new window
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "cd $ProjectRoot; `$env:PATH='$env:PATH'; `$env:PYTHONPATH='$env:PYTHONPATH'; & '$PythonDir\python.exe' -m streamlit run app/web/batch_gui/main.py --server.port 8503" -WindowStyle Normal

Write-Host "--- DEPLOYMENT COMPLETE ---" -ForegroundColor Green
Write-Host "API: http://localhost:8000"
Write-Host "GUI: http://localhost:8503"
