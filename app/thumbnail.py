"""YouTube thumbnail (1280x720): darkened footage frame + big Hindi title."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import slides, textutil

W, H = 1280, 720


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), method=Image.LANCZOS, centering=(0.5, 0.5))


def render_thumbnail(out_path: Path, text: str, frame: Path | None = None, channel: str = "",
                     theme_name: str | None = None) -> Path:
    t = slides.theme(theme_name)
    if frame and Path(frame).exists():
        bg = _cover(Image.open(frame), W, H)
        bg = bg.filter(ImageFilter.GaussianBlur(1.2))
    else:
        bg = slides.gradient_bg(W, H, top=t["top"], bottom=t["bottom"], glow=t["glow"], glow_center=(0.72, 0.45),
                                glow_strength=0.40)

    # Left-to-right dark gradient so text stays readable on any footage.
    # linear_gradient is black (top) -> white (bottom); ROTATE_270 turns that into white (left) -> black (right).
    shade = Image.linear_gradient("L").transpose(Image.ROTATE_270).resize((W, H), Image.BILINEAR)
    shade = shade.point(lambda v: int(v * 0.80))
    bg = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), bg, shade)
    img = bg.convert("RGBA")

    # Large Om on the right.
    try:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        om = textutil.load_font(int(H * 0.85), bold=True)
        ImageDraw.Draw(layer).text((int(W * 0.80), int(H * 0.50)), t["mark"], font=om, fill=(*t["glow"], 70), anchor="mm")
        img = Image.alpha_composite(img, layer)
    except Exception:
        pass

    d = ImageDraw.Draw(img)
    # Saffron accent bar.
    d.rectangle([0, 0, 18, H], fill=t["glow"])

    mx = 70
    font, lines, lh = textutil.fit_block(text, int(W * 0.62), int(H * 0.66), bold=True, start=170, minimum=70, spacing=1.12)
    total_h = len(lines) * lh
    y = (H - total_h) // 2 - 10
    for i, line in enumerate(lines):
        color = t["accent"] if (i == len(lines) - 1 and len(lines) > 1) else t["text"]
        d.text((mx, y), line, font=font, fill=color, stroke_width=7, stroke_fill=(0, 0, 0))
        y += lh

    if channel:
        cf = textutil.load_font(34, bold=True)
        d.text((mx, H - 62), channel, font=cf, fill=t["accent"], stroke_width=3, stroke_fill=(0, 0, 0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    quality = 92
    while True:
        rgb.save(out_path, quality=quality, optimize=True)
        if out_path.stat().st_size <= 1_900_000 or quality <= 60:  # YouTube limit is 2 MB
            break
        quality -= 8
    return out_path
