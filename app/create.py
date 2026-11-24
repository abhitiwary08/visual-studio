"""Standalone generators for the Create page: thumbnail, speech, music, scene picture and video clip.

Results are saved in data/creations and listed newest first. Video clips (Google Veo) take a minute or two,
so they run as background tasks the page polls.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audiogen, config, imagegen, thumbnail, tts

OUT_DIR = config.DATA_DIR / "creations"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SAFE_NAME = re.compile(r"[\w\-.]{1,120}\.(png|jpg|mp3|mp4)")
KINDS = {"thumbnail": "jpg", "speech": "mp3", "music": "mp3", "scene": "png", "video": "mp4"}
ASPECTS = {"16:9", "9:16", "1:1"}
VEO_BASE = "https://generativelanguage.googleapis.com/v1beta"

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="create")
_tasks: dict[str, dict] = {}
_lock = threading.Lock()


class CreateError(RuntimeError):
    pass


def _out(kind: str) -> Path:
    return OUT_DIR / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.{KINDS[kind]}"


def path_for(name: str) -> Path | None:
    if not SAFE_NAME.fullmatch(name):
        return None
    p = OUT_DIR / name
    return p if p.is_file() else None


def files(limit: int = 60) -> list[dict]:
    items = sorted((p for p in OUT_DIR.iterdir() if SAFE_NAME.fullmatch(p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [{"name": p.name, "kind": p.name.split("-")[0], "size_mb": round(p.stat().st_size / 1e6, 2)} for p in items]


def delete(name: str) -> bool:
    p = path_for(name)
    if not p:
        return False
    p.unlink()
    return True


# ------------------------------------------------------------ generators ----
def make_scene(prompt: str, aspect: str = "16:9") -> Path:
    prompt = prompt.strip()
    if len(prompt) < 5:
        raise CreateError("Describe the picture you want.")
    out = _out("scene")
    try:
        return imagegen.generate(prompt, [], aspect if aspect in ASPECTS else "16:9", out)
    except imagegen.ImageError as e:
        raise CreateError(str(e)) from e


def make_thumbnail(title: str, prompt: str = "", background: bytes | None = None) -> Path:
    """Title text over an AI picture (prompt), your own picture (background bytes) or a plain gradient."""
    title = title.strip()
    if not title:
        raise CreateError("Type the title to print on the thumbnail.")
    frame = None
    tmp = OUT_DIR / f".bg-{uuid.uuid4().hex[:6]}.png"
    try:
        if background:
            tmp.write_bytes(background)
            frame = tmp
        elif prompt.strip():
            try:
                frame = imagegen.generate(prompt.strip(), [], "16:9", tmp)
            except imagegen.ImageError as e:
                raise CreateError(str(e)) from e
        return thumbnail.render_thumbnail(_out("thumbnail"), title, frame, channel=config.channel_name())
    except CreateError:
        raise
    except Exception as e:  # unreadable upload
        raise CreateError(f"Could not make the thumbnail: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)


def make_speech(text: str, voice: str = "") -> Path:
    if not text.strip():
        raise CreateError("Type the words to speak.")
    out = _out("speech")
    try:
        return tts.synthesize(text, out, voice=voice or None)
    except Exception as e:
        raise CreateError(str(e)) from e


def make_music(prompt: str, seconds: int) -> Path:
    try:
        data, _ = audiogen.generate_music(prompt, seconds)
    except audiogen.AudioError as e:
        raise CreateError(str(e)) from e
    out = _out("music")
    out.write_bytes(data)
    return out


# ---------------------------------------------------------- video (Veo) ----
def _veo(prompt: str, aspect: str, out: Path, task: dict) -> None:
    import requests

    key = config.gemini_key()
    if not key:
        raise CreateError("Video clips use Google Veo and need GEMINI_API_KEY. Add it on the Setup page.")
    model = config.env("VEO_MODEL", "veo-3.0-fast-generate-001")
    head = {"x-goog-api-key": key, "Content-Type": "application/json"}
    r = requests.post(f"{VEO_BASE}/models/{model}:predictLongRunning", headers=head, timeout=60, json={
        "instances": [{"prompt": prompt}],
        "parameters": {"aspectRatio": "9:16" if aspect == "9:16" else "16:9"}})
    if r.status_code != 200:
        hint = " Veo needs a paid Gemini API plan." if r.status_code in (400, 403, 404, 429) else ""
        raise CreateError(f"Veo error {r.status_code}: {r.text[:300]}.{hint}")
    op = r.json().get("name")
    if not op:
        raise CreateError("Veo did not start the clip.")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(8)
        task["note"] = "Veo is drawing the clip..."
        s = requests.get(f"{VEO_BASE}/{op}", headers=head, timeout=60)
        if s.status_code != 200:
            raise CreateError(f"Veo status error {s.status_code}: {s.text[:200]}")
        d = s.json()
        if not d.get("done"):
            continue
        if d.get("error"):
            raise CreateError(f"Veo failed: {d['error'].get('message', d['error'])}")
        resp = d.get("response") or {}
        samples = (resp.get("generateVideoResponse") or {}).get("generatedSamples") or resp.get("videos") or []
        if not samples:
            reasons = (resp.get("generateVideoResponse") or {}).get("raiMediaFilteredReasons")
            raise CreateError(f"Veo returned no clip{': ' + '; '.join(reasons) if reasons else ''}. Reword the prompt.")
        uri = (samples[0].get("video") or {}).get("uri")
        v = requests.get(uri, headers={"x-goog-api-key": key}, timeout=300)
        if v.status_code != 200 or len(v.content) < 5000:
            raise CreateError("The clip could not be downloaded.")
        out.write_bytes(v.content)
        return
    raise CreateError("Veo took too long. Try again.")


def start_video(prompt: str, aspect: str = "16:9") -> str:
    prompt = prompt.strip()
    if len(prompt) < 8:
        raise CreateError("Describe the clip: who or what, the action, the camera, the mood.")
    if not config.gemini_key():
        raise CreateError("Video clips use Google Veo and need GEMINI_API_KEY. Add it on the Setup page.")
    tid = uuid.uuid4().hex[:10]
    task = {"status": "running", "note": "Starting...", "file": "", "error": ""}
    with _lock:
        _tasks[tid] = task

    def run() -> None:
        out = _out("video")
        try:
            _veo(prompt, aspect, out, task)
            task.update(status="done", file=out.name, note="Done")
        except CreateError as e:
            task.update(status="error", error=str(e))
        except Exception as e:
            task.update(status="error", error=f"Video clip failed: {e}")

    _pool.submit(run)
    return tid


def task(tid: str) -> dict | None:
    return _tasks.get(tid)
