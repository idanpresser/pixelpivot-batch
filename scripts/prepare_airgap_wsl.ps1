# scripts/prepare_airgap_wsl.ps1
# Automates the building, cleaning, and exporting of the WSL distribution (containing Docker CE
# and the cached PixelPivot batch images) to a portable .wsl file for air-gapped testing.

[CmdletBinding()]
param(
    [string]$DistroName = "Ubuntu-26.04",
    [string]$OutDir     = "out\airgap_bundle"
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   WSL DOCKER CE AIR-GAP DISTRO EXPORTER" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

# 1. Verify WSL distribution
Write-Host "🔍 Verifying WSL distribution '$DistroName'..." -ForegroundColor Yellow
$distros = wsl -l -v | Out-String
if ($distros -notmatch $DistroName) {
    Write-Error "WSL distribution '$DistroName' is not installed. Installed distros are:`n$distros"
}
Write-Host "✅ Found WSL distribution '$DistroName'" -ForegroundColor Green

# 2. Rebuild images with latest codebase changes
Write-Host "`n📦 Rebuilding Docker Compose stack inside WSL..." -ForegroundColor Yellow
wsl -d $DistroName docker compose build
Write-Host "✅ Docker Compose stack built successfully" -ForegroundColor Green

# 3. Stop running containers to release database locks & release RAM
Write-Host "`n🛑 Stopping any running PixelPivot containers to ensure a clean filesystem state..." -ForegroundColor Yellow
wsl -d $DistroName docker compose down
Write-Host "✅ Containers stopped" -ForegroundColor Green

# 4. Prune build cache and unused images/volumes to minimize export size
Write-Host "`n🧹 Pruning Docker build cache and system cache to minimize .wsl file size..." -ForegroundColor Yellow
wsl -d $DistroName docker builder prune -f
wsl -d $DistroName docker system prune -f
Write-Host "✅ Docker environment cleaned" -ForegroundColor Green

# 5. Create output directory if missing
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force $OutDir | Out-Null
}

$OutputFile = Join-Path $OutDir "$DistroName-docker-community.wsl"
if (Test-Path $OutputFile) {
    Write-Host "`n⚠️ Existing export file found at $OutputFile. Removing..." -ForegroundColor Yellow
    Remove-Item $OutputFile -Force
}

# 6. Export the distribution
Write-Host "`n🚀 Exporting WSL distribution '$DistroName' to $OutputFile..." -ForegroundColor Yellow
Write-Host "   (This may take a few minutes depending on SSD speed)..." -ForegroundColor DarkGray
wsl --export $DistroName $OutputFile

if (Test-Path $OutputFile) {
    $sizeMb = [Math]::Round((Get-Item $OutputFile).Length / 1MB, 2)
    Write-Host "✅ Export complete! File size: $sizeMb MB" -ForegroundColor Green
    
    # 7. Compute SHA256 checksum
    Write-Host "`n🧮 Computing SHA256 checksum..." -ForegroundColor Yellow
    $hash = (Get-FileHash $OutputFile -Algorithm SHA256).Hash
    Write-Host "   SHA256: $hash" -ForegroundColor Cyan
    
    # Write metadata info
    $metaFile = "$OutputFile.sha256"
    Set-Content -Path $metaFile -Value "$hash  $($DistroName)-docker-community.wsl"
    Write-Host "   Checksum saved to $metaFile" -ForegroundColor DarkGray
    
    # Handoff instructions
    Write-Host "`n=========================================================" -ForegroundColor Green
    Write-Host "🎉 PRODUCTION READY AIR-GAP WSL FILE PREPARED!" -ForegroundColor Green
    Write-Host "=========================================================" -ForegroundColor Green
    Write-Host "To deploy on your target air-gapped machine:"
    Write-Host "1. Copy the file '$OutputFile' to the target machine."
    Write-Host "2. Verify the SHA256 checksum on target."
    Write-Host "3. Import it using the WSL import command:"
    Write-Host "   wsl --import PixelPivot <InstallPath> <PathToDir>\$($DistroName)-docker-community.wsl --version 2"
    Write-Host "   Or on Windows 11, double-click the .wsl file (or run: wsl --install --from-file <PathToDir>\$($DistroName)-docker-community.wsl)"
    Write-Host "4. Launch it:"
    Write-Host "   wsl -d PixelPivot"
    Write-Host "5. Navigate to the project root and start the containers:"
    Write-Host "   cd <project_dir> && docker compose up -d"
    Write-Host "=========================================================" -ForegroundColor Green
} else {
    Write-Error "Failed to generate exported WSL distribution file."
}
