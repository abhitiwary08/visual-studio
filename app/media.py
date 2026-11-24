"""ffmpeg / ffprobe helpers."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise MediaError(f"{cmd[0]} is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{cmd[0]} timed out after {timeout}s") from e
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise MediaError(f"{cmd[0]} failed (exit {proc.returncode}):\n{tail}")
    return proc


def probe(path: Path) -> dict:
    proc = run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout=60,
    )
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError) as e:
        raise MediaError(f"Could not read duration of {path}") from e


def extract_frame(video: Path, out_png: Path, at: float = 1.0) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(out_png)], timeout=120)


def fmt_srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
