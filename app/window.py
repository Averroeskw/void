#!/usr/bin/env python3
"""Void overlay: a see-through GTK + WebKit surface that is *just* the little guy.

On Wayland desktops that support layer-shell (Hyprland, sway, KDE Plasma, river, niri, Wayfire, labwc, COSMIC…) he's
an *overlay* (like a bar or a notification), not an app window: he sits on top of every app on every workspace, never
gets tiled, has no taskbar entry and needs no window rules. Where there's no layer-shell (GNOME, or X11 window managers:
i3, Openbox, XFCE, Cinnamon, MATE…) he's a borderless always-on-top window on every workspace instead — on a Wayland
desktop without layer-shell he restarts himself under XWayland for that, since only X11 lets an app stay on top.

Runs with the system Python (needs PyGObject + WebKit2GTK 4.1).

    window.py URL [toggle|show|hide|quit|listen|look|hover|hover-on|hover-off]

The first instance owns the window; later calls just send it a command over a unix socket,
so binding `void` (or `void listen`) to a hotkey pops him in and out like a quick-shell.

Only the parts of the window you can see take clicks (the ball, open menus/panels); everything else clicks
through to whatever is underneath. In hover mode he floats along next to your mouse cursor
(Hyprland: through its control socket; X11/XWayland: by moving the window).
"""
import json
import math
import os
import socket
import sys
import threading
import time

# He's an overlay (a layer-shell surface, like a bar or a notification), which only works when GTK talks Wayland.
# Lots of setups (NVIDIA ones especially) set GDK_BACKEND=x11, or start apps without WAYLAND_DISPLAY — then GTK quietly
# uses XWayland and he ends up as an ordinary app window. Fix that up before GTK loads.
LAYER_WHY = []
if os.environ.get("VOID_NO_LAYER") != "1":
    _rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.environ.get("WAYLAND_DISPLAY") and os.path.isdir(_rt):
        _socks = sorted(f for f in os.listdir(_rt) if f.startswith("wayland-") and not f.endswith(".lock"))
        if _socks:
            os.environ["WAYLAND_DISPLAY"] = _socks[0]
            LAYER_WHY.append(f"WAYLAND_DISPLAY wasn't set — found {_socks[0]}")
    _be = os.environ.get("GDK_BACKEND", "")
    if os.environ.get("WAYLAND_DISPLAY") and _be and _be.split(",")[0].strip() not in ("wayland", "*"):
        LAYER_WHY.append(f"GDK_BACKEND was '{_be}' — using wayland for Void so it can be an overlay")
        os.environ["GDK_BACKEND"] = "wayland"

# His face (this window + WebKit's processes, which inherit this) on the first half of the CPU cores; the server
# (voice + brain) takes the other half — drawing can never starve his voice. Off with "cpu_split": false.
def _cpu_split():
    try:
        conf = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "void", "config.json")
        try:
            if json.load(open(conf)).get("cpu_split", True) is False:
                return
        except (OSError, ValueError):
            pass
        cores = sorted(os.sched_getaffinity(0))
        if len(cores) >= 4:
            os.sched_setaffinity(0, set(cores[:max(1, len(cores) // 2)]))
    except (AttributeError, OSError):
        pass


_cpu_split()

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib  # noqa: E402

GLib.set_prgname("Void")                # before Gtk loads → X11 WM_CLASS + Wayland app_id "Void"
GLib.set_application_name("Void")
from gi.repository import Gdk, Gtk, WebKit2  # noqa: E402

GtkLayerShell = None
if os.environ.get("VOID_LAYER_WHY"):            # we restarted ourselves under XWayland (below): keep the reason for the log
    LAYER_WHY.append(os.environ["VOID_LAYER_WHY"])
elif os.environ.get("VOID_NO_LAYER") == "1":
    LAYER_WHY.append("VOID_NO_LAYER=1 is set")
elif not os.environ.get("WAYLAND_DISPLAY"):
    LAYER_WHY.append("not running on Wayland (no WAYLAND_DISPLAY)")
else:
    try:
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell  # noqa: E402
        if not GtkLayerShell.is_supported():
            GtkLayerShell = None
            LAYER_WHY.append("the compositor doesn't support layer-shell (or GTK isn't on Wayland)")
    except (ValueError, ImportError, AttributeError) as _e:
        GtkLayerShell = None
        LAYER_WHY.append(f"gtk-layer-shell isn't installed ({_e}) — install gtk-layer-shell (Debian/Ubuntu: "
                         "gir1.2-gtklayershell-0.1)")
# A Wayland desktop without layer-shell (GNOME…): a plain Wayland window can't stay on top or place itself, so start
# over under XWayland, where it can (always-on-top, on every workspace, remembered position, hover mode).
if GtkLayerShell is None and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY") \
        and os.environ.get("GDK_BACKEND", "").split(",")[0] != "x11" and os.environ.get("VOID_NO_XWAYLAND") != "1":
    os.environ.update(GDK_BACKEND="x11", VOID_NO_LAYER="1",
                      VOID_LAYER_WHY="; ".join(LAYER_WHY) + " — running under XWayland so he can stay on top")
    os.execv(sys.executable, [sys.executable] + sys.argv)

try:                                          # python-cairo + gi's cairo bridge: input regions
    gi.require_foreign("cairo")
    import cairo
except (ImportError, Exception):  # noqa: BLE001
    cairo = None

W, H = 340, 460
WIDE = 470                   # while settings / history / memory are open the window widens to this
CODE_SIDE = 100              # room on each side for the code panel when he swings it out to his side
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"void-{os.getuid()}.sock")
BALL = (170, 165)            # where the ball's centre sits inside the window (CSS px)
POS_FILE = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "void", "pos.json")


_logged = set()


def log(msg):
    """→ `void log`. Each distinct message once, so a loop can't flood the log."""
    if msg not in _logged:
        _logged.add(msg)
        print(f"[void] {msg}", file=sys.stderr, flush=True)


def send(cmd):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1)
        s.connect(SOCK)
        s.sendall(cmd.encode())
        s.close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- Hyprland control socket
class Hypr:
    def __init__(self):
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        self.path = None
        if sig:
            for base in (os.environ.get("XDG_RUNTIME_DIR", ""), "/tmp"):
                p = os.path.join(base, "hypr", sig, ".socket.sock")
                if os.path.exists(p):
                    self.path = p
                    break
        self.addr = None

    def ok(self):
        return bool(self.path)

    def req(self, msg):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(0.5)
        try:
            s.connect(self.path)
            s.sendall(msg.encode())
            out = b""
            while True:
                b = s.recv(65536)
                if not b:
                    break
                out += b
            return out.decode(errors="replace")
        finally:
            s.close()

    def cursor(self):
        j = json.loads(self.req("j/cursorpos"))
        return j["x"], j["y"]

    def find(self):
        for c in json.loads(self.req("j/clients")):
            if c.get("class") == "Void" or c.get("initialClass") == "Void":
                self.addr = c["address"]
                return c
        return None

    def dispatch(self, what):
        r = self.req(f"dispatch {what}").strip()
        if r != "ok":
            log(f"hyprland: '{what}' -> {r!r}")
        return r == "ok"

    def setup(self):
        """Don't trust window rules (their syntax keeps changing): make him floating, pinned and exactly W×H directly."""
        c = self.find()
        if not c:
            log("hyprland: couldn't find the Void window in `hyprctl clients`")
            return False
        a = f"address:{self.addr}"
        if not c.get("floating"):
            self.dispatch(f"setfloating {a}") or self.dispatch(f"togglefloating {a}")
        if not c.get("pinned"):
            self.dispatch(f"pin {a}")
        if list(c.get("size") or []) != [W, H]:
            self.dispatch(f"resizewindowpixel exact {W} {H},{a}")
        c = self.find() or {}
        log(f"hyprland: window {self.addr} floating={c.get('floating')} pinned={c.get('pinned')} "
            f"size={c.get('size')} at={c.get('at')}")
        return True

    def monitors(self):
        out = []
        for m in json.loads(self.req("j/monitors")):
            sc = float(m.get("scale") or 1)
            w, h = m["width"] / sc, m["height"] / sc
            if m.get("transform", 0) % 2:
                w, h = h, w
            out.append((m["x"], m["y"], w, h))
        return out

    def move(self, x, y):
        if not self.addr:
            self.find()
        if self.addr:
            r = self.req(f"dispatch movewindowpixel exact {int(x)} {int(y)},address:{self.addr}").strip()
            if r != "ok":
                log(f"hyprland: move failed ({r!r}) — looking for the window again")
                self.addr = None


class VoidWindow:
    def __init__(self, url):
        w = self.win = Gtk.Window(title="Void")
        w.set_decorated(False)
        w.set_resizable(False)
        w.set_default_size(W, H)
        w.set_size_request(W, H)
        w.set_keep_above(True)
        w.set_type_hint(Gdk.WindowTypeHint.UTILITY)       # i3/sway(X) float utility windows by default
        w.set_app_paintable(True)
        visual = w.get_screen().get_rgba_visual()
        if visual is not None and w.get_screen().is_composited():
            w.set_visual(visual)
        else:
            print("[void] no compositor/RGBA visual — window won't be see-through", file=sys.stderr)
        # (no draw handler that clears the window: with GPU rendering WebKit only repaints what changed,
        #  so clearing here made everything outside the animating ball vanish until the mouse moved)
        self.cur_w, self.wide_shift = W, 0
        self.want_panel = self.want_code = False
        self.dragging = False                   # (set before _place() can run — it checks this)
        self.hover = False
        self.layer = GtkLayerShell is not None
        self.mon_i, self.mx, self.my = 0, 0, 0
        if self.layer:
            L = GtkLayerShell
            L.init_for_window(w)
            L.set_layer(w, L.Layer.OVERLAY)
            L.set_namespace(w, "void")
            L.set_anchor(w, L.Edge.TOP, True)
            L.set_anchor(w, L.Edge.LEFT, True)
            L.set_exclusive_zone(w, -1)                    # don't push other things around, and may sit over bars
            try:
                L.set_keyboard_mode(w, L.KeyboardMode.ON_DEMAND)   # takes the keyboard when you click him
            except AttributeError:
                L.set_keyboard_interactivity(w, True)
            self._load_pos()
            for why in LAYER_WHY:
                log(f"overlay: {why}")
            log(f"overlay: layer-shell on monitor {self.mon_i} at {self.mx},{self.my}")
        else:
            log("overlay: no layer-shell — always-on-top window instead, because: " + ("; ".join(LAYER_WHY) or "unknown"))
            w.set_skip_taskbar_hint(True)
            w.set_skip_pager_hint(True)
            w.stick()                                       # on every workspace
            self.save_t = None
            w.connect("configure-event", self._moved)

        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("voidui")
        ucm.connect("script-message-received::voidui", self._on_msg)
        v = self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        v.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        st = v.get_settings()
        st.set_enable_webaudio(True)
        st.set_media_playback_requires_user_gesture(False)
        st.set_enable_developer_extras(os.environ.get("VOID_DEVTOOLS") == "1")
        st.set_enable_back_forward_navigation_gestures(False)
        try:
            st.set_enable_write_console_messages_to_stdout(True)   # page errors land in `void log`
        except AttributeError:
            pass
        if os.environ.get("VOID_DEVTOOLS") != "1":
            v.connect("context-menu", lambda *a: True)   # right-click is his own menu
        v.connect("permission-request", lambda _v, req: (req.deny(), True)[1])   # the server records the mic, not the page
        # if the page's renderer crashes (GPU drivers…), bring him straight back instead of leaving an empty window
        try:
            v.connect("web-process-terminated", lambda _v, why: (log(f"page renderer stopped ({why}) — reloading"),
                                                                 GLib.timeout_add(400, lambda: (_v.reload(), False)[1]))[1] and False)
        except TypeError:
            pass
        v.load_uri(url)
        w.add(v)
        w.connect("delete-event", lambda *a: (self.hide(), True)[1])
        if not self.layer:
            self._load_xpos()
        w.show_all()
        v.grab_focus()
        # Redraw the whole window on every frame. WebKit only reports the bits that changed, and on some GPU/compositor
        # combos (NVIDIA + layer-shell especially) the untouched parts of the see-through window come back from an older
        # buffer: menus and panels around him flicker or vanish until something moves. A full redraw is cheap at this size.
        if os.environ.get("VOID_PARTIAL_REDRAW") != "1":
            v.add_tick_callback(lambda wid, _clock: (wid.queue_draw(), True)[1])

        self.hypr = Hypr()
        self.shown = True
        self.mons = None
        if self.hypr.ok() and not self.layer:
            GLib.timeout_add(400, self._hypr_setup)
        GLib.timeout_add(1500, self._tell_page)
        self.hpos = None          # where the window is (hover mode), smoothed
        self.last_sent = None
        self.rects = None
        self.region_on = False
        threading.Thread(target=self._serve, daemon=True).start()

    # ---- where he is (layer mode: margins from the top-left of a monitor)
    @staticmethod
    def _monitors():
        d = Gdk.Display.get_default()
        return [d.get_monitor(i) for i in range(d.get_n_monitors())]

    def _geom(self, i=None):
        ms = self._monitors()
        m = ms[min(self.mon_i if i is None else i, len(ms) - 1)]
        return m, m.get_geometry()

    def _load_pos(self):
        try:
            p = json.load(open(POS_FILE))
            self.mon_i, self.mx, self.my = int(p["mon"]), int(p["x"]), int(p["y"])
            if self.mon_i >= len(self._monitors()):
                raise ValueError
        except Exception:  # noqa: BLE001
            self.mon_i = 0
            _, g = self._geom()
            self.mx, self.my = g.width - W - 10, g.height - H - 10       # default: bottom-right corner
        GtkLayerShell.set_monitor(self.win, self._geom()[0])
        self._place(self.mx, self.my, clamp=True)

    # ---- where he is (window mode: global screen position, saved after you drag him)
    def _load_xpos(self):
        try:
            p = json.load(open(POS_FILE))
            x, y = int(p["gx"]), int(p["gy"])
            if not any(g.x <= x + W // 2 < g.x + g.width and g.y <= y + H // 2 < g.y + g.height
                       for g in (m.get_geometry() for m in self._monitors())):
                raise ValueError                              # that monitor is gone
        except Exception:  # noqa: BLE001
            g = self._geom(0)[1]
            x, y = g.x + g.width - W - 10, g.y + g.height - H - 10     # default: bottom-right corner
        self.mx, self.my = x, y
        self.win.move(x, y)

    def _moved(self, win, _ev):
        x, y = win.get_position()
        if (x, y) == (self.mx, self.my) or self.hover:
            return False
        self.mx, self.my = x, y
        if self.save_t:
            GLib.source_remove(self.save_t)
        self.save_t = GLib.timeout_add(700, self._save_xpos)
        return False

    def _save_xpos(self):
        self.save_t = None
        try:
            try:
                p = json.load(open(POS_FILE))
            except (OSError, ValueError):
                p = {}
            p.update(gx=self.mx, gy=self.my)
            os.makedirs(os.path.dirname(POS_FILE), exist_ok=True)
            json.dump(p, open(POS_FILE, "w"))
        except OSError:
            pass
        return False

    def _save_pos(self):
        try:
            os.makedirs(os.path.dirname(POS_FILE), exist_ok=True)
            try:
                p = json.load(open(POS_FILE))
            except (OSError, ValueError):
                p = {}
            p.update(mon=self.mon_i, x=self.mx + self.wide_shift, y=self.my)
            json.dump(p, open(POS_FILE, "w"))
        except OSError:
            pass

    def _place(self, x, y, clamp=False, mon=None):
        if mon is not None and mon != self.mon_i:
            self.mon_i = mon
            GtkLayerShell.set_monitor(self.win, self._geom()[0])
        _, g = self._geom()
        if clamp:                                  # keep the whole surface on screen
            x = min(max(x, 0), max(0, g.width - self.cur_w))
            y = min(max(y, 0), max(0, g.height - H))
        nx, ny = int(round(x)), int(round(y))
        self.mx, self.my = nx, ny
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.LEFT, self.mx)
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.TOP, self.my)
        return False

    def keyboard(self, grab):
        """While his password card is up, take the keyboard so you can just type (layer-shell windows normally only get
        it when clicked)."""
        if not self.layer:
            self.win.present()
            return
        L = GtkLayerShell
        try:
            L.set_keyboard_mode(self.win, L.KeyboardMode.EXCLUSIVE if grab else L.KeyboardMode.ON_DEMAND)
        except AttributeError:
            L.set_keyboard_interactivity(self.win, bool(grab))
        if grab:
            self.view.grab_focus()

    def set_wide(self, on, kind="panel"):
        """Make room: panels (settings / history) widen him to the left; the code panel wants room on both sides.
        The page is told how far he moved inside the window so he stays exactly where he was on screen."""
        if kind == "panel":
            self.want_panel = on
        else:
            self.want_code = on
        L = max(WIDE - W if self.want_panel else 0, CODE_SIDE if self.want_code else 0)
        R = CODE_SIDE if self.want_code else 0
        if self.hover:
            L = R = 0
        base = self.mx + self.wide_shift                 # where his own 340px strip starts on the monitor
        if self.layer:
            gw = self._geom()[1].width
            L, R = min(L, max(0, base)), min(R, max(0, gw - base - W))
        else:
            L, R = 0, L + R                              # a normal window can only grow to the right
        if W + L + R == self.cur_w and L == self.wide_shift:
            return
        self.wide_shift, self.cur_w = L, W + L + R
        self.win.set_size_request(self.cur_w, H)
        self.win.resize(self.cur_w, H)
        if self.layer:
            self._place(base - L, self.my)
        elif self.hypr.ok() and self.hypr.addr:
            try:
                self.hypr.dispatch(f"resizewindowpixel exact {self.cur_w} {H},address:{self.hypr.addr}")
            except Exception:  # noqa: BLE001
                pass
        self._js(f"window.voidLayout&&voidLayout({self.wide_shift})")
        self.win.queue_draw()

    def _mon_at(self, gx, gy):
        for i, m in enumerate(self._monitors()):
            g = m.get_geometry()
            if g.x <= gx < g.x + g.width and g.y <= gy < g.y + g.height:
                return i, g
        return self.mon_i, self._geom()[1]

    def _tell_page(self):
        # X11 / XWayland: always-on-top window is the normal mode there, nothing to complain about. Only a plain Wayland
        # window (no layer-shell and no XWayland) can end up behind other apps — then say why on screen.
        why = "" if self.layer or self._x11() else ("; ".join(LAYER_WHY) or "layer-shell isn't available")
        self._js(f"window.voidMode&&voidMode({json.dumps({'layer': self.layer, 'cursor': self.hypr.ok() or self._x11(), 'why': why})})")
        return False

    def _hypr_setup(self):
        try:
            self.hypr.setup()
        except Exception as e:  # noqa: BLE001
            log(f"hyprland setup failed: {e}")
        return False

    # ---- window plumbing
    def _js(self, code):
        try:
            self.view.evaluate_javascript(code, -1, None, None, None, None, None)
        except (AttributeError, TypeError):
            self.view.run_javascript(code, None, None, None)

    def show(self):
        if self.hover:
            self.cursor_hidden(True)
        self.shown = True
        self.win.show_all()
        if self.hypr.ok() and not self.layer:
            GLib.timeout_add(250, self._hypr_setup)
        self.win.present()
        self.view.grab_focus()
        self._apply_region()
        self._js("window.voidShown&&voidShown()")

    def quit(self):
        if self.hover:
            self.cursor_hidden(False)
        Gtk.main_quit()

    def hide(self):
        if self.hover:
            self.cursor_hidden(False)
        self.shown = False
        self._js("window.voidHidden&&voidHidden()")
        self.win.hide()

    def toggle(self):
        if self.hover:                       # he's being the cursor: calling him brings him home instead of hiding him
            self._js("window.voidCmd&&voidCmd('home')")
            return
        if self.win.get_visible() and (self.layer or self.win.is_active() or self.hover):
            self.hide()
        else:
            self.show()

    def cmd(self, name):
        """Something for the page to do (listen, look…) — make sure he's visible first."""
        if not self.win.get_visible():
            self.show()
        self._js(f"window.voidCmd&&voidCmd({json.dumps(name)})")

    def drag(self):
        if self.hover:
            return
        if not self.layer:
            seat = Gdk.Display.get_default().get_default_seat()
            _, x, y = seat.get_pointer().get_position()
            self.win.begin_move_drag(1, x, y, Gtk.get_current_event_time())
            return
        if self.hypr.ok() and not self.dragging:       # layer surface: follow the real cursor until the button is let go
            self.dragging = True
            threading.Thread(target=self._drag_loop, daemon=True).start()

    def _drag_loop(self):
        try:
            sx, sy = self.hypr.cursor()
        except Exception as e:  # noqa: BLE001
            log(f"drag: can't read the cursor ({e})")
            self.dragging = False
            return
        ox, oy, t0 = self.mx, self.my, time.monotonic()
        while self.dragging and time.monotonic() - t0 < 30:
            time.sleep(0.008)
            try:
                cx, cy = self.hypr.cursor()
            except Exception:  # noqa: BLE001
                continue
            GLib.idle_add(self._place, ox + cx - sx, oy + cy - sy, True)
        self.dragging = False
        GLib.idle_add(self._save_pos)
        GLib.idle_add(self._rewide)

    def _rewide(self):
        """After a drag the room either side of him has changed: re-fit the widened window."""
        if self.want_panel or self.want_code:
            self.set_wide(self.want_panel)
        return False

    def drag_by(self, dx, dy):                      # no global cursor (e.g. sway): the page sends how far you dragged
        if self.layer and not self.hover:
            self._place(self.mx + dx, self.my + dy, True)

    def drag_end(self):
        if self.dragging:
            self.dragging = False
        elif self.layer:
            self._save_pos()
            self._rewide()

    # ---- click-through: only what the page says is solid takes the mouse
    def _apply_region(self):
        gw = self.win.get_window()
        if gw is None:
            return
        if self.rects is None:                       # click-through off: the whole window takes clicks again
            if self.region_on:
                self.region_on = False
                try:
                    self.win.input_shape_combine_region(None)
                except (TypeError, AttributeError):
                    pass
            return
        if cairo is None or not hasattr(cairo, "Region"):
            return
        self.region_on = True
        reg = cairo.Region()
        for x, y, w, h in self.rects:
            if w > 0 and h > 0:
                reg.union(cairo.RectangleInt(int(x), int(y), int(math.ceil(w)), int(math.ceil(h))))
        try:
            self.win.input_shape_combine_region(reg)
        except (TypeError, AttributeError):
            pass

    # ---- hover mode
    def set_hover(self, on):
        if on and not (self.hypr.ok() or (self._x11() and not self.layer)):
            log("hover mode: needs Hyprland or X11/XWayland — this Wayland desktop doesn't tell apps where the mouse is")
            self._js("window.voidHoverUnsupported&&voidHoverUnsupported()")
            return
        if self.hover == on:
            return
        if on and self.cur_w > W:
            self.want_code = False
            self.set_wide(False)
        log(f"hover mode {'on' if on else 'off'}")
        self.cursor_hidden(on)
        self.hover = on
        if on:
            self.hpos = None
        self.mons = None
        if on:
            self.home = (self.mon_i, self.mx, self.my)
            threading.Thread(target=self._follow, daemon=True).start()
        elif getattr(self, "home", None):                          # glide from the cursor back to where he was parked
            threading.Thread(target=self._glide_home, daemon=True).start()
        self._js(f"window.voidHover&&voidHover({'true' if on else 'false'})")

    def _glide_home(self):
        m, hx, hy = self.home
        if not self.layer:                                 # window mode: plain screen coordinates
            start, (sx, sy) = self.hpos, (self.hpos or (hx, hy))
            t0, dur = time.monotonic(), 0.65
            while start:
                k = min(1.0, (time.monotonic() - t0) / dur)
                e = 1 - (1 - k) ** 3
                GLib.idle_add(self.win.move, int(sx + (hx - sx) * e), int(sy + (hy - sy) * e + math.sin(math.pi * k) * -40))
                if k >= 1:
                    break
                time.sleep(0.008)
            GLib.idle_add(self.win.move, int(hx), int(hy))
            return
        _, g = self._geom(m)
        start = self.hpos                     # global position he was at as the cursor
        if not start:
            GLib.idle_add(self._place, hx, hy, True, m)
            return
        sx, sy = start[0] - g.x, start[1] - g.y      # in home-monitor coordinates
        t0, dur = time.monotonic(), 0.65
        while True:
            k = min(1.0, (time.monotonic() - t0) / dur)
            e = 1 - (1 - k) ** 3                          # ease out
            arc = math.sin(math.pi * k) * -40             # a little swoop up on the way
            GLib.idle_add(self._place, sx + (hx - sx) * e, sy + (hy - sy) * e + arc, False, m)
            if k >= 1:
                break
            time.sleep(0.008)
        GLib.idle_add(self._place, hx, hy, True, m)

    def cursor_hidden(self, hide):
        """In hover mode he *is* the mouse pointer, so hide the real one (Hyprland's cursor:invisible; elsewhere it stays)."""
        if not self.hypr.ok():
            return
        try:
            r = self.hypr.req(f"keyword cursor:invisible {'true' if hide else 'false'}").strip()
            if r != "ok":
                log(f"hyprland: couldn't {'hide' if hide else 'show'} the real cursor ({r!r}) — your Hyprland may be too old")
        except Exception as e:  # noqa: BLE001
            log(f"hyprland: cursor:invisible failed ({e})")

    @staticmethod
    def _x11():
        return Gdk.Display.get_default().__class__.__name__.startswith("X11")

    def _cursor(self):
        if self.hypr.ok():
            return self.hypr.cursor()
        seat = Gdk.Display.get_default().get_default_seat()
        _, x, y = seat.get_pointer().get_position()
        return x, y

    def _screen_of(self, cx, cy):
        if self.hypr.ok():
            try:
                if self.mons is None:
                    self.mons = self.hypr.monitors()
                for m in self.mons:
                    if m[0] <= cx < m[0] + m[2] and m[1] <= cy < m[1] + m[3]:
                        return m
            except Exception as e:  # noqa: BLE001
                log(f"hover: couldn't read monitors ({e})")
        scr = self.win.get_screen()
        return (0, 0, scr.get_width(), scr.get_height())

    def _follow(self):
        """Hover mode: he is the mouse pointer. ~120 times a second the window is moved so the centre of the ball sits
        right on the cursor; his eyes look the way you're moving."""
        fails = 0
        last = None
        vx = vy = 0.0
        while self.hover:
            time.sleep(0.008)
            if not self.shown:
                continue
            try:
                cx, cy = self._cursor()
            except Exception as e:  # noqa: BLE001
                fails += 1
                if fails in (1, 200):
                    log(f"hover: can't read the cursor position ({e})")
                continue
            now = time.monotonic()
            if last:
                dtt = max(1e-3, now - last[2])
                vx = vx * 0.8 + (cx - last[0]) / dtt * 0.2
                vy = vy * 0.8 + (cy - last[1]) / dtt * 0.2
            last = (cx, cy, now)
            x, y = cx - BALL[0], cy - BALL[1]
            if self.hpos is None:
                self.hpos = [x, y]
            self.hpos[0] += (x - self.hpos[0]) * 0.7          # tiny bit of lag so he feels alive, not glued
            self.hpos[1] += (y - self.hpos[1]) * 0.7
            x, y = self.hpos
            GLib.idle_add(self._js, f"window.voidLook&&voidLook({max(-1, min(1, vx / 900)):.3f},{max(-1, min(1, vy / 900)):.3f})")
            if self.last_sent and abs(self.last_sent[0] - x) < 0.5 and abs(self.last_sent[1] - y) < 0.5:
                continue
            self.last_sent = (x, y)
            try:
                if self.layer:
                    i, g = self._mon_at(cx, cy)
                    GLib.idle_add(self._place, x - g.x, y - g.y, False, i)
                elif self.hypr.ok():
                    self.hypr.move(x, y)
                else:
                    GLib.idle_add(self.win.move, int(x), int(y))
            except Exception as e:  # noqa: BLE001
                log(f"hover: moving the window failed ({e})")

    # ---- messages from the page
    def _on_msg(self, _ucm, res):
        try:
            msg = res.get_js_value().to_string()
        except AttributeError:
            msg = res.to_string()
        if msg.startswith("region:"):
            try:
                self.rects = json.loads(msg[7:])
            except ValueError:
                self.rects = None
            self._apply_region()
            return
        if msg.startswith("dragby:"):
            try:
                dx, dy = (float(v) for v in msg[7:].split(","))
                self.drag_by(dx, dy)
            except ValueError:
                pass
            return
        {"drag": self.drag, "dragend": self.drag_end, "hide": self.hide, "show": self.show,
         "hover:on": lambda: self.set_hover(True), "hover:off": lambda: self.set_hover(False),
         "wide:on": lambda: self.set_wide(True), "wide:off": lambda: self.set_wide(False),
         "kbd:grab": lambda: self.keyboard(True), "kbd:release": lambda: self.keyboard(False),
         "code:on": lambda: self.set_wide(True, "code"), "code:off": lambda: self.set_wide(False, "code"),
         "peek:hide": self.win.hide, "peek:show": self.show,
         }.get(msg, lambda: None)()

    def _serve(self):
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.listen(4)
        while True:
            c, _ = srv.accept()
            try:
                cmd = c.recv(64).decode().strip()
            finally:
                c.close()
            fn = {"toggle": self.toggle, "show": self.show, "hide": self.hide, "quit": self.quit,
                  "listen": lambda: self.cmd("listen"), "look": lambda: self.cmd("look"),
                  "hover": lambda: self.cmd("hover"), "hover-on": lambda: self.cmd("hover-on"),
                  "hover-off": lambda: self.cmd("hover-off")}.get(cmd)
            if fn:
                GLib.idle_add(fn)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":          # `void doctor`: can he be an overlay here?
        notes = "; ".join(LAYER_WHY)
        if GtkLayerShell is not None:
            print("overlay: yes — he floats on a layer above your apps" + (f"  ({notes})" if notes else ""))
        else:
            print("overlay: no — he'll be an always-on-top window instead, because: " + (notes or "unknown"))
        return
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    url, cmd = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "toggle")
    if send(cmd):
        return
    if cmd in ("hide", "quit"):
        return
    win = VoidWindow(url)
    if cmd in ("listen", "look", "hover", "hover-on"):
        GLib.timeout_add(1800, lambda: (win.cmd(cmd), False)[1])     # first launch: let the page load, then do it
    try:
        Gtk.main()
    finally:
        if win.hover:
            win.cursor_hidden(False)
        try:
            os.unlink(SOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
