# PixelPivot air-gapped Windows bundle assembler.
#
# Run on a HOST with internet access. Produces a self-contained directory
# under out\airgap_bundle\ ready to zip and sneakernet to an air-gapped
# Windows target.
#
# Steps:
#   1. Verify prerequisites (Python 3.14, wheel mirror, vendored binaries)
#   2. Clean and re-create out\airgap_bundle\
#   3. Stage application code + scripts + tools + tests
#   4. Stage runtime: embedded Python, wheels, node, ffmpeg, magick, vips
#   5. Stage project metadata (pyproject, README, LICENSE, PixelPivot.wsb)
#   6. Generate MANIFEST.sha256 over the final tree
#
# After this completes, run:
#   Compress-Archive -Path out\airgap_bundle\* -DestinationPath out\pixelpivot-airgap-$(Get-Date -f yyyyMMdd).zip

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path '.').Path,
    [string]$BundleDir   = 'out\airgap_bundle',
    [switch]$SkipManifest
)

$ErrorActionPreference = 'Stop'

# Resolve to absolute paths up front so $PSScriptRoot / cwd shenanigans
# can't surprise us mid-run.
$BundleDir = Join-Path $ProjectRoot $BundleDir

function Step([string]$msg) {
    Write-Host ''
    Write-Host "==> $msg" -ForegroundColor Cyan
}

# --- 1. Preflight ---

Step 'Preflight checks'

$pyEmbed = Join-Path $ProjectRoot 'python-3.14.5-embed-amd64'
if (-not (Test-Path "$pyEmbed\python.exe")) {
    throw "Embedded Python not found at $pyEmbed. Extract python-3.14.5-embed-amd64.zip first."
}

$wheels = Join-Path $ProjectRoot 'vendor\wheels'
$wheelCount = if (Test-Path $wheels) { (Get-ChildItem $wheels -Filter '*.whl' -ErrorAction SilentlyContinue | Measure-Object).Count } else { 0 }
if ($wheelCount -lt 30) {
    throw "Wheel mirror at $wheels has only $wheelCount wheels (expected 50+). Run scripts\download_wheels.ps1 first."
}
Write-Host "  Embedded Python OK : $pyEmbed"
Write-Host "  Wheel mirror OK    : $wheelCount wheels"

# Required vendored binaries — each is a load-bearing piece of the runtime.
# Pair = (display name, relative path under ProjectRoot)
$required = @(
    @('ffmpeg',   'bin\ffmpeg\ffmpeg.exe'),
    @('ffprobe',  'bin\ffmpeg\ffprobe.exe'),
    @('magick',   'bin\magick\magick.exe'),
    @('vips',     'bin\vips\bin\vips.exe'),
    @('node',     'vendor\node\node.exe')
)
foreach ($r in $required) {
    $full = Join-Path $ProjectRoot $r[1]
    if (-not (Test-Path $full)) { throw "Missing vendored binary: $($r[0]) at $full" }
    Write-Host "  $($r[0].PadRight(8)) OK : $($r[1])"
}

# --- 2. Clean bundle dir ---

Step "Cleaning $BundleDir"
if (Test-Path $BundleDir) { Remove-Item $BundleDir -Recurse -Force }
New-Item -ItemType Directory -Force $BundleDir | Out-Null

# Helper: copy with destination implicit from source path
function Copy-Into {
    param([string]$Source, [string]$DestParent = $BundleDir)
    $src = Join-Path $ProjectRoot $Source
    if (-not (Test-Path $src)) { throw "Source missing: $src" }
    $name = Split-Path $src -Leaf
    Copy-Item -Recurse -Force $src (Join-Path $DestParent $name)
    Write-Host "  + $Source"
}

# --- 3. Application code ---

Step 'Staging application code'
Copy-Into 'app'
Copy-Into 'scripts'
Copy-Into 'tools'
Copy-Into 'tests'
Copy-Into 'image_samples'        # needed for smoke_test.ps1
if (Test-Path (Join-Path $ProjectRoot '.streamlit')) {
    Copy-Into '.streamlit'
}

# --- 4. Runtime ---

Step 'Staging runtime (Python + wheels + node + binaries)'
Copy-Into 'python-3.14.5-embed-amd64'
# vendor/ — but exclude python/ (stale; canonical is python-3.14.5-embed-amd64)
# and bin/imagemagick (already deleted; was a dupe of bin/magick).
New-Item -ItemType Directory -Force (Join-Path $BundleDir 'vendor') | Out-Null
Copy-Item -Recurse -Force (Join-Path $ProjectRoot 'vendor\wheels') (Join-Path $BundleDir 'vendor\wheels')
Write-Host '  + vendor\wheels'
Copy-Item -Recurse -Force (Join-Path $ProjectRoot 'vendor\node')   (Join-Path $BundleDir 'vendor\node')
Write-Host '  + vendor\node'
if (Test-Path (Join-Path $ProjectRoot 'vendor\bin')) {
    Copy-Item -Recurse -Force (Join-Path $ProjectRoot 'vendor\bin') (Join-Path $BundleDir 'vendor\bin')
    Write-Host '  + vendor\bin'
}

Copy-Into 'bin'                  # ffmpeg, magick, vips

# --- 5. Project metadata ---

Step 'Staging project metadata'
$metaFiles = @(
    'pyproject.toml',
    'package.json',
    'package-lock.json',
    'PixelPivot.wsb',
    'README.md',
    'CLAUDE.md',
    'CHANGELOG.md',
    'LICENSE',
    'NOTICE',
    'air_gapped_guide.md'
)
foreach ($f in $metaFiles) {
    $src = Join-Path $ProjectRoot $f
    if (Test-Path $src) {
        Copy-Item -Force $src $BundleDir
        Write-Host "  + $f"
    } else {
        Write-Host "  - $f (skipped, not present)" -ForegroundColor Yellow
    }
}

# --- 6. Manifest ---

if (-not $SkipManifest) {
    Step 'Generating SHA256 manifest'
    & (Join-Path $ProjectRoot 'scripts\manifest.ps1') -Mode create -Root $BundleDir
    if ($LASTEXITCODE -ne 0) { throw "manifest.ps1 create failed (exit $LASTEXITCODE)" }
}

# --- Done ---

Step 'Bundle ready'
$size = (Get-ChildItem $BundleDir -Recurse -File | Measure-Object Length -Sum).Sum
$mb   = [Math]::Round($size / 1MB, 1)
Write-Host "  $BundleDir"
Write-Host "  $mb MB total"
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host "  Compress-Archive -Path $BundleDir\* -DestinationPath out\pixelpivot-airgap-$(Get-Date -f yyyyMMdd).zip"
Write-Host "  Then transfer the ZIP to the target host."
