"""Makes VoidSetup.exe's pictures from Void's real app icon (app/static/icon.png): the tall picture on the
welcome/finish pages, the small one up top, in every size Inno Setup picks from for the screen's scaling, and the
setup icon. Needs Pillow. Run from the repo root:
    python installer/make_art.py
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
ICON = REPO / "app" / "static" / "icon.png"
OUT = Path(__file__).parent / "art"
SCALES = [1, 1.25, 1.5, 1.75, 2, 2.25, 2.5]
TOP, BOTTOM = (12, 18, 16), (22, 35, 30)                     # dark green-black, like his window's backdrop
TEXT, MINT = (238, 246, 242), (127, 209, 176)


def font(size):
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def wizard(icon, scale):
    w, h = round(164 * scale), round(314 * scale)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    size = int(w * 0.92)
    him = icon.resize((size, size), Image.LANCZOS)
    img.paste(him, ((w - size) // 2, int(h * 0.38 - size / 2)), him)
    d.text((w / 2, h * 0.72), "Void", font=font(int(w * 0.2)), fill=TEXT, anchor="mm")
    d.text((w / 2, h * 0.81), "voice for Claude Code", font=font(int(w * 0.062)), fill=MINT, anchor="mm")
    return img


def soft_edges(icon):
    """His glow reaches the icon's edges; fade it out in a circle so no square shows on a dark background."""
    n = icon.size[0]
    fade = Image.new("L", icon.size, 0)
    d = ImageDraw.Draw(fade)
    for i in range(64):                                       # 255 inside ~70% of the radius, 0 at the edge
        t = i / 63
        r = n / 2 * (1 - 0.3 * t)
        d.ellipse((n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r), fill=int(255 * t))
    out = icon.copy()
    out.putalpha(Image.composite(icon.getchannel("A"), Image.new("L", icon.size, 0), fade))
    return out


def main():
    icon = Image.open(ICON).convert("RGBA")
    icon_dark = soft_edges(icon)
    OUT.mkdir(exist_ok=True)
    for sc in SCALES:
        tag = int(sc * 100)
        wizard(icon_dark, sc).save(OUT / f"wizard-{tag}.png")
        s = round(55 * sc)
        icon.resize((s, s), Image.LANCZOS).save(OUT / f"small-{tag}.png")
    icon.save(OUT / "void.ico", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("wrote", len(list(OUT.iterdir())), "files to", OUT)


if __name__ == "__main__":
    sys.exit(main())
