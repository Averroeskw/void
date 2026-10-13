"""Draws VoidSetup.exe's wizard pictures (the tall one on the welcome/finish pages and the small one up top) in every
size Inno Setup picks from for the screen's scaling, plus the setup icon. Needs Pillow. Run from the repo root:
    python installer/make_art.py
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent / "art"
SCALES = [1, 1.25, 1.5, 1.75, 2, 2.25, 2.5]
MINT, MINT_LIGHT, PEACH, PUPIL = (61, 155, 116), (127, 209, 176), (243, 221, 208), (47, 143, 106)
SS = 4                                                       # draw big, shrink: smooth edges


def font(size):
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def orb(img, cx, cy, r, glow=True):
    """Void himself: glow, shadow, black glossy ball, peach eyes with green slit pupils."""
    w, h = img.size
    if glow:
        g = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse((cx - r * 1.35, cy - r * 1.35, cx + r * 1.35, cy + r * 1.35), fill=MINT + (150,))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(r * 0.45)))
    s = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(s).ellipse((cx - r * 0.8, cy + r * 1.22, cx + r * 0.8, cy + r * 1.36), fill=(0, 0, 0, 120))
    img.alpha_composite(s.filter(ImageFilter.GaussianBlur(r * 0.08)))
    ball = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ball)
    for i in range(40, 0, -1):                               # radial shading, lighter top-left
        t = i / 40
        c = int(4 + 40 * (1 - t) ** 2.2)
        rr = r * t
        ox, oy = cx - r * 0.25 * (1 - t), cy - r * 0.3 * (1 - t)
        d.ellipse((ox - rr, oy - rr, ox + rr, oy + rr), fill=(c, c + 3, c + 2, 255))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    img.paste(ball, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(42, 58, 52, 255), width=max(1, int(r * 0.02)))
    for ex in (-0.34, 0.34):                                 # eyes, looking a little to the right
        ew, eh = r * 0.29, r * 0.41
        x, y = cx + ex * r, cy - r * 0.08
        d.ellipse((x - ew, y - eh, x + ew, y + eh), fill=PEACH)
        pw, ph, px = r * 0.055, r * 0.145, x + r * 0.11
        d.ellipse((px - pw, y - ph, px + pw, y + ph), fill=PUPIL)


def sparks(img, cx, cy, r):
    d = ImageDraw.Draw(img)
    for ax, ay, a in ((-1.25, -0.75, 150), (1.2, -0.55, 110), (-1.35, 0.55, 90), (1.3, 0.7, 130), (0.55, -1.3, 80)):
        x, y, L = cx + ax * r, cy + ay * r, r * 0.12
        d.rounded_rectangle((x - L / 2, y - L / 7, x + L / 2, y + L / 7), radius=L / 7, fill=MINT_LIGHT + (a,))


def wizard(scale):
    w, h = round(164 * scale), round(314 * scale)
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W, H))
    top, bottom = (12, 18, 16), (22, 35, 30)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    cx, cy, r = W / 2, H * 0.4, W * 0.3
    orb(img, cx, cy, r)
    sparks(img, cx, cy, r)
    d = ImageDraw.Draw(img)
    f = font(int(W * 0.2))
    d.text((cx, H * 0.7), "Void", font=f, fill=(238, 246, 242), anchor="mm")
    f2 = font(int(W * 0.062))
    d.text((cx, H * 0.79), "voice for Claude Code", font=f2, fill=MINT_LIGHT, anchor="mm")
    return img.resize((w, h), Image.LANCZOS).convert("RGB")


def small(scale):
    s = round(55 * scale)
    S = s * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    orb(img, S / 2, S * 0.45, S * 0.33, glow=True)
    return img.resize((s, s), Image.LANCZOS)


def main():
    OUT.mkdir(exist_ok=True)
    for sc in SCALES:
        tag = int(sc * 100)
        wizard(sc).save(OUT / f"wizard-{tag}.png")
        small(sc).save(OUT / f"small-{tag}.png")
    icon = small(4.7)                                        # ~256 px
    icon.save(OUT / "void.ico", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("wrote", len(list(OUT.iterdir())), "files to", OUT)


if __name__ == "__main__":
    sys.exit(main())
