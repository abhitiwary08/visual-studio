"""Burned-in Hindi captions: page splitting, timing and PNG rendering."""
from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

from . import textutil

_CLAUSE_SPLIT = re.compile(r"(?<=[।\.\?\!;:,—])\s+")


def split_pages(text: str, max_words: int) -> list[str]:
    pages: list[str] = []
    for clause in _CLAUSE_SPLIT.split(text.strip()):
        words = clause.split()
        if not words:
            continue
        n = math.ceil(len(words) / max_words)
        per = math.ceil(len(words) / n)
        for i in range(0, len(words), per):
            pages.append(" ".join(words[i : i + per]))
    return pages


def time_pages(pages: list[str], speech_seconds: float, lead: float = 0.1) -> list[tuple[str, float, float]]:
    """Distribute pages across the spoken part of a scene by character weight."""
    if not pages:
        return []
    weights = [max(1, len(p.replace(" ", ""))) for p in pages]
    total = sum(weights)
    avail = max(0.5, speech_seconds - lead)
    t = lead
    out = []
    for p, wgt in zip(pages, weights):
        d = avail * wgt / total
        out.append((p, t, t + d))
        t += d
    return out


def render_caption_png(text: str, out_path: Path, frame_w: int, frame_h: int, orientation: str,
                       style: str = "box", scale: float = 1.0) -> tuple[Path, int, int]:
    """One caption page as a transparent PNG. Styles: "box" (white on a dark box), "outline" (white with
    a black outline) and "highlight" (bold yellow with an outline). `scale` makes the text bigger/smaller."""
    if orientation == "portrait":
        font_size, max_w = int(frame_h * 0.030 * scale), int(frame_w * 0.88)
    else:
        font_size, max_w = int(frame_h * 0.044 * scale), int(frame_w * 0.80)
    pad_x, pad_y = int(font_size * 0.7), int(font_size * 0.45)
    stroke = 0 if style == "box" else max(2, int(font_size * 0.09))

    font, lines, lh = textutil.fit_block(
        text, max_w - 2 * pad_x, int(font_size * 1.4) * 3, bold=True, start=font_size, minimum=int(font_size * 0.7),
        spacing=1.15,
    )
    text_w = int(max(font.getlength(l) for l in lines))
    box_w, box_h = text_w + 2 * pad_x + 2 * stroke, len(lines) * lh + 2 * pad_y + 2 * stroke

    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if style == "box":
        d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=int(font_size * 0.45), fill=(0, 0, 0, 150))
    color = (255, 221, 85, 255) if style == "highlight" else (255, 255, 255, 255)
    y = pad_y + stroke
    for line in lines:
        d.text((box_w // 2, y), line, font=font, fill=color, anchor="ma",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += lh
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path, box_w, box_h


def render_overlay_png(text: str, out_path: Path, frame_w: int, frame_h: int, orientation: str,
                       pos: str = "top") -> tuple[Path, int, int, int]:
    """On-screen text (a title or chapter name) as a transparent PNG: large cream text with an outline.
    Returns (path, width, height, y) where y is the top edge to place it at for `pos`."""
    font_size = int(frame_h * (0.05 if orientation == "portrait" else 0.065))
    max_w = int(frame_w * 0.82)
    stroke = max(3, int(font_size * 0.08))
    font, lines, lh = textutil.fit_block(
        text, max_w - 2 * stroke, int(font_size * 1.4) * 3, bold=True, start=font_size, minimum=int(font_size * 0.6),
        spacing=1.15,
    )
    text_w = int(max(font.getlength(l) for l in lines))
    w, h = text_w + 4 * stroke, len(lines) * lh + 4 * stroke
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 2 * stroke
    for line in lines:
        d.text((w // 2, y), line, font=font, fill=(255, 244, 224, 255), anchor="ma",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += lh
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    top = {"top": int(frame_h * 0.07), "middle": (frame_h - h) // 2, "bottom": int(frame_h * 0.70)}[pos]
    return out_path, w, h, max(0, min(top, int(frame_h * 0.96) - h))
