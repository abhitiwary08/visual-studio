"""Stock footage from Pexels (free API key) with caching and graceful fallbacks."""
from __future__ import annotations

import re
from pathlib import Path

import requests

from . import config

API = "https://api.pexels.com/videos/search"
GENERIC_FALLBACKS = ["sunrise sky", "river flowing", "forest path mist", "candle flame", "mountain clouds", "lotus water"]
MAX_BYTES = 120 * 1024 * 1024


class VisualsError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.pexels_key())


def _headers() -> dict:
    return {"Authorization": config.pexels_key(), "User-Agent": "yt-automation/1.0"}


def search(query: str, orientation: str, per_page: int = 12) -> list[dict]:
    r = requests.get(
        API,
        headers=_headers(),
        params={"query": query, "orientation": orientation, "per_page": per_page, "size": "medium"},
        timeout=30,
    )
    if r.status_code == 401:
        raise VisualsError("Pexels rejected the API key (401). Check PEXELS_API_KEY.")
    if r.status_code == 429:
        raise VisualsError("Pexels rate limit reached (429). Try again later.")
    r.raise_for_status()
    return r.json().get("videos", [])


def _pick_file(video: dict, target_w: int, target_h: int) -> dict | None:
    files = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")
    ]
    if not files:
        return None
    target = target_w * target_h
    # Prefer files at least as large as the target, but not absurdly larger (4K is overkill).
    good = [f for f in files if f["width"] * f["height"] >= target * 0.85 and f["width"] * f["height"] <= target * 2.3]
    pool = good or files
    return min(pool, key=lambda f: abs(f["width"] * f["height"] - target))


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        size = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    tmp.unlink(missing_ok=True)
                    raise VisualsError("Clip too large; skipped.")
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def _simplify(query: str) -> str:
    words = re.findall(r"[A-Za-z]+", query)
    return " ".join(words[:2]) if len(words) > 2 else query


def fetch_clip(query: str, size: tuple[int, int], orientation: str, used: set[int], min_seconds: float = 0) -> dict | None:
    """Return {"path", "id", "credit"} for a clip matching `query`, or None.

    `used` holds Pexels IDs already used in this video so scenes do not repeat.
    """
    if not available():
        return None
    w, h = size
    tried = []
    for q in (query, _simplify(query)):
        if q and q not in tried:
            tried.append(q)
    for q in GENERIC_FALLBACKS:
        tried.append(q)

    cache = config.CACHE_DIR / "pexels"
    for q in tried:
        try:
            results = search(q, orientation)
        except requests.RequestException:
            continue
        # Prefer clips long enough for the scene (less looping), then any.
        ordered = sorted(results, key=lambda v: (v.get("duration", 0) < min_seconds, v["id"] in used))
        for v in ordered:
            if v["id"] in used:
                continue
            f = _pick_file(v, w, h)
            if not f:
                continue
            path = cache / f"{v['id']}_{f['width']}x{f['height']}.mp4"
            try:
                if not path.exists():
                    _download(f["link"], path)
            except (requests.RequestException, VisualsError):
                continue
            used.add(v["id"])
            user = v.get("user", {}) or {}
            return {
                "path": str(path),
                "id": v["id"],
                "credit": f"{user.get('name', 'Pexels')} ({v.get('url', 'pexels.com')})",
            }
    return None
