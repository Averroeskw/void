# Checks a fresh Windows install of Void on a GitHub test machine. Prints PASS/FAIL lines; fails the job on any FAIL.
$ErrorActionPreference = "Continue"
$ROOT = Join-Path $env:LOCALAPPDATA "void"; $APP = Join-Path $ROOT "app"; $VPY = Join-Path $ROOT "venv\Scripts\python.exe"
$OUT = Join-Path $env:GITHUB_WORKSPACE "ci-out"; New-Item -ItemType Directory -Force $OUT | Out-Null
$env:PYTHONUTF8 = "1"
$script:fails = 0
function Check($name, $ok, $detail = "") { if ($ok) { Write-Host "PASS  $name $detail" } else { Write-Host "FAIL  $name $detail"; $script:fails++ } }

& $VPY -c "import PySide6, sounddevice, PIL, faster_whisper, kokoro_onnx, pycaw; print('ok')" | Out-Null
Check "python libraries import" ($LASTEXITCODE -eq 0)
Check "void.cmd" (Test-Path (Join-Path $ROOT "bin\void.cmd"))
Check "Start menu shortcut" (Test-Path (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Void.lnk"))
Check "start at login" (Test-Path (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Void.lnk"))
Check "Kokoro voice files" ((Test-Path "$ROOT\voices\kokoro\kokoro-v1.0.int8.onnx") -and (Test-Path "$ROOT\voices\kokoro\voices-v1.0.bin"))

# a pretend Claude, so a whole request can run
$fake = Join-Path $env:GITHUB_WORKSPACE ".github\ci\fakeclaude.py"
Set-Content -Encoding ASCII "$ROOT\fakeclaude.cmd" "@echo off`r`n`"$VPY`" `"$fake`" %*"
New-Item -ItemType Directory -Force (Join-Path $env:APPDATA "void") | Out-Null
Set-Content -Encoding UTF8 (Join-Path $env:APPDATA "void\config.json") ('{"claude_bin": "' + ("$ROOT\fakeclaude.cmd" -replace '\\','\\') + '", "wake_word": false, "stt_prewarm": false, "trust": "trust"}')

& "$ROOT\bin\void.cmd" show
Start-Sleep 12
try { $st = Invoke-RestMethod http://127.0.0.1:47777/api/state; Check "server answers" $true "(voice: $($st.tts))" } catch { Check "server answers" $false "$_" }
Check "window running" (Test-Path "$ROOT\window.port") "(port file)"

$tok = (Get-Content "$ROOT\runtime.json" | ConvertFrom-Json).token
try {
  $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://127.0.0.1:47777/api/ask -Headers @{"X-Void-Token" = $tok} -ContentType "application/json" -Body '{"prompt":"hello"}'
  $body = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
  Check "a request through Claude" ($body -match "Hi from Windows") ""
  $said = ($body -split "`n" | Where-Object { $_ -like "data: *" } | ForEach-Object { try { ($_.Substring(6) | ConvertFrom-Json).text } catch {} }) -join " "
  Check "unicode survives" ($said -match [regex]::Escape("ünïcödé ✓")) ""
  Check "tool shows on his terminal" ($body -match '"t": "tool"') ""
} catch { Check "a request through Claude" $false "$_" }

$t = & $VPY -c @"
import sys; sys.path.insert(0, r'$APP'); import winsys, time
winsys.dpi_aware()
print('mics', len(winsys.list_mics())); print('play', winsys.can_play())
print('screen', winsys.screenshot('screen', r'$OUT\desktop.png'))
print('window', winsys.screenshot('window', r'$OUT\active-window.png'))
print('volume', winsys.volume_get())
winsys.notify('Void', 'test notification'); time.sleep(3)
"@
Write-Host $t
Check "screenshots" (Test-Path "$OUT\desktop.png")
Set-Clipboard -Value "copied on windows"
$c = & $VPY -c "import sys; sys.path.insert(0, r'$APP'); import winsys; print(winsys.clipboard(r'$OUT\clip.png'))"
Check "clipboard" ($c -match "copied on windows") "$c"

& $VPY (Join-Path $APP "window_win.py") "http://127.0.0.1:47777/?native=1" --selftest "$OUT\face.png" 2>&1 | Out-Host
Check "his window renders" (Test-Path "$OUT\face.png")

& "$ROOT\bin\void.cmd" stop
Start-Sleep 2
$alive = $false; try { Invoke-RestMethod http://127.0.0.1:47777/api/state -TimeoutSec 2 | Out-Null; $alive = $true } catch {}
Check "void stop" (-not $alive)
Copy-Item "$ROOT\server.log" $OUT -ErrorAction SilentlyContinue
Write-Host "`n$script:fails failure(s)"
exit $script:fails
