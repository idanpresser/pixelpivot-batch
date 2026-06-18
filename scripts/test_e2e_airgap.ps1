# PixelPivot Air-Gap E2E Test Harness
#
# Launches the API from deploy\, runs 4 converters x AVIF over Z:\pics\real\,
# tests UNC raw path, exercises edge cases, then tears down.
#
# Exit codes:
#   0  All assertions pass
#   2  API failed to start within timeout
#   3  Matrix conversion failure
#   4  Batch did not complete within timeout
#   5  Dataset missing or incomplete
[CmdletBinding()]
param(
    [string]$DeployDir    = (Join-Path $PSScriptRoot '..\deploy'),
    [string]$PicsRoot     = 'Z:\pics',
    [string]$UncRoot      = '\\ipsds5\Share\pics',
    [string]$ApiUrl       = 'http://127.0.0.1:8000',
    [int]   $StartupSec   = 30,
    [int]   $BatchTimeout = 600,
    [int]   $ExpectedReal = 500
)
$ErrorActionPreference = 'Stop'

$report  = [System.Collections.Generic.List[string]]::new()
$allPass = $true

function Step([string]$msg)   { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Pass([string]$label) {
    $report.Add("PASS  $label")
    Write-Host "  [PASS] $label" -ForegroundColor Green
}
function Fail([string]$label, [string]$detail) {
    $report.Add("FAIL  $label  ($detail)")
    Write-Host "  [FAIL] $label -- $detail" -ForegroundColor Red
    $script:allPass = $false
}
function Abort([string]$msg, [int]$code) {
    Write-Host "ABORT: $msg" -ForegroundColor Red
    Write-Report
    exit $code
}
function Write-Report {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $lines = @("PixelPivot E2E Report -- $ts", ("=" * 50)) + $report + @(
        "",
        ("RESULT: " + ($script:allPass ? "PASS" : "FAIL"))
    )
    $lines | ForEach-Object { Write-Host $_ }
    $reportPath = Join-Path $DeployDir 'last_run.txt'
    try { $lines | Out-File $reportPath -Encoding utf8 } catch {}
}

$script:apiProc   = $null
$script:sharpProc = $null

function Cleanup {
    if ($script:apiProc)   { $script:apiProc   | Stop-Process -Force -ErrorAction SilentlyContinue }
    if ($script:sharpProc) { $script:sharpProc | Stop-Process -Force -ErrorAction SilentlyContinue }
    $outDir = Join-Path $PicsRoot 'out'
    if (Test-Path $outDir) {
        Remove-Item $outDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---- Phase 1: Preflight ----
Step "Phase 1: Preflight"
$resolvedDeploy = Resolve-Path $DeployDir -ErrorAction SilentlyContinue
if (-not $resolvedDeploy) {
    Abort "deploy\ not found at $DeployDir -- run build_deploy.ps1 first" 5
}
$DeployDir = $resolvedDeploy.Path
if (-not (Test-Path (Join-Path $DeployDir 'Run-PixelPivot.ps1'))) {
    Abort "Run-PixelPivot.ps1 missing from $DeployDir -- deploy folder incomplete" 5
}
$realCount = (Get-ChildItem (Join-Path $PicsRoot 'real') -File -ErrorAction SilentlyContinue).Count
if ($realCount -lt $ExpectedReal) {
    Abort "Z:\pics\real\ has $realCount files; need $ExpectedReal -- run gen_e2e_dataset.ps1 first" 5
}
try { Invoke-RestMethod "$ApiUrl/" -TimeoutSec 2 -ErrorAction Stop | Out-Null
      Abort "Port 8000 already in use. Stop existing API before running harness." 2
} catch {}
Pass "Preflight: deploy OK, $realCount real images, port 8000 free"

# ---- Phase 2: Launch API ----
Step "Phase 2: Launch API from deploy\"
$py = Join-Path $DeployDir 'python-3.14.5-embed-amd64\python.exe'
$env:PATH = (
    (Join-Path $DeployDir 'bin\ffmpeg'),
    (Join-Path $DeployDir 'bin\magick'),
    (Join-Path $DeployDir 'bin\vips\bin'),
    (Join-Path $DeployDir 'vendor\node')
) -join ';' + ';' + $env:PATH
$env:PYTHONPATH        = Join-Path $DeployDir 'vendor\site-packages'
$env:PIXELPIVOT_DB_PATH = Join-Path $DeployDir 'data\pixelpivot.db'
New-Item -ItemType Directory -Force (Join-Path $DeployDir 'data') | Out-Null

$script:apiProc = Start-Process -FilePath $py `
    -ArgumentList "-m uvicorn app.batch_api.main:app --host 127.0.0.1 --port 8000" `
    -WorkingDirectory $DeployDir -PassThru -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds($StartupSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try { Invoke-RestMethod "$ApiUrl/" -TimeoutSec 2 -ErrorAction Stop | Out-Null; $ready=$true; break } catch {}
    Start-Sleep -Seconds 2
}
if (-not $ready) { Cleanup; Abort "API did not start within ${StartupSec}s" 2 }
Pass "API started (PID $($script:apiProc.Id))"

# ---- Phase 3: Sharp Daemon ----
Step "Phase 3: Sharp daemon"
$node  = Join-Path $DeployDir 'vendor\node\node.exe'
$sharp = Join-Path $DeployDir 'app\scripts\sharp_daemon.js'
$script:sharpProc = Start-Process -FilePath $node -ArgumentList $sharp `
    -WorkingDirectory $DeployDir -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
if ($script:sharpProc.HasExited) {
    Fail "Sharp daemon" "exited immediately (code $($script:sharpProc.ExitCode))"
} else {
    Pass "Sharp daemon started (PID $($script:sharpProc.Id))"
}

# ---- Helper ----
function Invoke-Batch([string]$tool, [string]$srcDir, [string]$tgtDir, [string]$format = 'avif') {
    $body = @{
        source_dir    = $srcDir
        target_dir    = $tgtDir
        target_format = @($format)
        tool          = @($tool)
        category      = @('general')
        trigger_type  = 'e2e'
    } | ConvertTo-Json
    try {
        $resp = Invoke-RestMethod "$ApiUrl/api/v1/batch/start" -Method Post -Body $body `
            -ContentType 'application/json' -TimeoutSec 30
    } catch { return $null }
    $runId = $resp.run_id
    if (-not $runId) { return $null }

    $deadline = (Get-Date).AddSeconds($BatchTimeout)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        try {
            $s = Invoke-RestMethod "$ApiUrl/api/v1/batch/status/$runId" -TimeoutSec 10
            if ($s.status -in 'completed','failed') { return $s }
        } catch {}
    }
    return $null
}

# Flatten the status response so callers can read success_count / failure_count /
# cpu_avg uniformly. These live under .summary (populated only when completed).
function Get-BatchMetrics($s) {
    if (-not $s) { return $null }
    [pscustomobject]@{
        status        = $s.status
        success_count = if ($s.summary) { [int]$s.summary.success_count } else { 0 }
        failure_count = if ($s.summary) { [int]$s.summary.failure_count } else { 0 }
        cpu_avg       = if ($s.summary) { $s.summary.cpu_avg_pct } else { $null }
    }
}

# ---- Phase 4: Main Matrix ----
Step "Phase 4: Main matrix -- 4 tools x AVIF x $ExpectedReal images"
foreach ($tool in @('magick','vips','sharp','ffmpeg')) {
    $tgt = Join-Path $PicsRoot "out\$tool"
    New-Item -ItemType Directory -Force $tgt | Out-Null
    $t0 = Get-Date
    $s  = Get-BatchMetrics (Invoke-Batch $tool (Join-Path $PicsRoot 'real') $tgt)
    $elapsed = [int]((Get-Date) - $t0).TotalSeconds

    if (-not $s)                                    { Fail "$tool AVIF" "timeout after ${BatchTimeout}s"; continue }
    if ($s.status -eq 'failed')                     { Fail "$tool AVIF" "batch status=failed"; continue }
    if ($s.success_count -lt $ExpectedReal)         { Fail "$tool AVIF" "success=$($s.success_count) want $ExpectedReal"; continue }
    if ($s.failure_count -gt 0)                     { Fail "$tool AVIF" "failure_count=$($s.failure_count)"; continue }
    $cpuOk = $s.cpu_avg -ne $null
    if (-not $cpuOk)                                { Fail "$tool AVIF telemetry" "cpu_avg missing" }
    else { Pass "$tool AVIF | ${elapsed}s | $($s.success_count)/$ExpectedReal | CPU:OK" }
}

# ---- Phase 5: UNC Raw Path ----
Step "Phase 5: UNC raw path (magick AVIF on \\ipsds5\Share\)"
$uncTgt = Join-Path $PicsRoot 'out\unc_raw'
New-Item -ItemType Directory -Force $uncTgt | Out-Null
$s = Get-BatchMetrics (Invoke-Batch 'magick' "$UncRoot\real" $uncTgt)
if (-not $s)                              { Fail "UNC raw magick" "timeout" }
elseif ($s.success_count -lt $ExpectedReal) { Fail "UNC raw magick" "success=$($s.success_count)" }
elseif ($s.failure_count -gt 0)           { Fail "UNC raw magick" "failures=$($s.failure_count)" }
else { Pass "UNC raw path magick | $($s.success_count)/$ExpectedReal" }

# ---- Phase 6: Edge Cases ----
Step "Phase 6: Edge cases"
$ecRoot = Join-Path $PicsRoot 'edge_cases'

function Test-Edge([string]$label, [string]$sub, [bool]$expectFail, [bool]$expectSuccess) {
    $src = Join-Path $ecRoot $sub
    if (-not (Test-Path $src)) { Fail $label "source dir not found: $src"; return }
    $safeSub = $sub -replace '[\\:]','_'
    $tgt = Join-Path $PicsRoot "out\edge_$safeSub"
    New-Item -ItemType Directory -Force $tgt | Out-Null
    $s = Get-BatchMetrics (Invoke-Batch 'magick' $src $tgt)
    if (-not $s) { Fail $label "timeout"; return }
    # Verify API still alive
    try { Invoke-RestMethod "$ApiUrl/" -TimeoutSec 5 -ErrorAction Stop | Out-Null }
    catch { Fail $label "API unresponsive after edge case"; return }

    if ($expectFail  -and $s.failure_count -eq 0) { Fail $label "expected failure_count>0, got 0"; return }
    if ($expectSuccess -and $s.success_count -eq 0) { Fail $label "expected success_count>0, got 0"; return }
    Pass "$label | ok=$($s.success_count) fail=$($s.failure_count)"
}

Test-Edge "truncated files"  "truncated"  $true  $false
Test-Edge "empty file"       "empty"      $true  $false
Test-Edge "bad header"       "bad_header" $true  $false
Test-Edge "tiny 1x1"        "tiny"       $false $true

# huge 56MP -- must be rejected (MASSIVE_IMAGE filter)
$hugeS = Get-BatchMetrics (Invoke-Batch 'magick' (Join-Path $ecRoot 'huge') (Join-Path $PicsRoot 'out\edge_huge'))
if ($hugeS -and $hugeS.success_count -eq 0) {
    Pass "huge 56MP image rejected by MASSIVE_IMAGE filter"
} else {
    Fail "huge 56MP rejection" "success_count=$($hugeS.success_count) -- filter did not fire"
}

# Path edge cases: all should convert successfully
foreach ($pc in @('paths\unicode','paths\spaces','paths\deep\a\b\c\d','paths\longname')) {
    $src = Join-Path $ecRoot $pc
    if (Test-Path $src) {
        $safePc = $pc -replace '[\\:]','_'
        $s = Get-BatchMetrics (Invoke-Batch 'magick' $src (Join-Path $PicsRoot "out\edge_$safePc"))
        if ($s -and $s.failure_count -eq 0) { Pass "path: $pc" }
        else { Fail "path: $pc" "failure_count=$($s.failure_count)" }
    }
}

# ---- Phase 7: Teardown ----
Step "Phase 7: Teardown"
Cleanup
Pass "Teardown complete"

Write-Report
if (-not $allPass) { exit 3 }
exit 0
