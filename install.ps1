# Void installer for Windows 10 / 11. Run it from the folder you downloaded:
#     powershell -ExecutionPolicy Bypass -File install.ps1
# Everything goes in your user folder (%LOCALAPPDATA%\void); no admin rights needed.
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"                    # Void's files are UTF-8
$SRC  = $PSScriptRoot
$ROOT = Join-Path $env:LOCALAPPDATA "void"
$BIN  = Join-Path $ROOT "bin"
$VPY  = Join-Path $ROOT "venv\Scripts\python.exe"
$VPYW = Join-Path $ROOT "venv\Scripts\pythonw.exe"
$VOICE = "en_US-amy-medium"
function Say($m)  { Write-Host "* $m" -ForegroundColor Green }
function Warn($m) { Write-Host "! $m" -ForegroundColor Yellow }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
New-Item -ItemType Directory -Force -Path $ROOT, $BIN, (Join-Path $ROOT "voices") | Out-Null

# speech recognition (ctranslate2) and the voices (onnxruntime) need the Visual C++ runtime; VoidSetup.exe installs it
if (-not (Test-Path (Join-Path $env:WINDIR "System32\vcruntime140_1.dll"))) {
  Warn "The Microsoft Visual C++ runtime is missing - Void can't hear or speak without it. Install it (needs admin):"
  Write-Host "    https://aka.ms/vs/17/release/vc_redist.x64.exe"
}

# ---------------------------------------------------------------- uv (Python environment tool)
$script:PYUV = $null
# (named RunUv, not UV: PowerShell names ignore case, so a function "UV" calling uv would call itself)
function RunUv {
  $exe = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($exe) { & $exe.Source @args } elseif ($script:PYUV) { & $script:PYUV -m uv @args } else { $global:LASTEXITCODE = 1 }
}
if (-not (Have uv)) {
  Say "Installing uv (Python environment tool)..."
  foreach ($p in @("py", "python")) {
    if (-not $script:PYUV -and (Have $p)) {
      & $p -m pip install --user --quiet --disable-pip-version-check uv 2>$null
      if ($LASTEXITCODE -eq 0) { $script:PYUV = (Get-Command $p).Source }
    }
  }
  if (-not $script:PYUV) {
    try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression } catch { Warn "couldn't download uv: $_" }
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
  }
  if (-not (Have uv) -and -not $script:PYUV) { Warn "couldn't install uv - see https://docs.astral.sh/uv/ and run install.ps1 again"; exit 1 }
}

# ---------------------------------------------------------------- Python 3.12 + the libraries
Say "Python env (3.12)..."
if (-not (Test-Path $VPY)) {
  RunUv venv --python 3.12 (Join-Path $ROOT "venv")
  if ($LASTEXITCODE -ne 0) {                       # uv couldn't fetch one: use python.org's own installer
    $ver = "3.12.10"; $exe = Join-Path $env:TEMP "python-$ver-amd64.exe"
    Say "Downloading Python $ver from python.org..."
    Invoke-WebRequest "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe" -OutFile $exe -UseBasicParsing
    Start-Process $exe -ArgumentList "/quiet InstallAllUsers=0 PrependPath=0 Include_test=0" -Wait
    RunUv venv --python (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe") (Join-Path $ROOT "venv")
  }
  if (-not (Test-Path $VPY)) { Warn "couldn't set up Python 3.12"; exit 1 }
}
# the window (Qt + Chromium), mic/speakers, screenshots, volume control, speech recognition, voices
RunUv pip install --python $VPY -U PySide6 sounddevice pillow pycaw comtypes faster-whisper
if ($LASTEXITCODE -ne 0) { Warn "some Python libraries failed to install - see above"; exit 1 }
RunUv pip install --python $VPY -U piper-tts
if ($LASTEXITCODE -ne 0) { Warn "piper-tts failed to install - Void will use Kokoro only" }

Say "Copying app to $ROOT"
if (Test-Path (Join-Path $ROOT "app\launch_win.py")) { & $VPY (Join-Path $ROOT "app\launch_win.py") stop | Out-Null }
Remove-Item -Recurse -Force (Join-Path $ROOT "app") -ErrorAction SilentlyContinue
Copy-Item -Recurse (Join-Path $SRC "app") (Join-Path $ROOT "app")
$APP = Join-Path $ROOT "app"
& $VPY -c "from PIL import Image; Image.open(r'$APP\static\icon.png').save(r'$APP\static\icon.ico', sizes=[(256,256),(64,64),(48,48),(32,32),(16,16)])"

# ---------------------------------------------------------------- voices
# Kokoro: his main voice. GitHub first; if that's blocked, kokoro_fetch.py builds the same files from Hugging Face.
RunUv pip install --python $VPY -U kokoro-onnx
if ($LASTEXITCODE -eq 0) {
  Say "Kokoro voice..."
  & $VPY (Join-Path $APP "kokoro_fetch.py") (Join-Path $ROOT "voices\kokoro")
  if ($LASTEXITCODE -ne 0) { Warn "Kokoro download failed - Void will use piper until it can fetch it" }
} else { Warn "kokoro-onnx failed to install - Void will use piper instead" }
$parts = $VOICE -split "-"; $loc = $parts[0]; $lang = $loc.Split("_")[0]
$url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/$lang/$loc/$($parts[1])/$($parts[2])/$VOICE"
$vf = Join-Path $ROOT "voices\$VOICE.onnx"
if (-not (Test-Path $vf)) {
  Say "Downloading voice $VOICE..."
  try {
    Invoke-WebRequest "$url.onnx" -OutFile "$vf.part" -UseBasicParsing
    Invoke-WebRequest "$url.onnx.json" -OutFile "$vf.json" -UseBasicParsing
    Move-Item "$vf.part" $vf -Force
  } catch { Remove-Item "$vf*" -ErrorAction SilentlyContinue; Warn "voice download failed" }
}

if (Have nvidia-smi) {
  Say "NVIDIA GPU found - adding CUDA libraries for fast, accurate speech recognition (~1 GB)..."
  RunUv pip install --python $VPY -U nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
  if ($LASTEXITCODE -ne 0) { Warn "couldn't install CUDA libs - speech recognition will run on the CPU" }
}
if (-not $env:VOID_CI) {
  Say "Fetching the speech-recognition model..."
  & $VPY (Join-Path $APP "server.py") --warm
  if ($LASTEXITCODE -ne 0) { Warn "couldn't pre-load the speech model; it will download on first use" }
}

# ---------------------------------------------------------------- the `void` command, Start menu, start at login
Set-Content -Encoding ASCII (Join-Path $BIN "void.cmd") "@echo off`r`nset PYTHONUTF8=1`r`n`"$VPY`" `"$APP\launch_win.py`" %*"
$fwd = { param($p) $p -replace '\\', '/' }
[IO.File]::WriteAllText((Join-Path $BIN "void"), "#!/bin/sh`nexec `"$(& $fwd $VPY)`" `"$(& $fwd "$APP\launch_win.py")`" `"`$@`"`n")   # for Git Bash (Claude Code's shell)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (($userPath -split ";") -contains $BIN)) {
  [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $BIN), "User")
  $env:Path += ";$BIN"
}
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Void.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($menu)
$s.TargetPath = $VPYW; $s.Arguments = "`"$APP\launch_win.py`" show"; $s.WorkingDirectory = $APP
$s.IconLocation = "$APP\static\icon.ico"; $s.Description = "Voice shell for Claude Code"; $s.Save()
& $VPY (Join-Path $APP "server.py") --sync-autostart | Out-Null
if ($LASTEXITCODE -eq 0) { Say "Void will start when you log in (turn off: Settings -> More)" }

Write-Host ""
if (-not (Have claude)) {
  Warn "Claude Code isn't installed. Install it, then run 'claude' once to sign in:"
  Write-Host "    irm https://claude.ai/install.ps1 | iex"
}
Say "Done! Open a new terminal and run:  void     (or find Void in the Start menu)"
Write-Host "   Tip: right-click the Void shortcut -> Properties -> Shortcut key, to pop him up with a hotkey."
