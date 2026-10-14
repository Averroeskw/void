"""Void's window on Windows: a see-through, always-on-top, frameless Qt window with his page in it (QtWebEngine,
i.e. Chromium). It speaks the same messages with the page as window.py (the GTK/WebKit window on Linux).

    window_win.py URL [toggle|show|hide|quit|listen|look|live|hover|hover-on|hover-off]
    window_win.py URL --selftest out.png      (load him, save a picture of the window, quit — for test machines)

The first instance owns the window; later calls just send it a command over a local socket, so `void listen` on a
hotkey pops him up and listens. Only the parts of the window you can see take clicks (the page says which); the rest
clicks through to whatever is underneath.
"""
import ctypes
import json
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import winsys  # noqa: E402
for _s in (sys.stdout, sys.stderr):               # logs go to a file: write UTF-8, not the old Windows code page
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--autoplay-policy=no-user-gesture-required")
from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QTimer, QUrl, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

W, H = 340, 460
WIDE = 470                   # while settings / history / memory are open the window widens to this
CODE_SIDE = 100              # room either side for his terminal
BALL = (170, 165)            # where the ball's centre sits inside the window
DATA = winsys.data_dir()
POS_FILE, PORT_FILE = DATA / "pos.json", DATA / "window.port"

# the page talks to us through window.webkit.messageHandlers.voidui.postMessage (WebKit's API on Linux); on Windows
# that becomes a QWebChannel call. Messages sent before the channel is up are queued.
SHIM = """
window.voidQt = true;   // Chromium through Qt, not WebKitGTK: the page skips its WebKit-only workarounds
(function(){ var q = [], b = null;
  window.webkit = {messageHandlers: {voidui: {postMessage: function(m){ m = String(m); if (b) b.post(m); else q.push(m); }}}};
  (function go(){
    if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) return setTimeout(go, 30);
    new QWebChannel(qt.webChannelTransport, function(ch){ b = ch.objects.voidui; q.forEach(function(m){ b.post(m); }); q = []; });
  })();
})();
"""


# push-to-talk combos like "Ctrl+Alt+Space" → ({"ctrl", "alt"}, virtual-key code of Space)
MOD_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}
KEY_VKS = {"space": 0x20, "`": 0xC0, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
           "pause": 0x13, **{f"f{n}": 0x6F + n for n in range(1, 25)}}


def parse_combo(text):
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if not parts:
        return None
    key, mods = parts[-1], frozenset(p for p in parts[:-1] if p in MOD_VKS)
    if key in KEY_VKS:
        vk = KEY_VKS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())                                            # A–Z and 0–9 are their own codes
    else:
        return None
    return mods, vk


def log(msg):
    print(f"[void] {msg}", file=sys.stderr, flush=True)


def send(cmd):
    """Pass a command to the running window. False if there isn't one."""
    try:
        port, secret = PORT_FILE.read_text().split()[:2]                 # "port secret" (see VoidWindow._serve)
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1) as s:
            s.sendall(f"{secret} {cmd}".encode())
        return True
    except (OSError, ValueError):
        return False


class Bridge(QObject):
    msg = Signal(str)

    @Slot(str)
    def post(self, m):
        self.msg.emit(m)


class Remote(QObject):
    """Commands arriving on the socket thread → the UI thread."""
    cmd = Signal(str)


class VoidWindow(QWidget):
    def __init__(self, url):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)   # Tool: no taskbar button
        self.setWindowTitle("Void")
        self.setWindowIcon(QIcon(str(APP / "static" / "icon.png")))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(W, H)
        self.cur_w, self.wide_shift, self.want_panel, self.want_code = W, 0, False, False
        self.hover, self.hpos, self.home, self.rects, self.through = False, None, None, None, None
        self.bar, self.bar_home = False, None                                # top bar: his eyes in a notch up top
        self.shown = True
        self.save_t = QTimer(self, singleShot=True, interval=700, timeout=self._save_pos)   # save after you stop moving him

        v = self.view = QWebEngineView(self)
        page = v.page()
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        st = page.settings()
        st.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        st.setAttribute(QWebEngineSettings.ShowScrollBars, False)
        v.setContextMenuPolicy(Qt.NoContextMenu)                    # right-click is his own menu
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(v)

        self.bridge = Bridge()
        self.bridge.msg.connect(self._on_msg)
        chan = QWebChannel(page)
        chan.registerObject("voidui", self.bridge)
        page.setWebChannel(chan)
        f = QFile(":/qtwebchannel/qwebchannel.js")
        f.open(QIODevice.ReadOnly)
        sc = QWebEngineScript()
        sc.setSourceCode(bytes(f.readAll()).decode() + SHIM)
        sc.setInjectionPoint(QWebEngineScript.DocumentCreation)
        sc.setWorldId(QWebEngineScript.MainWorld)
        page.scripts().insert(sc)
        page.featurePermissionRequested.connect(                      # the server records the mic, not the page
            lambda origin, feat: page.setFeaturePermission(origin, feat, QWebEnginePage.PermissionDeniedByUser))
        page.renderProcessTerminated.connect(lambda *a: (log("page renderer stopped — reloading"),
                                                         QTimer.singleShot(400, v.reload)))
        self.url = url
        v.load(QUrl(self._authed_url()))

        self._load_pos()
        self.show()
        self.remote = Remote()
        self.remote.cmd.connect(self._remote_cmd)
        threading.Thread(target=self._serve, daemon=True).start()
        self.base = url.split("?")[0]
        threading.Thread(target=self._watch_server, daemon=True).start()
        self.ptt, self.ptt_held = None, False                           # the push-to-talk combo (the page sends it)
        threading.Thread(target=self._ptt_hook, daemon=True).start()
        QTimer.singleShot(1500, self._tell_page)
        self.poll = QTimer(self, interval=33, timeout=self._poll_mouse)    # click-through ~30×/s; hover ~120×/s
        self.poll.setTimerType(Qt.PreciseTimer)
        self.poll.start()
        self.look_t = 0.0

    # ---- position (remembered across runs)
    def _screen_rect(self):
        scr = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        return scr.availableGeometry()                                    # minus the taskbar

    def _load_pos(self):
        try:
            p = json.loads(POS_FILE.read_text())
            x, y = int(p["x"]), int(p["y"])
            if not any(s.availableGeometry().contains(x + W // 2, y + H // 2) for s in QGuiApplication.screens()):
                raise ValueError                                          # that monitor is gone
        except Exception:  # noqa: BLE001
            g = QGuiApplication.primaryScreen().availableGeometry()
            x, y = g.right() - W - 10, g.bottom() - H - 10                # default: bottom-right corner
        self.move(x, y)

    def moveEvent(self, ev):
        if not self.hover and not self.bar:
            self.save_t.start()
        super().moveEvent(ev)

    def _save_pos(self):
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            POS_FILE.write_text(json.dumps({"x": self.x() + self.wide_shift, "y": self.y()}))
        except OSError:
            pass

    # ---- page plumbing
    def _js(self, code):
        self.view.page().runJavaScript(code)

    def _tell_page(self):
        self._js("window.voidMode&&voidMode(%s)" % json.dumps({"layer": False, "cursor": True, "why": ""}))

    def _on_msg(self, msg):
        if msg.startswith("region:"):
            try:
                self.rects = json.loads(msg[7:])
            except ValueError:
                self.rects = None
            return
        if msg == "reauth":                                              # the server restarted: hand over its new token
            self.view.load(QUrl(self._authed_url()))
            return
        if msg.startswith("ptt:"):
            self.ptt = parse_combo(msg[4:])
            return
        if msg.startswith("dragby:"):
            try:
                dx, dy = (float(x) for x in msg[7:].split(","))
                self.move(self.x() + int(dx), self.y() + int(dy))
            except ValueError:
                pass
            return
        {"drag": self._drag, "pmove": self._pmove, "dragend": self._save_pos, "hide": self.hide_me, "show": self.show_me,
         "hover:on": lambda: self.set_hover(True), "hover:off": lambda: self.set_hover(False),
         "bar:top": lambda: self.set_bar("top"), "bar:bottom": lambda: self.set_bar("bottom"),
         "bar:left": lambda: self.set_bar("left"), "bar:right": lambda: self.set_bar("right"),
         "bar:off": lambda: self.set_bar(None),
         "wide:on": lambda: self.set_wide(True), "wide:off": lambda: self.set_wide(False),
         "code:on": lambda: self.set_wide(True, "code"), "code:off": lambda: self.set_wide(False, "code"),
         "kbd:grab": self._grab_keys, "kbd:release": lambda: None,
         "peek:hide": self.hide, "peek:show": self.show_me}.get(msg, lambda: None)()

    # ---- the bar: merged into a screen edge as a notch (the page draws it on the matching side of the window)
    def set_bar(self, edge, to=None):
        """Dock on `edge` (top / bottom / left / right) of the screen he's on, or undock (None): back to the blob's
        own spot, or to `to` when he's pulled out by the mouse. Tells the page with voidDock()."""
        edge = edge if edge in ("top", "bottom", "left", "right") else None
        if edge:
            if not self.bar:
                self.bar_home = (self.x() + self.wide_shift, self.y())
            self.bar = edge
            scr = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
            g = scr.availableGeometry()                                  # (the bottom edge sits above the taskbar)
            x = {"top": g.center().x() - W // 2, "bottom": g.center().x() - W // 2,
                 "left": g.left(), "right": g.right() - W + 1}[edge]
            y = {"top": g.top(), "bottom": g.bottom() - H + 1,
                 "left": g.center().y() - H // 2, "right": g.center().y() - H // 2}[edge]
            self.move(x - self.wide_shift, y)
        else:
            was, self.bar = self.bar, None
            if to:
                self.move(int(to[0]), int(to[1]))
                self._save_pos()
            elif was and self.bar_home:
                self.move(self.bar_home[0] - self.wide_shift, self.bar_home[1])
                self._save_pos()
        self._js("window.voidDock&&voidDock(%s)" % json.dumps(edge or ""))

    def _drag_done(self):
        """After you drop the blob: pushed into a screen edge? He merges into it as the bar. Judged by the mouse, not
        his ball: Windows won't let a window go above the top of the screen, so the ball can never reach the top."""
        c = QCursor.pos()
        scr = QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        dist = {"top": c.y() - g.top(), "bottom": g.bottom() - c.y(), "left": c.x() - g.left(), "right": g.right() - c.x()}
        edge = min(dist, key=dist.get)
        if dist[edge] < 40:                                              # dropped right at the edge
            self.set_bar(edge)

    def _watch_drag(self):
        """Windows moves the window itself while you drag (startSystemMove); watch for the mouse button coming up."""
        def check():
            if not (ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):   # VK_LBUTTON released
                self._drag_t.stop()
                self._drag_done()
        self._drag_t = QTimer(self, interval=50, timeout=check)
        self._drag_t.start()

    def _drag(self):
        if self.bar:                                                     # pulling the bar away: out pops the blob,
            c = QCursor.pos()                                            # under your mouse, still being dragged
            self.set_bar(None, to=(c.x() - BALL[0] - self.wide_shift, c.y() - BALL[1]))
        if not self.hover and self.windowHandle():
            self._set_through(False)
            self.windowHandle().startSystemMove()
            self._watch_drag()

    def _pmove(self):
        """Dragging a panel's header: move the window as it is (no undocking, no docking on drop; in the bar the
        page sends bar:<edge> when the panel closes, which puts the notch back)."""
        if not self.hover and self.windowHandle():
            self._set_through(False)
            self.windowHandle().startSystemMove()

    def _grab_keys(self):
        self.activateWindow()
        self.raise_()
        self.view.setFocus()

    # ---- click-through: only what the page says is solid takes the mouse
    def _set_through(self, on):
        if on == self.through:
            return
        self.through = on
        hwnd = int(self.winId())
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(hwnd, -20)                                  # GWL_EXSTYLE
        ex = (ex | 0x20 | 0x80000) if on else (ex & ~0x20)                # WS_EX_TRANSPARENT (+ WS_EX_LAYERED)
        u.SetWindowLongW(hwnd, -20, ex)

    def _poll_mouse(self):
        if self.hover:
            self._follow()
            return
        if self.rects is None or not self.isVisible():
            self._set_through(False)
            return
        p = QCursor.pos() - self.pos()
        solid = any(x <= p.x() < x + w and y <= p.y() < y + h for x, y, w, h in self.rects)
        self._set_through(not solid)

    # ---- show / hide / commands
    def show_me(self):
        self.shown = True
        self.show()
        self.raise_()
        self._js("window.voidShown&&voidShown()")

    def hide_me(self):
        self.shown = False
        self._js("window.voidHidden&&voidHidden()")
        self.hide()

    def _remote_cmd(self, c):
        if c == "quit":
            QApplication.quit()
        elif c == "toggle":
            if self.hover:
                self._js("window.voidCmd&&voidCmd('home')")
            elif self.isVisible():
                self.hide_me()
            else:
                self.show_me()
        elif c == "show":
            self.show_me()
        elif c == "hide":
            self.hide_me()
        elif c in ("ptt-down", "ptt-up"):
            log(f"push-to-talk: {c[4:]}")
            if c == "ptt-down" and not self.isVisible():
                self.show_me()
            self._js("window.voidCmd&&voidCmd(%s)" % json.dumps(c))
        elif c in ("listen", "look", "live", "hover", "hover-on", "hover-off"):
            if not self.isVisible():
                self.show_me()
            self._js("window.voidCmd&&voidCmd(%s)" % json.dumps(c))

    def _authed_url(self):
        """The page's address with the server's token after '#': read from runtime.json (only you can read it) and
        never on a command line or in the page the server hands out; the part after '#' is never sent anywhere."""
        for _ in range(50):                                              # (a just-started server writes it in a moment)
            try:
                return self.url + "#t=" + json.loads((DATA / "runtime.json").read_text())["token"]
            except (OSError, ValueError, KeyError):
                time.sleep(0.1)
        log("couldn't read the server's token (runtime.json)")
        return self.url

    def _server_up(self):
        import urllib.error
        import urllib.request
        try:
            urllib.request.urlopen(self.base + "api/state", timeout=2)
            return True
        except urllib.error.HTTPError:                                    # it answered: it's running
            return True
        except Exception:  # noqa: BLE001
            return False

    def _watch_server(self):
        """If the server stops (a crash), start it again instead of leaving him deaf with "failed to fetch". The page
        reconnects by itself: a new server means a new token, and it reloads when it sees "forbidden"."""
        misses, last = 0, 0.0
        while True:
            time.sleep(3)
            if self._server_up():
                misses = 0
                continue
            misses += 1
            if misses >= 2 and time.monotonic() - last > 20:
                last = time.monotonic()
                log("the server isn't answering — starting it again")
                try:
                    import launch_win
                    launch_win._start("server", [str(APP / "server.py")])
                except Exception as e:  # noqa: BLE001
                    log(f"couldn't restart the server: {e}")

    def _ptt_hook(self):
        """The push-to-talk hotkey, anywhere in Windows: a low-level keyboard hook (RegisterHotKey can't tell when a
        key is let go). It only compares each key with the combo; everything else passes straight through untouched,
        and nothing is recorded. The combo's own key presses are kept from the app you're in."""
        import ctypes.wintypes as wt
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.CallNextHookEx.argtypes = (wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
        user32.SetWindowsHookExW.restype = wt.HHOOK
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wt.HMODULE

        class KBD(ctypes.Structure):
            _fields_ = [("vk", wt.DWORD), ("scan", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                        ("extra", ctypes.c_size_t)]

        def held(vks):
            return any(user32.GetAsyncKeyState(v) & 0x8000 for v in vks)

        def proc(code, wparam, lparam):
            combo = self.ptt
            if code == 0 and combo:
                mods, key = combo
                vk = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents.vk
                if vk == key:
                    down = wparam in (0x100, 0x104)                     # WM_KEYDOWN / WM_SYSKEYDOWN
                    if down and not self.ptt_held and all(held(MOD_VKS[m]) for m in mods):
                        self.ptt_held = True
                        self.remote.cmd.emit("ptt-down")
                        return 1
                    if self.ptt_held:
                        if not down:
                            self.ptt_held = False
                            self.remote.cmd.emit("ptt-up")
                        return 1                                         # (and swallow the key's auto-repeat)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._ptt_proc = HOOKPROC(proc)                                  # keep it alive: Windows calls it
        if not user32.SetWindowsHookExW(13, self._ptt_proc, kernel32.GetModuleHandleW(None), 0):   # WH_KEYBOARD_LL
            log("push-to-talk hotkey unavailable (couldn't watch the keyboard)")
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:   # low-level hooks need a message loop
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _serve(self):
        """Commands from `void listen` & co. A local port any program (any user) could reach, so each command must
        start with this run's secret, which sits next to the port number in a file only you can read."""
        import hmac
        import secrets
        secret = secrets.token_urlsafe(18)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        DATA.mkdir(parents=True, exist_ok=True)
        PORT_FILE.write_text(f"{srv.getsockname()[1]} {secret}")
        while True:
            c, _ = srv.accept()
            try:
                got, _, cmd = c.recv(128).decode(errors="replace").strip().partition(" ")
            finally:
                c.close()
            if cmd and hmac.compare_digest(got, secret):
                self.remote.cmd.emit(cmd.strip())

    # ---- widening for panels / his terminal
    def set_wide(self, on, kind="panel"):
        if kind == "panel":
            self.want_panel = on
        else:
            self.want_code = on
        L = max(WIDE - W if self.want_panel else 0, CODE_SIDE if self.want_code else 0)
        R = CODE_SIDE if self.want_code else 0
        if self.hover:
            L = R = 0
        base = self.x() + self.wide_shift                                 # where his own 340px strip starts
        g = self._screen_rect()
        L, R = min(L, max(0, base - g.left())), min(R, max(0, g.right() - base - W))
        if W + L + R == self.cur_w and L == self.wide_shift:
            return
        self.wide_shift, self.cur_w = L, W + L + R
        self.setGeometry(base - L, self.y(), self.cur_w, H)
        self._js(f"window.voidLayout&&voidLayout({L})")

    # ---- hover mode: he is the mouse pointer
    def set_hover(self, on):
        if self.hover == on:
            return
        if on and self.bar:                                              # out of the notch first (it mustn't fly around)
            self.set_bar(None)
        if on and self.cur_w > W:
            self.want_code = False
            self.set_wide(False)
        self.hover = on
        self.poll.setInterval(8 if on else 33)                            # as a cursor he has to keep up with the mouse
        if on:
            self.home, self.hpos, self.vel, self.last = (self.x(), self.y()), None, [0.0, 0.0], None
            self._set_through(True)
        else:
            self._glide_home()
        self._js(f"window.voidHover&&voidHover({'true' if on else 'false'})")

    def _follow(self):
        c = QCursor.pos()
        now = time.monotonic()
        if self.last:
            dt = max(1e-3, now - self.last[2])
            self.vel = [self.vel[0] * 0.8 + (c.x() - self.last[0]) / dt * 0.2,
                        self.vel[1] * 0.8 + (c.y() - self.last[1]) / dt * 0.2]
        self.last = (c.x(), c.y(), now)
        self.hpos = [c.x() - BALL[0], c.y() - BALL[1]]                   # right on the pointer: he *is* the cursor
        if (self.x(), self.y()) != (self.hpos[0], self.hpos[1]):
            self.move(self.hpos[0], self.hpos[1])
        if now - self.look_t >= 0.033:                                    # his eyes only need ~30 updates a second
            self.look_t = now
            self._js("window.voidLook&&voidLook(%.3f,%.3f)" % (max(-1, min(1, self.vel[0] / 900)),
                                                               max(-1, min(1, self.vel[1] / 900))))

    def _glide_home(self):
        if not self.home:
            return
        (hx, hy), (sx, sy), t0 = self.home, (self.x(), self.y()), time.monotonic()

        def step():
            k = min(1.0, (time.monotonic() - t0) / 0.65)
            e = 1 - (1 - k) ** 3
            self.move(int(sx + (hx - sx) * e), int(sy + (hy - sy) * e + math.sin(math.pi * k) * -40))
            if k < 1:
                QTimer.singleShot(8, step)
            else:
                self._save_pos()
        step()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    url = sys.argv[1]
    if len(sys.argv) > 3 and sys.argv[2] == "--selftest":
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] += " --disable-gpu"
        app = QApplication(sys.argv[:1])
        win = VoidWindow(url)
        out = sys.argv[3]

        def snap():
            win.grab().save(out)
            log(f"selftest: saved {out} ({win.width()}x{win.height()})")
            app.quit()
        QTimer.singleShot(9000, snap)
        sys.exit(app.exec())
    cmd = sys.argv[2] if len(sys.argv) > 2 else "toggle"
    if send(cmd):
        return
    if cmd in ("hide", "quit"):
        return
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    win = VoidWindow(url)
    if cmd in ("listen", "look", "live", "hover", "hover-on"):
        QTimer.singleShot(1800, lambda: win._remote_cmd(cmd))         # first launch: let the page load, then do it
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
