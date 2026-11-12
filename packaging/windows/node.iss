; The same frozen CLI drives both the installer shortcut and standalone archive.
[Setup]
AppId={{F9FB98D1-97DC-43B7-BC51-1D1EA833B8C8}
AppName=Buggcraft Node
AppVersion={#Version}
AppPublisher=Buggcraft
DefaultDirName={localappdata}\Programs\BuggcraftNode
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir={#Output}
OutputBaseFilename=buggcraft-node-{#Version}-windows-x64-setup
Compression=none
SolidCompression=no
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\runtime\python.exe

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Buggcraft Node"; Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\node_cli.py"""; WorkingDir: "{app}"

[Run]
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\node_cli.py"""; Description: "Configure or start node"; Flags: postinstall nowait skipifsilent
