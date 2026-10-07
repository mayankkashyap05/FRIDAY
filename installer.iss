#define MyAppName "Friday"
#define MyAppVersion "7.0.0"
#define MyAppPublisher "PHENOMVALENCE"
#define MyAppExeName "FRIDAY.exe"

[Setup]
AppId={{8E4B839F-CA16-496B-B624-76A1C86FF0E8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\FRIDAY
DefaultGroupName={#MyAppName}
OutputDir=installer-output
OutputBaseFilename=FRIDAY-Setup-x64
SetupIconFile=GUI_images\friday.ico
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "dist\FRIDAY\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
; Ticked by default: the wake word only works while Friday is running, so
; a fresh install that does not start with Windows cannot answer "Hey Friday".
Name: "startup"; Description: "Start Friday when I sign in (needed for ""Hey Friday"")"; GroupDescription: "Hands-free:"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "FRIDAY"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Friday and finish setup"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Downloaded voice and wake word models live beside the app.
Type: filesandordirs; Name: "{app}\models"
