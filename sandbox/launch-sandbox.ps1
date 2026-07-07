#Requires -RunAsAdministrator
# Generates pixelpivot-sandbox.wsb with absolute paths and launches Windows Sandbox.
# Run from any directory: .\sandbox\launch-sandbox.ps1

$projectRoot = Split-Path -Parent $PSScriptRoot
$sandboxDir   = $PSScriptRoot
$airgapDir    = Join-Path $projectRoot "out\airgap_bundle"

# Check Sandbox feature is installed
$sbFeature = Get-WindowsOptionalFeature -Online -FeatureName "Containers-DisposableClientVM" -ErrorAction SilentlyContinue
if (-not $sbFeature -or $sbFeature.State -ne "Enabled") {
    Write-Error "Windows Sandbox is not enabled. Run: Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All"
    exit 1
}

$wsbContent = @"
<Configuration>
    <Networking>Enable</Networking>
    <MemoryInMB>8192</MemoryInMB>
    <MappedFolders>
        <MappedFolder>
            <HostFolder>$projectRoot</HostFolder>
            <SandboxFolder>C:\pixelpivot</SandboxFolder>
            <ReadOnly>true</ReadOnly>
        </MappedFolder>
        <MappedFolder>
            <HostFolder>$airgapDir</HostFolder>
            <SandboxFolder>C:\airgap</SandboxFolder>
            <ReadOnly>true</ReadOnly>
        </MappedFolder>
    </MappedFolders>
    <LogonCommand>
        <Command>PowerShell -ExecutionPolicy Bypass -WindowStyle Normal -File C:\pixelpivot\sandbox\setup.ps1 -ProjectRoot C:\pixelpivot -AirgapDir C:\airgap</Command>
    </LogonCommand>
</Configuration>
"@

$wsbPath = Join-Path $sandboxDir "pixelpivot-sandbox.wsb"
$wsbContent | Set-Content -Path $wsbPath -Encoding UTF8
Write-Host "Launching sandbox: $wsbPath"
Start-Process $wsbPath
