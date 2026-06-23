# PixelPivot Batch Engine — self-contained launcher.
# Works from any path: uses $PSScriptRoot to find the deploy root.
[CmdletBinding()]
param(
    [int]   $Port = 8000,
    [switch]$NoBrowser
)
$root = $PSScriptRoot

$py    = Join-Path $root 'python-3.14.5-embed-amd64\python.exe'
$node  = Join-Path $root 'vendor\node\node.exe'
$sharp = Join-Path $root 'app\scripts\sharp_daemon.js'
$db    = Join-Path $root 'data\pixelpivot.db'

if (-not (Test-Path $py))   { throw "Embedded Python not found: $py" }
if (-not (Test-Path $node)) { throw "Node.js not found: $node" }

$env:PATH = (
    (Join-Path $root 'bin\ffmpeg'),
    (Join-Path $root 'bin\magick'),
    (Join-Path $root 'bin\vips\bin'),
    (Join-Path $root 'vendor\node')
) -join ';' + ';' + $env:PATH

$env:PYTHONPATH         = Join-Path $root 'vendor\site-packages'
$env:PIXELPIVOT_DB_PATH = $db

New-Item -ItemType Directory -Force (Join-Path $root 'data') | Out-Null

Write-Host "Starting PixelPivot Batch Engine..." -ForegroundColor Cyan
Write-Host "  Root : $root"
Write-Host "  API  : http://localhost:$Port"

$sharpProc = Start-Process -FilePath $node -ArgumentList $sharp `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden
Write-Host "  Sharp PID: $($sharpProc.Id)"

$apiProc = Start-Process -FilePath $py `
    -ArgumentList "-m uvicorn app.batch_api.main:app --host 0.0.0.0 --port $Port" `
    -WorkingDirectory $root -PassThru -WindowStyle Normal
Write-Host "  API PID  : $($apiProc.Id)"

$deadline = (Get-Date).AddSeconds(30)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try { $r = Invoke-RestMethod "http://127.0.0.1:$Port/" -TimeoutSec 2 -EA Stop; $ready=$true; break } catch {}
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    $apiProc | Stop-Process -Force -EA SilentlyContinue
    $sharpProc | Stop-Process -Force -EA SilentlyContinue
    throw "API did not start within 30s"
}
Write-Host "API ready: http://localhost:$Port/docs" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow

try   { while ($true) { Start-Sleep -Seconds 5 } }
finally {
    Write-Host "Shutting down..."
    $apiProc   | Stop-Process -Force -EA SilentlyContinue
    $sharpProc | Stop-Process -Force -EA SilentlyContinue
}
