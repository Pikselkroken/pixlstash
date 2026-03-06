#define MyAppName "PixlVault"
#define MyAppPublisher "PixlVault"
#define MyAppExeName "Start-PixlVault-Server.bat"
#define EnvAppVersion GetEnv("PIXLVAULT_VERSION")
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
DefaultDirName={autopf}\PixlVault
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=pixlvault-{#MyAppVersion}-windows-x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\pixlvault-*.whl"; DestDir: "{app}\dist"; Flags: ignoreversion
Source: "install-pixlvault.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}\Start Server"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName} Server"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install-pixlvault.ps1"" -AppDir ""{app}"""; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PixlVault Server"; Flags: nowait postinstall skipifsilent
