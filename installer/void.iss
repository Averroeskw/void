; VoidSetup.exe: a normal Windows installer for Void. Built by .github/workflows/installer.yml, or by hand with
;     iscc /DAppVersion=1.0.0 installer\void.iss
; It installs the Visual C++ runtime if the PC is missing it (Windows asks for admin just for that), then runs
; install.ps1, which puts everything in %LOCALAPPDATA%\void without admin. Uninstalling undoes all of it.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{48E49768-D7F3-4989-B16D-2C62FFFAFD47}
AppName=Void
AppVersion={#AppVersion}
AppPublisher=PLAYER99-void
AppPublisherURL=https://github.com/PLAYER99-void/void
AppSupportURL=https://github.com/PLAYER99-void/void/issues
DefaultDirName={localappdata}\void
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesEnvironment=yes
CloseApplications=no
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=VoidSetup
#if FileExists(AddBackslash(SourcePath) + "void.ico")
SetupIconFile=void.ico
#endif
UninstallDisplayName=Void
UninstallDisplayIcon={app}\app\static\icon.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Files]
; install.ps1 copies app\ into {app} itself, so the sources only need to live in {tmp} while it runs
Source: "..\install.ps1"; DestDir: "{tmp}\src"
Source: "run-install.ps1"; DestDir: "{tmp}\src"
Source: "..\app\*"; DestDir: "{tmp}\src\app"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\app\launch_win.py"" show"; WorkingDir: "{app}\app"; \
  Description: "Start Void"; Flags: postinstall nowait skipifsilent; Check: InstallSucceeded

[UninstallRun]
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\app\launch_win.py"" stop"; \
  Flags: runhidden skipifdoesntexist; RunOnceId: "StopVoid"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: files; Name: "{userprograms}\Void.lnk"
Type: files; Name: "{userstartup}\Void.lnk"

[Code]
const
  VCRedistURL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe';

var
  InstallOk: Boolean;

function InstallSucceeded: Boolean;
begin
  Result := InstallOk;
end;

{ speech recognition (ctranslate2) and the voices (onnxruntime) can't load without it }
function NeedsVCRedist: Boolean;
begin
  Result := not FileExists(ExpandConstant('{sys}\vcruntime140_1.dll'));
end;

function InstallVCRedist: Boolean;
var
  Code: Integer;
begin
  Result := False;
  try
    DownloadTemporaryFile(VCRedistURL, 'vc_redist.x64.exe', '', nil);
  except
    Log('vc_redist download failed: ' + GetExceptionMessage);
    Exit;
  end;
  { 3010 = installed, needs a restart; 1638 = a newer one is already there }
  Result := ShellExec('runas', ExpandConstant('{tmp}\vc_redist.x64.exe'), '/install /passive /norestart', '',
                      SW_SHOW, ewWaitUntilTerminated, Code) and ((Code = 0) or (Code = 3010) or (Code = 1638));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if NeedsVCRedist and not InstallVCRedist then
    if SuppressibleMsgBox('Void needs the Microsoft Visual C++ runtime to hear and speak, and it couldn''t be installed.' + #13#10#13#10 +
                          'You can install it later from:' + #13#10 + VCRedistURL + #13#10#13#10 + 'Continue anyway?',
                          mbConfirmation, MB_YESNO, IDYES) = IDNO then
      Result := 'Install the Microsoft Visual C++ runtime from ' + VCRedistURL + ', then run this setup again.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Params: String;
begin
  if CurStep = ssPostInstall then begin
    WizardForm.StatusLabel.Caption := 'Setting up Python, the voice and the speech model - this takes a few minutes...';
    Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\src\run-install.ps1') + '"';
    if WizardSilent then
      Params := Params + ' -NoPause';
    InstallOk := Exec('powershell.exe', Params, '', SW_SHOW, ewWaitUntilTerminated, Code) and (Code = 0);
    if not InstallOk then
      SuppressibleMsgBox('Void didn''t finish installing - the PowerShell window showed what went wrong.' + #13#10#13#10 +
                         'Check your internet connection and run this setup again.', mbError, MB_OK, IDOK);
  end;
end;

{ take install.ps1's folder back off the user PATH }
procedure RemoveFromPath(Dir: String);
var
  Path: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Path) then
    Exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';');
  if P = 0 then
    Exit;
  { P is where Dir starts in Path: drop it with the ';' before it (or after it, when it's first) }
  if P = 1 then
    Delete(Path, 1, Length(Dir) + 1)
  else
    Delete(Path, P - 1, Length(Dir) + 1);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Path);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveFromPath(ExpandConstant('{app}\bin'));
  if CurUninstallStep = usPostUninstall then
    if DirExists(ExpandConstant('{userappdata}\void')) and
       (SuppressibleMsgBox('Also delete your Void settings?', mbConfirmation, MB_YESNO, IDNO) = IDYES) then
      DelTree(ExpandConstant('{userappdata}\void'), True, True, True);
end;
