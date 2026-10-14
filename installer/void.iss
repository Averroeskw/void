; VoidSetup.exe: a normal Windows installer for Void. Built by .github/workflows/installer.yml, or by hand with
;     python installer\make_art.py
;     iscc /DAppVersion=1.0.0 installer\void.iss
; Everything Void needs, each skipped when it's already there and updated when it's old:
;   Visual C++ runtime (admin prompt only if it has to be installed), Claude Code, Python + libraries, the CUDA
;   libraries on NVIDIA cards, his voices and the speech model. install.ps1 does the Void part, in %LOCALAPPDATA%\void.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Wizard "art\wizard-100.png,art\wizard-125.png,art\wizard-150.png,art\wizard-175.png,art\wizard-200.png,art\wizard-225.png,art\wizard-250.png"
#define Small "art\small-100.png,art\small-125.png,art\small-150.png,art\small-175.png,art\small-200.png,art\small-225.png,art\small-250.png"

[Setup]
AppId={{48E49768-D7F3-4989-B16D-2C62FFFAFD47}
AppName=Void
AppVersion={#AppVersion}
AppVerName=Void {#AppVersion}
AppPublisher=PLAYER99-void
AppPublisherURL=https://github.com/PLAYER99-void/void
AppSupportURL=https://github.com/PLAYER99-void/void/issues
AppUpdatesURL=https://github.com/PLAYER99-void/void/releases
DefaultDirName={localappdata}\void
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesEnvironment=yes
CloseApplications=no
; install.ps1 runs Python through uv's own folder links, which this Windows protection would refuse to follow
RedirectionGuard=no
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=VoidSetup
SetupIconFile=art\void.ico
UninstallDisplayName=Void
UninstallDisplayIcon={app}\app\static\icon.ico
WizardStyle=modern dynamic hidebevels
WizardImageFile={#Wizard}
WizardImageFileDynamicDark={#Wizard}
WizardSmallImageFile={#Small}
WizardSmallImageFileDynamicDark={#Small}
WizardImageStretch=no
WizardSizePercent=110
Compression=lzma2
SolidCompression=yes

[Messages]
WelcomeLabel1=Meet Void
WelcomeLabel2=A little orb for your desktop that listens, talks back, and drives Claude Code for you.%n%nSetup gets everything he needs: his voice, his hearing, Claude Code and the Microsoft runtime. Anything you already have is kept, or updated if it's old.%n%nIt takes a few minutes and about 1 GB of space.
FinishedHeadingLabel=Void is ready
FinishedLabelNoIcons=Say "hey Void" or click him to start talking.%n%nYou'll find him in the Start menu. Right-click him any time for settings.
FinishedLabel=Say "hey Void" or click him to start talking.%n%nYou'll find him in the Start menu. Right-click him any time for settings.
ReadyLabel1=Setup is ready to install Void.
ReadyLabel2a=Click Install. This downloads about 1 GB, so it takes a few minutes.

[Tasks]
Name: "claudecode"; Description: "Install Claude Code (Void needs it to do the work)"; Check: NeedsClaudeCode
Name: "desktopicon"; Description: "Put Void on my desktop"; Flags: unchecked

[Files]
; install.ps1 copies app\ into {app} itself, so the sources only need to live in {tmp} while it runs
Source: "..\install.ps1"; DestDir: "{tmp}\src"
Source: "run-install.ps1"; DestDir: "{tmp}\src"
Source: "..\app\*"; DestDir: "{tmp}\src\app"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\app\launch_win.py"" show"; WorkingDir: "{app}\app"; \
  Description: "Start Void now"; Flags: postinstall nowait skipifsilent; Check: InstallSucceeded
Filename: "powershell.exe"; Parameters: "-NoExit -NoProfile -Command ""Write-Host 'Sign in to Claude Code, then close this window.' -ForegroundColor Green; & '{%USERPROFILE}\.local\bin\claude.exe'"""; \
  Description: "Sign in to Claude Code (needed once)"; Flags: postinstall nowait skipifsilent; Check: ClaudeJustInstalled
; "Update now" in Void runs setup silently with /UPDATER: start him again when it's done
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\app\launch_win.py"" show"; WorkingDir: "{app}\app"; \
  Flags: nowait; Check: UpdaterRun

[UninstallRun]
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\app\launch_win.py"" stop"; \
  Flags: runhidden skipifdoesntexist; RunOnceId: "StopVoid"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: files; Name: "{userprograms}\Void.lnk"
Type: files; Name: "{userstartup}\Void.lnk"
Type: files; Name: "{userdesktop}\Void.lnk"

[Code]
const
  VCRedistURL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe';
  VCKey = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';
  VCMinMinor = 40;   { speech recognition and the voices are built against Visual C++ 14.40+ }

var
  InstallOk, ClaudeInstalled: Boolean;
  LastLines: TStringList;
  LogFile: String;

function InstallSucceeded: Boolean;
begin
  Result := InstallOk;
end;

function ClaudeJustInstalled: Boolean;
begin
  Result := InstallOk and ClaudeInstalled;
end;

function UpdaterRun: Boolean;
begin
  Result := InstallOk and (Pos('/UPDATER', Uppercase(GetCmdTail)) > 0);
end;

function ClaudePath: String;
begin
  Result := FileSearch('claude.exe', GetEnv('PATH'));
  if (Result = '') and FileExists(ExpandConstant('{%USERPROFILE}\.local\bin\claude.exe')) then
    Result := ExpandConstant('{%USERPROFILE}\.local\bin\claude.exe');
end;

function NeedsClaudeCode: Boolean;
begin
  Result := ClaudePath = '';
end;

{ ---------------------------------------------------------------- Visual C++ runtime: install if missing or old }
{ the x64 runtime registers itself in the 32-bit registry view on some versions, the 64-bit one on others }
function VCValue(const Name: String; var Value: Cardinal): Boolean;
begin
  Result := RegQueryDWordValue(HKLM64, VCKey, Name, Value) or RegQueryDWordValue(HKLM32, VCKey, Name, Value);
end;

function NeedsVCRedist: Boolean;
var
  Installed, Minor: Cardinal;
begin
  Result := not (VCValue('Installed', Installed) and (Installed = 1) and
                 VCValue('Minor', Minor) and (Minor >= VCMinMinor) and
                 FileExists(ExpandConstant('{sys}\vcruntime140_1.dll')));
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
  if NeedsVCRedist then begin
    WizardForm.PreparingLabel.Caption := 'Installing the Microsoft Visual C++ runtime...';
    if not InstallVCRedist then
      if SuppressibleMsgBox('Void needs the Microsoft Visual C++ runtime to hear and speak, and it couldn''t be installed.' + #13#10#13#10 +
                            'You can install it later from:' + #13#10 + VCRedistURL + #13#10#13#10 + 'Continue anyway?',
                            mbConfirmation, MB_YESNO, IDYES) = IDNO then
        Result := 'Install the Microsoft Visual C++ runtime from ' + VCRedistURL + ', then run this setup again.';
  end;
end;

{ ---------------------------------------------------------------- running the steps, shown inside the wizard }
procedure Status(const S: String);
begin
  WizardForm.StatusLabel.Caption := S;
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.Refresh;
end;

procedure OnOutput(const S: String; const Error, FirstLine: Boolean);
var
  Line: String;
begin
  Line := Trim(S);
  if Line = '' then
    Exit;
  SaveStringToFile(LogFile, Line + #13#10, True);
  LastLines.Add(Line);
  if LastLines.Count > 14 then
    LastLines.Delete(0);
  { install.ps1 marks its steps with "* "; everything else is detail }
  if Copy(Line, 1, 2) = '* ' then
    WizardForm.StatusLabel.Caption := Copy(Line, 3, Length(Line))
  else
    WizardForm.FilenameLabel.Caption := Copy(Line, 1, 110);
  WizardForm.Refresh;
end;

function RunStep(const Caption, Params: String): Boolean;
var
  Code: Integer;
begin
  Status(Caption);
  SaveStringToFile(LogFile, '=== ' + Caption + #13#10, True);
  try
    Result := ExecAndLogOutput('powershell.exe', '-NoProfile -ExecutionPolicy Bypass ' + Params, '',
                               SW_HIDE, ewWaitUntilTerminated, Code, @OnOutput) and (Code = 0);
  except
    OnOutput(GetExceptionMessage, True, False);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  LastLines := TStringList.Create;
  LogFile := ExpandConstant('{app}\install.log');
  SaveStringToFile(LogFile, 'Void ' + '{#AppVersion}' + ' setup, ' + GetDateTimeString('yyyy-mm-dd hh:nn', '-', ':') + #13#10, False);
  WizardForm.ProgressGauge.Style := npbstMarquee;

  if WizardIsTaskSelected('claudecode') and NeedsClaudeCode then begin
    ClaudeInstalled := RunStep('Installing Claude Code...', '-Command "irm https://claude.ai/install.ps1 | iex"') and
                       not NeedsClaudeCode;
    if not ClaudeInstalled then
      SuppressibleMsgBox('Claude Code couldn''t be installed. Void will still be set up; install Claude Code later from' + #13#10 +
                         'https://docs.anthropic.com/en/docs/claude-code', mbInformation, MB_OK, IDOK);
  end;

  InstallOk := RunStep('Setting up Void...', '-File "' + ExpandConstant('{tmp}\src\run-install.ps1') + '" -NoPause');
  WizardForm.ProgressGauge.Style := npbstNormal;
  { made here, not in [Icons]: those are created before Void is set up, when InstallOk isn't known yet }
  if InstallOk and WizardIsTaskSelected('desktopicon') then
    CreateShellLink(ExpandConstant('{userdesktop}\Void.lnk'), 'Voice shell for Claude Code',
                    ExpandConstant('{app}\venv\Scripts\pythonw.exe'), '"' + ExpandConstant('{app}\app\launch_win.py') + '" show',
                    ExpandConstant('{app}\app'), ExpandConstant('{app}\app\static\icon.ico'), 0, SW_SHOWNORMAL);
  if not InstallOk then
    SuppressibleMsgBox('Void didn''t finish installing. The last things it said were:' + #13#10#13#10 + LastLines.Text + #13#10 +
                       'The full log is in ' + LogFile + #13#10 + 'Check your internet connection and run this setup again.',
                       mbError, MB_OK, IDOK);
end;

{ ---------------------------------------------------------------- uninstall }
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
