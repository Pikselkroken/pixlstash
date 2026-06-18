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
OutputBaseFilename=pixlstash-server-{#MyAppVersion}-windows-x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\frontend\public\favicon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; We detect and stop a running PixlStash ourselves in PrepareToInstall (with a
; warning prompt), so suppress Inno's built-in Restart Manager dialog, whose
; "PixlStash cannot be closed" message is confusing and can't stop our console
; server / venv processes anyway.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\pixlstash-*.whl"; DestDir: "{app}\dist"; Flags: ignoreversion
Source: "install-pixlstash.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "Start-PixlStash-Server.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\frontend\public\favicon.ico"; DestDir: "{app}"; DestName: "PixlStash.ico"; Flags: ignoreversion
Source: "stop-pixlstash.ps1"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#MyAppName}\Start Server"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\PixlStash.ico"
Name: "{autodesktop}\{#MyAppName} Server"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\PixlStash.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\install-pixlstash.bat"; Parameters: """{app}"""; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PixlStash Server"; Flags: nowait postinstall skipifsilent

[Code]
var
  StopScriptExtracted: Boolean;

{ Run stop-pixlstash.ps1 against the install dir. Exit code 1 means a PixlStash
  process is (still, when Kill=True) running; 0 means none. -1 = launch failed. }
function RunStopScript(Kill: Boolean): Integer;
var
  Params: String;
  ResultCode: Integer;
begin
  if not StopScriptExtracted then
  begin
    ExtractTemporaryFile('stop-pixlstash.ps1');
    StopScriptExtracted := True;
  end;
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\stop-pixlstash.ps1') + '" -AppDir "' +
    ExpandConstant('{app}') + '"';
  if Kill then
    Params := Params + ' -Kill';
  if Exec('powershell.exe', Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode
  else
    Result := -1;
end;

{ Runs after the user clicks Install but before any files are copied or pip runs,
  so stopping the server here frees both the install-dir files Inno copies and the
  venv files the pip upgrade overwrites. A non-empty result aborts the install. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if RunStopScript(False) = 1 then
  begin
    if MsgBox(
         'PixlStash is currently running and must be closed before it can be updated.' + #13#10#13#10 +
         'Click Yes to stop the running PixlStash server/desktop now and continue, ' +
         'or No to cancel the installation.' + #13#10#13#10 +
         'Note: any uploads or processing in progress will be interrupted.',
         mbConfirmation, MB_YESNO) = IDYES then
    begin
      if RunStopScript(True) = 1 then
        Result := 'PixlStash could not be closed automatically. Please close it ' +
                  'manually and run the installer again.';
    end
    else
      Result := 'Installation was cancelled because PixlStash is still running.';
  end;
end;

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
