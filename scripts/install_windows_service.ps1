# install_windows_service.ps1 — Installs PixelPivot Batch Engine as a Windows Service using NSSM.
#
# Run as Administrator.
#
# Usage:
#   .\scripts\install_windows_service.ps1 -NssmPath "C:\path\to\nssm.exe"

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$NssmPath,
    
    [string]$ServiceName = "PixelPivotBatchEngine",
    [string]$ProjectRoot = (Resolve-Path '.').Path
)

$ErrorActionPreference = 'Stop'

# Check for Admin rights
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
}

if (-not (Test-Path $NssmPath)) {
    Write-Error "NSSM executable not found at '$NssmPath'"
}

$pythonExe = Join-Path $ProjectRoot "python-3.14.5-embed-amd64\python.exe"
if (-not (Test-Path $pythonExe)) {
    $whichPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($whichPython) {
        $pythonExe = $whichPython.Path
    } else {
        Write-Error "python.exe not found in embedded folder or PATH"
    }
}

Write-Host "Registering Windows Service '$ServiceName'..." -ForegroundColor Cyan
Write-Host "Project Root: $ProjectRoot"
Write-Host "Python Executable: $pythonExe"

# Configure NSSM service parameters
& $NssmPath install $ServiceName $pythonExe "-m uvicorn app.batch_api.main:app --host 0.0.0.0 --port 8000"
& $NssmPath set $ServiceName AppDirectory $ProjectRoot
& $NssmPath set $ServiceName DisplayName "PixelPivot Batch Engine"
& $NssmPath set $ServiceName Description "High-throughput image conversion API & Watcher service."
& $NssmPath set $ServiceName Start SERVICE_AUTO_START

# Setup standard redirect logs for troubleshooting
$logDir = Join-Path $ProjectRoot "data\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
& $NssmPath set $ServiceName AppStdout (Join-Path $logDir "service_stdout.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $logDir "service_stderr.log")

# Restart throttling options
& $NssmPath set $ServiceName AppThrottle 1500
& $NssmPath set $ServiceName AppExit Default Restart

Write-Host "Service '$ServiceName' registered successfully." -ForegroundColor Green
Write-Host "To start the service, run: Start-Service $ServiceName" -ForegroundColor Green
