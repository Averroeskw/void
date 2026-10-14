"""Windows versions of the parts of Void that talk to the operating system: microphone, speakers, screenshots,
clipboard, notifications, volume / media keys / brightness, opening apps, locking, start at login.

On Linux, server.py and quick.py use command-line tools for these (parecord, paplay, grim, wl-paste, pactl, …);
everything else in Void is the same on both.
"""
import ctypes
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW: helper programs never flash a console window
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def ps(script, timeout=15):
    """Run a PowerShell snippet quietly → (ok, output)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                            script], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def _q(s):
    """Quote for PowerShell single-quoted strings."""
    return "'" + str(s).replace("'", "''") + "'"


# ---------------------------------------------------------------- where things live
def data_dir():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "void"


def config_dir():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "void"


def cache_dir():
    return data_dir() / "cache"


def dpi_aware():
    """Screen coordinates in real pixels (otherwise Windows scaling would shrink screenshots / window positions)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- microphone
class Mic:
    """Records 16-bit mono at `rate`. Same shape as the Linux recorder: read(timeout) → bytes, close()."""

    def __init__(self, rate, device=None):
        import sounddevice as sd
        self.q = queue.Queue()
        dev = (device or "").strip() or None
        if dev and dev.isdigit():
            dev = int(dev)
        self.stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=int(rate * 0.02),
                                        device=dev, callback=lambda data, frames, t, status: self.q.put(bytes(data)))
        self.stream.start()
        self.name = "sounddevice"

    def read(self, timeout):
        out = bytearray()
        try:
            out += self.q.get(timeout=timeout)
            while True:
                out += self.q.get_nowait()
        except queue.Empty:
            pass
        return bytes(out)

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:  # noqa: BLE001
            pass

    def err(self):
        return ""


def list_mics():
    """Inputs for Settings → Voice (the id is the device name; any part of it works)."""
    try:
        import sounddevice as sd
        api = sd.query_devices(kind="input")["hostapi"]     # just the default driver's list (no MME/WASAPI repeats)
        return [{"id": d["name"], "label": d["name"]} for d in sd.query_devices()
                if d["max_input_channels"] > 0 and d["hostapi"] == api]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- speakers
class Player:
    """Looks like a `paplay` process to the Speaker: .stdin.write(raw 16-bit mono), .poll(), .kill(). Playback runs on
    sounddevice's own audio thread; the Speaker keeps feeding it ~0.8 s ahead, exactly as on Linux."""

    def __init__(self, rate):
        import sounddevice as sd
        self.buf, self.lock, self.alive = bytearray(), threading.Lock(), True

        def cb(outdata, frames, t, status):
            n = frames * 2
            with self.lock:
                chunk = bytes(self.buf[:n])
                del self.buf[:n]
            outdata[:len(chunk)] = chunk
            if len(chunk) < n:
                outdata[len(chunk):] = b"\0" * (n - len(chunk))

        self.stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", latency=0.1, callback=cb)
        self.stream.start()
        self.stdin = self

    def write(self, data):
        with self.lock:
            self.buf += data

    def flush(self):
        pass

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.alive = False
        try:
            self.stream.abort()
            self.stream.close()
        except Exception:  # noqa: BLE001
            pass


def can_play():
    try:
        import sounddevice as sd
        sd.query_devices(kind="output")
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- screenshots & clipboard
def active_window():
    """(x, y, w, h) of the window you're using, in screen pixels — None if there isn't one (or it's Void)."""
    from ctypes import wintypes
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h:
        return None
    title = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(h, title, 256)
    if title.value == "Void":
        return None
    r = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:   # frame w/o shadow
        u.GetWindowRect(h, ctypes.byref(r))
    w, ht = r.right - r.left, r.bottom - r.top
    return (r.left, r.top, w, ht) if w > 20 and ht > 20 else None


def _clip_image():
    from PIL import Image, ImageGrab
    img = ImageGrab.grabclipboard()
    return img if isinstance(img, Image.Image) else None


def screenshot(kind, out):
    """kind: window | screen | region → saves a PNG at `out`, returns the kind it really got."""
    from PIL import ImageGrab
    if kind == "region":                              # Windows' own snipping tool → the clipboard
        before = _clip_image()
        os.startfile("ms-screenclip:")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90:
            time.sleep(0.4)
            img = _clip_image()
            if img is not None and (before is None or img.tobytes()[:4096] != before.tobytes()[:4096]):
                img.save(out)
                return "region"
        raise RuntimeError("cancelled")
    box = None
    if kind == "window":
        g = active_window()
        if g:
            box = (g[0], g[1], g[0] + g[2], g[1] + g[3])
    ImageGrab.grab(bbox=box, all_screens=box is None).save(out)
    return "window" if box else "screen"


def clipboard(out):
    """→ ("image", path) or ("text", text)."""
    img = _clip_image()
    if img is not None:
        img.save(out)
        return "image", str(out)
    ok, text = ps("Get-Clipboard -Raw")
    return "text", text if ok else ""


# ---------------------------------------------------------------- notifications
def notify(title, body):
    """A Windows notification (toast), shown through PowerShell so nothing extra needs installing."""
    from xml.sax.saxutils import escape
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text>"
           f"<text>{escape(body[:300])}</text></binding></visual></toast>")
    script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]"
              " > $null; [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] > $null;"
              f" $x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml({_q(xml)});"
              " $id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
              " [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
              "[Windows.UI.Notifications.ToastNotification]::new($x))")
    threading.Thread(target=ps, args=(script,), daemon=True).start()


# ---------------------------------------------------------------- shortcuts & start at login
def shortcut(path, target, args="", icon=None, workdir=None):
    """Create a .lnk (Start menu, Startup folder)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_q(path)}); $s.TargetPath = {_q(target)};"
              f" $s.Arguments = {_q(args)}; $s.WorkingDirectory = {_q(workdir or Path(target).parent)};"
              + (f" $s.IconLocation = {_q(icon)};" if icon else "") + " $s.Save()")
    return ps(script)[0]


def startup_link():
    return Path(os.environ.get("APPDATA") or "") / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Void.lnk"


# ---------------------------------------------------------------- volume, media keys, brightness, lock, apps
def _endpoint_volume():
    import comtypes
    comtypes.CoInitialize()
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    spk = AudioUtilities.GetSpeakers()
    if hasattr(spk, "EndpointVolume"):                         # newer pycaw
        return spk.EndpointVolume
    from comtypes import CLSCTX_ALL
    iface = spk.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


def volume_get():
    """→ (percent, muted) or (None, False)."""
    try:
        v = _endpoint_volume()
        return round(v.GetMasterVolumeLevelScalar() * 100), bool(v.GetMute())
    except Exception:  # noqa: BLE001
        return None, False


def _mic_endpoint(hint=""):
    """The Windows volume control of the mic Void records from: the one whose name matches mic_device (sounddevice
    cuts names to 31 characters, so it may be just the start), else the default recording device."""
    import comtypes
    comtypes.CoInitialize()
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    dev, hint = None, (hint or "").strip().lower()
    if hint and not hint.isdigit():
        for d in AudioUtilities.GetAllDevices():
            if (d.id or "").startswith("{0.0.1.") and str(d.state).endswith("Active") and hint in (d.FriendlyName or "").lower():
                dev = en.GetDevice(d.id)                       # {0.0.1.…} = a recording device
                break
    dev = dev or en.GetDefaultAudioEndpoint(1, 0)              # eCapture, eConsole
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


def mic_level(hint=""):
    """→ the mic's Windows level, 0.0–1.0, or None."""
    try:
        return float(_mic_endpoint(hint).GetMasterVolumeLevelScalar())
    except Exception:  # noqa: BLE001
        return None


def set_mic_level(level, hint=""):
    try:
        _mic_endpoint(hint).SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None)
        return True
    except Exception:  # noqa: BLE001
        return False


def _key(vk, times=1):
    u = ctypes.windll.user32
    for _ in range(times):
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)          # KEYEVENTF_KEYUP


def volume_set(op, val=None):
    """op: set | up | down | mute | unmute. Uses the volume control itself, or the volume keys if that's unavailable."""
    try:
        v = _endpoint_volume()
        if op == "set":
            v.SetMasterVolumeLevelScalar(max(0, min(100, val)) / 100, None)
        elif op in ("up", "down"):
            cur = v.GetMasterVolumeLevelScalar() * 100
            v.SetMasterVolumeLevelScalar(max(0, min(100, cur + (val or 10) * (1 if op == "up" else -1))) / 100, None)
        else:
            v.SetMute(op == "mute", None)
        return True
    except Exception:  # noqa: BLE001
        if op in ("up", "down"):
            _key(0xAF if op == "up" else 0xAE, max(1, (val or 10) // 2))   # each key press is 2%
            return True
        if op in ("mute", "unmute"):
            _key(0xAD)
            return True
        return False


def media(op):
    """op: pause | play | toggle | next | previous — the media keys, which every Windows player listens to."""
    _key({"pause": 0xB3, "play": 0xB3, "toggle": 0xB3, "next": 0xB0, "previous": 0xB1}[op])
    return True


def brightness(op, val=None):
    """Laptop screens (WMI). → new percent or None."""
    ok, cur = ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    if not ok or not cur.strip().split()[0:1]:
        return None
    now = int(cur.split()[0])
    new = val if op == "set" else now + (val or 10) * (1 if op == "up" else -1)
    new = max(0, min(100, int(new)))
    ok, _ = ps(f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
               f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={new}}}")
    return new if ok else None


def lock():
    return bool(ctypes.windll.user32.LockWorkStation())


_apps_cache = {"t": 0, "apps": []}


def _start_apps():
    """Everything in the Start menu (normal and Store apps) → [{"Name", "AppID"}]."""
    if time.monotonic() - _apps_cache["t"] > 300:
        ok, out = ps("Get-StartApps | ConvertTo-Json -Compress", timeout=20)
        try:
            apps = json.loads(out) if ok and out else []
            _apps_cache.update(t=time.monotonic(), apps=apps if isinstance(apps, list) else [apps])
        except ValueError:
            pass
    return _apps_cache["apps"]


def open_app(name):
    """Start an app by (part of) its Start-menu name → its proper name, or None if nothing matches."""
    name = name.lower().strip(" .")
    best = None
    for a in _start_apps():
        nm = str(a.get("Name") or "")
        low = nm.lower()
        score = 3 if low == name else 2 if low.startswith(name) or name in low.split() else 1 if name in low else 0
        if score and (not best or score > best[0]):
            best = (score, a)
    if not best:
        return None
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{best[1]['AppID']}"], creationflags=NO_WINDOW)
    return best[1]["Name"]


def gpu_ok():
    """An NVIDIA card with drivers (for fast speech recognition)."""
    from shutil import which
    return which("nvidia-smi") is not None


def add_cuda_dirs():
    """faster-whisper on the GPU needs cuBLAS/cuDNN: the installer puts them in the venv (pip nvidia-* wheels)."""
    import glob
    import site
    for r in site.getsitepackages():
        for d in glob.glob(os.path.join(r, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass
