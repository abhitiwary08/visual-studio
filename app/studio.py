"""Studio: validated editing settings, channel branding, the music library and audio analysis.

Two kinds of settings, both non-destructive (the owner's files are never changed):
- per scene, saved inside the script as `scene["edit"]` (see normalize_edit);
- per video, saved on the job as `job["studio"]` (see normalize_studio).
Anything invalid is dropped or clamped, never trusted.
"""
from __future__ import annotations

import array
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from . import config, media, render

MEDIA_FILE_RE = re.compile(r"[0-9a-f]{12}\.(?:mp4|mov|webm|mkv|jpg|jpeg|png|webp)")
VIDEO_EXTS = ("mp4", "mov", "webm", "mkv")
IMAGE_EXTS = ("jpg", "jpeg", "png", "webp")
MAX_VIDEO_BYTES = 500 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_MUSIC_BYTES = 100 * 1024 * 1024

MOTIONS = {"none": "No movement", "zoom_in": "Slow zoom in", "zoom_out": "Slow zoom out",
           "pan_left": "Pan left", "pan_right": "Pan right"}
LOOKS = {"none": "Original", "warm": "Warm", "cool": "Cool", "soft": "Soft", "vivid": "Vivid",
         "mono": "Black and white"}
OVERLAY_POSITIONS = {"top": "Top", "middle": "Middle", "bottom": "Bottom"}
CAPTION_STYLES = {"box": "Dark box", "outline": "Outlined text", "highlight": "Bold yellow"}
CAPTION_POSITIONS = {"bottom": "Bottom", "middle": "Middle", "top": "Top"}
CAPTION_SIZES = {"small": 0.8, "medium": 1.0, "large": 1.25}
POLISH_LEVELS = {"off": "Off", "light": "Light", "studio": "Studio"}
DUCKING = {"off": "Off", "gentle": "Gentle", "strong": "Strong"}
QUALITY = {"small": ("Small file", 24), "standard": ("Standard", 20), "high": ("High", 17)}
LUFS_CHOICES = {-14.0: "-14 LUFS: YouTube, Instagram, Facebook", -16.0: "-16 LUFS: softer, for meditation"}
CORNERS = {"tr": "Top right", "tl": "Top left", "br": "Bottom right", "bl": "Bottom left"}

BRANDING_FILE = config.CONFIG_DIR / "branding.json"
LOGO_FILE = config.CONFIG_DIR / "logo.png"
DEFAULTS_FILE = config.CONFIG_DIR / "studio_defaults.json"
LICENSE_FILE = config.MUSIC_DIR / "licenses.json"


# ----------------------------------------------------------------- helpers ----
def _f(value, lo: float, hi: float, default=None):
    """A float clamped to [lo, hi]; `default` when it is not a number."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x != x:  # NaN
        return default
    return max(lo, min(hi, x))


def _choice(value, allowed, default):
    return value if value in allowed else default


# ------------------------------------------------------------- scene edits ----
def normalize_edit(raw) -> dict:
    """Clean one scene's edit settings. Only non-default values are kept, so scripts stay small."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    bg_file = str(raw.get("bg_file") or "").strip()
    if MEDIA_FILE_RE.fullmatch(bg_file):
        out["bg_file"] = bg_file
    start = _f(raw.get("bg_start"), 0, 3600, 0.0)
    if start:
        out["bg_start"] = round(start, 2)
    speed = _f(raw.get("bg_speed"), 0.5, 2.0, 1.0)
    if abs(speed - 1.0) > 1e-6:
        out["bg_speed"] = round(speed, 2)
    motion = _choice(raw.get("motion"), MOTIONS, "none")
    if motion != "none":
        out["motion"] = motion
    look = _choice(raw.get("look"), LOOKS, "none")
    if look != "none":
        out["look"] = look
    brightness = _f(raw.get("brightness"), -0.3, 0.3)
    if brightness is not None and raw.get("brightness") is not None:
        out["brightness"] = round(brightness, 3)
    saturation = _f(raw.get("saturation"), 0.0, 2.0)
    if saturation is not None and raw.get("saturation") is not None:
        out["saturation"] = round(saturation, 3)
    gain = _f(raw.get("voice_gain"), -12, 12, 0.0)
    if abs(gain) > 1e-6:
        out["voice_gain"] = round(gain, 1)
    v_in = _f(raw.get("voice_in"), 0, 3600, 0.0)
    v_out = _f(raw.get("voice_out"), 0, 3600, 0.0)
    if v_in:
        out["voice_in"] = round(v_in, 2)
    if v_out and v_out > v_in + 0.2:
        out["voice_out"] = round(v_out, 2)
    ov = raw.get("overlay")
    if isinstance(ov, dict):
        text = str(ov.get("text") or "").strip()[:120]
        if text:
            end = _f(ov.get("end"), 0, 3600, 0.0)
            start = _f(ov.get("start"), 0, 3600, 0.0)
            out["overlay"] = {"text": text, "pos": _choice(ov.get("pos"), OVERLAY_POSITIONS, "top"),
                              "start": round(start, 2), "end": round(end, 2) if end > start else 0.0}
    return out


# ---------------------------------------------------------- video settings ----
def default_studio() -> dict:
    return {
        "captions": {"style": "box", "position": "bottom", "size": "medium"},
        "transition": {"fade": 0.3},
        "logo": {"show": None},  # None: follow the channel branding setting
        "audio": {
            "voice_polish": "off", "master": True, "lufs": -14.0,
            "music": {"track": "", "volume": None, "ducking": "strong", "fade_in": 2.0, "fade_out": 3.0,
                      "start": 0.0},
        },
        "thumbnail": {"scene": ""},
        "export": {"quality": "standard", "audio_mp3": False},
    }


def normalize_studio(raw) -> dict:
    """Merge settings over the defaults, clamping everything; unknown keys are dropped."""
    d = default_studio()
    raw = raw if isinstance(raw, dict) else {}

    cap = raw.get("captions") if isinstance(raw.get("captions"), dict) else {}
    d["captions"] = {"style": _choice(cap.get("style"), CAPTION_STYLES, "box"),
                     "position": _choice(cap.get("position"), CAPTION_POSITIONS, "bottom"),
                     "size": _choice(cap.get("size"), CAPTION_SIZES, "medium")}

    tr = raw.get("transition") if isinstance(raw.get("transition"), dict) else {}
    d["transition"] = {"fade": round(_f(tr.get("fade"), 0.0, 1.0, 0.3), 2)}

    logo = raw.get("logo") if isinstance(raw.get("logo"), dict) else {}
    d["logo"] = {"show": logo.get("show") if isinstance(logo.get("show"), bool) else None}

    au = raw.get("audio") if isinstance(raw.get("audio"), dict) else {}
    mu = au.get("music") if isinstance(au.get("music"), dict) else {}
    track = str(mu.get("track") or "").strip()
    if len(track) > 120 or any(c in track for c in "/\\") or ".." in track:
        track = ""
    volume = _f(mu.get("volume"), 0.0, 1.0)
    lufs = _f(au.get("lufs"), -23.0, -9.0, -14.0)
    d["audio"] = {
        "voice_polish": _choice(au.get("voice_polish"), POLISH_LEVELS, "off"),
        "master": au["master"] if isinstance(au.get("master"), bool) else True,
        "lufs": min(LUFS_CHOICES, key=lambda c: abs(c - lufs)),
        "music": {
            "track": track,
            "volume": round(volume, 3) if mu.get("volume") is not None and volume is not None else None,
            "ducking": _choice(mu.get("ducking"), DUCKING, "strong"),
            "fade_in": round(_f(mu.get("fade_in"), 0.0, 10.0, 2.0), 1),
            "fade_out": round(_f(mu.get("fade_out"), 0.0, 10.0, 3.0), 1),
            "start": round(_f(mu.get("start"), 0.0, 600.0, 0.0), 1),
        },
    }

    th = raw.get("thumbnail") if isinstance(raw.get("thumbnail"), dict) else {}
    scene = str(th.get("scene") or "")
    d["thumbnail"] = {"scene": scene if re.fullmatch(r"s\d{2,5}", scene) else ""}

    ex = raw.get("export") if isinstance(raw.get("export"), dict) else {}
    d["export"] = {"quality": _choice(ex.get("quality"), QUALITY, "standard"),
                   "audio_mp3": bool(ex.get("audio_mp3"))}
    return d


def effective(job: dict) -> dict:
    """The settings a render uses. Videos made before the studio existed keep their old sound (no
    loudness mastering) until their studio settings are saved."""
    raw = job.get("studio")
    if raw is None:
        return normalize_studio({"audio": {"master": False}})
    return normalize_studio(raw)


def defaults_for_new() -> dict:
    """Settings a new video starts with: the owner's saved defaults, else the built-in ones."""
    try:
        return normalize_studio(json.loads(DEFAULTS_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return default_studio()


def save_defaults(cfg: dict) -> None:
    DEFAULTS_FILE.write_text(json.dumps(normalize_studio(cfg), indent=2), encoding="utf-8")


def crf_for(cfg: dict) -> int:
    return QUALITY[cfg["export"]["quality"]][1]


def caption_y(H: int, orientation: str, position: str) -> int:
    """Top edge of the caption area for a position (bottom is where captions always were)."""
    frac = {"bottom": 0.66 if orientation == "portrait" else 0.80,
            "middle": 0.44 if orientation == "portrait" else 0.46,
            "top": 0.09 if orientation == "portrait" else 0.07}[position]
    return int(H * frac)


# ---------------------------------------------------------------- branding ----
def branding() -> dict:
    try:
        data = json.loads(BRANDING_FILE.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"has_logo": LOGO_FILE.exists(), "corner": _choice(data.get("corner"), CORNERS, "tr"),
            "size": round(_f(data.get("size"), 0.05, 0.30, 0.12), 3),
            "opacity": round(_f(data.get("opacity"), 0.3, 1.0, 0.85), 2), "auto": bool(data.get("auto"))}


def save_branding(**changes) -> dict:
    cur = branding()
    cur.update({k: v for k, v in changes.items() if k in ("corner", "size", "opacity", "auto") and v is not None})
    BRANDING_FILE.write_text(json.dumps({k: cur[k] for k in ("corner", "size", "opacity", "auto")}, indent=2),
                             encoding="utf-8")
    return branding()


def save_logo(data: bytes) -> None:
    """Store the channel logo as a PNG (at most 600 px wide). Raises ValueError if it is not an image."""
    import io

    from PIL import Image

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("The logo must be under 25 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ValueError("That file is not a picture the studio can read (use PNG, JPG or WebP).") from e
    img = img.convert("RGBA")
    img.thumbnail((600, 600))
    img.save(LOGO_FILE, "PNG")


def delete_logo() -> None:
    LOGO_FILE.unlink(missing_ok=True)


def effective_logo(cfg: dict) -> dict | None:
    b = branding()
    show = cfg["logo"]["show"]
    show = b["auto"] if show is None else show
    if not (show and b["has_logo"]):
        return None
    return {"path": LOGO_FILE, "corner": b["corner"], "size": b["size"], "opacity": b["opacity"],
            "stamp": f"{LOGO_FILE.stat().st_mtime_ns}"}


# --------------------------------------------------------- uploaded media ----
def save_job_media(job_dir: Path, data: bytes, ext: str) -> dict:
    """Store a scene background the owner uploaded (video or photo) under the job folder, named by
    content. It must really be playable, otherwise it is deleted again."""
    ext = ext.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    is_video = ext in VIDEO_EXTS
    if not (is_video or ext in IMAGE_EXTS):
        raise ValueError(f"Use one of: {', '.join(VIDEO_EXTS + IMAGE_EXTS)}.")
    if len(data) < 1000:
        raise ValueError("That file is empty or too small.")
    if len(data) > (MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES):
        raise ValueError("Videos must be under 500 MB and photos under 25 MB.")
    name = f"{hashlib.sha1(data).hexdigest()[:12]}.{ext}"
    folder = job_dir / "media"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    try:
        if is_video:
            info = media.probe(path)
            stream = next((x for x in info["streams"] if x.get("codec_type") == "video"), None)
            if stream is None:
                raise ValueError("No picture found in that file.")
            duration = float(info.get("format", {}).get("duration") or 0)
            if duration < 0.5:
                raise ValueError("That video is too short to use.")
            width, height = stream.get("width"), stream.get("height")
        else:
            # ffprobe takes a picture's extension on trust, so a renamed file would pass; decode it instead.
            from PIL import Image

            with Image.open(path) as img:
                img.load()
                width, height = img.size
            duration = 0.0
    except ValueError:
        path.unlink(missing_ok=True)
        raise
    except Exception as e:  # not playable / not a picture
        path.unlink(missing_ok=True)
        raise ValueError("That file could not be read as a video or photo.") from e
    return {"bg_file": name, "kind": "video" if is_video else "image", "duration": round(duration, 1),
            "width": width, "height": height}


# ------------------------------------------------------------ music library ----
_duration_cache: dict[tuple[str, int], float] = {}


def _licenses() -> dict:
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def library_license(name: str) -> str:
    return str(_licenses().get(name, "")).strip()


def music_path(name: str) -> Path | None:
    """The library file with exactly this name (never builds a path from the request)."""
    return next((p for p in render.list_music() if p.name == name), None)


def library(used: str = "") -> list[dict]:
    lic = _licenses()
    out = []
    for p in render.list_music():
        key = (p.name, p.stat().st_mtime_ns)
        if key not in _duration_cache:
            try:
                _duration_cache[key] = round(media.duration(p), 1)
            except media.MediaError:
                _duration_cache[key] = 0.0
        out.append({"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1), "duration": _duration_cache[key],
                    "license": str(lic.get(p.name, "")), "used": p.name == used})
    return out


def safe_music_name(name: str) -> str:
    base = Path(str(name).replace("\\", "/")).name.strip()
    base = re.sub(r"[^\w\-. ()ऀ-ॿ]", "_", base).strip(" .")
    stem, dot, ext = base.rpartition(".")
    if not dot or f".{ext.lower()}" not in render.MUSIC_EXTS or not stem:
        raise ValueError(f"Use an audio file ({', '.join(sorted(e.lstrip('.') for e in render.MUSIC_EXTS))}).")
    return f"{stem[:70]}.{ext.lower()}"


def save_music(name: str, data: bytes) -> dict:
    name = safe_music_name(name)
    if len(data) < 2000:
        raise ValueError("That file is empty or too small.")
    if len(data) > MAX_MUSIC_BYTES:
        raise ValueError("Tracks must be under 100 MB.")
    target = config.MUSIC_DIR / name
    n = 1
    while target.exists():  # never overwrite someone's track
        n += 1
        target = config.MUSIC_DIR / f"{Path(name).stem}-{n}{Path(name).suffix}"
    target.write_bytes(data)
    try:
        info = media.probe(target)
        if not any(x.get("codec_type") == "audio" for x in info["streams"]):
            raise ValueError("No sound found in that file.")
        if float(info.get("format", {}).get("duration") or 0) < 1.0:
            raise ValueError("That track is too short or could not be read.")
    except (media.MediaError, KeyError, ValueError) as e:
        target.unlink(missing_ok=True)
        raise ValueError(str(e) if isinstance(e, ValueError) else "That file could not be read as audio.") from e
    return next(t for t in library() if t["name"] == target.name)


def delete_music(name: str) -> bool:
    p = music_path(name)
    if not p:
        return False
    p.unlink()
    lic = _licenses()
    if lic.pop(name, None) is not None:
        LICENSE_FILE.write_text(json.dumps(lic, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def set_license(name: str, note: str) -> None:
    if not music_path(name):
        raise KeyError(name)
    lic = _licenses()
    note = note.strip()[:300]
    if note:
        lic[name] = note
    else:
        lic.pop(name, None)
    LICENSE_FILE.write_text(json.dumps(lic, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------ audio analysis ----
def waveform(path: Path, buckets: int = 600) -> dict:
    """Peak levels (0 to 1) of an audio file, for drawing, and its length in seconds."""
    try:
        proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000",
                               "-f", "s16le", "-"], capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise media.MediaError("Could not read the audio (is ffmpeg installed?).") from e
    if proc.returncode != 0 or len(proc.stdout) < 4:
        raise media.MediaError("Could not read the audio.")
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    per = max(1, n // buckets)
    peaks = []
    for i in range(0, n, per):
        chunk = samples[i:i + per]
        peaks.append(round(max(max(chunk), -min(chunk)) / 32768, 3))
    return {"duration": round(n / 8000, 2), "peaks": peaks[:buckets + 1]}
