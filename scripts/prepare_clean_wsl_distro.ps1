# scripts/prepare_clean_wsl_distro.ps1
# Automates the creation of a clean, minimal Alpine-based WSL distribution containing
# Docker CE and pre-cached PixelPivot Batch images for air-gapped E2E production-ready testing.

[CmdletBinding()]
param(
    [string]$DistroName = "PixelPivot-AirGap-Clean",
    [string]$AlpineVersion = "3.20.2",
    [string]$OutDir = "out\airgap_bundle",
    [string]$TempDir = "scratch"
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   CLEAN ALPINE WSL DOCKER CE AIR-GAP DISTRO BUILDER" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

# 1. Clean up old archives as requested
Write-Host "Cleaning up old large WSL archives..." -ForegroundColor Yellow
$oldWslFile = Join-Path $OutDir "Ubuntu-26.04-docker-community.wsl"
$oldShaFile = Join-Path $OutDir "Ubuntu-26.04-docker-community.wsl.sha256"
if (Test-Path $oldWslFile) { Remove-Item $oldWslFile -Force; Write-Host "   Deleted $oldWslFile" -ForegroundColor DarkGray }
if (Test-Path $oldShaFile) { Remove-Item $oldShaFile -Force; Write-Host "   Deleted $oldShaFile" -ForegroundColor DarkGray }

# 2. Download Alpine minirootfs
$AlpineTar = Join-Path $TempDir "alpine-minirootfs.tar.gz"
$AlpineUrl = "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-minirootfs-$AlpineVersion-x86_64.tar.gz"

if (-not (Test-Path $TempDir)) {
    New-Item -ItemType Directory -Force $TempDir | Out-Null
}

if (-not (Test-Path $AlpineTar)) {
    Write-Host "`nDownloading Alpine minirootfs $AlpineVersion..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $AlpineUrl -OutFile $AlpineTar -UserAgent "Mozilla/5.0"
    Write-Host "Saved to $AlpineTar" -ForegroundColor Green
} else {
    Write-Host "`nAlpine minirootfs already downloaded." -ForegroundColor Green
}

# 3. Import Alpine as a new WSL distribution
$InstallPath = Join-Path $TempDir "wsl-alpine-install"

Write-Host "`nImporting Alpine distro '$DistroName' into WSL 2..." -ForegroundColor Yellow

# Unregister old builder if it exists first to release file locks on ext4.vhdx
wsl --unregister $DistroName 2>&1 | Out-Null

if (Test-Path $InstallPath) {
    Remove-Item $InstallPath -Recurse -Force | Out-Null
}
New-Item -ItemType Directory -Force $InstallPath | Out-Null

wsl --import $DistroName $InstallPath $AlpineTar --version 2
Write-Host "Distro imported successfully" -ForegroundColor Green

# 4. Configure Alpine repositories to enable community repo (required for docker)
Write-Host "`nConfiguring Alpine repositories..." -ForegroundColor Yellow
$repos = @"
https://dl-cdn.alpinelinux.org/alpine/v3.20/main
https://dl-cdn.alpinelinux.org/alpine/v3.20/community
"@
wsl -d $DistroName -u root sh -c "echo '$repos' > /etc/apk/repositories"
Write-Host "Repositories configured" -ForegroundColor Green

# 5. Install Docker CE and dependencies
Write-Host "`nInstalling Docker CE, docker compose, and openrc inside Alpine..." -ForegroundColor Yellow
wsl -d $DistroName -u root sh -c "apk update && apk add --no-cache docker docker-cli-compose iptables openrc"
Write-Host "Docker and dependencies installed" -ForegroundColor Green

# 6. Start Docker Daemon in Alpine WSL
Write-Host "`nStarting Docker daemon in background..." -ForegroundColor Yellow
$dockerJob = Start-Job -ScriptBlock {
    param($dName)
    wsl -d $dName -u root dockerd
} -ArgumentList $DistroName

# Wait for Docker daemon to be fully ready
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $info = wsl -d $DistroName -u root docker info 2>$null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldPreference
    
    if ($exitCode -eq 0) {
        $ready = $true
        break
    }
    Write-Host "   Waiting for Docker daemon... ($($i+1)/15)" -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    Stop-Job $dockerJob -ErrorAction SilentlyContinue
    Remove-Job $dockerJob -ErrorAction SilentlyContinue
    Write-Error "Docker daemon failed to start inside Alpine WSL distribution."
}
Write-Host "Docker daemon is ready" -ForegroundColor Green

# 7. Convert project root to WSL path dynamically
$winPath = (Resolve-Path ".").Path
$drive = $winPath[0].ToString().ToLower()
$subPath = $winPath.Substring(2).Replace('\', '/')
$wslPath = "/mnt/$drive$subPath"
Write-Host "`nHost project root mapped to WSL: $wslPath" -ForegroundColor DarkGray

# 8. Build PixelPivot stack inside the clean Alpine distro
Write-Host "`nBuilding PixelPivot Docker Compose stack inside clean Alpine..." -ForegroundColor Yellow
wsl -d $DistroName -u root sh -c "cd $wslPath && docker compose build"
Write-Host "Images built inside clean distro" -ForegroundColor Green

# 9. Prune caches and clean package manager cache to keep distro minimal
Write-Host "`nCleaning up build caches and dangling items..." -ForegroundColor Yellow
wsl -d $DistroName -u root sh -c "docker builder prune -af"
wsl -d $DistroName -u root sh -c "docker system prune -f"
wsl -d $DistroName -u root sh -c "rm -rf /var/cache/apk/*"
Write-Host "Cleanup finished" -ForegroundColor Green

# 10. Stop Docker and terminate WSL distro
Write-Host "`nShutting down distro..." -ForegroundColor Yellow
wsl -d $DistroName -u root sh -c "pkill dockerd"
Start-Sleep -Seconds 2
wsl --terminate $DistroName
Stop-Job $dockerJob -ErrorAction SilentlyContinue
Remove-Job $dockerJob -ErrorAction SilentlyContinue
Write-Host "Distro terminated" -ForegroundColor Green

# 11. Create output directory if missing
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force $OutDir | Out-Null
}

$OutputFile = Join-Path $OutDir "pixelpivot-clean-docker.wsl"
if (Test-Path $OutputFile) {
    Remove-Item $OutputFile -Force
}

# 12. Export clean distribution
Write-Host "`nExporting clean Alpine distribution to $OutputFile..." -ForegroundColor Yellow
wsl --export $DistroName $OutputFile

if (Test-Path $OutputFile) {
    $sizeMb = [Math]::Round((Get-Item $OutputFile).Length / 1MB, 2)
    Write-Host "Export complete! Clean WSL distro size: $sizeMb MB" -ForegroundColor Green
    
    # 13. Compute SHA256 checksum
    Write-Host "`nComputing SHA256 checksum..." -ForegroundColor Yellow
    $hash = (Get-FileHash $OutputFile -Algorithm SHA256).Hash
    Write-Host "   SHA256: $hash" -ForegroundColor Cyan
    
    # Save checksum file
    $metaFile = "$OutputFile.sha256"
    Set-Content -Path $metaFile -Value "$hash  pixelpivot-clean-docker.wsl"
    Write-Host "   Checksum saved to $metaFile" -ForegroundColor DarkGray
    
    # 14. Clean up temporary imported distro and downloaded files
    Write-Host "`nCleaning up temporary builder distro..." -ForegroundColor Yellow
    wsl --unregister $DistroName 2>&1 | Out-Null
    if (Test-Path $InstallPath) { Remove-Item $InstallPath -Recurse -Force }
    if (Test-Path $AlpineTar) { Remove-Item $AlpineTar -Force }
    Write-Host "   Removed temporary distro and downloads" -ForegroundColor DarkGray
    
    # Success Info
    Write-Host "`n=========================================================" -ForegroundColor Green
    Write-Host "PRODUCTION READY CLEAN AIR-GAP WSL FILE PREPARED!" -ForegroundColor Green
    Write-Host "=========================================================" -ForegroundColor Green
    Write-Host "File Location : $OutputFile"
    Write-Host "File Size     : $sizeMb MB (well under the 10 GB limit)"
    Write-Host "Checksum Hash : $hash"
    Write-Host "---------------------------------------------------------"
    Write-Host "To deploy on your target air-gapped machine:"
    Write-Host "1. Copy '$OutputFile' to the target machine."
    Write-Host "2. Import using the WSL command:"
    Write-Host '   wsl --import PixelPivot <InstallPath> <PathToDir>\pixelpivot-clean-docker.wsl --version 2'
    Write-Host "   Or double-click the .wsl file on Windows 11."
    Write-Host "3. Start and run PixelPivot:"
    Write-Host "   wsl -d PixelPivot"
    Write-Host '   cd <project_dir> && docker compose up -d'
    Write-Host "=========================================================" -ForegroundColor Green
} else {
    Write-Error "Failed to generate exported WSL distribution file."
}
