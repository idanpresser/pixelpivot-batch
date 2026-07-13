; PixelPivot Batch Engine — Inno Setup 6 installer script
;
; Prerequisites:
;   Run scripts\build_exe.ps1 first to produce dist\PixelPivot\
;   then: iscc installer\pixelpivot.iss
;
; What this installer does:
;   - Copies dist\PixelPivot\ to {autopf}\PixelPivot\
;   - Registers PixelPivotBatchEngine as a Windows service (auto-start)
;   - Adds Windows Firewall inbound rules for port 8000 (API) and 8503 (GUI)
;   - Optionally adds PixelPivotTray.exe to HKCU Run at startup

#define AppName      "PixelPivot Batch Engine"
#define AppVersion   "1.0"
#define AppPublisher "PixelPivot"
#define ServiceName  "PixelPivotBatchEngine"
#define ServiceExe   "PixelPivotService.exe"
#define TrayExe      "PixelPivotTray.exe"
#define SourceDir    "..\dist\PixelPivot"

[Setup]
AppId={{6F2A3B1C-7D4E-4890-B5CA-DE0123456789}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\PixelPivot
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=..\dist\installer
OutputBaseFilename=PixelPivot-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0.19041

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Launch tray icon at Windows startup"; GroupDescription: "Startup options:"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Registry]
; Add tray to HKCU run (per-user, survives UAC, removed on uninstall)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "PixelPivotTray"; \
  ValueData: """{app}\{#TrayExe}"""; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
; Register service with SCM (auto-start)
Filename: "{app}\{#ServiceExe}"; Parameters: "--startup auto install"; \
  Flags: runhidden waituntilterminated; \
  StatusMsg: "Registering Windows service..."

; Start service immediately
Filename: "{app}\{#ServiceExe}"; Parameters: "start"; \
  Flags: runhidden waituntilterminated; \
  StatusMsg: "Starting PixelPivot service..."

; Inbound firewall rule — API port 8000
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""PixelPivot API"" protocol=TCP dir=in localport=8000 action=allow program=""{app}\{#ServiceExe}"""; \
  Flags: runhidden waituntilidle; \
  StatusMsg: "Adding firewall rule (API port 8000)..."

; Inbound firewall rule — GUI port 8503
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""PixelPivot GUI"" protocol=TCP dir=in localport=8503 action=allow program=""{app}\{#ServiceExe}"""; \
  Flags: runhidden waituntilidle; \
  StatusMsg: "Adding firewall rule (GUI port 8503)..."

; Offer to launch tray after install (skipped in silent mode)
Filename: "{app}\{#TrayExe}"; \
  Flags: postinstall nowait skipifsilent; \
  Description: "Launch PixelPivot tray icon now"

[UninstallRun]
; Stop service before file removal (ignore failure if already stopped)
Filename: "net"; Parameters: "stop {#ServiceName}"; \
  Flags: runhidden waituntilidle; RunOnceId: "SvcStop"

; Remove service from SCM
Filename: "{app}\{#ServiceExe}"; Parameters: "remove"; \
  Flags: runhidden waituntilidle; RunOnceId: "SvcRemove"

; Delete firewall rules
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""PixelPivot API"""; \
  Flags: runhidden waituntilidle; RunOnceId: "FwAPI"
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""PixelPivot GUI"""; \
  Flags: runhidden waituntilidle; RunOnceId: "FwGUI"

[UninstallDelete]
; Remove runtime data (logs, DB) — user-generated content
Type: filesandordirs; Name: "{app}\data"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // Stop any running service before PyInstaller _internal/ files are overwritten.
  // Intentionally ignores ResultCode — service may not be installed yet (fresh install).
  Exec(ExpandConstant('{sys}\net.exe'), 'stop {#ServiceName}', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
