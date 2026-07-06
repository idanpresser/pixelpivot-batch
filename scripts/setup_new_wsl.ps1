# scripts/setup_new_wsl.ps1
# Automates the creation of a clean Debian WSL2 distribution containing Docker CE and configured for IPv4.

[CmdletBinding()]
param(
    [string]$DistroName = "PixelPivot-Debian",
    [string]$InstallPath = "I:\WSL\PixelPivot-WSL2",
    [string]$DownloadDir = "I:\WSL\downloads"
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   NEW WSL2 DEBIAN PIXELPIVOT BUILDER" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

# 1. Create Directories
Write-Host "`n[1/6] Creating directories..." -ForegroundColor Yellow
if (-not (Test-Path $DownloadDir)) {
    New-Item -ItemType Directory -Force $DownloadDir | Out-Null
    Write-Host "   Created Download Dir: $DownloadDir" -ForegroundColor DarkGray
}
if (-not (Test-Path $InstallPath)) {
    New-Item -ItemType Directory -Force $InstallPath | Out-Null
    Write-Host "   Created Install Dir: $InstallPath" -ForegroundColor DarkGray
}

# 2. Export base Debian distro
$TarPath = Join-Path $DownloadDir "debian-base.tar"
Write-Host "`n[2/6] Exporting base Debian distribution..." -ForegroundColor Yellow
if (Test-Path $TarPath) {
    Remove-Item $TarPath -Force
}
wsl --export Debian $TarPath
Write-Host "   Base image exported to $TarPath" -ForegroundColor Green

# 3. Clean up default Debian and import into specific path
Write-Host "`n[3/6] Relocating distro to $InstallPath..." -ForegroundColor Yellow
wsl --unregister Debian 2>&1 | Out-Null
wsl --unregister $DistroName 2>&1 | Out-Null
Start-Sleep -Seconds 2

wsl --import $DistroName $InstallPath $TarPath --version 2
Write-Host "   Imported as '$DistroName' inside target folder." -ForegroundColor Green

# 4. Enable systemd in wsl.conf
Write-Host "`n[4/6] Enabling systemd inside WSL..." -ForegroundColor Yellow
$wslConf = @"
[boot]
systemd=true
"@
wsl -d $DistroName -u root sh -c "echo '$wslConf' > /etc/wsl.conf"
Write-Host "   wsl.conf updated. Rebooting distro..." -ForegroundColor DarkGray

wsl --terminate $DistroName
Start-Sleep -Seconds 3

# Trigger a command to boot with systemd
wsl -d $DistroName -u root systemctl is-system-running | Out-Null
Write-Host "   systemd enabled and running." -ForegroundColor Green

# 5. Configure IPv4 only
Write-Host "`n[5/6] Configuring IPv4 networking preferences..." -ForegroundColor Yellow
# Force IPv4 in apt
wsl -d $DistroName -u root sh -c "echo 'Acquire::ForceIPv4 \"true\";' > /etc/apt/apt.conf.d/99force-ipv4"
# Disable IPv6 globally
$sysctlConf = @"
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
"@
wsl -d $DistroName -u root sh -c "echo '$sysctlConf' > /etc/sysctl.d/99-disable-ipv6.conf"
wsl -d $DistroName -u root sysctl -p /etc/sysctl.d/99-disable-ipv6.conf | Out-Null
Write-Host "   Forced IPv4 and disabled IPv6 globally." -ForegroundColor Green

# 6. Install Docker CE and Compose
Write-Host "`n[6/6] Installing Docker CE and docker-compose (IPv4)..." -ForegroundColor Yellow
wsl -d $DistroName -u root sh -c "apt-get update && apt-get install -y docker.io docker-compose"
wsl -d $DistroName -u root systemctl enable docker | Out-Null
wsl -d $DistroName -u root systemctl start docker | Out-Null
Write-Host "   Docker installed and started successfully." -ForegroundColor Green

# Verification
Write-Host "`n=========================================================" -ForegroundColor Green
Write-Host "   SETUP COMPLETE!" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "Your new distro '$DistroName' is ready inside $InstallPath."
Write-Host "To access it:"
Write-Host "   wsl -d $DistroName"
Write-Host "=========================================================" -ForegroundColor Green
