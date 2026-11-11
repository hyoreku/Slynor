#define AppName "Slynor"
[Setup]
AppId={{93CA6C34-26B8-4058-8A93-61D7DE129233}
AppName={#AppName}
AppVersion=0.23.0
DefaultDirName={localappdata}\Programs\Slynor
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=Slynor-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\Slynor.exe
[Files]
Source: "..\dist\Slynor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Slynor"; Filename: "{app}\Slynor.exe"; WorkingDir: "{app}"
[Run]
Filename: "{app}\Slynor.exe"; Description: "Open Slynor"; Flags: postinstall nowait skipifsilent
