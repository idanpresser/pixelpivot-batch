# Assemble a minimal self-contained deploy\ folder for air-gapped Windows deployment.
#
# Run from the project root on an ONLINE machine with all vendored binaries
# and the embedded Python already present.
#
# Result: deploy\ can be copied to any Windows 10+ machine; double-click start.bat.
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path '.').Path,
    [string]$DeployDir   = 'deploy',
    [switch]$SkipManifest
)
$ErrorActionPreference = 'Stop'

function Step([string]$msg)              { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Require([string]$path, [string]$label) {
    if (-not (Test-Path $path)) { throw "Missing $label at $path" }
    Write-Host "  OK: $label"
}

$DeployDir = Join-Path $ProjectRoot $DeployDir

# ---- 1. Preflight ----
Step "Preflight"
$pyEmbed = Join-Path $ProjectRoot 'python-3.14.5-embed-amd64'
Require "$pyEmbed\python.exe"                              "Embedded Python"
Require (Join-Path $ProjectRoot 'vendor\wheels')           "Wheel mirror"
Require (Join-Path $ProjectRoot 'vendor\node\node.exe')    "Node.js"
Require (Join-Path $ProjectRoot 'bin\ffmpeg\ffmpeg.exe')   "ffmpeg"
Require (Join-Path $ProjectRoot 'bin\magick\magick.exe')   "magick"
Require (Join-Path $ProjectRoot 'bin\vips\bin\vips.exe')   "vips"

$wheelCount = (Get-ChildItem (Join-Path $ProjectRoot 'vendor\wheels') -Filter '*.whl' -ErrorAction SilentlyContinue).Count
if ($wheelCount -lt 10) { throw "Wheel mirror has only $wheelCount wheels -- run download_wheels.ps1 first" }
Write-Host "  OK: $wheelCount wheels found"

# ---- 2. Clean (preserve data\) ----
Step "Clean deploy\"
if (Test-Path $DeployDir) {
    Get-ChildItem $DeployDir -Exclude 'data' | Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Force $DeployDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $DeployDir 'data') | Out-Null

# ---- 3. Stage app source ----
Step "Stage app\"
& robocopy (Join-Path $ProjectRoot 'app') (Join-Path $DeployDir 'app') `
    /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null

# ---- 4. Stage embedded Python ----
Step "Stage embedded Python"
& robocopy $pyEmbed (Join-Path $DeployDir 'python-3.14.5-embed-amd64') /E /NFL /NDL /NJH /NJS | Out-Null

# ---- 5. Stage Node.js ----
Step "Stage Node.js"
& robocopy (Join-Path $ProjectRoot 'vendor\node') (Join-Path $DeployDir 'vendor\node') `
    /E /NFL /NDL /NJH /NJS | Out-Null

# ---- 6. Stage vendored binaries ----
Step "Stage bin\ (ffmpeg, magick, vips)"
& robocopy (Join-Path $ProjectRoot 'bin') (Join-Path $DeployDir 'bin') /E /NFL /NDL /NJH /NJS | Out-Null

# ---- 7. Copy wheel mirror ----
Step "Copy wheel mirror"
& robocopy (Join-Path $ProjectRoot 'vendor\wheels') (Join-Path $DeployDir 'vendor\wheels') `
    /E /NFL /NDL /NJH /NJS | Out-Null

# ---- 8. Pre-install wheels ----
Step "Pre-install wheels -> vendor\site-packages\"
$sitePackages = Join-Path $DeployDir 'vendor\site-packages'
New-Item -ItemType Directory -Force $sitePackages | Out-Null
$py     = Join-Path $DeployDir 'python-3.14.5-embed-amd64\python.exe'
$wheels = Join-Path $DeployDir 'vendor\wheels'

& $py -m pip install `
    --no-index `
    --find-links $wheels `
    --target $sitePackages `
    --no-deps `
    (Join-Path $ProjectRoot '.') `
    2>&1 | Write-Host
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

# ---- 9. Wire up embedded Python package discovery ----
Step "Configure embedded Python (.pth)"
# Uncomment 'import site' so .pth files are processed
$embedPth = Join-Path $DeployDir 'python-3.14.5-embed-amd64\python314._pth'
if (Test-Path $embedPth) {
    (Get-Content $embedPth) -replace '^#import site', 'import site' | Set-Content $embedPth
    Write-Host "  Enabled 'import site' in python314._pth"
}
# Add a .pth pointing at our site-packages (path relative to the embed dir)
"..\vendor\site-packages" | Out-File `
    (Join-Path $DeployDir 'python-3.14.5-embed-amd64\pixelpivot.pth') `
    -Encoding ascii -NoNewline
Write-Host "  Wrote pixelpivot.pth"

# ---- 10. Copy launchers + project metadata ----
Step "Copy launchers"
Copy-Item (Join-Path $ProjectRoot 'Run-PixelPivot.ps1') $DeployDir -Force
Copy-Item (Join-Path $ProjectRoot 'start.bat')          $DeployDir -Force
Copy-Item (Join-Path $ProjectRoot 'pyproject.toml')     $DeployDir -Force

# ---- 11. Manifest ----
if (-not $SkipManifest) {
    $manifestScript = Join-Path $ProjectRoot 'scripts\manifest.ps1'
    if (Test-Path $manifestScript) {
        Step "Generate MANIFEST.sha256"
        & $manifestScript -Mode create -Root $DeployDir
    } else {
        Write-Host "  Skipping manifest (scripts\manifest.ps1 not found)"
    }
}

$fileCount = (Get-ChildItem $DeployDir -Recurse -File).Count
Write-Host "`nDeploy folder ready: $DeployDir ($fileCount files)" -ForegroundColor Green
