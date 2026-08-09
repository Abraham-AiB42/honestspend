; Inno Setup 6 script — compile AFTER .\scripts\package-release.ps1
; Output: dist\Floatpile-Setup-x64.exe
;
; Prerequisites:
;   - Inno Setup 6 installed
;   - dist\Floatpile-Windows-x64\ populated by package-release.ps1

#define MyAppName "Floatpile"
#define MyAppVersion "1.0.6"
; Keep in sync with pyproject.toml / financial_os.__version__
; Install folder / EXE may still say Floatpile for path continuity (docs/BRAND.md)
#define MyAppPublisher "Floatpile contributors"
#define MyAppExeName "Floatpile.WinUI.exe"
#define MyAppURL "https://github.com/"
#define SourceDir "..\dist\Floatpile-Windows-x64"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\Floatpile
DefaultGroupName=Floatpile
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Floatpile-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
; Uncomment and configure for signed builds:
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "logonstart"; Description: "Start Floatpile tray-only at Windows logon"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Optional logon — tray-only (user can also set this in-app Settings)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Floatpile"; ValueData: """{app}\{#MyAppExeName}"" --tray-only"; Flags: uninsdeletevalue; Tasks: logonstart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  if not DirExists(ExpandConstant('{#SourceDir}')) then
  begin
    MsgBox('Source folder missing: dist\Floatpile-Windows-x64\'#13#10 +
      'Run: .\scripts\package-release.ps1 from the repo root first.', mbError, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;
