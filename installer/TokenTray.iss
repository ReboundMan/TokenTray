; ============================================================================
;  TokenTray -- Inno Setup installer script
;
;  Wraps the PyInstaller --onedir build (dist\TokenTray\) into a single
;  Setup.exe that installs into %LOCALAPPDATA%\Programs\TokenTray, drops a
;  Start Menu shortcut, and (optionally) sets it to auto-launch at login.
;
;  Build with:
;      .\build.ps1 -Installer
;  or directly:
;      & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\TokenTray.iss
; ============================================================================

#define MyAppName        "TokenTray"
#define MyAppVersion     "0.6.0"
#define MyAppPublisher   "Jeff James"
#define MyAppURL         "https://github.com/jeffjame_microsoft/TokenTray"
#define MyAppExeName     "TokenTray.exe"
#define MyAppId          "{{4ACDCE17-4BBD-4313-A1E2-FA6E42B30D7C}"
#define SourceDir        "..\dist\TokenTray"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=TokenTray-Setup-{#MyAppVersion}
SetupIconFile=..\assets\tokentray.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "autostart";   Description: "Launch TokenTray automatically at &Windows sign-in"; \
    GroupDescription: "Startup:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; The classic Startup folder shortcut is the same mechanism the app's
; --install-startup flag uses. We just write it here at install time.
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; Best-effort: stop the running instance before uninstall removes the files.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#MyAppExeName} >NUL 2>&1"; \
    Flags: runhidden; RunOnceId: "StopTokenTray"
