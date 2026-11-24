"""ffmpeg scene rendering, voice processing, loudness mastering and music mixing."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
from pathlib import Path

from . import config, media

FPS = 30
FADE = 0.3
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

# Colour looks. They are applied before the brightness/saturation sliders.
LOOK_FILTERS = {
    "warm": "colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.05:bm=-0.05:rh=0.03:bh=-0.03",
    "cool": "colorbalance=rs=-0.05:bs=0.06:rm=-0.04:bm=0.05:rh=-0.02:bh=0.03",
    "soft": "eq=contrast=0.92:brightness=0.02:saturation=0.88",
    "vivid": "eq=contrast=1.08:saturation=1.3",
    "mono": "hue=s=0,eq=contrast=1.05",
}
# Voice polish. "light" evens out the level; "studio" also warms the tone and adds presence.
POLISH_BASE = "highpass=f=80"
POLISH_CHAINS = {
    "light": "equalizer=f=250:t=q:w=1:g=-1.5,acompressor=threshold=-20dB:ratio=2.2:attack=15:release=200:makeup=2",
    "studio": ("equalizer=f=200:t=q:w=1:g=1.5,equalizer=f=3500:t=q:w=1.2:g=2,"
               "acompressor=threshold=-18dB:ratio=3:attack=8:release=150:makeup=3,alimiter=limit=0.9"),
}
DENOISE = "afftdn=nr=12:nf=-40"
# sidechain threshold and ratio: how far the music dips while someone is speaking
DUCK_SETTINGS = {"gentle": (0.06, 3), "strong": (0.04, 6)}


def short_hash(*parts, n: int = 8) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:n]


# ----------------------------------------------------------------- picture ----
def grade_filter(look: str = "none", brightness: float | None = None, saturation: float | None = None,
                 is_video: bool = True) -> str:
    """Colour filter for a scene's background. With no choices made it is exactly what videos always
    had: a slight darkening of stock footage and nothing for still pictures."""
    parts = []
    if look in LOOK_FILTERS:
        parts.append(LOOK_FILTERS[look])
    b = brightness if brightness is not None else (-0.05 if is_video else 0.0)
    s = saturation if saturation is not None else (1.05 if is_video else 1.0)
    if abs(b) > 1e-9 or abs(s - 1.0) > 1e-9:
        parts.append(f"eq=brightness={b:g}:saturation={s:g}")
    return ",".join(parts)


def zoom_exprs(motion: str, frames: int, amount: float = 0.12) -> tuple[str, str, str]:
    """zoompan expressions (zoom, x, y) for a slow move over `frames` frames."""
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        return f"1+{amount}*on/{frames}", cx, cy
    if motion == "zoom_out":
        return f"{1 + amount}-{amount}*on/{frames}", cx, cy
    span = "(iw-iw/zoom)"
    if motion == "pan_left":
        return f"{1 + amount}", f"{span}*(1-on/{frames})", cy
    return f"{1 + amount}", f"{span}*on/{frames}", cy  # pan_right


def render_scene(
    *, out: Path, size: tuple[int, int], orientation: str, bg_kind: str, bg_path: Path, audio: Path,
    total_dur: float, caption_overlays: list[dict], preset: str | None = None, counter: dict | None = None,
    motion: str = "none", grade: str = "", fade: float = FADE, crf: int = 20,
    overlays: list[dict] | None = None, logo: dict | None = None,
) -> Path:
    """Render one scene: background, narration, timed captions, on-screen text and logo.

    caption_overlays: [{"png", "w", "h", "start", "end", optional "y"}] (y is the top edge in pixels)
    overlays: [{"png", "y", "start", "end"}] on-screen text; end 0 means until the scene ends
    logo: {"path", "corner", "size", "opacity"}
    counter: {"period", "total"} draws a "7 / 108" jaap counter that ticks every `period` seconds.
    """
    W, H = size
    overlays = overlays or []
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if bg_kind == "video":
        cmd += ["-stream_loop", "-1", "-i", str(bg_path)]
    else:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(bg_path)]
    cmd += ["-i", str(audio)]
    still = ["-loop", "1", "-framerate", str(FPS), "-t", f"{total_dur:.3f}", "-i"]
    for item in [*caption_overlays, *overlays]:
        cmd += [*still, str(item["png"])]
    if logo:
        cmd += [*still, str(logo["path"])]

    if bg_kind != "video" and motion != "none":
        # Work at twice the size so the slow move does not jitter, then come back down to the frame size.
        z, x, y = zoom_exprs(motion, max(1, int(round(total_dur * FPS))))
        geom = (f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase,crop={2 * W}:{2 * H},"
                f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},setsar=1")
    else:
        geom = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}"
    chain = [f"[0:v]{geom}" + (f",{grade}" if grade else "") + ",format=yuv420p[v0]"]
    cur, idx = "v0", 2

    default_y = int(H * (0.66 if orientation == "portrait" else 0.80))
    for i, cap in enumerate(caption_overlays):
        y = min(cap.get("y", default_y), H - cap["h"] - int(H * 0.04))
        chain.append(
            f"[{cur}][{idx}:v]overlay=x=(W-w)/2:y={y}:"
            f"enable='between(t,{cap['start']:.3f},{cap['end']:.3f})'[c{i}]"
        )
        cur, idx = f"c{i}", idx + 1
    for j, ov in enumerate(overlays):
        end = ov["end"] if ov.get("end") else total_dur
        chain.append(
            f"[{cur}][{idx}:v]overlay=x=(W-w)/2:y={ov['y']}:enable='between(t,{ov['start']:.3f},{end:.3f})'[t{j}]"
        )
        cur, idx = f"t{j}", idx + 1
    if logo:
        lw, m = max(16, int(W * logo["size"])), int(W * 0.03)
        x = f"W-w-{m}" if logo["corner"] in ("tr", "br") else str(m)
        y = f"H-h-{m}" if logo["corner"] in ("bl", "br") else str(m)
        chain.append(f"[{idx}:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={logo['opacity']}[lg]")
        chain.append(f"[{cur}][lg]overlay=x={x}:y={y}[lo]")
        cur, idx = "lo", idx + 1
    if counter:
        chain.append(f"[{cur}]{counter_filter(counter['period'], counter['total'], W, H)}[vc]")
        cur = "vc"
    f = min(fade, total_dur / 3) if fade > 0 else 0.0
    if f >= 0.02:
        chain.append(f"[{cur}]fade=t=in:st=0:d={f:.2f},fade=t=out:st={max(0.0, total_dur - f):.3f}:d={f:.2f}[vout]")
    else:
        chain.append(f"[{cur}]null[vout]")  # a hard cut: no fades
    chain.append("[1:a]aresample=44100,apad[aout]")

    cmd += [
        "-filter_complex", ";".join(chain),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total_dur:.3f}",
        "-c:v", "libx264", "-preset", preset or config.x264_preset(), "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    media.run(cmd, timeout=1800)
    return out


def prepare_background(src: Path, out: Path, start: float = 0.0, speed: float = 1.0) -> Path:
    """The part of a clip a scene should loop: begin at `start` seconds, at `speed` times normal speed.
    Very large sources are shrunk (long edge 1920) so scenes still render quickly."""
    length = media.duration(src)
    start = max(0.0, min(start, max(0.0, length - 1.0)))
    vf = (f"setpts=PTS/{speed:.4f},fps={FPS},"
          "scale=w='if(gt(iw,ih),min(1920,iw),-2)':h='if(gt(iw,ih),-2,min(1920,ih))'")
    out.parent.mkdir(parents=True, exist_ok=True)
    media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(src), "-an", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)], timeout=1800)
    return out


def _filter_path(path: Path) -> str:
    """A file path as an ffmpeg filter option value (Windows drive colons must be escaped)."""
    return str(Path(path).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "")


def counter_filter(period: float, total: int, W: int, H: int) -> str:
    """drawtext showing which repetition is playing, e.g. "27 / 108", hidden after the last one."""
    from . import textutil

    size = int(H * 0.055)
    end = period * total
    text = f"%{{eif\\:floor(t/{period:.4f})+1\\:d}} / {total}"
    return (
        f"drawtext=fontfile='{_filter_path(textutil.find_font(True))}':text='{text}':"
        f"fontsize={size}:fontcolor=white@0.92:box=1:boxcolor=black@0.35:boxborderw={size // 3}:"
        f"x=w-tw-{int(W * 0.04)}:y={int(H * 0.05)}:enable='lt(t,{end:.3f})'"
    )


# ------------------------------------------------------------------- voice ----
def repeat_audio(src: Path, out: Path, times: int, gap: float) -> tuple[Path, float]:
    """Loop one chant `times` times with `gap` seconds of silence after each.
    Returns (file, period) where period is the length of one chant plus its gap."""
    unit = out.with_name(out.stem + "_unit.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"apad=pad_dur={gap:.3f}",
               "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(unit)], timeout=600)
    period = media.duration(unit)
    media.run(["ffmpeg", "-y", "-v", "error", "-stream_loop", str(max(0, times - 1)), "-i", str(unit),
               "-c", "copy", str(out)], timeout=1800)
    unit.unlink(missing_ok=True)
    return out, period


def prepare_voice(src: Path, out: Path, start: float = 0.0, end: float = 0.0) -> Path:
    """Turn the owner's uploaded/recorded narration into a clean mp3: keep only start..end seconds
    (0 = the whole recording), trim leading and trailing silence, cut rumble, and normalise
    loudness to match the TTS scenes."""
    trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.15"
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    if end > start:
        cmd += ["-t", f"{end - start:.2f}"]
    cmd += ["-i", str(src), "-vn",
            "-af", f"{trim},areverse,{trim},areverse,highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "2", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    media.run(cmd, timeout=600)
    return out


def polish_chain(level: str, gain_db: float = 0.0, denoise: bool = False) -> str:
    """The ffmpeg audio filters for a polish level plus gain; "" when there is nothing to do."""
    parts = []
    if abs(gain_db) > 1e-6:
        parts.append(f"volume={gain_db:g}dB")
    if level in POLISH_CHAINS:
        parts.append(POLISH_BASE)
        if denoise and level == "studio":
            parts.append(DENOISE)
        parts.append(POLISH_CHAINS[level])
    return ",".join(parts)


def polish_voice(src: Path, out: Path, level: str, gain_db: float = 0.0, denoise: bool = False) -> Path:
    chain = polish_chain(level, gain_db, denoise)
    if not chain:
        return src
    out.parent.mkdir(parents=True, exist_ok=True)
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", chain, "-ar", "44100", "-ac", "1",
               "-c:a", "libmp3lame", "-q:a", "2", str(out)], timeout=600)
    return out


# ------------------------------------------------------------- assembly/mix ----
def concat_scenes(scene_files: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lines = []
    for f in scene_files:
        p = str(Path(f).resolve()).replace("'", r"'\''")
        lines.append(f"file '{p}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
               "-movflags", "+faststart", str(out)], timeout=1800)
    lst.unlink(missing_ok=True)
    return out


def ensure_drone(path: Path) -> Path:
    """A plain tanpura-style drone (Sa-Pa-Sa) so the tool works with no music files.
    Frequencies are whole cycles over 100 s, so it loops without a click.
    It is a placeholder: real royalty-free music in assets/music/ sounds far better."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    expr = "0.5*sin(2*PI*130*t)+0.32*sin(2*PI*195*t)+0.26*sin(2*PI*260*t)+0.1*sin(2*PI*390*t)"
    media.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}|{expr}:s=44100:d=100",
        "-af", "lowpass=f=900,tremolo=f=0.2:d=0.3,volume=0.55", "-c:a", "pcm_s16le", str(path),
    ], timeout=300)
    return path


def list_music() -> list[Path]:
    return sorted(p for p in config.MUSIC_DIR.glob("*") if p.suffix.lower() in MUSIC_EXTS)


def pick_music(seed: str, track: str = "") -> tuple[Path, str]:
    """Returns (path, label). `track` names a library file; "" picks one by `seed`. Falls back to the
    generated drone when there is no library or the named track is gone."""
    files = list_music()
    if track and track != "drone":
        named = next((p for p in files if p.name == track), None)
        if named:
            return named, named.name
    elif files and track != "drone":
        choice = random.Random(seed).choice(files)
        return choice, choice.name
    return ensure_drone(config.CACHE_DIR / "drone_100s.wav"), "generated drone (placeholder)"


def mix_music(video_in: Path, music: Path, out: Path, total_dur: float, volume: float,
              ducking: str = "strong", fade_in: float = 2.0, fade_out: float = 3.0, start: float = 0.0) -> Path:
    """Lay music under the narration. `ducking` lowers the music while someone speaks (strong, gentle)
    or leaves it level (off). `start` skips that many seconds into the track."""
    fades = ""
    if fade_in > 0:
        fades += f",afade=t=in:st=0:d={fade_in:g}"
    if fade_out > 0:
        fades += f",afade=t=out:st={max(0.0, total_dur - fade_out):.2f}:d={fade_out:g}"
    music_part = (f"[1:a]atrim=start={start:g}:end={start + total_dur:.2f},asetpts=PTS-STARTPTS,"
                  f"volume={volume}{fades}[m];")
    if ducking in DUCK_SETTINGS:
        thr, ratio = DUCK_SETTINGS[ducking]
        fc = (music_part + "[0:a]asplit=2[nar][sc];"
              f"[m][sc]sidechaincompress=threshold={thr}:ratio={ratio}:attack=30:release=500[duck];"
              "[nar][duck]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc = music_part + "[0:a][m]amix=inputs=2:duration=first:normalize=0[a]"
    media.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video_in), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(out),
    ], timeout=1800)
    return out


def loudnorm_measure(path: Path, lufs: float = -14.0, tp: float = -1.0, lra: float = 11.0) -> dict:
    """Measure integrated loudness (LUFS), true peak (dBTP) and range (LU) of a file's audio."""
    proc = media.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                      f"loudnorm=I={lufs:g}:TP={tp:g}:LRA={lra:g}:print_format=json", "-f", "null", "-"],
                     timeout=1800)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:
        raise media.MediaError("Could not measure the loudness.")
    return {k: float(v) for k, v in json.loads(m.group(0)).items() if k != "normalization_type"}


def master(video_in: Path, out: Path, lufs: float = -14.0, tp: float = -1.0, lra: float = 11.0) -> dict:
    """Bring the whole video's sound to a target loudness in two passes (measure, then correct without
    squashing the dynamics). Silent or near-silent audio is copied untouched.
    Returns {"before", "after", "applied"} in LUFS (None when not measured)."""
    m = loudnorm_measure(video_in, lufs, tp, lra)
    before = m["input_i"]
    if not math.isfinite(before) or before < -60:
        shutil.copy(video_in, out)
        return {"before": None, "after": None, "applied": False}
    af = (f"loudnorm=I={lufs:g}:TP={tp:g}:LRA={lra:g}:measured_I={before}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
          "linear=true:print_format=summary")
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(video_in), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
               "-af", af, "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out)],
              timeout=1800)
    after = loudnorm_measure(out, lufs, tp, lra)["input_i"]
    return {"before": round(before, 1), "after": round(after, 1), "applied": True}


def audio_mp3(video: Path, out: Path) -> Path:
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(out)],
              timeout=1800)
    return out


def write_srt(entries: list[tuple[str, float, float]], path: Path) -> Path:
    lines = []
    for i, (text, a, b) in enumerate(entries, 1):
        lines += [str(i), f"{media.fmt_srt_time(a)} --> {media.fmt_srt_time(b)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
