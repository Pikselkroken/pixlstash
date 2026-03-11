#define MyAppName "PixlStash"
#define MyAppPublisher "PixlStash"
#define MyAppExeName "Start-PixlStash-Server.bat"
#define EnvAppVersion GetEnv("PIXLSTASH_VERSION")
#if EnvAppVersion == ""
	#define MyAppVersion "0.0.0"
#else
	#define MyAppVersion EnvAppVersion
#endif

[Setup]
AppId={{F12EBC4A-3D37-4DE2-AED8-9D5F6EE7F884}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PixlStash
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=pixlstash-{#MyAppVersion}-windows-x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\frontend\public\favicon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\pixlstash-*.whl"; DestDir: "{app}\dist"; Flags: ignoreversion
Source: "install-pixlstash.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "Start-PixlStash-Server.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\frontend\public\favicon.ico"; DestDir: "{app}"; DestName: "PixlStash.ico"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\Start Server"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\PixlStash.ico"
Name: "{autodesktop}\{#MyAppName} Server"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\PixlStash.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\install-pixlstash.bat"; Parameters: """{app}"""; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PixlStash Server"; Flags: nowait postinstall skipifsilent

[Code]
function IsPythonAvailable(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('cmd.exe', '/c py -3.12 --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
    Result := Exec('cmd.exe', '/c python --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsPythonAvailable() then
  begin
    MsgBox(
      'Python 3.10 or newer is required to install PixlStash.' + #13#10#13#10 +
      'Please install Python from https://www.python.org/ and run this installer again.' + #13#10 +
      'Make sure to check "Add Python to PATH" during installation.',
      mbCriticalError, MB_OK);
    Result := False;
  end;
end;
