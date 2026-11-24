"""Generated visuals: gradient backgrounds, shloka / title slides."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from . import textutil

# Palette: deep indigo night, saffron glow, gold accents, warm cream text.
INDIGO_TOP = (14, 32, 66)
INDIGO_BOTTOM = (4, 8, 20)
SAFFRON = (255, 153, 51)
GOLD = (242, 193, 78)
GOLD_SOFT = (245, 213, 138)
CREAM = (255, 244, 224)

# Visual themes. Each video uses one, so a channel does not look like a single template.
# Keys: top/bottom (background gradient), glow, accent (headings, ornament), soft (meaning text),
# text (main text), mark (watermark glyph).
THEMES = {
    "indigo_night": {"label": "Indigo night", "top": INDIGO_TOP, "bottom": INDIGO_BOTTOM, "glow": SAFFRON,
                     "accent": GOLD, "soft": GOLD_SOFT, "text": CREAM, "mark": "ॐ"},
    "saffron_dawn": {"label": "Saffron dawn", "top": (92, 30, 18), "bottom": (28, 8, 6), "glow": (255, 170, 60),
                     "accent": (255, 200, 90), "soft": (255, 222, 170), "text": (255, 246, 230), "mark": "ॐ"},
    "forest_ashram": {"label": "Forest ashram", "top": (16, 58, 44), "bottom": (4, 18, 14), "glow": (214, 190, 96),
                      "accent": (232, 202, 110), "soft": (210, 228, 190), "text": (246, 244, 226), "mark": "ॐ"},
    "lotus_dusk": {"label": "Lotus dusk", "top": (70, 22, 64), "bottom": (20, 6, 24), "glow": (255, 120, 150),
                   "accent": (255, 190, 150), "soft": (250, 210, 220), "text": (255, 242, 240), "mark": "॥"},
    "ganga_river": {"label": "Ganga river", "top": (10, 60, 82), "bottom": (2, 16, 28), "glow": (120, 210, 230),
                    "accent": (240, 205, 120), "soft": (196, 230, 236), "text": (240, 250, 252), "mark": "ॐ"},
}
DEFAULT_THEME = "indigo_night"


def theme(name: str | None) -> dict:
    return THEMES.get(name or "", THEMES[DEFAULT_THEME])


def default_theme_for(seed: str) -> str:
    """Stable per-video choice so consecutive videos rotate through the themes."""
    keys = list(THEMES)
    return keys[sum(seed.encode("utf-8")) % len(keys)]

# Fraction of the frame height kept free at the bottom for burned-in captions.
# Portrait captions sit higher (about 66% down) to stay clear of the Shorts/Reels UI,
# so slides must end above that.
CAPTION_ZONE = {"landscape": 0.22, "portrait": 0.40}


def gradient_bg(
    w: int, h: int, top=INDIGO_TOP, bottom=INDIGO_BOTTOM, glow=SAFFRON,
    glow_strength: float = 0.30, glow_center=(0.5, 0.40),
) -> Image.Image:
    grad = Image.linear_gradient("L").resize((w, h), Image.BILINEAR)
    base = ImageOps.colorize(grad, black=top, white=bottom).convert("RGB")

    rw, rh = int(w * 1.15), int(h * 0.85)
    # radial_gradient is black at the centre and only reaches white at the corners, so the
    # inverted mask is still non-zero at the edge midpoints. Clamp it so the glow fades to
    # exactly zero before the paste boundary (otherwise a visible rectangle appears).
    radial = ImageOps.invert(Image.radial_gradient("L")).point(
        lambda v: max(0, int((v - 85) * 255 / 170))
    ).resize((rw, rh), Image.BILINEAR)
    mask = Image.new("L", (w, h), 0)
    cx, cy = int(w * glow_center[0]), int(h * glow_center[1])
    mask.paste(radial, (cx - rw // 2, cy - rh // 2))
    mask = mask.point(lambda v: int(v * glow_strength))
    return Image.composite(Image.new("RGB", (w, h), glow), base, mask)


def _ornament(draw: ImageDraw.ImageDraw, cx: int, y: int, half: int, color=GOLD) -> None:
    draw.line([(cx - half, y), (cx - 18, y)], fill=color, width=3)
    draw.line([(cx + 18, y), (cx + half, y)], fill=color, width=3)
    d = 8
    draw.polygon([(cx, y - d), (cx + d, y), (cx, y + d), (cx - d, y)], fill=color)


def render_slide(
    out_path: Path, size: tuple[int, int], *, heading: str = "", text: str = "", sub: str = "",
    orientation: str = "landscape", theme_name: str | None = None,
) -> Path:
    w, h = size
    t = theme(theme_name)
    img = gradient_bg(w, h, top=t["top"], bottom=t["bottom"], glow=t["glow"]).convert("RGBA")

    # Faint Om watermark behind the text.
    try:
        wm_font = textutil.load_font(int(h * 0.62), bold=True)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((w // 2, int(h * 0.42)), t["mark"], font=wm_font, fill=(*t["glow"], 22), anchor="mm")
        img = Image.alpha_composite(img, layer)
    except Exception:
        pass

    draw = ImageDraw.Draw(img)
    mx = int(w * 0.09)
    max_w = w - 2 * mx
    top = int(h * 0.07)
    bottom_limit = int(h * (1 - CAPTION_ZONE.get(orientation, 0.22)))

    y = top
    if heading:
        hf, hlines, hlh = textutil.fit_block(
            heading, max_w, int(h * 0.12), bold=True, start=int(h * 0.05), minimum=int(h * 0.03)
        )
        for line in hlines:
            draw.text((w // 2, y), line, font=hf, fill=t["accent"], anchor="ma")
            y += hlh
        y += int(h * 0.012)
        _ornament(draw, w // 2, y, int(w * 0.09), color=t["accent"])
        y += int(h * 0.035)

    region_h = bottom_limit - y
    if text and sub:
        main_h, sub_h = int(region_h * 0.58), int(region_h * 0.36)
    elif text:
        main_h, sub_h = int(region_h * 0.95), 0
    else:
        main_h, sub_h = 0, int(region_h * 0.95)

    block_gap = int(h * 0.03)
    if text:
        mf, mlines, mlh = textutil.fit_block(
            text, max_w, main_h, bold=True, start=int(h * 0.095), minimum=int(h * 0.04), spacing=1.22
        )
        used = len(mlines) * mlh
        yy = y + max(0, (main_h - used) // 2)
        for line in mlines:
            draw.text((w // 2, yy), line, font=mf, fill=t["text"], anchor="ma")
            yy += mlh
        y = y + main_h + block_gap
    if sub:
        sf, slines, slh = textutil.fit_block(
            sub, max_w, sub_h, bold=False, start=int(h * 0.05), minimum=int(h * 0.028), spacing=1.25
        )
        used = len(slines) * slh
        yy = y + max(0, (sub_h - used) // 2) if not text else y
        for line in slines:
            draw.text((w // 2, yy), line, font=sf, fill=t["soft"], anchor="ma")
            yy += slh

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, quality=95)
    return out_path


def render_background(out_path: Path, size: tuple[int, int], variant: int = 0, theme_name: str | None = None) -> Path:
    """Text-free fallback background (used when no stock footage is available)."""
    w, h = size
    t = theme(theme_name)
    centers = [(0.5, 0.40), (0.3, 0.55), (0.7, 0.35), (0.5, 0.65)]
    img = gradient_bg(w, h, top=t["top"], bottom=t["bottom"], glow=t["glow"],
                      glow_center=centers[variant % len(centers)], glow_strength=0.34)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=92)
    return out_path
