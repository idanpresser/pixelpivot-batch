# End-to-end smoke test for an air-gapped PixelPivot install.
#
# Assumes the API is already running on http://localhost:8000
# (sandbox_init.ps1 launches it). Runs a real conversion against a handful of
# samples from image_samples/ for each in-scope converter, polls the status
# endpoint until the batch finishes, and exits non-zero on ANY failure.
#
# Exit codes:
#   0  pass
#   2  API not reachable
#   3  at least one converter reported a failure
#   4  batch did not complete within timeout
#   5  no samples found to test with

[CmdletBinding()]
param(
    [string]$ApiUrl     = 'http://127.0.0.1:8000',
    [string]$ProjectRoot = (Resolve-Path '.').Path,
    [string[]]$Tools    = @('magick','vips'),
    [string]$Format     = 'webp',
    [int]$SampleCount   = 5,
    [int]$TimeoutSec    = 120
)

$ErrorActionPreference = 'Stop'

function Fail([int]$code, [string]$msg) {
    Write-Host $msg -ForegroundColor Red
    exit $code
}

# --- 1. API reachability ---
try {
    $health = Invoke-RestMethod -Uri "$ApiUrl/" -TimeoutSec 5
    if (-not $health.message) { Fail 2 "API at $ApiUrl returned unexpected body: $health" }
    Write-Host "API up: $($health.message)" -ForegroundColor Green
} catch {
    Fail 2 "API at $ApiUrl not reachable: $_"
}

# --- 2. Stage a temp source dir with N sample images ---
$samplesRoot = Join-Path $ProjectRoot 'image_samples'
if (-not (Test-Path $samplesRoot)) {
    Fail 5 "Sample dir not found: $samplesRoot (bundle is incomplete)"
}

$candidates = Get-ChildItem $samplesRoot -File -Include *.jpg,*.jpeg,*.png -Recurse | Select-Object -First $SampleCount
if ($candidates.Count -lt $SampleCount) {
    Fail 5 "Need $SampleCount samples, found $($candidates.Count) under $samplesRoot"
}

$smokeRoot   = Join-Path $ProjectRoot 'out\smoke'
$sourceDir   = Join-Path $smokeRoot   'in'
$targetDir   = Join-Path $smokeRoot   'out'
if (Test-Path $smokeRoot) { Remove-Item $smokeRoot -Recurse -Force }
New-Item -ItemType Directory -Force $sourceDir | Out-Null
New-Item -ItemType Directory -Force $targetDir | Out-Null

foreach ($f in $candidates) {
    Copy-Item $f.FullName (Join-Path $sourceDir $f.Name)
}
Write-Host "Staged $($candidates.Count) samples in $sourceDir"

# --- 3. For each tool, fire a batch and wait for completion ---
$overallFailures = 0

foreach ($tool in $Tools) {

    Write-Host ''
    Write-Host "--- $tool / $Format ---" -ForegroundColor Cyan

    $body = @{
        source_dir    = $sourceDir
        target_dir    = $targetDir
        target_format = $Format
        tool          = $tool
        category      = 'general'
        trigger_type  = 'smoke'
    } | ConvertTo-Json

    try {
        $start = Invoke-RestMethod -Uri "$ApiUrl/api/v1/batch/start" `
                                   -Method Post `
                                   -Body $body `
                                   -ContentType 'application/json' `
                                   -TimeoutSec 10
    } catch {
        Write-Host "  start failed: $_" -ForegroundColor Red
        $overallFailures++
        continue
    }

    $runId = $start.run_id
    Write-Host "  run_id=$runId queued; polling..."

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $final    = $null

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        try {
            $status = Invoke-RestMethod -Uri "$ApiUrl/api/v1/batch/status/$runId" -TimeoutSec 5
        } catch {
            continue
        }
        if ($status.status -in @('completed','failed')) {
            $final = $status
            break
        }
    }

    if (-not $final) {
        Write-Host "  TIMEOUT after ${TimeoutSec}s" -ForegroundColor Red
        $overallFailures++
        exit 4
    }

    if ($final.status -ne 'completed') {
        Write-Host "  status=$($final.status) (expected completed)" -ForegroundColor Red
        $overallFailures++
        continue
    }

    $summary = $final.summary
    if (-not $summary) {
        Write-Host "  completed but summary missing" -ForegroundColor Yellow
        $overallFailures++
        continue
    }

    $success = [int]$summary.success_count
    $failure = [int]$summary.failure_count
    Write-Host "  success=$success failure=$failure"

    if ($failure -gt 0 -or $success -lt $SampleCount) {
        $overallFailures++
    }
}

Write-Host ''
if ($overallFailures -gt 0) {
    Fail 3 "SMOKE FAILED ($overallFailures of $($Tools.Count) converters had failures)"
}

Write-Host 'SMOKE OK' -ForegroundColor Green
exit 0
