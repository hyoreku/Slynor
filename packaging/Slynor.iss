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

#ifdef SystemWebView2
[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function RuntimeInstalled(Hive: Integer): Boolean;
var
  Version: String;
  Dot: Integer;
begin
  Result := False;
  if RegQueryStringValue(Hive, WebView2Key, 'pv', Version) then
  begin
    Dot := Pos('.', Version);
    if Dot > 0 then
      Result := StrToIntDef(Copy(Version, 1, Dot - 1), 0) >= 111;
  end;
end;

function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := RuntimeInstalled(HKCU) or RuntimeInstalled(HKLM32);
  if not Result and (SuppressibleMsgBox(
    'Slynor requires Microsoft Edge WebView2 Runtime. Open the Microsoft download page?',
    mbInformation, MB_YESNO, IDNO) = IDYES) then
    ShellExec('open', 'https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
#endif
