# VoidSetup.exe runs this: install.ps1, then a check that Void really works, keeping the window open if it didn't.
param([switch]$NoPause)
& (Join-Path $PSScriptRoot "install.ps1")
$ROOT = Join-Path $env:LOCALAPPDATA "void"
& (Join-Path $ROOT "venv\Scripts\python.exe") -c "import PySide6, sounddevice" 2>$null
if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $ROOT "bin\void.cmd"))) { exit 0 }
Write-Host ""
Write-Host "Void didn't finish installing - see the messages above." -ForegroundColor Red
if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
exit 1
