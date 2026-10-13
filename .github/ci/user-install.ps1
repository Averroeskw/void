# Runs as a plain (non-admin) Windows user, like most people: installs Void with VoidSetup.exe twice (a fresh install,
# then an update over it) and records PASS/FAIL lines in $Out\user-results.txt for the workflow to check.
param([string]$Setup, [string]$Out)
# Start-Process -Credential hands over the admin's environment: point it at this user's own folders, as a login would
$env:USERNAME = [Environment]::UserName
$env:USERPROFILE = [Environment]::GetFolderPath("UserProfile")
$env:APPDATA = [Environment]::GetFolderPath("ApplicationData")
$env:LOCALAPPDATA = [Environment]::GetFolderPath("LocalApplicationData")
$env:TEMP = $env:TMP = Join-Path $env:LOCALAPPDATA "Temp"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:VOID_CI = "1"
$root = Join-Path $env:LOCALAPPDATA "void"
$results = Join-Path $Out "user-results.txt"
function Check($name, $ok, $detail = "") { Add-Content $results ("{0}  {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $name, $detail) }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole("Administrators")
Check "running as a normal user" (-not $admin) "($env:USERNAME)"

foreach ($round in 1, 2) {
  $tasks = if ($round -eq 1) { "claudecode" } else { "" }       # round 1 also installs Claude Code; round 2 finds it
  $p = Start-Process $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=`"$tasks`"", "/LOG=`"$Out\user-setup-$round.log`"" -Wait -PassThru
  Check "round ${round}: setup finished" ($p.ExitCode -eq 0) "(exit $($p.ExitCode))"
  & "$root\venv\Scripts\python.exe" -c "import PySide6, sounddevice, faster_whisper, kokoro_onnx" 2>$null
  Check "round ${round}: Python libraries import" ($LASTEXITCODE -eq 0)
  $home_ = (Get-Content "$root\venv\pyvenv.cfg" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^home' }) -replace '^home\s*=\s*', ''
  Check "round ${round}: Python is Void's own, exact version" ($home_ -like "$root\python\cpython-3.12.*") "($home_)"
  Check "round ${round}: void command" (Test-Path "$root\bin\void.cmd")
  Copy-Item "$root\install.log" "$Out\user-install-$round.log" -ErrorAction SilentlyContinue
  if ($round -eq 1) { Check "Claude Code installed" (Test-Path "$env:USERPROFILE\.local\bin\claude.exe") }
}
Add-Content $results "DONE"
