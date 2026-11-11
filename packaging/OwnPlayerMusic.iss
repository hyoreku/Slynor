#define AppName "OwnPlayerMusic"
[Setup]
AppId={{93CA6C34-26B8-4058-8A93-61D7DE129233}
AppName={#AppName}
AppVersion=0.18.0
DefaultDirName={localappdata}\Programs\OwnPlayerMusic
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=OwnPlayerMusic-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\OwnPlayerMusic.exe
[Files]
Source: "..\dist\OwnPlayerMusic\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\OwnPlayerMusic"; Filename: "{app}\OwnPlayerMusic.exe"; WorkingDir: "{app}"
Name: "{group}\Check dependencies"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\INSTALL.ps1"""; WorkingDir: "{app}"
[Run]
Filename: "{app}\OwnPlayerMusic.exe"; Description: "Open OwnPlayerMusic"; Flags: postinstall nowait skipifsilent; Check: CanLaunch
[Code]
var DependenciesReady: Boolean;
function CanLaunch: Boolean;
begin Result := DependenciesReady; end;
procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
 if CurStep = ssPostInstall then begin
  DependenciesReady := False;
  if WizardSilent then begin
   Log('Dependencies were not installed in silent mode. Run INSTALL.ps1 before opening the app.');
   exit;
  end;
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\INSTALL.ps1') + '"', ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, Code) then
   DependenciesReady := Code = 0;
  if not DependenciesReady then
   MsgBox('Dependency installation did not finish successfully. Run INSTALL.ps1 from the installation folder and fix the reported error. The player will not be opened automatically.', mbError, MB_OK);
 end;
end;
