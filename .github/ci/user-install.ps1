# Runs as a plain (non-admin) Windows user, like most people: installs Void with VoidSetup.exe three times (a fresh
# install, an update over it, and a repair of a broken install) and records PASS/FAIL lines in $Out\user-results.txt.
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

foreach ($round in 1, 2, 3) {
  $tasks = if ($round -eq 1) { "claudecode" } else { "" }       # round 1 also installs Claude Code; later rounds find it
  if ($round -eq 3) {
    # break it the way a real PC did: Void still running from the venv (files locked), its Python's files zero-filled
    # (a half-written download), and the venv's python.exe gone
    $sleeper = Start-Process "$root\venv\Scripts\python.exe" -ArgumentList "-c", "`"import time; time.sleep(900)`"" -PassThru -WindowStyle Hidden
    Start-Sleep 2
    $base = Get-ChildItem "$root\python" -Directory | Where-Object { -not $_.LinkType -and $_.Name -match '^cpython-3\.12\.\d+-' } | Select-Object -First 1
    $enc = Join-Path $base.FullName "Lib\encodings\__init__.py"
    [IO.File]::WriteAllBytes($enc, [byte[]]::new((Get-Item $enc).Length))
    & "$($base.FullName)\python.exe" -I -c "pass" 2>$null
    Check "round 3: Python really broken before setup" ($LASTEXITCODE -ne 0)
    # and something outside Void's folder (like an Explorer window) keeps app\static busy, so app\ can't be removed
    $holder = Start-Process powershell.exe -WorkingDirectory "$root\app\static" -ArgumentList "-NoProfile", "-Command", "Start-Sleep 900" -PassThru -WindowStyle Hidden
    Start-Sleep 2
  }
  $p = Start-Process $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=`"$tasks`"", "/LOG=`"$Out\user-setup-$round.log`"" -Wait -PassThru
  Check "round ${round}: setup finished" ($p.ExitCode -eq 0) "(exit $($p.ExitCode))"
  & "$root\venv\Scripts\python.exe" -c "import PySide6, sounddevice, faster_whisper, kokoro_onnx" 2>$null
  Check "round ${round}: Python libraries import" ($LASTEXITCODE -eq 0)
  $home_ = (Get-Content "$root\venv\pyvenv.cfg" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^home' }) -replace '^home\s*=\s*', ''
  Check "round ${round}: Python is Void's own, exact version" ($home_ -like "$root\python\cpython-3.12.*") "($home_)"
  Check "round ${round}: void command" (Test-Path "$root\bin\void.cmd")
  Copy-Item "$root\install.log" "$Out\user-install-$round.log" -ErrorAction SilentlyContinue
  if ($round -eq 1) { Check "Claude Code installed" (Test-Path "$env:USERPROFILE\.local\bin\claude.exe") }
  if ($round -eq 3) {
    Check "round 3: setup stopped the old Void" ($sleeper.HasExited)
    Check "round 3: app files in place despite the busy folder" ((Test-Path "$root\app\server.py") -and -not (Test-Path "$root\app\app"))
    Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
  }
}
Add-Content $results "DONE"
