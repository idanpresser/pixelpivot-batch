param(
    [string]$ProjectRoot = "C:\pixelpivot",
    [string]$AirgapDir   = "C:\airgap"
)

$ErrorActionPreference = "Stop"

function Log($msg) { Write-Host "[SETUP] $msg" -ForegroundColor Cyan }

# ── 1. WSL + Debian ──────────────────────────────────────────────────────────
Log "Installing WSL and Debian (requires nested virtualisation - Windows 11 22H2+)..."

# Modern WSL from Store installs without feature reboot on Win 11 Sandbox
# Enable VirtualMachinePlatform silently (already active in Sandbox kernel)
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart /quiet 2>$null

# Install WSL via winget (Store version, no reboot needed in Sandbox)
winget install --id Microsoft.WSL -e --accept-package-agreements --accept-source-agreements --silent
if ($LASTEXITCODE -ne 0) {
    # Fallback: inbox wsl --install
    wsl --install --no-distribution
    Start-Sleep -Seconds 5
}

# Install Debian distribution
Log "Installing Debian..."
winget install --id Debian.Debian -e --accept-package-agreements --accept-source-agreements --silent
if ($LASTEXITCODE -ne 0) {
    wsl --install -d Debian --no-launch
}

# Wait for Debian to register
$timeout = 120
$elapsed = 0
while ($elapsed -lt $timeout) {
    $distros = wsl --list --quiet 2>$null
    if ($distros -match "Debian") { break }
    Start-Sleep -Seconds 3
    $elapsed += 3
}
if ($elapsed -ge $timeout) { Write-Error "Debian WSL distro did not register in time." }

# ── 2. WSL networking (mirrored mode = IPv4 localhost access) ────────────────
Log "Configuring WSL mirrored networking..."
$wslconfig = @"
[wsl2]
networkingMode=mirrored
dnsTunneling=true
firewall=false
autoProxy=false
"@
$wslconfig | Set-Content -Path "$env:USERPROFILE\.wslconfig" -Encoding UTF8

# ── 3. Provision Debian root + Docker ───────────────────────────────────────
Log "Provisioning Debian: Docker install + image load..."

# Copy setup script into WSL (read-only mount can't execute directly due to noexec)
wsl -d Debian -u root -- bash -c "cp /mnt/c/pixelpivot/sandbox/debian-docker-setup.sh /root/setup.sh && chmod +x /root/setup.sh"
wsl -d Debian -u root -- bash /root/setup.sh $AirgapDir.Replace('\','/')

# ── 4. Done ──────────────────────────────────────────────────────────────────
Log "Setup complete."
Log "  API:  http://localhost:8000"
Log "  GUI:  http://localhost:8503"
Log ""
Log "To start services:"
Log "  wsl -d Debian -u root -- bash -c 'cd /pixelpivot && docker-compose up -d'"

# Open browser to GUI
Start-Sleep -Seconds 5
Start-Process "http://localhost:8503"
