"""Local dashboard + JSON API (FastAPI). Bind to 127.0.0.1 only."""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import (audiogen, config, create, festivals, imagegen, llm, media, meta, pipeline, render, scriptgen, slides, storyboard, studio,
               textutil, tts, uploader, visuals)
from .jobs import JobStore

store = JobStore()
BUILD = f"{time.time():.0f}"  # see /api/version
_script_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="script")
_render_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
_upload_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upload")
_image_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="images")
_connect_lock = threading.Lock()
_connect_state = {"running": False, "error": ""}
VOICE_EXTS = {"webm", "ogg", "mp3", "wav", "m4a"}
MAX_VOICE_BYTES = 50 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.recover_interrupted()
    yield


app = FastAPI(title="Video studio", lifespan=lifespan)
# Protects against DNS-rebinding: only answer requests addressed to this machine.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])


@app.middleware("http")
async def require_dashboard_header(request: Request, call_next):
    """Mutating calls need a custom header, which browsers will not send cross-site
    without a CORS preflight (we do not allow any), so other websites cannot trigger uploads."""
    if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
        if request.headers.get("x-dashboard") != "1":
            return JSONResponse({"detail": "Missing X-Dashboard header."}, status_code=403)
    return await call_next(request)


# ------------------------------------------------------------------ models ----
class NewJob(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    kind: str = "upanishad_story"
    format: str = "long"
    minutes: float = 8
    voice: str = ""
    notes: str = Field(default="", max_length=2000)
    captions: bool = True
    music: bool = True
    auto_render: bool = False
    script_json: str = ""
    repeat: int = 108  # mantra jaap only
    ai_images: bool = False  # plan AI pictures with the same characters once the script is written


class BatchJobs(BaseModel):
    topics: list[str]
    kind: str = "upanishad_story"
    format: str = "long"
    minutes: float = 8
    voice: str = ""
    notes: str = ""
    captions: bool = True
    music: bool = True
    auto_render: bool = False
    repeat: int = 108
    ai_images: bool = False


class ScriptBody(BaseModel):
    script: dict


class MetaBody(BaseModel):
    metadata: dict


class ReviseBody(BaseModel):
    feedback: str = Field(min_length=3, max_length=3000)


class UploadBody(BaseModel):
    privacy: str = "private"
    publish_at: str = ""
    force: bool = False
    override_limit: bool = False


class SettingsBody(BaseModel):
    theme: str | None = None
    captions: bool | None = None
    music: bool | None = None


class ReviewBody(BaseModel):
    reviewed: bool


class LLMBody(BaseModel):
    provider: str
    model: str = Field(default="", max_length=120)
    api_key: str = Field(default="", max_length=400)


class MetaConnectBody(BaseModel):
    app_id: str = Field(max_length=40)
    app_secret: str = Field(max_length=200)
    user_token: str = Field(max_length=1000)


class MetaPageBody(BaseModel):
    page_id: str = Field(max_length=40)


class SocialBody(BaseModel):
    caption: str = Field(default="", max_length=2200)
    hashtags: list[str] | str = []


class SocialPostBody(BaseModel):
    platforms: list[str] = Field(min_length=1, max_length=2)
    force: bool = False


class StudioBody(BaseModel):
    studio: dict


class PreviewBody(BaseModel):
    scene: str = Field(pattern=r"^s\d{2,5}$")


class BrandingBody(BaseModel):
    corner: str | None = None
    size: float | None = Field(default=None, ge=0.05, le=0.3)
    opacity: float | None = Field(default=None, ge=0.3, le=1.0)
    auto: bool | None = None


class LicenseBody(BaseModel):
    note: str = Field(default="", max_length=300)


class ImageSettingsBody(BaseModel):
    provider: str
    model: str = Field(default="", max_length=80)
    quality: str = ""


class AudioSettingsBody(BaseModel):
    engine: str
    music_provider: str = ""
    voice: str = ""
    keys: dict[str, str] = Field(default_factory=dict)


class MusicGenBody(BaseModel):
    prompt: str = Field(min_length=5, max_length=600)
    seconds: int = Field(default=60, ge=10, le=300)
    license: str = Field(default="", max_length=300)


class PlanBody(BaseModel):
    style: str = ""
    style_note: str = Field(default="", max_length=300)
    include_slides: bool = False


class StoryboardBody(BaseModel):
    storyboard: dict


class DrawBody(BaseModel):
    scenes: list[str] | None = Field(default=None, max_length=300)  # None: every AI-picture scene
    only_missing: bool = True
    characters: list[str] | None = Field(default=None, max_length=8)  # redraw these character sheets


class UseImageBody(BaseModel):
    file: str = Field(pattern=r"^[0-9a-f]{12}\.(png|jpg|jpeg|webp)$")


class ShortBody(BaseModel):
    scenes: list[int] = Field(min_length=1, max_length=20)


class IdeasBody(BaseModel):
    kind: str = "upanishad_story"
    count: int = Field(default=10, ge=3, le=20)
    theme: str = ""
    festival: str = ""  # a festival date from /api/festivals: plan topics for that festival


# ----------------------------------------------------------------- helpers ----
def _get(job_id: str) -> dict:
    try:
        return store.get(job_id)
    except KeyError:
        raise HTTPException(404, "Job not found")


def _validate(kind: str, fmt: str, minutes: float) -> float:
    if kind not in config.KINDS:
        raise HTTPException(400, f"Unknown content type: {kind}")
    if fmt not in config.FORMATS:
        raise HTTPException(400, f"Unknown format: {fmt}")
    cap = config.FORMATS[fmt]["max_minutes"]
    return max(0.5, min(float(minutes), cap))


def _busy(job: dict) -> bool:
    return job["status"] in ("generating_script", "rendering", "uploading") or bool(job.get("social_uploading"))


def _summary(job: dict) -> dict:
    script = job.get("script") or {}
    return {
        "id": job["id"], "topic": job["topic"], "kind": job["kind"], "format": job["format"],
        "status": job["status"], "stage": job.get("stage", ""), "progress": job.get("progress", 0),
        "title": (job.get("metadata") or {}).get("title") or script.get("title") or "",
        "created": job["created"], "error": job.get("error"),
        "url": (job.get("youtube") or {}).get("url", ""),
    }


def _detail(job: dict) -> dict:
    out = dict(job)
    out["description_footer"] = pipeline.description_footer(job)
    out["busy"] = _busy(job)
    out["theme_effective"] = pipeline.video_theme(job)
    out["studio_effective"] = studio.effective(job)
    if job.get("script"):
        out["social_preview"] = {p: pipeline.social_text(job, p) for p in ("instagram", "facebook")}
    return out


def uploads_last_24h() -> int:
    """Uploads finished in the last 24 hours, plus any in progress."""
    cutoff = time.time() - 86400
    n = 0
    for j in store.list():
        if j["status"] == "uploading":
            n += 1
            continue
        at = (j.get("youtube") or {}).get("uploaded_at", "")
        try:
            if at and time.mktime(time.strptime(at, "%Y-%m-%d %H:%M:%S")) >= cutoff:
                n += 1
        except ValueError:
            pass
    return n


def _task(fn, job_id: str, *args) -> None:
    try:
        fn(store, job_id, *args)
    except Exception as e:  # last-resort guard so a crash never leaves a job spinning
        try:
            job = store.get(job_id)
            fallback = {"generating_script": "new", "rendering": "script_ready", "uploading": "rendered"}
            store.update(job_id, status=fallback.get(job["status"], job["status"]), stage="", error=f"Unexpected error: {e}")
            store.log(job_id, f"Unexpected error: {e}")
        except KeyError:
            pass


def _enqueue_render(job_id: str) -> None:
    store.update(job_id, status="rendering", stage="Waiting in queue", progress=0.0, error=None)
    _render_pool.submit(_task, pipeline.run_render, job_id)


def _generate_task(job_id: str, feedback: str = "") -> None:
    _task(pipeline.run_generate_script, job_id, feedback)
    try:
        job = store.get(job_id)
    except KeyError:
        return
    if job.get("ai_images") and job["status"] == "script_ready" and job.get("script") and not job.get("storyboard"):
        try:
            storyboard.plan(store, job_id)  # describes the pictures; drawing them is started from the Studio
        except Exception as e:
            store.warn(job_id, f"Planning the AI pictures failed: {e}. Use 'Plan pictures' in the Studio.")
        job = store.get(job_id)
    if job.get("auto_render") and job["status"] == "script_ready" and not job.get("error") and job.get("script"):
        _enqueue_render(job_id)


def _create(body: NewJob | BatchJobs, topic: str) -> dict:
    minutes = _validate(body.kind, body.format, body.minutes)
    extra = {}
    if config.KINDS[body.kind].get("jaap"):
        if body.format != "long":
            raise HTTPException(400, "A mantra jaap is a long video. Choose the 16:9 format.")
        if not 1 <= body.repeat <= scriptgen.MAX_REPEAT:
            raise HTTPException(400, f"Repetitions must be between 1 and {scriptgen.MAX_REPEAT}.")
        extra["repeat"] = body.repeat
    return store.create(
        topic=topic.strip(), kind=body.kind, format=body.format, minutes=minutes, voice=body.voice,
        notes=body.notes.strip(), captions=body.captions, music=body.music, auto_render=body.auto_render,
        studio=studio.defaults_for_new(), ai_images=bool(getattr(body, "ai_images", False)), **extra,
    )


# --------------------------------------------------------------------- API ----
@app.get("/api/options")
def options():
    return {
        "kinds": {k: {"label": v["label"], "default_minutes": v["default_minutes"], "jaap": bool(v.get("jaap"))}
                  for k, v in config.KINDS.items()},
        "jaap_counts": list(scriptgen.JAAP_COUNTS),
        "formats": {k: {"label": v["label"], "max_minutes": v["max_minutes"]} for k, v in config.FORMATS.items()},
        "voices": config.voices(),
        "default_voice": config.default_voice(),
        "tts_engine": config.tts_engine(),
        "themes": {k: v["label"] for k, v in slides.THEMES.items()},
        "studio": {
            "motions": studio.MOTIONS, "looks": studio.LOOKS, "overlay_positions": studio.OVERLAY_POSITIONS,
            "caption_styles": studio.CAPTION_STYLES, "caption_positions": studio.CAPTION_POSITIONS,
            "caption_sizes": list(studio.CAPTION_SIZES), "polish": studio.POLISH_LEVELS, "ducking": studio.DUCKING,
            "quality": {k: v[0] for k, v in studio.QUALITY.items()}, "corners": studio.CORNERS,
            "lufs": {str(int(k)): v for k, v in studio.LUFS_CHOICES.items()},
        },
        "default_privacy": config.default_privacy(),
        "build": BUILD,
        "image_styles": {k: v["label"] for k, v in storyboard.STYLES.items()},
    }


@app.get("/api/version")
def version():
    """Changes every time the server starts, so open pages can tell the user to reload after an update."""
    return {"build": BUILD}


@app.get("/api/health")
def health():
    try:
        font = {"ok": True, "path": textutil.find_font(True).name, "raqm": textutil.raqm_available()}
    except textutil.FontError as e:
        font = {"ok": False, "path": "", "raqm": textutil.raqm_available(), "error": str(e)}
    yt = uploader.status()
    yt["connecting"] = _connect_state["running"]
    yt["error"] = _connect_state["error"]
    return {
        "ffmpeg": media.have_ffmpeg(),
        "font": font,
        "anthropic": llm.ready(),  # kept for older dashboards: "an AI writer is ready"
        "model": llm.model(),
        "llm": llm.describe(),
        "meta": meta.status(),
        "images": imagegen.describe(),
        "pexels": visuals.available(),
        "music_files": len(render.list_music()),
        "youtube": yt,
        "channel": config.channel_name(),
        "audio": audiogen.describe(),
        "tts": {"engine": config.tts_engine(), "sarvam_key": bool(config.sarvam_key()),
                "pronunciations": len(tts.pronunciation_map())},
        "uploads_today": uploads_last_24h(), "upload_limit": config.max_uploads_per_day(),
    }


@app.get("/api/jobs")
def list_jobs():
    return [_summary(j) for j in store.list()]


@app.post("/api/jobs")
def create_job(body: NewJob):
    job = _create(body, body.topic)
    if body.script_json.strip():
        try:
            script = scriptgen.parse_pasted(body.script_json, body.format)
            pipeline.set_script(store, job["id"], script)
        except scriptgen.ScriptError as e:
            store.delete(job["id"])
            raise HTTPException(400, str(e))
        if body.auto_render:
            _enqueue_render(job["id"])
    else:
        store.update(job["id"], status="generating_script", stage=f"Writing script with {llm.label()}")
        _script_pool.submit(_generate_task, job["id"])
    return _detail(store.get(job["id"]))


@app.post("/api/jobs/batch")
def create_batch(body: BatchJobs):
    topics = [t.strip() for t in body.topics if t and t.strip()]
    if not topics:
        raise HTTPException(400, "Add at least one topic.")
    if len(topics) > 30:
        raise HTTPException(400, "Up to 30 topics at a time.")
    ids = []
    for t in topics:
        job = _create(body, t)
        store.update(job["id"], status="generating_script", stage="Waiting in queue")
        _script_pool.submit(_generate_task, job["id"])
        ids.append(job["id"])
    return {"ids": ids}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _detail(_get(job_id))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = _get(job_id)
    if _busy(job) or job.get("previewing") or job.get("imaging"):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    store.delete(job_id)
    return {"ok": True}


@app.put("/api/jobs/{job_id}/script")
def save_script(job_id: str, body: ScriptBody):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    try:
        pipeline.set_script(store, job_id, body.script)
    except scriptgen.ScriptError as e:
        raise HTTPException(400, str(e))
    # Editing the script invalidates the previous video.
    fresh = store.get(job_id)
    if fresh["status"] in ("rendered", "uploaded") and not fresh.get("youtube"):
        store.update(job_id, status="script_ready")
    return _detail(store.get(job_id))


@app.put("/api/jobs/{job_id}/metadata")
def save_metadata(job_id: str, body: MetaBody):
    job = _get(job_id)
    m = body.metadata
    clean = dict(job.get("metadata") or {})
    for key in ("title", "description", "category", "privacy", "publish_at"):
        if key in m:
            clean[key] = str(m[key]).strip()
    if "tags" in m:
        tags = m["tags"] if isinstance(m["tags"], list) else str(m["tags"]).split(",")
        clean["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    for key in ("made_for_kids", "synthetic"):
        if key in m:
            clean[key] = bool(m[key])
    if clean.get("privacy") not in (None, "private", "unlisted", "public"):
        raise HTTPException(400, "Privacy must be private, unlisted or public.")
    store.update(job_id, metadata=clean)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/revise")
def revise(job_id: str, body: ReviseBody):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    if not job.get("script"):
        raise HTTPException(400, "There is no script to revise yet.")
    store.update(job_id, status="generating_script", stage=f"Revising script with {llm.label()}", error=None)
    _script_pool.submit(_generate_task, job_id, body.feedback)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/render")
def render_job(job_id: str):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    if not job.get("script"):
        raise HTTPException(400, "Write or paste a script first.")
    if job.get("imaging"):
        raise HTTPException(409, "Pictures are still being drawn. Wait for them, or stop them in the Studio.")
    todo = pipeline.missing_personal(job["script"])
    if todo:
        raise HTTPException(400, f"Write your personal reflection first (scene {', '.join(map(str, todo))}). "
                                 "It is what makes the video yours.")
    _enqueue_render(job_id)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/upload")
def upload_job(job_id: str, body: UploadBody):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    if not job.get("outputs", {}).get("video"):
        raise HTTPException(400, "Render the video first.")
    if job.get("youtube") and not body.force:
        raise HTTPException(409, "Already uploaded. Confirm to upload a second copy.")
    if body.privacy not in ("private", "unlisted", "public"):
        raise HTTPException(400, "Privacy must be private, unlisted or public.")
    if not job.get("reviewed"):
        raise HTTPException(400, "Confirm that you watched the video and checked every item in "
                                 "'Check these before you publish'.")
    limit = config.max_uploads_per_day()
    if limit and not body.override_limit and uploads_last_24h() >= limit:
        raise HTTPException(429, f"You have uploaded {limit} video(s) in the last 24 hours, your daily limit. "
                                 "Schedule this one for later, or confirm to upload anyway.")
    st = uploader.status()
    if not st["configured"]:
        raise HTTPException(400, "Add config/client_secret.json first (see README, YouTube setup).")
    if not st["connected"]:
        raise HTTPException(400, "Connect YouTube first.")
    store.update(job_id, status="uploading", stage="Waiting in queue", progress=0.0, error=None)
    _upload_pool.submit(_task, pipeline.run_upload, job_id, body.privacy, body.publish_at or None)
    return _detail(store.get(job_id))


@app.put("/api/jobs/{job_id}/settings")
def save_settings(job_id: str, body: SettingsBody):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    changes = {}
    if body.theme is not None:
        if body.theme and body.theme not in slides.THEMES:
            raise HTTPException(400, "Unknown theme.")
        changes["theme"] = body.theme
    if body.captions is not None:
        changes["captions"] = body.captions
    if body.music is not None:
        changes["music"] = body.music
    store.update(job_id, **changes)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/review")
def set_review(job_id: str, body: ReviewBody):
    job = _get(job_id)
    if body.reviewed and not job.get("outputs", {}).get("video"):
        raise HTTPException(400, "Render and watch the video first.")
    reviewed = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "verify_notes": len((job.get("script") or {}).get("verify_notes") or [])} if body.reviewed else None
    store.update(job_id, reviewed=reviewed)
    if body.reviewed:
        store.log(job_id, "Owner confirmed the video and verify notes were reviewed")
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/short")
def make_short(job_id: str, body: ShortBody):
    job = _get(job_id)
    if job["format"] != "long" or not job.get("script"):
        raise HTTPException(400, "Shorts are made from a long video with a script.")
    try:
        new = pipeline.derive_short(store, job_id, body.scenes)
    except (ValueError, scriptgen.ScriptError) as e:
        raise HTTPException(400, str(e))
    return _detail(new)


@app.post("/api/jobs/{job_id}/translate")
def translate_job(job_id: str):
    job = _get(job_id)
    if _busy(job) or job.get("translating"):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    if not job.get("outputs", {}).get("video"):
        raise HTTPException(400, "Render the video first.")
    store.update(job_id, translating=True)
    _script_pool.submit(_task, pipeline.run_translate, job_id)
    return _detail(store.get(job_id))


@app.get("/api/stats")
def stats(refresh: bool = False):
    """Performance of uploaded videos. Fetches from YouTube when asked (1 quota unit per 50 videos)
    and keeps the numbers on each job, so the page and topic ideas work offline."""
    uploaded = [j for j in store.list() if (j.get("youtube") or {}).get("video_id")]
    error = ""
    if refresh and uploaded:
        try:
            fresh = uploader.video_stats([j["youtube"]["video_id"] for j in uploaded])
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for j in uploaded:
                st = fresh.get(j["youtube"]["video_id"])
                if st:
                    j["youtube"] = {**j["youtube"], "stats": {**st, "at": stamp}}
                    store.update(j["id"], youtube=j["youtube"])
        except uploader.UploadError as e:
            error = str(e)
    rows = []
    for j in uploaded:
        st = j["youtube"].get("stats") or {}
        script = j.get("script") or {}
        own = sum(1 for sc in script.get("scenes") or [] if sc.get("voice_file"))
        rows.append({
            "id": j["id"], "title": (j.get("metadata") or {}).get("title") or script.get("title") or j["topic"],
            "kind": j["kind"], "format": j["format"], "url": j["youtube"].get("url", ""),
            "uploaded_at": j["youtube"].get("uploaded_at", ""), "own_voice_scenes": own,
            "scenes": len(script.get("scenes") or []), **{k: st.get(k) for k in ("views", "likes", "comments", "at")},
        })
    rows.sort(key=lambda r: r["views"] or 0, reverse=True)
    by_kind: dict[str, dict] = {}
    for r in rows:
        if r["views"] is None:
            continue
        k = by_kind.setdefault(r["kind"], {"videos": 0, "views": 0})
        k["videos"] += 1
        k["views"] += r["views"]
    for k in by_kind.values():
        k["avg_views"] = round(k["views"] / k["videos"])
    return {"videos": rows, "by_kind": by_kind, "error": error, "can_read": uploader.status().get("manage", False)}


def _top_titles(n: int = 3) -> list[str]:
    rows = [r for r in stats()["videos"] if r["views"]]
    return [f"{r['title']} ({r['views']} views)" for r in rows[:n]]


# ------------------------------------------------------------------ studio ----
@app.put("/api/jobs/{job_id}/studio")
def save_studio(job_id: str, body: StudioBody):
    _get(job_id)  # settings can be saved while a render runs; that render keeps the settings it started with
    store.update(job_id, studio=studio.normalize_studio(body.studio))
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/studio/default")
def studio_make_default(job_id: str):
    studio.save_defaults(studio.effective(_get(job_id)))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/media")
async def upload_media(job_id: str, request: Request, ext: str = "mp4"):
    """Raw body: the owner's own video or photo for a scene. Returns the stored name for scene.edit.bg_file."""
    _get(job_id)
    data = await request.body()
    try:
        res = await run_in_threadpool(studio.save_job_media, store.job_dir(job_id), data, ext)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.log(job_id, f"Own {res['kind']} added ({len(data) / 1e6:.1f} MB)")
    return res


@app.get("/media/{job_id}/{name}")
def media_file(job_id: str, name: str):
    if not studio.MEDIA_FILE_RE.fullmatch(name):
        raise HTTPException(404)
    try:
        path = store.job_dir(job_id) / "media" / name
    except KeyError:
        raise HTTPException(404)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/waveform")
def job_waveform(job_id: str, scene: str):
    """Peaks of a scene's sound: the owner's recording (to trim it) or the rendered voice-over."""
    job = _get(job_id)
    sc = next((x for x in (job.get("script") or {}).get("scenes", []) if x["id"] == scene), None)
    if not sc:
        raise HTTPException(404, "Scene not found.")
    path, source = None, ""
    if sc.get("voice_file"):
        own = store.job_dir(job_id) / "voice" / sc["voice_file"]
        if own.exists():
            path, source = own, "recording"
    if path is None:
        audio = (job.get("assets") or {}).get(scene, {}).get("audio")
        if audio and Path(audio).exists():
            path, source = Path(audio), "voice-over"
    if path is None:
        raise HTTPException(404, "Make a preview or render once (or record your voice) to see this scene's sound.")
    try:
        return {**studio.waveform(path), "source": source}
    except media.MediaError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{job_id}/preview")
def preview_scene(job_id: str, body: PreviewBody):
    job = _get(job_id)
    if _busy(job) or job.get("previewing") or job.get("imaging"):
        raise HTTPException(409, "Something is already running on this video. Try again in a moment.")
    sc = next((x for x in (job.get("script") or {}).get("scenes", []) if x["id"] == body.scene), None)
    if not sc:
        raise HTTPException(404, "Scene not found. Save the script first.")
    if not sc["narration"].strip():
        raise HTTPException(400, "Write this scene's narration first.")
    store.update(job_id, previewing=body.scene, error=None)
    _render_pool.submit(_task, pipeline.run_preview, job_id, body.scene)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/audio-export")
def audio_export(job_id: str):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    if not job.get("outputs", {}).get("video"):
        raise HTTPException(400, "Render the video first.")
    jd = store.job_dir(job_id)
    try:
        render.audio_mp3(jd / job["outputs"]["video"], jd / "output" / "audio.mp3")
    except media.MediaError as e:
        raise HTTPException(400, str(e))
    outputs = dict(job["outputs"])
    outputs["audio"] = "output/audio.mp3"
    store.update(job_id, outputs=outputs)
    return _detail(store.get(job_id))


# --------------------------------------------------------------- AI pictures ----
@app.put("/api/images/settings")
def image_settings(body: ImageSettingsBody):
    try:
        imagegen.save(body.provider, body.model, body.quality)
    except imagegen.ImageError as e:
        raise HTTPException(400, str(e))
    return imagegen.describe()


@app.get("/api/images/models")
def image_models(provider: str):
    if provider not in imagegen.PROVIDERS:
        raise HTTPException(400, "Unknown provider.")
    try:
        return {"models": imagegen.list_models(provider)}
    except imagegen.ImageError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not list models: {e}")


@app.post("/api/jobs/{job_id}/storyboard/plan")
def storyboard_plan(job_id: str, body: PlanBody):
    job = _get(job_id)
    if job.get("imaging") or _busy(job):
        raise HTTPException(409, "Something is already running on this video. Try again in a moment.")
    if body.style and body.style not in storyboard.STYLES:
        raise HTTPException(400, "Unknown style.")
    try:
        storyboard.plan(store, job_id, body.style, body.include_slides, body.style_note)
    except scriptgen.ScriptError as e:
        raise HTTPException(400, str(e))
    return _detail(store.get(job_id))


@app.put("/api/jobs/{job_id}/storyboard")
def storyboard_save(job_id: str, body: StoryboardBody):
    job = _get(job_id)
    sb = storyboard.normalize_storyboard(body.storyboard, keep=job.get("storyboard"))
    store.update(job_id, storyboard=sb)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/characters/{cid}/ref")
async def character_ref(job_id: str, cid: str, request: Request, ext: str = "png"):
    """Raw body: the owner's own picture of a character, used as its reference sheet."""
    job = _get(job_id)
    if not any(c["id"] == cid for c in (job.get("storyboard") or {}).get("characters", [])):
        raise HTTPException(404, "Character not found. Save the characters first.")
    if ext.lower() not in studio.IMAGE_EXTS:
        raise HTTPException(400, "Use a PNG, JPG or WebP picture.")
    data = await request.body()
    try:
        res = await run_in_threadpool(studio.save_job_media, store.job_dir(job_id), data, ext)
    except ValueError as e:
        raise HTTPException(400, str(e))
    storyboard._set_ref(store, job_id, cid, res["bg_file"])
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/images")
def draw_images(job_id: str, body: DrawBody):
    job = _get(job_id)
    if job.get("imaging") or _busy(job) or job.get("previewing"):
        raise HTTPException(409, "Something is already running on this video. Try again in a moment.")
    if not job.get("storyboard"):
        raise HTTPException(400, "Plan the pictures first.")
    if not imagegen.ready():
        raise HTTPException(400, f"{imagegen.PROVIDERS[imagegen.provider()]['label']} has no API key. "
                                 "Add it on the Setup page, or choose the free placeholder.")
    known = {s["id"] for s in (job.get("script") or {}).get("scenes", [])}
    scenes = [x for x in body.scenes if x in known] if body.scenes is not None else None
    store.update(job_id, imaging={"total": 0, "done": 0, "current": "Starting", "errors": {}}, imaging_cancel=False)
    _image_pool.submit(_task, storyboard.run_images, job_id, scenes, body.only_missing, body.characters)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/images/cancel")
def cancel_images(job_id: str):
    job = _get(job_id)
    if job.get("imaging"):
        store.update(job_id, imaging_cancel=True)
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/images/{scene_id}/use")
def use_image(job_id: str, scene_id: str, body: UseImageBody):
    job = _get(job_id)
    entry = (job.get("scene_images") or {}).get(scene_id) or {}
    if body.file != entry.get("file") and body.file not in entry.get("history", []):
        raise HTTPException(404, "That picture is not one of this scene's versions.")
    storyboard.set_scene_image(store, job_id, scene_id, body.file)
    return _detail(store.get(job_id))


# -------------------------------------------------- music library, branding ----
@app.get("/api/music")
def music_list():
    return {"tracks": studio.library()}


@app.post("/api/music")
async def music_upload(request: Request, name: str):
    data = await request.body()
    try:
        return await run_in_threadpool(studio.save_music, name, data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/music/{name}")
def music_delete(name: str):
    if not studio.delete_music(name):
        raise HTTPException(404, "Track not found.")
    return {"tracks": studio.library()}


@app.put("/api/music/{name}/license")
def music_license(name: str, body: LicenseBody):
    try:
        studio.set_license(name, body.note)
    except KeyError:
        raise HTTPException(404, "Track not found.")
    return {"tracks": studio.library()}


@app.post("/api/music/generate")
async def music_generate(body: MusicGenBody):
    try:
        data, name = await run_in_threadpool(audiogen.generate_music, body.prompt, body.seconds)
        track = await run_in_threadpool(studio.save_music, name, data)
        note = body.license.strip() or "AI-generated with the provider named in .env (MUSIC_PROVIDER); check its terms for monetized use."
        studio.set_license(track["name"], note)
    except (audiogen.AudioError, ValueError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Music generation failed: {e}")
    return {"tracks": studio.library(), "name": track["name"]}


# ------------------------------------------------ voice engine, cloned voices ----
@app.get("/api/audio")
def audio_get():
    return audiogen.describe()


@app.put("/api/audio")
def audio_save(body: AudioSettingsBody):
    try:
        audiogen.save(body.engine, body.music_provider, body.keys)
        if body.voice and body.voice in config.voices():
            audiogen.set_default_voice(body.voice)
    except audiogen.AudioError as e:
        raise HTTPException(400, str(e))
    return audiogen.describe()


@app.post("/api/audio/clone")
async def audio_clone(request: Request, name: str, ext: str = "webm", consent: bool = False):
    data = await request.body()
    if len(data) > MAX_VOICE_BYTES:
        raise HTTPException(400, "That recording is too large (50 MB max).")
    try:
        await run_in_threadpool(audiogen.clone_voice, name, data, ext, consent)
    except audiogen.AudioError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Voice cloning failed: {e}")
    return audiogen.describe()


@app.delete("/api/audio/clone/{voice_id}")
def audio_clone_delete(voice_id: str):
    try:
        audiogen.delete_clone(voice_id)
    except audiogen.AudioError as e:
        raise HTTPException(400, str(e))
    return audiogen.describe()


# ------------------------------------------------------------- Create page ----
class CreateBody(BaseModel):
    prompt: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=20000)  # speech words, or the thumbnail title
    voice: str = Field(default="", max_length=80)
    aspect: str = "16:9"
    seconds: int = Field(default=30, ge=10, le=300)


def _created(path: Path) -> dict:
    return {"name": path.name, "files": create.files()}


@app.get("/api/create")
def create_list():
    return {"files": create.files(), "voices": config.voices(), "voice": config.default_voice(),
            "images_ready": imagegen.ready(), "image_label": imagegen.PROVIDERS[imagegen.provider()]["label"],
            "video_ready": bool(config.gemini_key())}


async def _run_create(fn, *args):
    try:
        return _created(await run_in_threadpool(fn, *args))
    except create.CreateError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Generation failed: {e}")


@app.post("/api/create/scene")
async def create_scene(body: CreateBody):
    return await _run_create(create.make_scene, body.prompt, body.aspect)


@app.post("/api/create/speech")
async def create_speech(body: CreateBody):
    return await _run_create(create.make_speech, body.text, body.voice)


@app.post("/api/create/music")
async def create_music(body: CreateBody):
    return await _run_create(create.make_music, body.prompt, body.seconds)


@app.post("/api/create/thumbnail")
async def create_thumbnail(body: CreateBody):
    return await _run_create(create.make_thumbnail, body.text, body.prompt, None)


@app.post("/api/create/thumbnail-upload")
async def create_thumbnail_upload(request: Request, title: str):
    """Raw body: your own picture to put the title on."""
    data = await request.body()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "That picture is too large (15 MB max).")
    return await _run_create(create.make_thumbnail, title, "", data)


@app.post("/api/create/video")
def create_video(body: CreateBody):
    try:
        return {"task": create.start_video(body.prompt, body.aspect)}
    except create.CreateError as e:
        raise HTTPException(400, str(e))


@app.get("/api/create/task/{tid}")
def create_task(tid: str):
    t = create.task(tid)
    if not t:
        raise HTTPException(404, "Unknown task.")
    return dict(t, files=create.files() if t["status"] == "done" else None)


@app.delete("/api/create/files/{name}")
def create_delete(name: str):
    if not create.delete(name):
        raise HTTPException(404, "File not found.")
    return {"files": create.files()}


@app.get("/creations/{name}")
def creation_file(name: str):
    path = create.path_for(name)
    if not path:
        raise HTTPException(404)
    return FileResponse(path, headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@app.get("/music/{name}")
def music_file(name: str):
    path = studio.music_path(name)
    if not path:
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/branding")
def branding_get():
    return studio.branding()


@app.put("/api/branding")
def branding_save(body: BrandingBody):
    if body.corner is not None and body.corner not in studio.CORNERS:
        raise HTTPException(400, "Unknown corner.")
    return studio.save_branding(corner=body.corner, size=body.size, opacity=body.opacity, auto=body.auto)


@app.post("/api/branding/logo")
async def branding_logo(request: Request):
    try:
        await run_in_threadpool(studio.save_logo, await request.body())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return studio.branding()


@app.delete("/api/branding/logo")
def branding_logo_delete():
    studio.delete_logo()
    return studio.branding()


@app.get("/branding/logo")
def branding_logo_file():
    if not studio.LOGO_FILE.is_file():
        raise HTTPException(404)
    return FileResponse(studio.LOGO_FILE, headers={"Cache-Control": "no-cache"})


@app.post("/api/jobs/{job_id}/voice")
async def upload_voice(job_id: str, request: Request, ext: str = "webm"):
    """Raw audio body (the owner's own narration for one scene). Returns the stored file name,
    which the client puts on the scene as `voice_file` and saves with the script."""
    _get(job_id)
    ext = ext.lower().lstrip(".")
    if ext not in VOICE_EXTS:
        raise HTTPException(400, f"Use one of: {', '.join(sorted(VOICE_EXTS))}.")
    data = await request.body()
    if len(data) < 1000:
        raise HTTPException(400, "That recording is empty or too short.")
    if len(data) > MAX_VOICE_BYTES:
        raise HTTPException(413, "Recordings must be under 50 MB.")
    name = f"{hashlib.sha1(data).hexdigest()[:12]}.{ext}"
    folder = store.job_dir(job_id) / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    store.log(job_id, f"Own narration added ({len(data) / 1e6:.1f} MB)")
    return {"voice_file": name}


@app.get("/api/llm")
def llm_settings():
    return llm.describe()


@app.put("/api/llm")
def llm_save(body: LLMBody):
    try:
        llm.save(body.provider, body.model, body.api_key)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    return llm.describe()


@app.get("/api/llm/models")
def llm_models(provider: str):
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, "Unknown provider.")
    try:
        return {"models": llm.list_models(provider)}
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not list models: {e}")


@app.post("/api/llm/test")
def llm_test():
    """One tiny structured request, to check the key, model and function calling."""
    if not llm.ready():
        raise HTTPException(400, f"{llm.label()} has no API key yet.")
    t0 = time.time()
    llm.last_model_used = ""
    try:
        ideas = scriptgen.generate_ideas("short_story", 3, "")
    except scriptgen.ScriptError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "seconds": round(time.time() - t0, 1), "sample": ideas[:3],
            "provider": llm.label(), "model": (llm.last_model_used if llm.provider() != "anthropic" else "") or llm.model(),
            "fallback": llm.provider() != "anthropic" and bool(llm.last_model_used) and llm.last_model_used != llm.model()}


# ------------------------------------------------------ Facebook / Instagram ----
@app.post("/api/meta/connect")
def meta_connect(body: MetaConnectBody):
    try:
        return meta.connect(body.app_id, body.app_secret, body.user_token)
    except meta.MetaError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not reach Facebook: {e}")


@app.post("/api/meta/page")
def meta_page(body: MetaPageBody):
    try:
        return meta.select_page(body.page_id)
    except meta.MetaError as e:
        raise HTTPException(400, str(e))


@app.post("/api/meta/disconnect")
def meta_disconnect():
    meta.disconnect()
    return meta.status()


@app.put("/api/jobs/{job_id}/social")
def save_social(job_id: str, body: SocialBody):
    _get(job_id)
    store.update(job_id, social={"caption": body.caption.strip(), "hashtags": meta.clean_hashtags(body.hashtags)})
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/social/write")
def write_social(job_id: str):
    job = _get(job_id)
    if not job.get("script"):
        raise HTTPException(400, "Write or paste a script first.")
    try:
        social = scriptgen.generate_social(job["script"])
    except scriptgen.ScriptError as e:
        raise HTTPException(400, str(e))
    store.update(job_id, social={"caption": social["caption"], "hashtags": meta.clean_hashtags(social["hashtags"])})
    store.log(job_id, f"Instagram/Facebook caption written by {llm.label()}")
    return _detail(store.get(job_id))


@app.post("/api/jobs/{job_id}/social/post")
def post_social(job_id: str, body: SocialPostBody):
    job = _get(job_id)
    if _busy(job):
        raise HTTPException(409, "This job is running. Wait for it to finish.")
    if not job.get("outputs", {}).get("video"):
        raise HTTPException(400, "Render the video first.")
    if not job.get("reviewed"):
        raise HTTPException(400, "Confirm that you watched the video and checked every item in "
                                 "'Check these before you publish'.")
    platforms = [x for x in dict.fromkeys(body.platforms) if x in ("facebook", "instagram")]
    if not platforms:
        raise HTTPException(400, "Choose Facebook, Instagram or both.")
    st = meta.status()
    if not st["connected"]:
        raise HTTPException(400, "Connect Facebook and Instagram on the Setup page first.")
    if st["expired"] and "instagram" in platforms:
        raise HTTPException(400, "The Instagram login has expired. Connect again on the Setup page.")
    if "instagram" in platforms:
        if not st["instagram"]:
            raise HTTPException(400, "No Instagram account is linked to the selected Facebook Page.")
        if job["format"] != "short":
            raise HTTPException(400, "Instagram Reels must be vertical. Use 'Make a Short' on the Video tab first.")
    done = [x for x in platforms if (job.get("social_posts") or {}).get(x)]
    if done and not body.force:
        raise HTTPException(409, f"Already posted to {', '.join(d.title() for d in done)}. Confirm to post again.")
    store.update(job_id, social_uploading=platforms[0], error=None)
    _upload_pool.submit(_task, pipeline.run_social_post, job_id, platforms)
    return _detail(store.get(job_id))


@app.get("/api/festivals")
def upcoming_festivals(days: int = 60):
    return {"festivals": festivals.upcoming(days=max(1, min(days, 400))), "lead_days": festivals.load()[1]}


@app.post("/api/ideas")
def ideas(body: IdeasBody):
    if body.kind not in config.KINDS:
        raise HTTPException(400, "Unknown content type.")
    theme = body.theme
    if body.festival:
        fest = next((f for f in festivals.load()[0] if f["date"] == body.festival), None)
        if not fest:
            raise HTTPException(400, "Unknown festival.")
        theme = festivals.ideas_theme(fest) + (f" Also: {body.theme}" if body.theme else "")
    top = _top_titles()
    if top:  # learn from what already works on this channel
        theme = (theme + " " if theme else "") + "Our best-performing videos so far: " + "; ".join(top) + \
            ". Suggest topics in a similar spirit, not copies of them."
    try:
        return {"ideas": scriptgen.generate_ideas(body.kind, body.count, theme)}
    except scriptgen.ScriptError as e:
        raise HTTPException(400, str(e))


@app.post("/api/youtube/connect")
def youtube_connect():
    if not uploader.status()["configured"]:
        raise HTTPException(400, "Add config/client_secret.json first (see README, YouTube setup).")
    with _connect_lock:
        if _connect_state["running"]:
            return {"ok": True, "running": True}
        _connect_state.update(running=True, error="")

    def work():
        try:
            uploader.connect()
        except Exception as e:
            _connect_state["error"] = str(e)
        finally:
            _connect_state["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "running": True}


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    uploader.disconnect()
    return {"ok": True}


# ------------------------------------------------------------------- files ----
@app.get("/files/{job_id}/{rel:path}")
def job_file(job_id: str, rel: str, download: int = 0):
    try:
        base = (store.job_dir(job_id) / "output").resolve()
    except KeyError:
        raise HTTPException(404)
    path = (base / Path(rel).name).resolve()  # only files directly inside output/
    if not path.is_file() or base not in path.parents:
        raise HTTPException(404)
    return FileResponse(path, filename=path.name if download else None)


@app.get("/voice/{job_id}/{name}")
def voice_file(job_id: str, name: str):
    if not scriptgen.VOICE_FILE_RE.fullmatch(name):
        raise HTTPException(404)
    try:
        path = store.job_dir(job_id) / "voice" / name
    except KeyError:
        raise HTTPException(404)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/fonts/{name}")
def font_file(name: str):
    path = (config.FONT_DIR / Path(name).name).resolve()
    if not path.is_file() or path.suffix.lower() not in textutil.FONT_EXTS:
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/studio.js")
def studio_js():
    return FileResponse(config.WEB_DIR / "studio.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/theme.css")
def theme_css():
    return FileResponse(config.WEB_DIR / "theme.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/create.js")
def create_js():
    return FileResponse(config.WEB_DIR / "create.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})


@app.get("/studio.css")
def studio_css():
    return FileResponse(config.WEB_DIR / "studio.css", media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})
