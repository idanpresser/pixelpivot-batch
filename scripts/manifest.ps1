# SHA256 manifest tool for air-gap bundles.
#
# Usage:
#   .\scripts\manifest.ps1 create                # build out\airgap_bundle\MANIFEST.sha256
#   .\scripts\manifest.ps1 verify                # verify it
#   .\scripts\manifest.ps1 verify -Root C:\dest  # verify a bundle that was extracted elsewhere
#
# Exit codes:
#   0  ok
#   1  bad arguments / missing manifest
#   2  one or more files mismatch / missing

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('create','verify')]
    [string]$Mode,

    [string]$Root = 'out\airgap_bundle'
)

$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path $Root -ErrorAction Stop).Path
$Manifest = Join-Path $Root 'MANIFEST.sha256'

function Get-RelativePath {
    param([string]$Full, [string]$Base)
    # Trim the base, drop any leading separator the strip left behind,
    # normalise to forward slashes for cross-host portability of the manifest.
    return $Full.Substring($Base.Length).TrimStart('\','/').Replace('\','/')
}

if ($Mode -eq 'create') {

    Write-Host "Hashing files under $Root ..." -ForegroundColor Cyan

    $entries = Get-ChildItem $Root -Recurse -File |
               Where-Object { $_.Name -ne 'MANIFEST.sha256' } |
               ForEach-Object {
                   $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
                   $rel  = Get-RelativePath -Full $_.FullName -Base $Root
                   "$hash  $rel"
               }

    Set-Content -Path $Manifest -Value $entries -Encoding ASCII
    Write-Host "Wrote $($entries.Count) entries to $Manifest" -ForegroundColor Green
    exit 0
}

# --- verify ---

if (-not (Test-Path $Manifest)) {
    Write-Host "Manifest not found: $Manifest" -ForegroundColor Red
    exit 1
}

$mismatch = 0
$missing  = 0
$ok       = 0

Get-Content $Manifest | ForEach-Object {
    if (-not $_) { return }
    # Format: "<hash><two spaces><relative-path>"
    $hash, $rel = $_ -split '  ', 2
    if (-not $rel) { return }

    $full = Join-Path $Root ($rel -replace '/','\')
    if (-not (Test-Path $full)) {
        Write-Host "MISSING : $rel" -ForegroundColor Red
        $missing++
        return
    }

    $actual = (Get-FileHash $full -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $hash.ToLower()) {
        Write-Host "MISMATCH: $rel" -ForegroundColor Red
        Write-Host "  expected $hash"
        Write-Host "  got      $actual"
        $mismatch++
    } else {
        $ok++
    }
}

Write-Host ''
Write-Host "OK: $ok  MISMATCH: $mismatch  MISSING: $missing"

if ($mismatch -gt 0 -or $missing -gt 0) {
    Write-Host 'Bundle is NOT trustworthy. Re-transfer from a known-good source.' -ForegroundColor Red
    exit 2
} else {
    Write-Host 'All files verified.' -ForegroundColor Green
    exit 0
}
