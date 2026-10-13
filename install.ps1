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
# uv's own installer puts it here, and a window opened before that doesn't have it on PATH yet: use it, don't reinstall
$uvHome = Join-Path $env:USERPROFILE ".local\bin"
if ((Test-Path (Join-Path $uvHome "uv.exe")) -and -not (($env:Path -split ";") -contains $uvHome)) { $env:Path += ";$uvHome" }
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
Say "Setting up Python 3.12..."
# Void keeps his own Python in $ROOT\python. The venv must point at the exact version's folder: uv's "3.12" folder is
# a junction, and Windows can refuse to follow junctions a normal user made ("untrusted mount point").
# Every Python is checked by actually starting it: a half-written install can look fine on disk and still crash.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ROOT "python"
$venv = Join-Path $ROOT "venv"
function PyWorks($exe) {
  if (-not (Test-Path $exe)) { return $false }
  & $exe -I -c "import encodings, venv" 2>$null | Out-Null
  $LASTEXITCODE -eq 0
}
function ExactPython {   # newest real (not junction) 3.12.x folder in $ROOT\python
  Get-ChildItem $env:UV_PYTHON_INSTALL_DIR -Directory -ErrorAction SilentlyContinue |
    Where-Object { -not $_.LinkType -and $_.Name -match '^cpython-3\.12\.\d+-' } |
    Sort-Object { [int](($_.Name -replace '^cpython-3\.12\.(\d+)-.*', '$1')) } | Select-Object -Last 1
}
# keep the venv only if it starts and points at Void's own exact-version Python
$home_ = (Get-Content (Join-Path $venv "pyvenv.cfg") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^home\s*=' }) -replace '^home\s*=\s*', ''
if ((Test-Path $venv) -and -not ((PyWorks $VPY) -and $home_ -like "$($env:UV_PYTHON_INSTALL_DIR)\cpython-3.12.*")) {
  Say "Rebuilding the Python environment (the old one was broken or used a shared Python)..."
  # anything still running from Void's folder keeps venv files locked, so the folder can't be removed
  Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$ROOT\*" } | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
  Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
}
if (-not (PyWorks $VPY)) {
  RunUv python install 3.12 --no-bin
  $py = ExactPython
  if ($py -and -not (PyWorks (Join-Path $py.FullName "python.exe"))) {
    Say "Repairing Void's Python (some of its files were damaged)..."
    RunUv python install ($py.Name -replace '^cpython-(3\.12\.\d+)-.*', '$1') --reinstall --no-bin
    $py = ExactPython
  }
  if (-not $py -or -not (PyWorks (Join-Path $py.FullName "python.exe"))) {
    Warn "couldn't download Python 3.12 - check your internet connection and run setup again"; exit 1
  }
  RunUv venv --clear --force --python ($py.Name -replace '^cpython-(3\.12\.\d+)-.*', '$1') $venv
  if (-not (PyWorks $VPY)) { Warn "couldn't set up Void's Python environment in $venv"; exit 1 }
}
# the window (Qt + Chromium), mic/speakers, screenshots, volume control, speech recognition, voices
Say "Installing his window, hearing and voice libraries..."
RunUv pip install --python $VPY -U PySide6 sounddevice pillow pycaw comtypes faster-whisper
if ($LASTEXITCODE -ne 0) { Warn "some Python libraries failed to install - see above"; exit 1 }
RunUv pip install --python $VPY -U piper-tts
if ($LASTEXITCODE -ne 0) { Warn "piper-tts failed to install - Void will use Kokoro only" }

Say "Copying Void's files..."
if (Test-Path (Join-Path $ROOT "app\launch_win.py")) { & $VPY (Join-Path $ROOT "app\launch_win.py") stop | Out-Null }
Remove-Item -Recurse -Force (Join-Path $ROOT "app") -ErrorAction SilentlyContinue
Copy-Item -Recurse (Join-Path $SRC "app") (Join-Path $ROOT "app")
$APP = Join-Path $ROOT "app"
& $VPY -c "from PIL import Image; Image.open(r'$APP\static\icon.png').save(r'$APP\static\icon.ico', sizes=[(256,256),(64,64),(48,48),(32,32),(16,16)])"

# ---------------------------------------------------------------- voices
# Kokoro: his main voice. GitHub first; if that's blocked, kokoro_fetch.py builds the same files from Hugging Face.
RunUv pip install --python $VPY -U kokoro-onnx
if ($LASTEXITCODE -eq 0) {
  Say "Downloading his voice (Kokoro)..."
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

# ---------------------------------------------------------------- NVIDIA: CUDA libraries for fast speech recognition
# The CUDA libraries come as Python packages into the venv; only the graphics driver itself must be on the PC.
$nvidia = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" })
if ($nvidia -or (Have nvidia-smi)) {
  $driver = $null
  if (Have nvidia-smi) { try { $driver = [version](((& nvidia-smi --query-gpu=driver_version --format=csv,noheader) | Select-Object -First 1).Trim()) } catch {} }
  if (-not $driver) {
    Warn "NVIDIA graphics card found, but its driver isn't working - install it from https://www.nvidia.com/drivers (speech recognition uses the CPU until then)"
  } elseif ($driver -lt [version]"528.33") {
    Warn "Your NVIDIA driver ($driver) is too old for CUDA 12 - update it from https://www.nvidia.com/drivers (speech recognition uses the CPU until then)"
  } else {
    Say "NVIDIA $($nvidia[0].Name) found - adding CUDA libraries for fast speech recognition (~1 GB the first time)..."
    RunUv pip install --python $VPY -U nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
    $cublas = Get-ChildItem (Join-Path $ROOT "venv\Lib\site-packages\nvidia") -Recurse -Filter "cublas64_12.dll" -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0 -or -not $cublas) { Warn "couldn't install the CUDA libraries - speech recognition will run on the CPU" }
  }
}
if (-not $env:VOID_CI) {
  Say "Downloading the speech-recognition model..."
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
