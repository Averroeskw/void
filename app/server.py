#!/usr/bin/env python3
"""Void — a tiny voice-first shell for Claude Code.

Local server (127.0.0.1 only) that:
  * serves the Void UI
  * records the mic itself (parecord / pw-record / arecord — no browser mic permissions needed)
    with a little voice-activity detector, and transcribes it with faster-whisper
  * runs `claude -p --output-format stream-json` and streams events back
  * speaks replies with piper (or espeak-ng as a fallback)
"""
import argparse
import array
import io
import json
import math
import os
import re
import select
import shlex
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
import quick  # noqa: E402
STATIC = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "void"
CONF_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "void" / "config.json"
STATE_PATH = DATA_DIR / "state.json"
TOKEN = secrets.token_urlsafe(24)

VOICE_PROMPT = (
    "You are being driven from Void, a small voice-first desktop shell for Claude Code. "
    "The user is often speaking, so their message may contain speech-to-text mistakes; interpret them sensibly. "
    "Use your tools normally to get the work done, but keep your final reply short and conversational "
    "(1-4 sentences, no tables) because it is read aloud. If there is long output, put it in a file and say which one. "
    "Talk about files, folders and commands the way a person would (\"your Downloads folder\", \"the install script\", "
    "\"I'll update your packages\"), never by reading out full paths or raw commands. "
    "Whenever you run a Bash command, always fill in its description with a short plain-English summary of what it "
    "does, for someone who isn't technical (e.g. \"Restart Firefox\", \"Clear out old package downloads to free up "
    "space\"); Void reads it aloud when asking the user for permission. "
    "Void has an expressive face. In your final spoken reply only, start each sentence with one tag in square brackets "
    "saying how you mean it, so the face matches your words: [neutral] [happy] [proud] [excited] [laugh] [warm] "
    "[curious] [unsure] [thinking] [surprised] [sorry] [serious]. Example: \"[proud] Fixed it, all nine tests pass now. "
    "[curious] Want me to commit it?\" You can switch tags mid-sentence if your feeling changes. Never put these tags in "
    "files, code, commands or tool input — only in the words you say to the user."
)
# the face tags Claude puts in its replies (see VOICE_PROMPT) — never spoken, never shown, never saved
FACE_TAG_RE = re.compile(r"\[(?:neutral|happy|proud|excited|laugh|warm|curious|unsure|thinking|surprised|sorry|serious)\]\s*", re.I)


def untag(t):
    return FACE_TAG_RE.sub("", t or "")

DEFAULTS = {
    "port": 47777,
    "cwd": "~",
    "claude_bin": "claude",
    # how much he may do on his own:
    #   ask      – edits files himself, asks you (out loud + a card) before commands / web requests   (default)
    #   trust    – does anything without asking
    #   readonly – looks but doesn't touch
    "trust": "ask",
    # (old setting, still honoured if you set it by hand and "trust" is missing)
    "permission_mode": "acceptEdits",
    "model": "",
    "extra_args": [],
    "partial_messages": True,
    "voice_prompt": VOICE_PROMPT,
    # auto = small.en on an NVIDIA GPU (fast + accurate), base.en on CPU. Or pin one: tiny.en / base.en / small.en / medium.en
    "whisper_model": "auto",
    "whisper_beam": 5,
    # words he should expect (helps with names/jargon)
    "stt_hint": "Hey Void, Claude, Claude Code, Void, git, commit, README, Python, JavaScript, npm, pacman, sudo, Arch Linux, "
                "Hyprland, folder, directory, file, terminal, config, server, function, bug, refactor.",
    "whisper_device": "auto",
    "language": "en",
    "stt_prewarm": True,

    "tts": "auto",          # auto (Kokoro → piper → espeak) | kokoro | piper | espeak | off
    "piper_voice": "",
    "espeak_voice": "en-us",
    "speak_max_chars": 600,
    # voice preset: girl | cute | guy | deep | british | story | robot
    # (engine: Kokoro if installed, else piper — piper voices download on first use)
    "voice": "girl",
    # root mode: Claude may run anything, incl. sudo (you get a password box each time it needs root)
    "root_mode": False,
    # ultra root: on top of root mode, Void types your sudo password for him — you give it once per session (kept in
    # memory only, never written to disk; forgotten when Void restarts or you turn this off)
    "ultra_root": False,
    "cpu_split": True,       # face (window) and voice/brain (server) on separate CPU cores (4+ cores only)
    # pupil colour: "#rrggbb", or "random" for a new one every time he pops up
    "eye_color": "#3d9b74",
    # eye-white colour: "#rrggbb" or "random" (a soft pastel each time)
    "eye_white": "#f3dccf",
    # resting pupil shape: slit | circle | dot  (they still change shape with his mood)
    "pupil_style": "slit",
    # "hey Void" wake word (keeps the mic open and listens for his name; nothing leaves your machine)
    "wake_word": True,
    # start Void when you log in (Hyprland exec-once + XDG autostart)
    "autostart": True,
    # start listening the moment he pops up (`void`)
    "listen_on_show": False,
    # say "stop" / "hey Void" while he's talking to cut him off
    "barge_in": True,
    # for this many seconds after he stops talking, just talk — no "hey Void" needed (0 = off)
    "follow_up": 4,
    # live view ("watch my screen"): turns itself off after this many minutes without you saying anything
    "live_minutes": 10,
    # little sound effects
    "sfx": True,
    # how he looks: body + each eye + each eye white, a skin (accessory), and whether to roll random colours each time
    "look": {},
    # projects you can switch to by voice: {"delivery app": "/home/you/code/diq"}
    "projects": {},
    # when a long task finishes while he's hidden: desktop notification / pop back up
    "notify_done": True,
    "popup_done": False,
    "code_panel": True,      # the floating code / terminal panel while he works
    "code_face": "him",      # which way the terminal faces: "him" (you see the back, mirrored) or "you" (readable)
    # hover mode: he floats next to your mouse cursor
    "hover": False,
    # clicks on the see-through space around him go to the window behind (can flicker with some GPU drivers)
    "click_through": False,
    # one-tap prompts on the right-click menu
    "quick_actions": [
        {"label": "Explain my clipboard", "prompt": "Explain what's on my clipboard in plain words. If it's an error, tell me how to fix it.", "attach": "clipboard"},
        {"label": "What's on my screen?", "prompt": "Look at my screen and tell me briefly what's going on. Point out anything that looks wrong.", "attach": "screen"},
        {"label": "Commit my changes", "prompt": "Look at the git changes in this folder and commit them with a clear message. Tell me what you committed."},
        {"label": "Run the tests", "prompt": "Run this project's tests. Tell me what passed and what failed, and why."},
        {"label": "What's this folder?", "prompt": "Give me a short tour of this folder: what the project is and the important files."},
    ],
    # mic: leave empty for the system default input. `void doctor` lists inputs.
    "mic_device": "",
    # optional custom recorder; must write raw s16le, 16 kHz, mono to stdout
    "mic_cmd": "",
}


def load_config():
    cfg = dict(DEFAULTS)
    if CONF_PATH.exists():
        try:
            user = json.loads(CONF_PATH.read_text())
            if user.get("whisper_model") == "base.en":     # the old default → let it pick (GPU gets a better model)
                user["whisper_model"] = "auto"
            cfg.update(user)
            if "trust" not in user and user.get("permission_mode"):
                cfg["trust"] = {"bypassPermissions": "trust", "plan": "readonly"}.get(user["permission_mode"], "ask")
        except Exception as e:  # noqa: BLE001
            print(f"[void] could not parse {CONF_PATH}: {e}", file=sys.stderr)
    else:
        CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONF_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    return cfg


CFG = load_config()

SKINS = ["none", "cat", "bunny", "bear", "antenna", "devil", "halo", "crown", "sprout", "party", "headphones", "bow"]
HEX = re.compile(r"#[0-9a-f]{6}")


def look():
    lk = dict(CFG.get("look") or {})
    ec, ew = (CFG.get("eye_color") or "#3d9b74").lower(), (CFG.get("eye_white") or "#f3dccf").lower()
    pup = ec if HEX.fullmatch(ec) else "#3d9b74"
    wht = ew if HEX.fullmatch(ew) else "#f3dccf"
    out = {"body": "#0b0b0b", "pupL": pup, "pupR": pup, "whiteL": wht, "whiteR": wht, "skin": "none",
           "skinColor": "", "link": True, "random": ec == "random" or ew == "random"}
    for k, v in lk.items():
        if k in ("body", "pupL", "pupR", "whiteL", "whiteR", "skinColor") and isinstance(v, str) and (HEX.fullmatch(v.lower()) or (k == "skinColor" and v == "")):
            out[k] = v.lower()
        elif k == "skin" and v in SKINS:
            out[k] = v
        elif k in ("link", "random"):
            out[k] = bool(v)
    return out


# ---------------------------------------------------------------- state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.stopped = False
        s = {}
        try:
            s = json.loads(STATE_PATH.read_text())
        except Exception:  # noqa: BLE001
            pass
        self.cwd = s.get("cwd") or os.path.expanduser(CFG["cwd"])
        if not os.path.isdir(self.cwd):
            self.cwd = str(Path.home())
        self.session = s.get("session")
        self.auto_pick = ""

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"cwd": self.cwd, "session": self.session}))


ST = State()


def pretty_path(p):
    home = str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


# ---------------------------------------------------------------- memory + history
MEM_PATH = DATA_DIR / "memory.json"
HIST_PATH = DATA_DIR / "history.jsonl"
_mem_lock = threading.Lock()


def mem_load():
    try:
        return json.loads(MEM_PATH.read_text())
    except Exception:  # noqa: BLE001
        return []


def mem_save(items):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEM_PATH.write_text(json.dumps(items, indent=1))


def mem_add(text):
    text = " ".join(str(text).split())[:400]
    if not text:
        return None
    with _mem_lock:
        items = mem_load()
        if any(i["text"].lower() == text.lower() for i in items):
            return None
        it = {"id": secrets.token_hex(4), "text": text, "ts": int(time.time())}
        items.append(it)
        mem_save(items[-200:])
        return it


def mem_del(ident=None, match=None):
    """Remove by id, or every note containing `match` (case-insensitive). Returns how many went."""
    with _mem_lock:
        items = mem_load()
        keep = [i for i in items if not (i["id"] == ident or
                                         (match and match.lower().strip(" .") in i["text"].lower()))]
        mem_save(keep)
        return len(items) - len(keep)


def hist_add(rec):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HIST_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


def hist_load(limit=60):
    try:
        lines = HIST_PATH.read_text().splitlines()[-2000:]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def memory_prompt(fresh):
    parts = []
    notes = mem_load()
    if notes:
        parts.append("Things the user asked you to remember (keep using them):\n" +
                     "\n".join(f"- {n['text']}" for n in notes[-60:]))
    if fresh:                                   # a new conversation: give him a sense of what happened lately
        recent = [h for h in hist_load(8) if h.get("you")][:6]
        if recent:
            parts.append("Recent conversations with the user (background only — don't bring these up unless the user "
                     "refers to them):\n" + "\n".join(
                f"- [{time.strftime('%b %d %H:%M', time.localtime(h['ts']))}, in {h.get('cwd_display', '?')}] "
                f"they said: {h['you'][:160]!r} — you answered: {(h.get('him') or '')[:200]!r}" for h in recent))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- start with the computer
AUTOSTART_DESKTOP = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "void.desktop"
HYPR_CONF = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hypr" / "hyprland.conf"
HYPR_MARK = "# added by Void (Settings → More → Start when I log in)"


def sync_autostart():
    """Add/remove Void from login startup to match the `autostart` setting."""
    on = bool(CFG.get("autostart"))
    launcher = Path.home() / ".local/bin/void"
    try:
        if on:
            AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_DESKTOP.write_text(
                "[Desktop Entry]\nType=Application\nName=Void\nComment=Voice shell for Claude Code\n"
                f"Exec={launcher} show\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
        else:
            AUTOSTART_DESKTOP.unlink(missing_ok=True)
    except OSError as e:
        print(f"[void] autostart file: {e}", file=sys.stderr)
    if HYPR_CONF.exists():                    # Hyprland doesn't read XDG autostart → exec-once line, clearly marked
        try:
            lines = HYPR_CONF.read_text().splitlines()
            keep = [ln for ln in lines if HYPR_MARK not in ln]
            if on:
                keep.append(f"exec-once = {launcher} show   {HYPR_MARK}")
            if keep != lines:
                HYPR_CONF.write_text("\n".join(keep) + "\n")
        except OSError as e:
            print(f"[void] hyprland.conf: {e}", file=sys.stderr)


# ---------------------------------------------------------------- event bus (server → page, one long stream)
class Bus:
    def __init__(self):
        self.subs = []
        self.lock = threading.Lock()

    def sub(self):
        import queue
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsub(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def pub(self, **ev):
        with self.lock:
            for q in self.subs:
                q.put(ev)


BUS = Bus()


# ---------------------------------------------------------------- "ask me first" approvals
def summarize_command(cmd):
    """No description from Claude: ask the fast model what the command does, in a few plain words (None if it can't)."""
    binary = claude_bin()
    if not binary or not cmd.strip():
        return None
    ask = ("In 3 to 10 plain words, for someone who isn't technical, say what this shell command does. Start with a "
           "verb, e.g. \"restart Firefox\" or \"delete the old download in your Downloads folder\". Only say what it "
           "does, never guess why. Reply with only that phrase.\n\nCommand:\n" + cmd[:2000])
    try:
        out = subprocess.run([binary, "-p", "--model", "haiku", "--tools", "", "--no-session-persistence"],
                             input=ask, capture_output=True, text=True, timeout=15, cwd=str(DATA_DIR)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.strip().splitlines() or [""])[0].strip(" \"'.`*")
    return line if 0 < len(line) < 120 else None


def approval_say(tool, inp):
    """What he asks out loud: what the thing does, not the raw command (the card still shows the real thing)."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        # Claude's own one-line summary of the command (it's told to always write one), else a quick summary from the
        # fast model — never the raw command (the card under him still shows that)
        desc = " ".join(str(inp.get("description") or "").split()) or summarize_command(str(inp.get("command") or ""))
        desc = speakable(desc or "").rstrip(".")
        if not desc:
            return "Can I run this command?"
        return "Can I run a command to " + desc[0].lower() + desc[1:] + "?"
    if tool == "WebFetch":
        host = re.sub(r"^https?://(www\.)?", "", str(inp.get("url") or "")).split("/")[0]
        return f"Can I open a page on {host}?" if host else "Can I open this page?"
    if tool == "WebSearch":
        return "Can I search the web?"
    return f"Can I use {tool}?"


class Approvals:
    def __init__(self):
        self.pending = {}
        self.always = set()                    # tools approved "for this chat"
        self.lock = threading.Lock()

    def ask(self, tool, inp, timeout=600):
        key = tool if tool != "Bash" else "Bash:" + (summarize_input(inp).split() or [""])[0]
        if key in self.always:
            return {"ok": True, "why": "the user said yes to this for the whole chat"}
        ev = threading.Event()
        aid = secrets.token_hex(4)
        item = {"id": aid, "tool": tool, "key": key, "detail": approval_text(tool, inp), "ev": ev, "ans": None}
        with self.lock:
            self.pending[aid] = item
        BUS.pub(t="approve", id=aid, tool=tool, detail=item["detail"], key=key, say=approval_say(tool, inp))
        ev.wait(timeout)
        with self.lock:
            self.pending.pop(aid, None)
        ans = item["ans"] or "no"
        BUS.pub(t="approve_done", id=aid, ans=ans)
        if ans == "always":
            self.always.add(key)
        if ans in ("yes", "always"):
            return {"ok": True}
        if item["ans"] is None:
            return {"ok": False, "why": "the user didn't answer in time, so it wasn't run"}
        return {"ok": False}

    def answer(self, aid, ans):
        with self.lock:
            it = self.pending.get(aid)
        if it:
            it["ans"] = ans
            it["ev"].set()
        return bool(it)

    def cancel_all(self):
        with self.lock:
            items = list(self.pending.values())
        for it in items:
            it["ans"] = "no"
            it["ev"].set()


APPROVALS = Approvals()


class AskPass:
    """sudo's password, asked for in a small card right next to Void (askpass.py → here → the page). In Ultra root mode
    the password you type is remembered in memory for this session and handed to sudo automatically after that."""
    lock = threading.Lock()
    pending = {}
    remembered = None
    last_given = 0.0

    @classmethod
    def ask(cls, prompt):
        now = time.monotonic()
        with cls.lock:
            if CFG.get("ultra_root") and cls.remembered is not None:
                if now - cls.last_given < 4:          # sudo asked again straight away: that password didn't work
                    cls.remembered = None
                else:
                    cls.last_given = now
                    return {"pw": cls.remembered, "auto": True}
            aid = secrets.token_hex(4)
            ev = threading.Event()
            cls.pending[aid] = {"ev": ev, "pw": None}
        BUS.pub(t="askpass", id=aid, prompt=prompt[:120], ultra=bool(CFG.get("ultra_root")),
                retry=now - cls.last_given < 4)
        ev.wait(300)
        with cls.lock:
            it = cls.pending.pop(aid, {})
        BUS.pub(t="askpass_done", id=aid)
        return {"pw": it.get("pw")}

    @classmethod
    def answer(cls, aid, pw):
        with cls.lock:
            it = cls.pending.get(aid)
            if not it:
                return False
            it["pw"] = pw
            if pw is not None:
                cls.last_given = time.monotonic()
                if CFG.get("ultra_root"):
                    cls.remembered = pw
            it["ev"].set()
            return True

    @classmethod
    def forget(cls):
        with cls.lock:
            cls.remembered = None
            for it in cls.pending.values():
                it["ev"].set()


def approval_text(tool, inp):
    if not isinstance(inp, dict):
        return tool
    if tool == "Bash":
        return inp.get("command") or "a command"
    if tool in ("WebFetch",):
        return inp.get("url") or "a web page"
    if tool == "WebSearch":
        return "search: " + (inp.get("query") or "")
    return summarize_input(inp) or tool


# ---------------------------------------------------------------- screenshots, clipboard, notifications
SHOTS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "void" / "shots"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, timeout=kw.pop("timeout", 30), **kw)


def _active_window():
    """Where the window you're using is, as (x, y, w, h) on the whole desktop — or None if we can't tell (or it's Void)."""
    try:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            w = json.loads(_run(["hyprctl", "activewindow", "-j"], timeout=3).stdout or b"{}")
            if w.get("size") and w.get("class") != "Void":
                return (*w["at"], *w["size"])
        elif os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            def find(n):
                if n.get("focused") and n.get("type") in ("con", "floating_con"):
                    return n
                for c in n.get("nodes", []) + n.get("floating_nodes", []):
                    f = find(c)
                    if f:
                        return f
            n = find(json.loads(_run(["swaymsg", "-t", "get_tree"], timeout=3).stdout or b"{}"))
            if n and n.get("app_id") != "Void":
                r = n["rect"]
                return r["x"], r["y"], r["width"], r["height"]
        elif not os.environ.get("WAYLAND_DISPLAY") and shutil.which("xdotool"):
            if _run(["xdotool", "getactivewindow", "getwindowclassname"], timeout=3).stdout.strip() != b"Void":
                g = dict(ln.split("=", 1) for ln in _run(["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                                                          timeout=3).stdout.decode().split() if "=" in ln)
                return int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except Exception as e:  # noqa: BLE001
        print(f"[void] couldn't find the active window: {e}", file=sys.stderr)
    return None


def _shrink(path):
    """Make a picture cheap for Claude: longest side ≤ 1280 px, JPEG. (Image tokens ≈ width × height / 750.)"""
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((1280, 1280))
        small = path.with_suffix(".jpg")
        im.convert("RGB").save(small, quality=82)
        if small != path:
            path.unlink(missing_ok=True)
        return small
    except Exception as e:  # noqa: BLE001  (no Pillow / odd file: send it as it is)
        print(f"[void] couldn't shrink the picture: {e}", file=sys.stderr)
        return path


def capture(kind):
    """kind: window | screen | region | clipboard → dict describing the attachment."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    for old in sorted(SHOTS.glob("void-*.*"))[:-30]:        # keep the folder small
        old.unlink(missing_ok=True)
    out = SHOTS / f"void-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.png"
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    geo = _active_window() if kind == "window" else None
    if kind == "window" and not geo and not (wayland and (shutil.which("spectacle") or shutil.which("gnome-screenshot"))):
        kind = "screen"                                       # can't tell which window: the whole screen it is
    if kind in ("window", "screen", "region"):
        area, win = kind == "region", kind == "window"
        gs = f"{geo[0]},{geo[1]} {geo[2]}x{geo[3]}" if geo else None
        # every desktop has its own screenshot tool: try the ones that are installed until one works
        tries = []
        if wayland:
            if shutil.which("grim") and (not area or shutil.which("slurp")):      # wlroots: Hyprland, sway, river, niri…
                tries.append("grim")
            tries += [t for t in ("spectacle", "gnome-screenshot") if shutil.which(t)]   # KDE · GNOME
        else:
            tries += [t for t in ("maim", "scrot", "import") if shutil.which(t)]
        if not tries:
            raise RuntimeError("no screenshot tool — install grim + slurp (Wayland), spectacle (KDE), "
                               "gnome-screenshot (GNOME) or maim (X11)")
        r = None
        for tool in tries:
            out.unlink(missing_ok=True)
            if tool == "grim":
                cmd = ["grim"]
                if area:
                    g = _run(["slurp"], timeout=120)
                    if g.returncode != 0:
                        raise RuntimeError("cancelled")
                    cmd += ["-g", g.stdout.decode().strip()]
                elif win and gs:
                    cmd += ["-g", gs]
                r = _run(cmd + [str(out)])
            elif tool == "spectacle":
                r = _run(["spectacle", "-b", "-n", "-r" if area else "-a" if win else "-f", "-o", str(out)], timeout=120)
            elif tool == "gnome-screenshot":
                r = _run(["gnome-screenshot"] + (["-a"] if area else ["-w"] if win else []) + ["-f", str(out)], timeout=120)
            elif tool == "maim":
                g = ["-g", f"{geo[2]}x{geo[3]}+{geo[0]}+{geo[1]}"] if win and geo else []
                r = _run(["maim"] + (["-s"] if area else g) + [str(out)], timeout=120)
            elif tool == "scrot":
                g = ["-a", f"{geo[0]},{geo[1]},{geo[2]},{geo[3]}"] if win and geo else []
                r = _run(["scrot"] + (["-s"] if area else g) + ["-o", str(out)], timeout=120)
            else:
                crop = ["-crop", f"{geo[2]}x{geo[3]}+{geo[0]}+{geo[1]}"] if win and geo else []
                r = _run(["import"] + ([] if area else ["-window", "root"] + crop) + [str(out)], timeout=120)
            if r.returncode == 0 and out.exists():
                break
        if r is None or r.returncode != 0 or not out.exists():
            raise RuntimeError("screenshot failed: " + (r.stderr or b"").decode(errors="replace")[-200:])
        return {"kind": kind, "path": str(_shrink(out)),
                "label": {"screen": "screenshot", "window": "your window", "region": "screen area"}[kind]}
    if kind == "clipboard":
        if wayland and shutil.which("wl-paste"):
            types = _run(["wl-paste", "--list-types"]).stdout.decode(errors="replace").split()
            img = next((t for t in types if t.startswith("image/")), None)
            if img:
                out.write_bytes(_run(["wl-paste", "--type", img]).stdout)
                return {"kind": "clipimg", "path": str(_shrink(out)), "label": "copied image"}
            r = _run(["wl-paste", "--no-newline"])
        elif shutil.which("xclip"):
            tg = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]).stdout.decode(errors="replace")
            if "image/png" in tg:
                out.write_bytes(_run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]).stdout)
                return {"kind": "clipimg", "path": str(_shrink(out)), "label": "copied image"}
            r = _run(["xclip", "-selection", "clipboard", "-o"])
        elif shutil.which("xsel"):
            r = _run(["xsel", "-b", "-o"])
        else:
            raise RuntimeError("no clipboard tool — install wl-clipboard (Wayland) or xclip (X11)")
        text = r.stdout.decode(errors="replace").strip()
        if not text:
            raise RuntimeError("the clipboard is empty")
        return {"kind": "clip", "text": text[:12000], "label": f"clipboard ({len(text)} chars)"}
    raise RuntimeError("unknown capture")


def attach_prompt(atts):
    parts = []
    for a in atts or []:
        k = a.get("kind")
        if k in ("window", "screen", "region", "clipimg") and a.get("path") and Path(a["path"]).is_file():
            what = {"window": "a screenshot of the window they're using", "screen": "a screenshot of their whole screen",
                    "region": "a screenshot of part of their screen", "clipimg": "an image they copied"}[k]
            if a.get("live"):          # live view: a fresh picture comes with everything they say
                parts.append(f"[Live view is on — this is what the user is looking at right now: {a['path']}. "
                             "Open it with the Read tool only if their message is about something on screen.]")
            else:
                parts.append(f"[The user attached {what}: {a['path']} — open it with the Read tool to see it.]")
        elif k == "clip" and a.get("text"):
            parts.append("[What's on the user's clipboard:]\n```\n" + a["text"][:12000] + "\n```")
    return "\n".join(parts)


def notify(title, body):
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Void", "-i", str(STATIC / "icon.png"), title, body[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wire_quick():
    quick.notify, quick.publish, quick.cfg, quick.save_config = notify, BUS.pub, CFG, save_config


# ---------------------------------------------------------------- speech to text
_whisper = None
_whisper_err = None
_whisper_lock = threading.Lock()


def _preload_cuda_libs():
    """faster-whisper needs cuBLAS + cuDNN for the GPU. The installer puts them in the venv (pip nvidia-* wheels);
    load them by full path so ctranslate2 finds them without LD_LIBRARY_PATH."""
    import ctypes
    import glob
    import site
    roots = site.getsitepackages() + [site.getusersitepackages()]
    loaded = 0
    for pat in ("nvidia/cublas/lib/libcublasLt.so.*", "nvidia/cublas/lib/libcublas.so.*",
                "nvidia/cuda_runtime/lib/libcudart.so.*", "nvidia/cudnn/lib/libcudnn*.so.*"):
        for r in roots:
            for f in sorted(glob.glob(os.path.join(r, pat))):
                try:
                    ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
                    loaded += 1
                except OSError:
                    pass
    return loaded


def _gpu_ok():
    if CFG["whisper_device"] == "cpu" or not os.path.exists("/proc/driver/nvidia/version"):
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _load(device):
    from faster_whisper import WhisperModel
    name = CFG["whisper_model"]
    if name in ("", "auto"):
        name = "small.en" if device == "cuda" else "base.en"
    m = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
    m._void_name, m._void_dev = name, device
    print(f"[void] speech model {name} on {device}", file=sys.stderr)
    return m


def whisper():
    global _whisper, _whisper_err
    with _whisper_lock:
        if _whisper is None and _whisper_err is None:
            try:
                import faster_whisper  # noqa: F401
            except Exception as e:  # noqa: BLE001
                _whisper_err = f"faster-whisper not installed ({e})"
            else:
                dev = "cpu"
                if _gpu_ok():
                    _preload_cuda_libs()
                    dev = "cuda"
                try:
                    _whisper = _load(dev)
                except Exception as e:  # noqa: BLE001
                    print(f"[void] whisper on {dev} failed ({e}); using CPU", file=sys.stderr)
                    try:
                        _whisper = _load("cpu")
                    except Exception as e2:  # noqa: BLE001
                        _whisper_err = str(e2)
        if _whisper is None:
            raise RuntimeError(_whisper_err or "speech model unavailable")
        return _whisper


def run_whisper(audio, beam=None, hint=None) -> str:
    """Transcribe; if the GPU path blows up at run time (missing CUDA libs etc.), drop to CPU for good."""
    global _whisper

    def go():
        segs, _ = whisper().transcribe(
            audio, language=CFG["language"] or None, beam_size=int(beam or CFG.get("whisper_beam") or 5),
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 600, "speech_pad_ms": 300},
            condition_on_previous_text=False, initial_prompt=(hint if hint is not None else CFG.get("stt_hint")) or None,
            no_speech_threshold=0.5)
        out = " ".join(s.text.strip() for s in segs).strip()      # segments are lazy: errors surface here
        # whisper sometimes "hears" the hint or filler on near-silence
        if out.strip(" .").lower() in ("", "you", "thank you", "thanks for watching", "bye"):
            return ""
        return out
    try:
        return go()
    except Exception as e:  # noqa: BLE001
        if not re.search(r"cuda|cublas|cudnn|libcu|gpu", str(e), re.I):
            raise
        print(f"[void] GPU speech-to-text failed ({e}); switching to CPU", file=sys.stderr)
        with _whisper_lock:
            _whisper = _load("cpu")
        return go()


def transcribe_pcm(pcm: bytes, **kw) -> str:
    """Raw s16le 16 kHz mono → text."""
    import numpy as np
    if len(pcm) < 16000 * 2 * 0.3:          # < 0.3 s: nothing worth sending
        return ""
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    audio -= float(audio.mean())                          # DC offset some cheap mics have
    pk = float(np.abs(audio).max() or 1.0)
    if pk < 0.4:                                           # quiet mic → bring it up (but don't blow up hiss)
        audio *= min(0.7 / pk, 12.0)
    return run_whisper(audio, **kw)


# ---------------------------------------------------------------- microphone
RATE = 16000
FRAME = RATE * 2 * 40 // 1000               # 40 ms of s16le mono = 1280 bytes


def mic_cmds():
    dev = (CFG.get("mic_device") or "").strip()
    cmds = []
    if CFG.get("mic_cmd"):
        cmds.append(shlex.split(CFG["mic_cmd"]))
    if shutil.which("parecord"):            # PulseAudio or PipeWire (pipewire-pulse)
        cmds.append(["parecord", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=30"]
                    + ([f"--device={dev}"] if dev else []))
    if shutil.which("pw-record"):           # native PipeWire
        cmds.append(["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16"]
                    + (["--target", dev] if dev else []) + ["-"])
    if shutil.which("arecord"):             # plain ALSA
        cmds.append(["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"]
                    + (["-D", dev] if dev else []))
    return cmds


def open_mic():
    """Start the first recorder that actually produces audio. Returns (proc, name, first_bytes)."""
    errs = []
    for c in mic_cmds():
        try:
            p = subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            errs.append(f"{c[0]}: {e}")
            continue
        r, _, _ = select.select([p.stdout], [], [], 2.5)
        first = os.read(p.stdout.fileno(), 65536) if r else b""
        if first:
            return p, c[0], first
        kill(p)
        err = (p.stderr.read() or b"").decode(errors="replace").strip().splitlines()
        errs.append(f"{c[0]}: {err[-1] if err else 'no audio came out'}")
    if not errs:
        raise RuntimeError("no recorder found — install libpulse (parecord), pipewire or alsa-utils")
    raise RuntimeError("couldn't open the mic — " + "; ".join(errs))


def kill(p):
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)


class Listen:
    """One mic session. mode 'auto' stops by itself after you stop talking; 'ptt' waits for stop()."""
    current = None
    lock = threading.Lock()

    def __init__(self, mode):
        self.mode = mode
        self.stop_ev = threading.Event()
        self.cancel = False

    def stop(self, cancel=False):
        self.cancel = self.cancel or cancel
        self.stop_ev.set()

    def run(self, emit):
        proc, name, buf = open_mic()
        emit(t="open", rec=name)
        pcm = bytearray()
        t0 = time.monotonic()
        floor, spoke, voiced, last_voice, peak, clipped = None, False, 0, t0, 0.0, 0
        reason = "stop"
        fd = proc.stdout.fileno()
        try:
            while True:
                while len(buf) >= FRAME:
                    fr, buf = buf[:FRAME], buf[FRAME:]
                    pcm += fr
                    n = len(fr) // 2
                    s = memoryview(fr).cast("h")
                    rms = (sum(x * x for x in s) / n) ** 0.5 / 32768.0
                    peak = max(peak, rms)
                    clipped += sum(1 for x in s if x > 32500 or x < -32500)
                    now = time.monotonic()
                    # noise floor: settles over the first ~quarter second, then drifts slowly toward quiet
                    if floor is None:
                        floor = rms
                    elif now - t0 < 0.3 or rms < floor:
                        floor = floor * 0.8 + rms * 0.2
                    else:
                        floor = floor * 0.998 + rms * 0.002
                    thr = max(0.012, floor * 3.0)
                    voiced = min(4, voiced + 1) if rms > thr else max(0, voiced - 1)
                    if voiced >= 3:
                        if not spoke:
                            emit(t="speech")
                        spoke, last_voice = True, now
                    elif spoke and rms > thr * 0.6:
                        last_voice = now
                    emit(t="lvl", v=round(min(1.0, max(0.0, (rms - floor) * 12)), 3))
                    if self.mode == "auto":
                        if spoke and now - last_voice > 1.2:
                            reason = "done"
                        elif not spoke and now - t0 > 8:
                            reason, self.cancel = "nospeech", True
                    if now - t0 > 1.5 and peak < 0.0005:
                        reason = "silent"          # dead input: say so right away instead of waiting
                    if now - t0 > 120:
                        reason = "toolong"
                    if reason != "stop":
                        self.stop_ev.set()
                if self.stop_ev.is_set():
                    break
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    chunk = os.read(fd, 8192)
                    if not chunk:
                        if len(pcm) < FRAME * 10:
                            err = (proc.stderr.read() or b"").decode(errors="replace").strip()
                            raise RuntimeError(f"{name} stopped unexpectedly {err[-200:]}".strip())
                        break
                    buf += chunk
        finally:
            kill(proc)
        if peak < 0.0005:
            emit(t="error", text="the mic is completely silent — is the right input selected/unmuted? "
                                 "(`void doctor` lists inputs; set mic_device in the config)")
            return
        if self.cancel:
            emit(t="cancel", why=reason)
            return
        if clipped > RATE * 0.05:
            emit(t="note", text="your mic is clipping (too loud) — turn the input down to ~65% for better recognition")
        emit(t="transcribing")
        emit(t="text", text=transcribe_pcm(bytes(pcm)))


def reset_whisper():
    global _whisper, _whisper_err
    with _whisper_lock:
        _whisper, _whisper_err = None, None
    threading.Thread(target=lambda: _try(whisper), daemon=True).start()


def list_mics():
    if not shutil.which("pactl"):
        return []
    out = subprocess.run(["pactl", "-f", "json", "list", "sources"], capture_output=True, text=True).stdout
    try:
        return [{"id": s["name"], "label": s.get("description") or s["name"]} for s in json.loads(out)
                if ".monitor" not in s.get("name", "")]
    except ValueError:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
        return [{"id": ln.split("\t")[1], "label": ln.split("\t")[1]} for ln in out.splitlines()
                if "\t" in ln and ".monitor" not in ln]


STOP_RE = re.compile(r"(?:ok(?:ay)?\W+|void\W+|hey\W+void\W+)?(?:stop|wait|shush|shh+|quiet|be quiet|shut up|hold on|hang on|"
                     r"enough|stop talking|that's enough|thanks?(?: you)?|got it)(?:\W+(?:void|please|now))?")
# sent ahead of a follow-up (said without "hey Void") so Claude can tell if it was even meant for him
FOLLOW_NOTE = ("(Voice note from Void: the mic picked this up a few seconds after your last reply, without \"hey Void\". "
               "If it is clearly not meant for you, e.g. the user thinking out loud, talking to themselves or to someone "
               "else, reply with exactly [ignore] and nothing else, and use no tools. Otherwise just answer it normally.)\n\n")
IGNORE = "[ignore]"

# what the speech model "hears" in background noise — never treat these as a follow-up request
NOISE_WORDS = {"you", "thank you", "thanks", "bye", "thanks for watching", "thank you for watching", "uh", "um", "hmm"}
WAKE_RE = re.compile(r"^\W*(?:(?:hey|hi|hay|okay|ok|yo|oi)\W+)?(void|voyd|boyd|voi|vod)\b\W*(.*)$", re.I | re.S)


class Wake:
    """Optional "hey Void": keeps a recorder open, cuts out short bursts of speech, and checks them for his name.
    Steps aside whenever the normal mic session runs (or while he's talking, so he can't wake himself)."""
    thread = None
    speaking_until = 0.0
    follow_from = follow_until = 0.0         # the "just talk, no 'hey Void'" window right after he stops talking
    follow_on = False                        # what the page was last told (it shows him listening while it's open)
    cut = None                               # you clicked him ("finish") or pressed Space/Esc ("cancel") while he heard you

    @classmethod
    def wanted(cls):
        return bool(CFG.get("wake_word") or CFG.get("barge_in") or CFG.get("follow_up"))

    @classmethod
    def speaking(cls):
        return time.monotonic() < cls.speaking_until

    @classmethod
    def talking_until(cls, end):
        """He's talking until `end`; the follow-up window opens then."""
        cls.speaking_until = cls.follow_from = end
        cls.follow_until = end + float(CFG.get("follow_up") or 0)

    @classmethod
    def end_follow(cls, how):
        """Close the follow-up window. "finish": take what you've said so far · "cancel": throw it away."""
        cls.cut = how
        if how != "finish":
            cls.follow_until = 0.0

    @classmethod
    def hush(cls):
        """He was cut off: no follow-up window."""
        cls.speaking_until = cls.follow_until = 0.0

    @classmethod
    def ensure(cls):
        if cls.wanted() and not (cls.thread and cls.thread.is_alive()):
            cls.thread = threading.Thread(target=cls.loop, daemon=True)
            cls.thread.start()

    @classmethod
    def should_run(cls):
        if Listen.lock.locked():
            return False
        if cls.speaking():                       # while he talks: only to catch "stop" / "hey Void" (barge-in)
            return bool(CFG.get("barge_in"))
        return bool(CFG.get("wake_word")) or time.monotonic() < cls.follow_until

    @classmethod
    def loop(cls):
        while cls.wanted():
            if not cls.should_run():
                time.sleep(0.2)
                continue
            try:
                cls.session()
            except Exception as e:  # noqa: BLE001
                print(f"[void] wake word: {e}", file=sys.stderr)
                time.sleep(5)

    @classmethod
    def session(cls):
        proc, _name, buf = open_mic()
        fd = proc.stdout.fileno()
        floor, voiced, seg, in_speech, last_voice, started = None, 0, bytearray(), False, 0.0, 0.0
        pre, n = bytearray(), 0
        cls.cut = None
        try:
            while cls.should_run():
                r, _, _ = select.select([fd], [], [], 0.2)
                if r:
                    chunk = os.read(fd, 8192)
                    if not chunk:
                        return
                    buf += chunk
                while len(buf) >= FRAME:
                    fr, buf = buf[:FRAME], buf[FRAME:]
                    s = memoryview(fr).cast("h")
                    rms = (sum(x * x for x in s) / (len(fr) // 2)) ** 0.5 / 32768.0
                    floor = rms if floor is None else (floor * 0.9 + rms * 0.1 if rms < floor else floor * 0.999 + rms * 0.001)
                    thr = max(0.015, floor * 3.2)
                    voiced = min(4, voiced + 1) if rms > thr else max(0, voiced - 1)
                    now = time.monotonic()
                    if cls.cut:                          # clicked (finish now) / Space or Esc (drop it)
                        how, cls.cut = cls.cut, None
                        if in_speech and how == "finish" and len(seg) > RATE * 2 * 0.35:
                            cls.check(bytes(seg), started)
                        in_speech, seg, cls.follow_until = False, bytearray(), 0.0
                    # you started talking inside the follow-up window: show him hearing you, ring/eyes move with your voice
                    fol = in_speech and not cls.speaking() and cls.follow_from <= started < cls.follow_until
                    if fol != cls.follow_on:
                        cls.follow_on = fol
                        BUS.pub(t="follow", on=fol)
                    n += 1
                    if fol and n % 2 == 0:
                        BUS.pub(t="hear", v=round(min(1.0, max(0.0, (rms - floor) * 12)), 3))
                    if in_speech:
                        seg += fr
                        if rms > thr * 0.6:
                            last_voice = now
                        if now - last_voice > 0.7 or len(seg) > RATE * 2 * 12:
                            in_speech = False
                            if len(seg) > RATE * 2 * 0.35:
                                cls.check(bytes(seg), started)
                            seg = bytearray()
                    else:
                        pre = (pre + fr)[-FRAME * 8:]
                        if voiced >= 3:
                            in_speech, last_voice, seg, started = True, now, bytearray(pre), now
        finally:
            if cls.follow_on:
                cls.follow_on = False
                BUS.pub(t="follow", on=False)
            kill(proc)

    @classmethod
    def check(cls, pcm, started):
        # started after he finished talking, inside the follow-up window → it's a request, no "hey Void" needed
        follow = not cls.speaking() and cls.follow_from <= started < cls.follow_until
        try:
            text = transcribe_pcm(pcm) if follow else transcribe_pcm(pcm, beam=1, hint="Hey Void.")
        except Exception as e:  # noqa: BLE001
            print(f"[void] wake word stt: {e}", file=sys.stderr)
            return
        m = WAKE_RE.match(text or "")
        if cls.speaking():
            if STOP_RE.fullmatch((text or "").strip(" .,!?").lower()) or m:
                BUS.pub(t="barge", listen=bool(m), text=(m.group(2).strip(" .,!?") if m else ""))
                cls.hush()
            return
        if m:
            rest = m.group(2).strip(" .,!?")
            BUS.pub(t="wake", text=rest)
        elif follow:
            said = (text or "").strip(" .,!?")
            if said and said.lower() not in NOISE_WORDS:
                cls.follow_until = 0.0
                BUS.pub(t="wake", text=said, follow=True)


def doctor():
    print("Void doctor\n")
    if shutil.which("pactl"):
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
        dflt = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True).stdout.strip()
        print("inputs (use the 2nd column as mic_device):")
        for ln in out.splitlines():
            if ".monitor" not in ln:
                print("  " + ln + ("   <- default" if dflt and dflt in ln else ""))
        print()
    print("recorders tried in order:", ", ".join(c[0] for c in mic_cmds()) or "NONE")
    print("recording 3 seconds — say something…", flush=True)
    try:
        proc, name, buf = open_mic()
        t0, data = time.monotonic(), bytearray(buf)
        while time.monotonic() - t0 < 3:
            r, _, _ = select.select([proc.stdout], [], [], 0.2)
            if r:
                data += os.read(proc.stdout.fileno(), 8192)
        kill(proc)
        s = memoryview(bytes(data[: len(data) // 2 * 2])).cast("h")
        peak = max((abs(x) for x in s), default=0) / 32768
        bar = "#" * int(min(1, peak * 3) * 30)
        print(f"  {name}: {len(data) / 32000:.1f}s captured, loudest {peak:.3f}  [{bar:<30}]")
        print("  -> CLIPPING: input is too loud — try: wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.65" if peak > 0.98 else
              "  -> looks good" if peak > 0.02 else "  -> very quiet: wrong input, muted, or gain too low")
        if peak > 0.02:
            try:
                print("  heard:", repr(transcribe_pcm(bytes(data))))
                print(f"  (speech model: {_whisper._void_name} on {_whisper._void_dev})")
            except Exception as e:  # noqa: BLE001
                print("  speech-to-text failed:", e)
    except Exception as e:  # noqa: BLE001
        print("  MIC FAILED:", e)
    print("\nvoice:", tts_engine() or "none (install espeak-ng / piper)")
    print("claude:", claude_bin() or "NOT FOUND")


# ---------------------------------------------------------------- text to speech
def piper_bin():
    local = Path(sys.executable).parent / "piper"
    if local.exists():
        return str(local)
    return shutil.which("piper") or shutil.which("piper-tts")


def piper_voice():
    v = CFG["piper_voice"]
    if v:
        v = os.path.expanduser(v)
        return v if os.path.exists(v) else None
    voices = sorted((DATA_DIR / "voices").glob("*.onnx"))
    return str(voices[0]) if voices else None


def tts_engine():
    mode = CFG["tts"]
    if mode == "off":
        return None
    if mode in ("auto", "kokoro") and kokoro_ready():
        return "kokoro"
    if mode in ("auto", "piper") and piper_bin() and piper_voice():
        return "piper"
    if mode in ("auto", "espeak") and shutil.which("espeak-ng"):
        return "espeak"
    return None


PATH_RE = re.compile(r"(?<![\w.])(?:~|/)[\w.\-]*(?:/[\w.\-]+)+/?|(?<![\w.])/(?:home|etc|usr|tmp|opt|var)\b")


def say_path(p):
    """/home/you/Downloads → "your Downloads folder" · ~/code/void/app/server.py → "server.py" (how a person says it)."""
    parts = [x for x in p.split("/") if x]
    home = parts[:1] == ["~"] or parts[:1] == ["home"]
    parts = parts[1:] if parts[:1] == ["~"] else parts[2:] if parts[:1] == ["home"] else parts
    if not parts:
        return "your home folder" if home else "the main folder"
    last = parts[-1]
    if "." in last.strip("."):                       # a file: just its name
        return last
    return f"your {last} folder" if home and len(parts) == 1 else f"the {last} folder"


def speakable(t: str) -> str:
    t = untag(t)
    t = re.sub(r"```.*?```", " I put some code on screen. ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = PATH_RE.sub(lambda m: say_path(m.group(0).rstrip(".")) + "." * (len(m.group(0)) - len(m.group(0).rstrip("."))), t)
    t = re.sub(r"^\s*(?:[#>*+\-]+|\d+\.)\s*", "", t, flags=re.M)
    t = re.sub(r"[*_#|~]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    lim = int(CFG["speak_max_chars"])
    if len(t) > lim:
        cut = t[:lim]
        i = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = (cut[: i + 1] if i > lim // 3 else cut) + " The rest is on screen."
    return t


# Voice presets. `pitch` is applied on playback (played faster/slower);
# piper is asked to speak slower/faster by the same factor so the talking speed stays natural.
# With Kokoro (the default when it's installed) each preset is one of its voices; `kpitch` is the pitch nudge for it.
VOICES = {
    "girl":     {"label": "Girl",        "kokoro": "af_heart",   "piper": "en_US-amy-medium",  "pitch": 1.0},
    "cute":     {"label": "Cute",        "kokoro": "af_bella",   "piper": "en_US-amy-medium",  "pitch": 1.28, "kpitch": 1.12},
    "guy":      {"label": "Guy",         "kokoro": "am_michael", "piper": "en_US-ryan-medium", "pitch": 1.0},
    "deep":     {"label": "Deep",        "kokoro": "am_onyx",    "piper": "en_US-ryan-medium", "pitch": 0.82, "kpitch": 0.94},
    "british":  {"label": "British",     "kokoro": "bm_george",  "piper": "en_GB-alan-medium", "pitch": 1.0},
    "story":    {"label": "Storyteller", "kokoro": "bm_fable",   "piper": "en_GB-alan-medium", "pitch": 1.0},
    "robot":    {"label": "Robot",       "espeak": "en-us+m3",   "pitch": 0.9},
}

# ---- Kokoro (kokoro-onnx): a much more natural voice than piper, still fully local. Small int8 model (~120 MB total).
KOKORO_DIR = DATA_DIR / "voices" / "kokoro"
KOKORO_FILES = {"kokoro-v1.0.int8.onnx": "model", "voices-v1.0.bin": "voices"}
_KOKORO = {}
_downloading = set()


def kokoro_installed():
    import importlib.util
    return importlib.util.find_spec("kokoro_onnx") is not None


def kokoro_ready():
    return all((KOKORO_DIR / f).exists() for f in KOKORO_FILES) and kokoro_installed()


def fetch_kokoro():
    """Download Kokoro's model files in the background (the installer normally does this)."""
    if "kokoro" in _downloading or not kokoro_installed() or all((KOKORO_DIR / f).exists() for f in KOKORO_FILES):
        return
    _downloading.add("kokoro")

    def go():
        import kokoro_fetch   # GitHub first, Hugging Face if that's blocked
        try:
            src = kokoro_fetch.fetch(KOKORO_DIR)
            print(f"[void] kokoro voice ready (from {src})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[void] kokoro download failed: {e}", file=sys.stderr)
        finally:
            _downloading.discard("kokoro")
    threading.Thread(target=go, daemon=True).start()


def kokoro_synth(text, voice, speed):
    """→ wav bytes (16-bit mono, 24 kHz)"""
    from kokoro_onnx import Kokoro
    with _TTS_LOCK:
        k = _KOKORO.get("k")
        if k is None:
            k = _KOKORO["k"] = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.int8.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        lang = "en-gb" if voice[:1] == "b" else "en-us"
        samples, sr = k.create(text, voice=voice, speed=max(0.5, min(2.0, speed)), lang=lang)
    import numpy as np
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
    return buf.getvalue()


def voice_preset():
    return VOICES.get(CFG.get("voice") or "girl", VOICES["girl"])


def voice_file(name):
    return DATA_DIR / "voices" / f"{name}.onnx"


def fetch_voice(name):
    """Download a piper voice in the background (same source the installer uses)."""
    if name in _downloading or voice_file(name).exists():
        return
    _downloading.add(name)

    def go():
        import urllib.request
        try:
            loc, spk, q = name.split("-")
            base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{loc.split('_')[0]}/{loc}/{spk}/{q}/{name}"
            d = DATA_DIR / "voices"
            d.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(base + ".onnx.json", d / f"{name}.onnx.json")
            urllib.request.urlretrieve(base + ".onnx", d / f"{name}.onnx.part")
            (d / f"{name}.onnx.part").rename(d / f"{name}.onnx")
            print(f"[void] voice {name} ready", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[void] voice download {name} failed: {e}", file=sys.stderr)
        finally:
            _downloading.discard(name)
    threading.Thread(target=go, daemon=True).start()


_piper_speed_flag = None


def piper_run(model, text, out, length_scale):
    global _piper_speed_flag
    base = [piper_bin(), "-m", model, "-f", out]
    flags = [_piper_speed_flag] if _piper_speed_flag else ["--length-scale", "--length_scale"]
    if abs(length_scale - 1) > 0.01:
        for f in flags:
            r = subprocess.run(base + [f, f"{length_scale:.3f}"], input=text, text=True, capture_output=True, timeout=90)
            if r.returncode == 0:
                _piper_speed_flag = f
                return
        print("[void] piper doesn't take a speed flag; pitch preset will also change speed", file=sys.stderr)
    subprocess.run(base, input=text, text=True, capture_output=True, timeout=90, check=True)


# ---- piper, loaded once and kept in memory. Running the piper CLI for every sentence reloads the voice model each
# time (~1 s); the next sentence then isn't ready when the last one ends and his speech stutters.
_PIPER = {}
_TTS_LOCK = threading.Lock()


def piper_inproc(model, text, length_scale):
    """Synthesize with the piper Python package (1.2 or 1.3 API). → wav bytes, or None if it isn't usable."""
    try:
        import piper as _pp
        v = _PIPER.get(model)
        if v is None:
            v = _PIPER[model] = _pp.PiperVoice.load(model)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            if hasattr(v, "synthesize_wav"):                      # piper-tts 1.3+
                cfg = None
                if abs(length_scale - 1) > 0.01:                    # otherwise keep the voice's own pacing
                    try:
                        cfg = _pp.SynthesisConfig(length_scale=length_scale)
                    except Exception:  # noqa: BLE001
                        pass
                v.synthesize_wav(text, wf, syn_config=cfg) if cfg else v.synthesize_wav(text, wf)
            else:                                                  # piper-tts 1.2
                v.synthesize(text, wf, **({"length_scale": length_scale} if abs(length_scale - 1) > 0.01 else {}))
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        if not _PIPER.get("_warned"):
            _PIPER["_warned"] = True
            print(f"[void] piper in-process unavailable ({e}); using the piper command instead", file=sys.stderr)
        return None


def warm_tts():
    """Load the voice in the background at start-up so his first sentence isn't slow."""
    try:
        if CFG.get("tts", "auto") in ("auto", "kokoro"):
            fetch_kokoro()
        if tts_engine() in ("piper", "kokoro"):
            synth("Hi.")
    except Exception as e:  # noqa: BLE001
        print(f"[void] couldn't warm up the voice: {e}", file=sys.stderr)


def _syllables(w):
    w = w.lower()
    digits = sum(c.isdigit() for c in w)
    letters = re.sub(r"[^a-z]", "", w)
    v = len(re.findall(r"[aeiouy]+", letters))
    if letters.endswith("e") and v > 1 and not letters.endswith(("le", "ee")):
        v -= 1
    return max(1 if letters else 0, v) + digits * 1.1 + (0.4 if len(letters) > 7 else 0) + (0.3 if not letters and not digits else 0)


def tidy_and_time(wav, text):
    w2, times, _env = tidy_full(wav, text)[:3]
    return w2, times


def tidy_full(wav, text):
    """Trim the silence piper puts before/after each sentence (that's dead air between sentences) and work out when
    each word is spoken: find where the voice actually starts and stops and the pauses at commas/full stops, then
    spread the words between them by syllables. → (wav bytes, [[start, end], …] seconds, or None)"""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
        if sw != 2 or not n:
            return wav, None, None
        a = array.array("h", raw)
        if sys.byteorder == "big":
            a.byteswap()
        if ch > 1:
            a = a[::ch]
        hop = max(1, sr // 100)                                   # 10 ms frames
        env = []
        try:
            import numpy as _np
            arr = _np.frombuffer(a.tobytes(), dtype=_np.int16).astype(_np.float32)
            nfr = len(arr) // hop
            if nfr:
                env = list(_np.sqrt((arr[:nfr * hop].reshape(nfr, hop) ** 2).mean(axis=1)))
        except ImportError:
            for i in range(0, len(a) - hop + 1, hop):
                seg = a[i:i + hop:4]
                env.append(math.sqrt(sum(x * x for x in seg) / len(seg)) if len(seg) else 0)
        if not env:
            return wav, None, None
        thr = max(max(env) * 0.06, 90)
        on = [e > thr for e in env]
        if not any(on):
            return wav, None, None
        f0 = on.index(True)
        f1 = len(on) - on[::-1].index(True)
        k0, k1 = max(0, f0 - 3), min(len(on), f1 + 5)             # keep 30 ms before, 50 ms after
        out = a[k0 * hop:k1 * hop]
        if sys.byteorder == "big":
            out.byteswap()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(out.tobytes())
        # pauses (≥ 80 ms of quiet) inside the speech
        gaps, i = [], f0
        while i < f1:
            if not on[i]:
                j = i
                while j < f1 and not on[j]:
                    j += 1
                if j - i >= 8:
                    gaps.append([(i - k0) / 100, (j - k0) / 100])
                i = j
            else:
                i += 1
        a0, a1 = (f0 - k0) / 100, (f1 - k0) / 100
        lv = env[k0:k1]                                           # loudness every 40 ms, 0-100 (drives his "mouth")
        level = [min(100, round(max(lv[i:i + 4]) / 32768 * 550)) for i in range(0, len(lv), 4)]
        words = text.split()
        if not words:
            return buf.getvalue(), None, level
        wt = [_syllables(w) + 0.35 for w in words]
        pz = [2.2 if re.search(r"[.!?]['\")]*$", w) else 1.2 if re.search(r"[,;:—–]['\")]*$", w) else 0 for w in words]
        tot = sum(wt) + sum(pz[:-1]) or 1
        # anchor phrase ends to real pauses near where we'd expect them
        anchors, used, acc = [], set(), 0.0
        for i in range(len(words) - 1):
            acc += wt[i]
            if pz[i]:
                exp = a0 + (a1 - a0) * acc / tot
                best = min((g for gi, g in enumerate(gaps) if gi not in used), key=lambda g: abs((g[0] + g[1]) / 2 - exp), default=None)
                if best and abs((best[0] + best[1]) / 2 - exp) < 0.5:
                    used.add(gaps.index(best))
                    anchors.append((i, best[0], best[1]))
            acc += pz[i]
        anchors.sort(key=lambda x: x[1])
        times, start_i, t_start = [None] * len(words), 0, a0
        for idx, g0, g1 in anchors + [(len(words) - 1, a1, a1)]:
            if idx < start_i or g0 <= t_start:
                continue
            seg = range(start_i, idx + 1)
            sw_ = sum(wt[k] for k in seg) or 1
            t = t_start
            for k in seg:
                d = (g0 - t_start) * wt[k] / sw_
                times[k] = [round(t, 3), round(t + d, 3)]
                t += d
            start_i, t_start = idx + 1, g1
        if any(x is None for x in times):                         # anchors out of order: plain spread instead
            t, times = a0, []
            for k in range(len(words)):
                d = (a1 - a0) * (wt[k] + (pz[k] if k < len(words) - 1 else 0)) / tot
                times.append([round(t, 3), round(t + d * wt[k] / (wt[k] + (pz[k] if k < len(words) - 1 else 0)), 3)])
                t += d
        return buf.getvalue(), times, level
    except Exception as e:  # noqa: BLE001
        print(f"[void] word timing failed: {e}", file=sys.stderr)
        return wav, None, None


def audio_player_cmd(rate):
    """A plain audio player that takes raw 16-bit mono on stdin."""
    if shutil.which("paplay"):
        return ["paplay", "--raw", f"--rate={rate}", "--channels=1", "--format=s16le", "--latency-msec=100",
                "--client-name=Void", "--stream-name=Void voice"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1", "--buffer-time=120000", "-"]
    return None


class Speaker:
    """Plays his voice straight to PipeWire/Pulse, not through the window (the window's drawing can starve audio).

    Two workers, so nothing ever waits on anything else:
      * the maker turns upcoming sentences into audio ahead of time (up to 3 ready), in order;
      * the player streams finished audio to the speakers, keeping ~0.8 s buffered — so even if Void (or the whole
        computer) is busy for a moment, there's still sound queued up and nothing stutters.
    It always knows when each sentence will start playing and tells the page, which times the captions and his face
    from that. If it ever does run dry, it counts it (see `void stress`) and shifts its clock so sync stays right."""
    cv = threading.Condition()
    todo = []            # (gen, id, text) waiting to be made
    ready = []           # (gen, id, text, pcm, prate, words, level) waiting to be played
    making = False
    gen = 0
    proc = None
    prate = None
    t0 = 0.0
    written = 0
    threads = []
    LEAD = 0.8           # seconds of audio kept buffered ahead of the speakers
    filled = False
    LAT = 0.12           # roughly how long the audio server takes to get it out of the speakers
    stats = {}

    @classmethod
    def reset_stats(cls):
        cls.stats = {"underruns": 0, "worst_gap_ms": 0.0, "min_ahead_ms": None, "sentences": 0, "audio_s": 0.0,
                     "worst_rtf": 0.0, "slowest_make_s": 0.0}

    @classmethod
    def available(cls):
        return CFG.get("voice_out", "auto") != "page" and audio_player_cmd(22050) is not None

    @classmethod
    def _start(cls):
        if not cls.stats:
            cls.reset_stats()
        if not cls.threads or not all(t.is_alive() for t in cls.threads):
            cls.threads = [threading.Thread(target=cls._maker, daemon=True), threading.Thread(target=cls._player_loop, daemon=True)]
            for t in cls.threads:
                t.start()

    @classmethod
    def say(cls, text, sid):
        with cls.cv:
            cls.todo.append((cls.gen, sid, text))
            if all(isinstance(x[1], int) for x in cls.todo):     # sentences can arrive out of order: play them in order
                cls.todo.sort(key=lambda x: (x[0], x[1]))
            cls.cv.notify_all()
        cls._start()

    @classmethod
    def idle(cls):
        with cls.cv:
            return not cls.todo and not cls.ready and not cls.making and cls.written / (cls.prate or 1) < time.monotonic() - cls.t0

    @classmethod
    def stop(cls):
        with cls.cv:
            cls.gen += 1
            cls.todo.clear()
            cls.ready.clear()
            p, cls.proc = cls.proc, None
            cls.written, cls.t0 = 0, 0.0
            cls.cv.notify_all()
        if p:
            try:
                p.kill()
            except OSError:
                pass
        Wake.hush()

    # ---- worker 1: make the audio ahead of time
    @classmethod
    def _maker(cls):
        while True:
            with cls.cv:
                while not cls.todo or len(cls.ready) >= 3:
                    cls.cv.wait()
                g, sid, text = cls.todo.pop(0)
                cls.making = True
            try:
                if g != cls.gen:
                    continue
                t = time.monotonic()
                wav, rate = synth(text)
                wav, times, level = tidy_full(wav, text)
                with wave.open(io.BytesIO(wav)) as w:
                    sr, pcm = w.getframerate(), w.readframes(w.getnframes())
                took = time.monotonic() - t
                prate = int(round(sr * rate))                     # the pitch preset: play it a touch faster/slower
                dur = len(pcm) / 2 / prate
                st = cls.stats
                st["slowest_make_s"] = max(st.get("slowest_make_s", 0), round(took, 3))
                if dur > 0.3:
                    st["worst_rtf"] = max(st.get("worst_rtf", 0), round(took / dur, 3))
                words = [[round(a * sr / prate, 3), round(b * sr / prate, 3)] for a, b in times] if times else None
                with cls.cv:
                    if g == cls.gen:
                        cls.ready.append((g, sid, text, pcm, prate, words, level, round(40 * sr / prate, 2)))
                        cls.cv.notify_all()
            except Exception as e:  # noqa: BLE001
                print(f"[void] speech: {e}", file=sys.stderr)
                BUS.pub(t="spk_skip", id=sid)
            finally:
                with cls.cv:
                    cls.making = False
                    cls.cv.notify_all()

    # ---- worker 2: play it
    @classmethod
    def _player(cls, rate):
        if cls.proc and cls.proc.poll() is None and cls.prate == rate:
            return cls.proc
        if cls.proc:
            try:
                cls.proc.kill()
            except OSError:
                pass
        cls.proc = subprocess.Popen(audio_player_cmd(rate), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        cls.prate, cls.written, cls.t0 = rate, 0, time.monotonic()
        return cls.proc

    @classmethod
    def _write(cls, data, g):
        """Stream raw samples, keeping LEAD seconds buffered. If we were ever late (the speakers ran dry) count it and
        move the clock on, so the start times we tell the page stay true."""
        step = max(2, int(cls.prate * 0.02)) * 2
        for i in range(0, len(data), step):
            while True:
                if g != cls.gen:
                    return False
                ahead = cls.written / cls.prate - (time.monotonic() - cls.t0)
                if ahead <= cls.LEAD:
                    break
                time.sleep(min(0.05, ahead - cls.LEAD + 0.005))
            if ahead < 0 and cls.written:                         # ran dry: an audible gap
                gap = -ahead
                cls.t0 += gap
                cls.stats["underruns"] = cls.stats.get("underruns", 0) + 1
                cls.stats["worst_gap_ms"] = max(cls.stats.get("worst_gap_ms", 0), round(gap * 1000, 1))
                ahead = 0.0
            if ahead >= cls.LEAD * 0.9:
                cls.filled = True                                 # (only judge the buffer once it has filled up)
            if cls.written and cls.filled:
                m = cls.stats.get("min_ahead_ms")
                cls.stats["min_ahead_ms"] = round(ahead * 1000) if m is None else min(m, round(ahead * 1000))
            try:
                cls.proc.stdin.write(data[i:i + step])
                cls.proc.stdin.flush()
            except (BrokenPipeError, OSError, AttributeError):
                return False
            cls.written += len(data[i:i + step]) // 2
        return True

    @classmethod
    def _player_loop(cls):
        while True:
            with cls.cv:
                while not cls.ready:
                    if not cls.todo and not cls.making and cls.prate and cls.written:
                        end = cls.t0 + cls.written / cls.prate + cls.LAT  # nothing left anywhere: say when he's done
                        g = cls.gen
                        cls.cv.wait(timeout=max(0.0, end - time.monotonic()))
                        if not cls.ready and not cls.todo and not cls.making and g == cls.gen and time.monotonic() >= end - 0.01:
                            BUS.pub(t="spk_done")
                            cls.written, cls.t0 = 0, time.monotonic()
                        continue
                    cls.cv.wait()
                g, sid, text, pcm, prate, words, level, lstep = cls.ready.pop(0)
                cls.cv.notify_all()
            if g != cls.gen:
                continue
            try:
                cls._player(prate)
            except Exception as e:  # noqa: BLE001
                print(f"[void] audio player: {e}", file=sys.stderr)
                BUS.pub(t="spk_skip", id=sid)
                continue
            now = time.monotonic()
            if cls.written / cls.prate < now - cls.t0:           # it had gone quiet: restart the clock
                cls.t0, cls.written, cls.filled = now, 0, False
            start = cls.t0 + cls.written / cls.prate
            dur = len(pcm) / 2 / prate
            if g == cls.gen:                                     # (not if he was cut off meanwhile)
                Wake.talking_until(start + dur + 0.8)
            cls.stats["sentences"] = cls.stats.get("sentences", 0) + 1
            cls.stats["audio_s"] = round(cls.stats.get("audio_s", 0) + dur, 2)
            BUS.pub(t="spk", id=sid, at=round((start - now + cls.LAT) * 1000), dur=round(dur, 3), words=words,
                    level=level, step=lstep)
            if not cls._write(pcm, g):
                continue
            pause = 0.3 if re.search(r"[.!?]['\")]*$", text) else 0.14 if re.search(r"[,;:—–]$", text) else 0.06
            cls._write(b"\0\0" * int(prate * pause), g)


STRESS_LINES = [
    "Okay, I had a look through the whole project.",
    "The crash happens when the list of items comes back empty, so the loop never runs and the next step falls over.",
    "I added a check before the loop, and a test that covers the empty case.",
    "All nine tests pass now.",
    "I also noticed the deploy script still points at the old server, which might cause trouble later.",
    "Want me to update that too, or leave it for now?",
]


def _cpu_burn(stop):
    x = 1
    while not stop.is_set():
        for _ in range(20000):
            x = (x * x + 1) % 1000003


def stress_voice(silent=False):
    """`void stress`: speak a few sentences three times — on a quiet computer, with every CPU core flat out, and with Void
    itself busy — and report whether the voice ever ran dry (a stutter), and how close it came."""
    import multiprocessing as mp
    eng = tts_engine()
    print(f"voice engine: {eng or 'none'}" + (f" ({Path(piper_voice()).name})" if eng == 'piper' and piper_voice() else ""))
    if not eng:
        print("no voice engine installed — nothing to test"); return 1
    if silent:
        globals()["audio_player_cmd"] = lambda rate: [sys.executable, "-c", "import sys\nwhile sys.stdin.buffer.read(65536): pass"]
    else:
        cmd = audio_player_cmd(22050)
        if not cmd:
            print("no audio player (paplay / aplay) — install libpulse or alsa-utils"); return 1
        print(f"player: {cmd[0]}   (you'll hear him say the same few sentences three times)")
    print("plays his voice four times: on a quiet computer, then with your CPU and Void itself as busy as they can be")
    print("warming up the voice…", flush=True)
    synth("Hi.")
    results, ok = [], True
    phases = [("quiet computer", ""), ("every CPU core busy", "cpu"), ("Void itself busy", "gil"),
              ("everything busy at once", "cpu+gil")]
    for n, (name, load) in enumerate(phases):
        stop, workers = mp.Event(), []
        if "cpu" in load:
            workers += [mp.Process(target=_cpu_burn, args=(stop,), daemon=True) for _ in range(os.cpu_count() or 2)]
        if "gil" in load:
            workers += [threading.Thread(target=_cpu_burn, args=(stop,), daemon=True) for _ in range(2)]
        for w in workers:
            w.start()
        Speaker.reset_stats()
        print(f"\n▶ {name}…", flush=True)
        for i, line in enumerate(STRESS_LINES):
            Speaker.say(line, n * 100 + i + 1)
        t = time.monotonic()
        time.sleep(0.5)
        while not Speaker.idle() and time.monotonic() - t < 180:
            time.sleep(0.1)
        stop.set()
        for w in workers:
            w.join(timeout=2)
        st = dict(Speaker.stats)
        verdict = "smooth — no stutter" if not st["underruns"] else f"STUTTERED {st['underruns']}× (worst gap {st['worst_gap_ms']:.0f} ms)"
        ok &= not st["underruns"]
        print(f"  {verdict}")
        if st["min_ahead_ms"] is not None and st["min_ahead_ms"] < 150 and not st["underruns"]:
            print("  (it got close, though)")
        print(f"  {st['sentences']} sentences, {st['audio_s']:.1f}s of speech · slowest sentence took {st['slowest_make_s']:.2f}s to make"
              f" (that's {st['worst_rtf']:.2f}× its own length) · closest it came to running dry: "
              f"{st['min_ahead_ms'] if st['min_ahead_ms'] is not None else '—'} ms of sound left")
        results.append((name, st))
    worst_rtf = max(r[1]["worst_rtf"] for r in results)
    print()
    if ok:
        print("✓ PASS — the voice never ran dry, even with your computer flat out.")
    elif worst_rtf > 0.8:
        print("✗ The voice takes almost as long to make as to say on this computer. Pick a lighter voice "
              "(Settings → Voice), or a '-low' piper voice, and run `void stress` again.")
    else:
        print("✗ It ran dry under load. Please send the numbers above.")
    return 0 if ok else 2


def synth(text: str):
    """→ (wav bytes, playback rate)"""
    eng = tts_engine()
    if not eng:
        raise RuntimeError("no TTS engine (install espeak-ng or piper)")
    vp = voice_preset()
    if eng == "kokoro" and vp.get("kokoro"):
        # pitch: play it back a touch faster/slower, and have Kokoro speak slower/faster by the same factor
        kp = float(vp.get("kpitch", 1.0))
        return kokoro_synth(text, CFG.get("kokoro_voice") or vp["kokoro"], 1.0 / kp), kp
    if eng == "kokoro":                                       # (the robot preset is espeak on purpose)
        eng = "piper" if piper_bin() and piper_voice() else "espeak"
    pitch = float(vp.get("pitch", 1.0))
    fd, out = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        model = None
        if eng == "piper" and not vp.get("espeak") and not CFG["piper_voice"]:
            if voice_file(vp["piper"]).exists():
                model = str(voice_file(vp["piper"]))
            else:
                fetch_voice(vp["piper"])          # use whatever we have until it lands
        if eng == "piper" and not vp.get("espeak") and model is None:
            model = piper_voice()
        if model:
            with _TTS_LOCK:                                         # one at a time, in order: no CPU fight between sentences
                audio = piper_inproc(model, text, pitch)
            if audio:
                return audio, pitch
            piper_run(model, text, out, pitch)
            rate = pitch
        elif shutil.which("espeak-ng"):
            ev = vp.get("espeak") or CFG["espeak_voice"]
            subprocess.run(["espeak-ng", "-v", ev, "-s", "160", "-p", str(int(clamp01(pitch / 2) * 99)), "-w", out, text],
                           capture_output=True, timeout=90, check=True)
            rate = 1.0
        else:
            raise RuntimeError("no usable voice")
        return Path(out).read_bytes(), rate
    finally:
        os.unlink(out)


def clamp01(x):
    return max(0.0, min(1.0, x))


def save_config(**kw):
    """Persist a few settings into config.json without clobbering the user's other edits."""
    try:
        cur = json.loads(CONF_PATH.read_text()) if CONF_PATH.exists() else {}
    except Exception:  # noqa: BLE001
        cur = {}
    cur.update(kw)
    CFG.update(kw)
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONF_PATH.write_text(json.dumps(cur, indent=2) + "\n")


# ---------------------------------------------------------------- claude code
def claude_bin():
    b = CFG["claude_bin"]
    found = shutil.which(b)
    if found:
        return found
    for c in (Path.home() / ".local/bin/claude", Path.home() / ".claude/local/claude",
              Path("/usr/bin/claude"), Path("/usr/local/bin/claude")):
        if c.exists():
            return str(c)
    return None


def summarize_input(inp):
    if not isinstance(inp, dict):
        return ""
    s = ""
    for k in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if isinstance(inp.get(k), str):
            s = inp[k]
            break
    s = " ".join(s.split())
    return s[:90] + ("…" if len(s) > 90 else "")


def _cut(v, n):
    v = v if isinstance(v, str) else json.dumps(v)
    return v if len(v) <= n else v[:n] + "\n…"


def panel_input(name, inp):
    """What the code panel shows for a tool call: the real code / command / query, trimmed to a sane size."""
    if not isinstance(inp, dict):
        return {}
    out = {}
    for k in ("file_path", "path", "command", "description", "pattern", "glob", "url", "query", "notebook_path"):
        if isinstance(inp.get(k), str):
            out[k] = _cut(inp[k], 600)
    for k in ("old_string", "new_string", "content", "new_source"):
        if isinstance(inp.get(k), str):
            out[k] = _cut(inp[k], 4000)
    if isinstance(inp.get("edits"), list):
        out["edits"] = [{"old_string": _cut(e.get("old_string", ""), 1500), "new_string": _cut(e.get("new_string", ""), 1500)}
                        for e in inp["edits"][:6] if isinstance(e, dict)]
    if isinstance(inp.get("todos"), list):
        out["todos"] = [{"content": _cut(t.get("content", ""), 120), "status": t.get("status", "")}
                        for t in inp["todos"][:12] if isinstance(t, dict)]
    # which line an edit lands on, so the panel shows real line numbers (the file isn't changed yet at this point)
    if name in ("Edit", "MultiEdit") and isinstance(inp.get("file_path"), str):
        try:
            fp = Path(inp["file_path"]) if os.path.isabs(inp["file_path"]) else Path(ST.cwd) / inp["file_path"]
            if fp.is_file() and fp.stat().st_size < 3_000_000:
                txt = fp.read_text(errors="replace")
                old = inp.get("old_string") or ((inp.get("edits") or [{}])[0] or {}).get("old_string") or ""
                i = txt.find(old) if old else -1
                if i >= 0:
                    out["line"] = txt.count("\n", 0, i) + 1
        except Exception:  # noqa: BLE001
            pass
    if name == "Write" and isinstance(inp.get("file_path"), str):
        try:
            fp = Path(inp["file_path"]) if os.path.isabs(inp["file_path"]) else Path(ST.cwd) / inp["file_path"]
            out["exists"] = fp.exists()
        except Exception:  # noqa: BLE001
            pass
    return out


def result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else
                         ("[image]" if isinstance(b, dict) and b.get("type") == "image" else "") for b in c)
    return ""


def trust():
    return "root" if CFG.get("root_mode") else (CFG.get("trust") if CFG.get("trust") in ("ask", "trust", "readonly") else "ask")


HOOKS_PATH = DATA_DIR / "hooks.json"


def hooks_file():
    """Claude Code settings that route commands / web requests through approve.py (→ a yes/no card under Void)."""
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(APP_DIR / 'approve.py'))}"
    data = {"hooks": {"PreToolUse": [{"matcher": "Bash|WebFetch|WebSearch|NotebookEdit|mcp__.*",
                                      "hooks": [{"type": "command", "command": cmd, "timeout": 900}]}]}}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HOOKS_PATH.write_text(json.dumps(data, indent=1))
    return str(HOOKS_PATH)


def build_cmd(binary, partial):
    c = [binary, "-p", "--output-format", "stream-json", "--verbose"]
    if partial:
        c.append("--include-partial-messages")
    tr = trust()
    if tr in ("root", "trust"):
        c += ["--permission-mode", "bypassPermissions"]
    elif tr == "readonly":
        c += ["--permission-mode", "plan"]
    else:
        c += ["--permission-mode", "acceptEdits", "--settings", hooks_file()]
    mdl = CFG["model"]
    if mdl == "auto":                            # quick chit-chat → the fast model; real work → the default one
        mdl = ST.auto_pick or ""
    if mdl:
        c += ["--model", mdl]
    sp = CFG["voice_prompt"] or ""
    if CFG.get("root_mode"):
        sp += ("\n\nRoot mode is ON: the user allows you to run any command, including ones that need root. "
               "When you need root, use `sudo -A ...` (Void asks the user for the password itself, or fills it in "
               "automatically; never ask for the password in chat). Still be careful: say what you're about to change before destructive or "
               "system-wide actions.")
    if tr == "ask":
        sp += ("\n\nThe user approves commands and web requests one at a time (Void asks them out loud). "
               "Prefer one clear command over many small ones, and don't retry a command the user said no to.")
    mp = memory_prompt(fresh=not ST.session)
    if mp:
        sp += "\n\n" + mp
    if sp:
        c += ["--append-system-prompt", sp]
    if ST.session:
        c += ["--resume", ST.session]
    c += list(CFG["extra_args"])
    return c


def run_claude(prompt, emit, partial):
    binary = claude_bin()
    if not binary:
        emit(t="error", text=f"Couldn't find Claude Code (`{CFG['claude_bin']}`). Install it, run `claude` once to "
                             f"log in, or set claude_bin in {CONF_PATH}.")
        return "done"
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:" + env.get("PATH", "")
    if CFG.get("root_mode"):
        env["SUDO_ASKPASS"] = str(APP_DIR / "askpass.py")
    env["VOID_PORT"], env["VOID_TOKEN"] = str(CFG["port"]), TOKEN
    proc = subprocess.Popen(build_cmd(binary, partial), cwd=ST.cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                            start_new_session=True)
    ST.proc = proc
    err = []
    t_err = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
    t_err.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except BrokenPipeError:
        pass

    got_result = streamed = emitted = False
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        ty = o.get("type")
        if ty == "system" and o.get("subtype") == "init":
            if o.get("session_id"):
                ST.session = o["session_id"]
                ST.save()
            emit(t="init", model=o.get("model", ""))
        elif ty == "stream_event":
            ev = o.get("event") or {}
            d = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                streamed = emitted = True
                emit(t="delta", text=d.get("text", ""))
            elif ev.get("type") == "message_start":
                emit(t="break")
        elif ty == "assistant":
            for b in (o.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and not streamed and b.get("text"):
                    emitted = True
                    emit(t="break")
                    emit(t="delta", text=b["text"])
                elif b.get("type") == "tool_use":
                    emitted = True
                    emit(t="tool", name=b.get("name", "tool"), detail=summarize_input(b.get("input")), id=b.get("id", ""),
                         input=panel_input(b.get("name", ""), b.get("input")))
        elif ty == "user":
            # tool output comes back as a "user" message — the code panel shows it (terminal output, file contents, hits)
            content = (o.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    txt = result_text(b.get("content"))
                    lines = txt.splitlines()
                    if len(lines) > 80:
                        lines = lines[:40] + [f"… {len(lines) - 70} more lines …"] + lines[-30:]
                    emit(t="toolres", id=b.get("tool_use_id", ""), text=_cut("\n".join(lines), 6000),
                         is_error=bool(b.get("is_error")))
        elif ty == "result":
            got_result = True
            if o.get("session_id"):
                ST.session = o["session_id"]
                ST.save()
            denied = o.get("permission_denials") or []
            if denied:
                emit(t="denied", tools=sorted({d.get("tool_name", "?") for d in denied}))
            emit(t="result", text=o.get("result") or "", is_error=bool(o.get("is_error")),
                 cost=o.get("total_cost_usd"), turns=o.get("num_turns"))
    proc.wait()
    t_err.join(timeout=2)
    ST.proc = None
    if got_result:
        return "done"
    e = "".join(err).strip()
    if ST.stopped:
        emit(t="stopped")
        return "done"
    low = e.lower()
    if partial and not emitted and ("include-partial-messages" in low or "unknown option" in low):
        return "retry_nopartial"
    if ST.session and not emitted and ("session" in low or "conversation" in low):
        return "retry_fresh"
    emit(t="error", text=e[-1200:] or f"claude exited with code {proc.returncode}")
    return "done"


def stop_proc():
    APPROVALS.cancel_all()
    p = ST.proc
    if p and p.poll() is None:
        ST.stopped = True
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def state_info():
    return {
        "cwd": ST.cwd, "cwd_display": pretty_path(ST.cwd), "session": bool(ST.session),
        "tts": tts_engine(), "mic": bool(mic_cmds()), "stt": "ready" if _whisper else (_whisper_err or "loading"),
        "trust": trust(), "model": CFG["model"] or "default", "root": bool(CFG.get("root_mode")),
        "ultra_root": bool(CFG.get("root_mode") and CFG.get("ultra_root")), "pw_remembered": AskPass.remembered is not None,
        "wake_word": bool(CFG.get("wake_word")), "notify_done": bool(CFG.get("notify_done")),
        "popup_done": bool(CFG.get("popup_done")), "hover": bool(CFG.get("hover")), "code_panel": CFG.get("code_panel", True) is not False, "live_minutes": int(CFG.get("live_minutes") or 10), "voice_native": Speaker.available(), "code_face": "you" if CFG.get("code_face") == "you" else "him",
        "click_through": bool(CFG.get("click_through")),
        "autostart": bool(CFG.get("autostart")), "listen_on_show": bool(CFG.get("listen_on_show")),
        "barge_in": bool(CFG.get("barge_in")), "sfx": bool(CFG.get("sfx")), "look": look(), "skins": SKINS,
        "projects": quick.projects(),
        "quick_actions": CFG.get("quick_actions") or [], "memory_count": len(mem_load()),
        "mic_device": CFG.get("mic_device") or "", "whisper_model": CFG.get("whisper_model") or "auto",
        "can_shoot": any(shutil.which(t) for t in ("grim", "spectacle", "gnome-screenshot", "maim", "scrot", "import")),
        "eye_color": CFG.get("eye_color") or "#3d9b74", "eye_white": CFG.get("eye_white") or "#f3dccf",
        "pupil_style": CFG.get("pupil_style") or "slit",
        "voice": CFG.get("voice") or "girl", "voice_pitch": voice_preset().get("pitch", 1.0),
        "voices": [{"id": k, "label": v["label"],
                    "ready": bool(v.get("espeak")) or (bool(v.get("kokoro")) and tts_engine() == "kokoro")
                             or voice_file(v.get("piper", "")).exists(),
                    "downloading": v.get("piper") in _downloading or (bool(v.get("kokoro")) and "kokoro" in _downloading)}
                   for k, v in VOICES.items()],
        "voice_engine": tts_engine() or "none",
        "claude": bool(claude_bin()), "busy": ST.lock.locked(), "config": str(CONF_PATH),
    }


# ---------------------------------------------------------------- http
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
          ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "Void"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        p = urlparse(self.path).path
        if p == "/api/state":
            return self._json(state_info())
        if p == "/":
            p = "/index.html"
        f = (STATIC / p.lstrip("/")).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        data = f.read_bytes()
        if f.name == "index.html":
            data = data.replace(b"__VOID_TOKEN__", TOKEN.encode())
        self.send_response(200)
        self.send_header("Content-Type", CTYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Void-Token") != TOKEN:
            return self._json({"error": "forbidden"}, 403)
        p = urlparse(self.path).path
        try:
            if p == "/api/ask":
                return self.ask()
            if p == "/api/tts":
                text = speakable(json.loads(self._body() or b"{}").get("text", ""))
                if not text or not tts_engine():
                    self.send_response(204)
                    self.end_headers()
                    return None
                audio, rate = synth(text)
                audio, words = tidy_and_time(audio, text)
                pause = 0.3 if re.search(r"[.!?]['\")]*$", text) else 0.14 if re.search(r"[,;:—–]$", text) else 0.06
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("X-Void-Rate", f"{rate:.3f}")
                self.send_header("X-Void-Pause", f"{pause:.2f}")
                if words:
                    self.send_header("X-Void-Words", json.dumps(words, separators=(",", ":")))
                self.send_header("Access-Control-Expose-Headers", "X-Void-Rate, X-Void-Pause, X-Void-Words")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
                return None
            if p == "/api/say":                       # speak this (played by the server, see Speaker)
                b = json.loads(self._body() or b"{}")
                text = speakable(b.get("text", ""))
                if not text or not tts_engine():
                    BUS.pub(t="spk_skip", id=b.get("id"))
                else:
                    Speaker.say(text, b.get("id"))
                return self._json({"ok": True})
            if p == "/api/say/stop":
                Speaker.stop()
                return self._json({"ok": True})
            if p == "/api/listen":
                Wake.end_follow("cancel")                # a normal recording takes over from the follow-up window
                return self.listen()
            if p == "/api/follow":
                Wake.end_follow("finish" if json.loads(self._body() or b"{}").get("act") == "finish" else "cancel")
                return self._json({"ok": True})
            if p == "/api/listen/stop":
                cur = Listen.current
                if cur:
                    cur.stop(bool(json.loads(self._body() or b"{}").get("cancel")))
                return self._json({"ok": True})
            if p == "/api/stop":
                stop_proc()
                return self._json({"ok": True})
            if p == "/api/settings":
                b = json.loads(self._body() or b"{}")
                upd = {}
                if "model" in b:
                    m = str(b["model"] or "").strip()
                    upd["model"] = "" if m in ("", "default") else m
                if "voice" in b and b["voice"] in VOICES:
                    upd["voice"] = b["voice"]
                    if VOICES[b["voice"]].get("piper"):
                        fetch_voice(VOICES[b["voice"]]["piper"])
                if "root" in b:
                    upd["root_mode"] = bool(b["root"])
                    if not b["root"]:
                        upd["ultra_root"] = False
                if "ultra_root" in b:
                    upd["ultra_root"] = bool(b["ultra_root"])
                    if b["ultra_root"]:
                        upd["root_mode"] = True
                if upd.get("ultra_root") is False or upd.get("root_mode") is False:
                    AskPass.forget()
                for key in ("eye_color", "eye_white"):
                    if key in b:
                        ec = str(b[key] or "").strip().lower()
                        if ec == "random" or re.fullmatch(r"#[0-9a-f]{6}", ec):
                            upd[key] = ec
                if isinstance(b.get("look"), dict):
                    cur = dict(CFG.get("look") or {})
                    cur.update(b["look"])
                    CFG["look"] = cur
                    upd["look"] = look()
                if b.get("pupil_style") in ("slit", "circle", "dot"):
                    upd["pupil_style"] = b["pupil_style"]
                if b.get("trust") in ("ask", "trust", "readonly"):
                    upd["trust"] = b["trust"]
                    upd["root_mode"] = upd["ultra_root"] = False
                    AskPass.forget()
                if "follow_up" in b:
                    upd["follow_up"] = max(0, min(60, int(b.get("follow_up") or 0)))
                for key in ("wake_word", "notify_done", "popup_done", "code_panel", "hover", "click_through", "autostart", "listen_on_show",
                            "barge_in", "sfx"):
                    if key in b:
                        upd[key] = bool(b[key])
                if b.get("code_face") in ("him", "you"):
                    upd["code_face"] = b["code_face"]
                if "mic_device" in b:
                    upd["mic_device"] = str(b["mic_device"] or "")
                if b.get("whisper_model") in ("auto", "tiny.en", "base.en", "small.en", "medium.en"):
                    upd["whisper_model"] = b["whisper_model"]
                if isinstance(b.get("quick_actions"), list):
                    qa = []
                    for a in b["quick_actions"][:12]:
                        if isinstance(a, dict) and str(a.get("label", "")).strip() and str(a.get("prompt", "")).strip():
                            qa.append({"label": str(a["label"]).strip()[:40], "prompt": str(a["prompt"]).strip()[:1000],
                                       **({"attach": a["attach"]} if a.get("attach") in ("screen", "clipboard") else {})})
                    upd["quick_actions"] = qa
                if upd:
                    save_config(**upd)
                if "whisper_model" in upd:
                    reset_whisper()
                if "autostart" in upd:
                    sync_autostart()
                if upd.get("wake_word"):
                    threading.Thread(target=lambda: _try(whisper), daemon=True).start()
                Wake.ensure()
                return self._json(state_info())
            if p == "/api/events":
                return self.events()
            if p == "/api/quick":
                r = quick.handle(json.loads(self._body() or b"{}").get("text", ""))
                if r and r.get("cd"):
                    ST.cwd, ST.session = r["cd"], None
                    ST.save()
                    r["state"] = state_info()
                return self._json({"handled": bool(r), **(r or {})})
            if p == "/api/projects":
                b = json.loads(self._body() or b"{}")
                if b.get("op") == "add":
                    got = quick.add_project(str(b.get("name") or ""), str(b.get("path") or ""))
                    if not got:
                        return self._json({"error": "that folder doesn't exist"}, 400)
                elif b.get("op") == "del":
                    pr = quick.projects()
                    pr.pop(str(b.get("name")), None)
                    save_config(projects=pr)
                return self._json({"projects": quick.projects()})
            if p == "/api/approve":
                b = json.loads(self._body() or b"{}")
                return self._json(APPROVALS.ask(str(b.get("tool") or "?"), b.get("input") or {}))
            if p == "/api/askpass":                   # askpass.py (sudo -A) is waiting for the password
                b = json.loads(self._body() or b"{}")
                return self._json(AskPass.ask(str(b.get("prompt") or "Password:")))
            if p == "/api/askpass/answer":
                b = json.loads(self._body() or b"{}")
                pw = b.get("pw")
                return self._json({"ok": AskPass.answer(str(b.get("id")), pw if isinstance(pw, str) else None)})
            if p == "/api/approve/answer":
                b = json.loads(self._body() or b"{}")
                ans = b.get("ans") if b.get("ans") in ("yes", "no", "always") else "no"
                return self._json({"ok": APPROVALS.answer(str(b.get("id")), ans)})
            if p == "/api/speaking":                 # the page tells us while he talks (so "hey Void" can't wake himself)
                b = json.loads(self._body() or b"{}")
                Wake.talking_until(time.monotonic() + (60 if b.get("on") else 0.8))
                return self._json({"ok": True})
            if p == "/api/capture":
                kind = json.loads(self._body() or b"{}").get("kind", "screen")
                try:
                    return self._json(capture(kind))
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e)}, 400)
            if p == "/api/notify":
                b = json.loads(self._body() or b"{}")
                notify(str(b.get("title") or "Void"), str(b.get("body") or ""))
                return self._json({"ok": True})
            if p == "/api/memory":
                b = json.loads(self._body() or b"{}")
                op = b.get("op", "list")
                if op == "add":
                    it = mem_add(b.get("text", ""))
                    return self._json({"added": it, "items": mem_load()})
                if op == "del":
                    n = mem_del(ident=b.get("id"), match=b.get("match"))
                    return self._json({"removed": n, "items": mem_load()})
                if op == "clear":
                    mem_save([])
                return self._json({"items": mem_load()})
            if p == "/api/history":
                b = json.loads(self._body() or b"{}")
                if b.get("op") == "clear":
                    HIST_PATH.unlink(missing_ok=True)
                return self._json({"items": hist_load(int(b.get("limit") or 60))})
            if p == "/api/resume":
                b = json.loads(self._body() or b"{}")
                d = b.get("cwd") or ST.cwd
                if not os.path.isdir(d):
                    return self._json({"error": f"that folder is gone: {pretty_path(d)}"}, 400)
                ST.cwd, ST.session = d, b.get("session") or None
                ST.save()
                return self._json(state_info())
            if p == "/api/devices":
                return self._json({"mics": list_mics()})
            if p == "/api/new":
                ST.session = None
                ST.save()
                return self._json(state_info())
            if p == "/api/cd":
                raw = json.loads(self._body() or b"{}").get("path", "~")
                path = os.path.abspath(os.path.join(ST.cwd, os.path.expanduser(raw.strip() or "~")))
                if not os.path.isdir(path):
                    return self._json({"error": f"not a directory: {pretty_path(path)}"}, 400)
                ST.cwd, ST.session = path, None
                ST.save()
                return self._json(state_info())
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        alive = [True]

        def emit(**kw):
            if not alive[0]:
                return
            try:
                self.wfile.write(b"data: " + json.dumps(kw).encode() + b"\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                alive[0] = False
        return emit, alive

    def listen(self):
        mode = json.loads(self._body() or b"{}").get("mode", "auto")
        if not Listen.lock.acquire(blocking=False):
            if Listen.current:
                Listen.current.stop(cancel=True)
            if not Listen.lock.acquire(timeout=2):
                return self._json({"error": "mic busy"}, 409)
        try:
            ls = Listen.current = Listen("ptt" if mode == "ptt" else "auto")
            emit, alive = self._sse()

            def emit2(**kw):
                emit(**kw)
                if not alive[0]:
                    ls.stop(cancel=True)        # window closed / request aborted
            try:
                ls.run(emit2)
            except Exception as e:  # noqa: BLE001
                emit(t="error", text=str(e))
            emit(t="done")
        finally:
            Listen.current = None
            Listen.lock.release()
        return None

    def events(self):
        emit, alive = self._sse()
        q = BUS.sub()
        import queue
        try:
            emit(t="hello")
            while alive[0]:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    emit(t="ping")
                    continue
                emit(**ev)
        finally:
            BUS.unsub(q)
        return None

    def ask(self):
        body = json.loads(self._body() or b"{}")
        said = (body.get("prompt") or "").strip()
        extra = attach_prompt(body.get("attach"))
        prompt = (extra + "\n\n" + said).strip() if extra else said
        follow = bool(body.get("follow")) and not extra
        if follow:
            prompt = FOLLOW_NOTE + said
        if not prompt:
            return self._json({"error": "empty prompt"}, 400)
        workish = re.search(r"\b(code|file|files|fix|bug|commit|test|tests|run|build|refactor|install|folder|project|git|"
                            r"error|script|function|write|edit|create|make|deploy|server|repo|update|delete|rename|"
                            r"search|find|install|package|config)\b", said, re.I)
        ST.auto_pick = "haiku" if (len(said) < 160 and not extra and not workish) else ""
        if not ST.lock.acquire(blocking=False):
            return self._json({"error": "still working on the last one"}, 409)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            alive = [True]

            rec = {"ts": int(time.time()), "cwd": ST.cwd, "cwd_display": pretty_path(ST.cwd), "you": said or "(attachment)",
                   "him": "", "tools": [], "ok": True,
                   "attach": [a.get("label") for a in body.get("attach") or [] if isinstance(a, dict)]}

            # a follow-up: hold back his first words until we know it isn't "[ignore]"
            gate = {"open": not follow, "held": [], "text": "", "ignored": False}

            def emit(**kw):
                if not gate["open"]:
                    t = kw.get("t")
                    if t in ("delta", "result"):
                        gate["text"] += kw.get("text", "") if t == "delta" else ("" if gate["text"] else kw.get("text", ""))
                    said_so_far = gate["text"].strip().lower()
                    if said_so_far.startswith(IGNORE):                   # not meant for him: drop the whole reply
                        gate["open"] = gate["ignored"] = True
                        return send_ev({"t": "ignored"})
                    if t == "delta" and IGNORE.startswith(said_so_far):    # could still turn into "[ignore]"
                        gate["held"].append(kw)
                        return
                    if t in ("delta", "tool", "result", "error", "stopped"):  # it's a real answer: let it all through
                        gate["open"] = True
                        for h in gate["held"]:
                            real_emit(**h)
                        gate["held"].clear()
                    else:
                        gate["held"].append(kw)
                        return
                if gate["ignored"]:
                    return
                real_emit(**kw)

            def send_ev(kw):
                try:
                    self.wfile.write(b"data: " + json.dumps(kw).encode() + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    alive[0] = False

            def real_emit(**kw):
                t = kw.get("t")
                if t == "delta":
                    rec["him"] += kw.get("text", "")
                elif t == "break" and rec["him"] and not rec["him"].endswith("\n\n"):
                    rec["him"] += "\n\n"
                elif t == "tool" and len(rec["tools"]) < 30:
                    rec["tools"].append(kw.get("name"))
                elif t == "result":
                    if not rec["him"].strip() and kw.get("text"):
                        rec["him"] = kw["text"]
                    rec["ok"] = not kw.get("is_error")
                elif t == "error":
                    rec["ok"] = False
                    rec["him"] = rec["him"] or kw.get("text", "")
                if not alive[0]:
                    return
                try:
                    self.wfile.write(b"data: " + json.dumps(kw).encode() + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    alive[0] = False
                    stop_proc()

            ST.stopped = False
            partial = bool(CFG["partial_messages"])
            for _ in range(3):
                r = run_claude(prompt, emit, partial)
                if r == "retry_nopartial":
                    partial = False
                    continue
                if r == "retry_fresh":
                    ST.session = None
                    ST.save()
                    emit(t="note", text="previous session couldn't be resumed — started fresh")
                    continue
                break
            if gate["ignored"]:
                return
            rec["session"] = ST.session
            rec["him"] = untag(rec["him"]).strip()[-6000:]
            try:
                hist_add(rec)
            except OSError:
                pass
            emit(t="done")
        finally:
            ST.lock.release()
        return None


def cpu_split(role):
    """Keep his face (the window) and his voice/brain (this server) on different CPU cores, so drawing can never
    starve the voice. Window → first half of the cores, server → second half. Children (the audio player, Kokoro's
    threads, WebKit's processes) inherit it. Off with "cpu_split": false."""
    try:
        cores = sorted(os.sched_getaffinity(0))
        if len(cores) < 4:                     # too few cores to share out: leave everything free
            return None
        half = max(1, len(cores) // 2)
        mine = set(cores[:half]) if role == "window" else set(cores[half:])
        os.sched_setaffinity(0, mine)
        return sorted(mine)
    except (AttributeError, OSError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-port", action="store_true")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--warm", action="store_true", help="download/load the speech model and exit")
    ap.add_argument("--sync-autostart", action="store_true", help="add/remove login startup to match the config")
    ap.add_argument("--stress-voice", action="store_true", help="test the voice for stutters under heavy load")
    ap.add_argument("--silent", action="store_true", help="with --stress-voice: don't actually play the sound")
    args = ap.parse_args()
    if args.sync_autostart:
        sync_autostart()
        print("autostart:", "on" if CFG.get("autostart") else "off")
        return None
    if args.doctor:
        return doctor()
    if args.warm:
        m = whisper()
        print(f"speech model ready: {m._void_name} on {m._void_dev}")
        return None
    if CFG.get("cpu_split", True) and not args.print_port:
        got = cpu_split("server")
        if got:
            print(f"[void] voice & brain on CPU cores {got[0]}-{got[-1]} (the window gets the others)", flush=True)
    if args.stress_voice:
        sys.exit(stress_voice(silent=args.silent))
    if args.print_port:
        print(CFG["port"])
        return
    if CFG["stt_prewarm"] or CFG.get("wake_word"):
        threading.Thread(target=lambda: _try(whisper), daemon=True).start()
    _wire_quick()
    threading.Thread(target=warm_tts, daemon=True).start()
    Wake.ensure()
    srv = ThreadingHTTPServer(("127.0.0.1", int(CFG["port"])), Handler)
    try:                                       # where askpass.py finds us if sudo cleaned its environment (owner-only)
        rt = DATA_DIR / "runtime.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(rt, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": CFG["port"], "token": TOKEN}, f)
    except OSError:
        pass
    srv.daemon_threads = True
    print(f"[void] http://127.0.0.1:{CFG['port']}/  cwd={ST.cwd}  tts={tts_engine()}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def _try(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"[void] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
