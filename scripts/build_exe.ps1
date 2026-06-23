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
$distDir = Join-Path $ProjectRoot "dist\pixelpivot"

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
    throw "Smoke test failed! pixelpivot.exe doctor returned non-zero exit code $($proc.ExitCode)"
}

Write-Host "Smoke test PASSED!" -ForegroundColor Green
