# setup_sharp_portable.ps1
# Provisions a portable Node.js and Sharp environment for Windows.

$NodeVersion = "v20.11.1"
$NodeArch = "x64"
$NodeDir = Join-Path $PSScriptRoot "..\node"
$NodeZip = "node-$NodeVersion-win-$NodeArch.zip"
$NodeUrl = "https://nodejs.org/dist/$NodeVersion/$NodeZip"

if (-not (Test-Path $NodeDir)) {
    New-Item -ItemType Directory -Force -Path $NodeDir | Out-Null
}

$ZipPath = Join-Path $NodeDir $NodeZip
if (-not (Test-Path $ZipPath)) {
    Write-Host "Downloading Node.js $NodeVersion..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $NodeUrl -OutFile $ZipPath
}

Write-Host "Extracting Node.js..." -ForegroundColor Cyan
Expand-Archive -Path $ZipPath -DestinationPath $NodeDir -Force

# Move files up one level if they are in a subfolder
$ExtractedDir = Join-Path $NodeDir "node-$NodeVersion-win-$NodeArch"
if (Test-Path $ExtractedDir) {
    Get-ChildItem -Path $ExtractedDir | Move-Item -Destination $NodeDir -Force
    Remove-Item $ExtractedDir -Recurse -Force
}

# Add node to current path for npm install
$env:Path = "$NodeDir;" + $env:Path

Write-Host "Installing Sharp dependencies..." -ForegroundColor Cyan
Set-Location "..\services\sharp-daemon"
& npm install

Write-Host "`nPortable Sharp environment ready!" -ForegroundColor Green
Write-Host "Node.js location: $NodeDir"
