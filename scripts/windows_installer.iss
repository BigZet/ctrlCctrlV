; Build with scripts/build_windows.py --installer (Inno Setup 6.3 or newer).
#ifndef AppVersion
  #error AppVersion must be provided by the build script
#endif
#ifndef SourceDir
  #error SourceDir must point to the verified PyInstaller folder
#endif

[Setup]
; Keep this identifier stable across releases so upgrades reuse one installation.
AppId=ctrlCctrlV.Desktop
AppName=ctrlCctrlV
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=ctrlCctrlV contributors
DefaultDirName={localappdata}\Programs\ctrlCctrlV
DefaultGroupName=ctrlCctrlV
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
UninstallDisplayIcon={app}\ctrlCctrlV.exe
SetupIconFile=..\src\ctrlcctrlv\assets\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
OutputBaseFilename=ctrlCctrlV-{#AppVersion}-windows-x64-setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ctrlCctrlV"; Filename: "{app}\ctrlCctrlV.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\ctrlCctrlV"; Filename: "{app}\ctrlCctrlV.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ctrlCctrlV.exe"; Description: "{cm:LaunchProgram,ctrlCctrlV}"; Flags: nowait postinstall skipifsilent unchecked

; Settings, downloaded models, runtime and credentials live outside {app}.
; Deliberately no UninstallDelete entries: uninstall preserves user data.
