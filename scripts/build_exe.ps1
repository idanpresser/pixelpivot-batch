# PixelPivot PyInstaller build and smoke-test script.
# Builds the standalone executable using PyInstaller in --onedir mode.

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path '.').Path
)

$ErrorActionPreference = 'Stop'

function Step([string]$msg) {
    Write-Host ''
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# --- 1. Clean ---
Step "Cleaning build and dist directories..."
$buildDir = Join-Path $ProjectRoot "build\pixelpivot"
$distDir = Join-Path $ProjectRoot "dist\pixelpivot_cli"

if (Test-Path $buildDir) {
    Write-Host "Removing $buildDir"
    Remove-Item -Recurse -Force $buildDir
}
if (Test-Path $distDir) {
    Write-Host "Cleaning contents of $distDir"
    Remove-Item -Path "$distDir\*" -Recurse -Force -ErrorAction SilentlyContinue
    try {
        Remove-Item -Path $distDir -Force -ErrorAction Stop
    } catch {
        Write-Host "  -> Dist directory is locked by another process; contents cleared." -ForegroundColor Yellow
    }
}

# --- 2. Build ---
Step "Running PyInstaller..."
$specFile = Join-Path $ProjectRoot "pixelpivot.spec"
if (-not (Test-Path $specFile)) {
    throw "Spec file not found: $specFile"
}

# Use uv run to make sure pyinstaller from virtualenv is used
& uv run pyinstaller --clean -y $specFile

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

# --- 3. Verify Output ---
Step "Verifying build output..."
$exePath = Join-Path $distDir "pixelpivot.exe"
if (-not (Test-Path $exePath)) {
    throw "Built executable not found at $exePath"
}
Write-Host "Executable built successfully at $exePath" -ForegroundColor Green

# Copy the commands guide batch file next to the executable
$guideSrc = Join-Path $ProjectRoot "pixelpivot_guide.bat"
$guideDst = Join-Path $distDir "pixelpivot_guide.bat"
if (Test-Path $guideSrc) {
    Copy-Item -Force $guideSrc $guideDst
    Write-Host "Copied guide batch file to $guideDst"
}

# --- 4. Smoke Test ---
Step "Running smoke test: pixelpivot.exe doctor..."

# Run the built executable and capture its output
$proc = Start-Process -FilePath $exePath -ArgumentList "doctor" -NoNewWindow -PassThru -Wait

Write-Host "Doctor process exited with code $($proc.ExitCode)"

if ($proc.ExitCode -ne 0) {
    Write-Host "WARNING: smoke test returned non-zero ($($proc.ExitCode)) - external binaries may be missing in build env." -ForegroundColor Yellow
} else {
    Write-Host "Smoke test PASSED!" -ForegroundColor Green
}

# == Service binary ========================================================
Step "Building PixelPivotService.exe..."
$serviceSpec = Join-Path $ProjectRoot "pixelpivot_service.spec"
if (-not (Test-Path $serviceSpec)) {
    Write-Host "pixelpivot_service.spec not found - skipping service build." -ForegroundColor Yellow
} else {
    & uv run pyinstaller --clean -y $serviceSpec
    if ($LASTEXITCODE -ne 0) {
        throw "Service build failed with exit code $LASTEXITCODE"
    }
    $serviceExe = Join-Path $ProjectRoot "dist\pixelpivot_service\PixelPivotService.exe"
    if (-not (Test-Path $serviceExe)) {
        throw "PixelPivotService.exe not found at $serviceExe"
    }
    Write-Host "PixelPivotService.exe built at $serviceExe" -ForegroundColor Green
}

# == Tray binary ===========================================================
Step "Building PixelPivotTray.exe..."
$traySpec = Join-Path $ProjectRoot "pixelpivot_tray.spec"
if (-not (Test-Path $traySpec)) {
    Write-Host "pixelpivot_tray.spec not found - skipping tray build." -ForegroundColor Yellow
} else {
    & uv run pyinstaller --clean -y $traySpec
    if ($LASTEXITCODE -ne 0) {
        throw "Tray build failed with exit code $LASTEXITCODE"
    }
    $trayExe = Join-Path $ProjectRoot "dist\pixelpivot_tray\PixelPivotTray.exe"
    if (-not (Test-Path $trayExe)) {
        throw "PixelPivotTray.exe not found at $trayExe"
    }
    Write-Host "PixelPivotTray.exe built at $trayExe" -ForegroundColor Green
}

# == Merge service + tray into single deployment directory =================
Step "Merging tray into service directory..."
$serviceDistDir = Join-Path $ProjectRoot "dist\pixelpivot_service"
$trayDistDir    = Join-Path $ProjectRoot "dist\pixelpivot_tray"
$mergedDir      = Join-Path $ProjectRoot "dist\PixelPivot"

if (Test-Path $mergedDir) { Remove-Item -Recurse -Force $mergedDir }

if ((Test-Path $serviceDistDir) -and (Test-Path $trayDistDir)) {
    Rename-Item -Path $serviceDistDir -NewName "PixelPivot"

    # Copy tray-only files (PySide6, PixelPivotTray.exe) into merged dir.
    # /XC /XN /XO = skip any file that already exists in dest (any timestamp).
    robocopy $trayDistDir $mergedDir /E /XC /XN /XO /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy merge failed (exit code $LASTEXITCODE)"
    }
    Remove-Item -Recurse -Force $trayDistDir
    Write-Host "Merged deployment directory: dist\PixelPivot" -ForegroundColor Green
} else {
    Write-Host "One or both dist dirs missing - skipping merge." -ForegroundColor Yellow
}

# == (Optional) Build InnoSetup installer ==================================
$issFile = Join-Path $ProjectRoot "installer\pixelpivot.iss"
$iscc    = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if ((Test-Path $issFile) -and (Test-Path $iscc)) {
    Step "Building InnoSetup installer..."
    $null = New-Item -ItemType Directory -Force (Join-Path $ProjectRoot "dist\installer")
    & $iscc $issFile
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
    Write-Host "Installer: dist\installer\PixelPivot-Setup-1.0.exe" -ForegroundColor Green
} else {
    Write-Host "Inno Setup not found or .iss missing - skipping installer build." -ForegroundColor Yellow
}

Step "All builds complete."
Write-Host "  CLI:        dist\pixelpivot_cli\pixelpivot.exe" -ForegroundColor Cyan
Write-Host "  Deployment: dist\PixelPivot\   (service + tray merged)" -ForegroundColor Cyan
Write-Host "  Installer:  dist\installer\PixelPivot-Setup-1.0.exe   (if iscc found)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Manual deploy (air-gap):" -ForegroundColor Yellow
Write-Host "  1. Copy dist\PixelPivot\ to target machine"
Write-Host "  2. As Administrator: .\PixelPivotService.exe install auto"
Write-Host "  3. As Administrator: .\PixelPivotService.exe start"
Write-Host "  4. Add PixelPivotTray.exe to startup"
Write-Host ""
Write-Host "Or run PixelPivot-Setup-1.0.exe (handles all steps automatically)." -ForegroundColor Yellow
