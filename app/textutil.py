"""Devanagari-safe text measuring, wrapping and fitting with Pillow (+ Raqm shaping)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont, features

from . import config

FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2", ".ttc"}


class FontError(RuntimeError):
    pass


def raqm_available() -> bool:
    return bool(features.check("raqm"))


def _system_candidates() -> list[Path]:
    hints = ("devanagari", "hind", "mukta", "lohit-deva", "sarala", "tiro", "kalam", "poppins")
    out: list[Path] = []
    for base in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                 Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library/Fonts",
                 Path("C:/Windows/Fonts")):
        if base.exists():
            for p in base.rglob("*"):
                if p.suffix.lower() in FONT_EXTS and any(h in p.name.lower() for h in hints[:4]):
                    out.append(p)
    return out


@lru_cache(maxsize=4)
def find_font(bold: bool = True) -> Path:
    env_name = "FONT_BOLD" if bold else "FONT_REGULAR"
    env_path = config.env(env_name)
    if env_path and Path(env_path).exists():
        return Path(env_path)

    local = sorted(p for p in config.FONT_DIR.glob("*") if p.suffix.lower() in FONT_EXTS)
    key = ("bold", "700") if bold else ("regular", "400")
    for p in local:
        if any(k in p.name.lower() for k in key):
            return p
    if local:
        return local[0]

    sys_fonts = _system_candidates()
    for p in sys_fonts:
        if bold and "bold" in p.name.lower():
            return p
    if sys_fonts:
        return sys_fonts[0]

    raise FontError(
        "No Devanagari font found. Put a font such as Noto Sans Devanagari "
        "(.ttf preferred) in assets/fonts/, or set FONT_BOLD / FONT_REGULAR in .env."
    )


@lru_cache(maxsize=64)
def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = find_font(bold)
    engine = ImageFont.Layout.RAQM if raqm_available() else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(path), int(size), layout_engine=engine)


def line_height(font: ImageFont.FreeTypeFont, spacing: float = 1.18) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * spacing)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Greedy word wrap that keeps explicit newlines."""
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if font.getlength(trial) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def fit_block(
    text: str, max_w: int, max_h: int, *, bold: bool = True, start: int = 96, minimum: int = 28,
    spacing: float = 1.18,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Largest font (<= start) whose wrapped text fits in max_w x max_h.
    Returns (font, lines, line_height)."""
    size = start
    while True:
        font = load_font(size, bold)
        lines = wrap(text, font, max_w)
        lh = line_height(font, spacing)
        too_wide = any(font.getlength(l) > max_w for l in lines)
        if (len(lines) * lh <= max_h and not too_wide) or size <= minimum:
            return font, lines, lh
        size -= 2
