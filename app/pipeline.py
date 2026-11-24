"""Orchestration: script -> voice -> visuals -> scenes -> final video -> thumbnail -> upload."""
from __future__ import annotations

import shutil
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

from . import captions, config, llm, media, render, scriptgen, slides, storyboard, studio, textutil, thumbnail, tts, visuals
from .jobs import JobStore

SHORT_MAX_SECONDS = 58.0
DEFAULT_GAP = 0.6  # seconds of breathing room after a scene when no pause is requested
JAAP_GAP = 0.8  # silence between repetitions of a chant


# ---------------------------------------------------------------- metadata ----
def default_metadata(script: dict, job: dict) -> dict:
    title = script["title"]
    if job["format"] == "short" and "#shorts" not in title.lower():
        title = f"{title[:88]} #Shorts"
    return {
        "title": title[:100],
        "description": script["description"],
        "tags": script["tags"],
        "category": config.yt_category(),
        "privacy": config.default_privacy(),
        "publish_at": "",
        "made_for_kids": False,
        "synthetic": True,
    }


def description_footer(job: dict) -> str:
    """Lines the system appends automatically: sources, stock credit and AI-voice disclosure."""
    script = job.get("script") or {}
    lines: list[str] = []
    links = description_links()
    if links:
        lines.append(links)
    if script.get("sources"):
        lines.append("स्रोत / Sources:\n" + "\n".join(f"- {s}" for s in script["sources"]))
    credits = []
    makers = sorted({a["bg_ai"] for a in (job.get("assets") or {}).values() if a.get("bg_ai")})
    if makers:
        credits.append("Pictures: AI-generated (" + ", ".join(makers) + ") | चित्र: AI-जनित")
    if any(a.get("bg_id") for a in (job.get("assets") or {}).values()):
        credits.append("Stock footage: Pexels (pexels.com)")
    credits.append(narration_credit(script))
    lines.append("\n".join(credits))
    return "\n\n".join(lines)


def description_links() -> str:
    """Owner's standing links (membership, affiliate, social) from config/description_links.txt."""
    try:
        text = (config.CONFIG_DIR / "description_links.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    return "\n".join(l.rstrip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#"))


def default_social(script: dict) -> dict:
    """Instagram/Facebook caption and hashtags: the writer's own, else built from the YouTube metadata."""
    import re

    caption = script.get("social_caption") or re.sub(r"(?:^|\s)#\S+", "", script.get("description", "")).strip()
    tags = script.get("hashtags") or [t.replace(" ", "") for t in script.get("tags") or []]
    from . import meta
    return {"caption": caption, "hashtags": meta.clean_hashtags(tags)[:15]}


def social_text(job: dict, platform: str) -> str:
    """Final caption for one platform. Instagram links are not clickable, so links go to Facebook only."""
    from . import meta
    social = job.get("social") or default_social(job.get("script") or {})
    footer = []
    if platform == "facebook":
        links = description_links()
        if links:
            footer.append(links)
    credit = narration_credit(job.get("script") or {})
    if "recorded by the channel" not in credit:
        footer.append("आवाज़: AI-जनित (AI voice)")
    return meta.compose_caption(social.get("caption", ""), social.get("hashtags") or [], "\n".join(footer))


def run_social_post(store: JobStore, job_id: str, platforms: list[str]) -> None:
    """Post the rendered video to Facebook and/or Instagram. Each platform is independent: one failing
    does not stop the other."""
    from . import meta

    job = store.get(job_id)
    jd = store.job_dir(job_id)
    video = jd / job["outputs"]["video"]
    duration = float(job["outputs"].get("duration") or media.duration(video))
    vertical = job["format"] == "short"
    title = (job.get("metadata") or {}).get("title") or (job.get("script") or {}).get("title", "")
    posts = dict(job.get("social_posts") or {})
    errors = []
    for platform in platforms:
        store.update(job_id, social_uploading=platform)
        try:
            if platform == "facebook":
                res = meta.post_facebook(video, description=social_text(job, "facebook"), title=title,
                                         duration=duration, vertical=vertical, log=lambda m: store.log(job_id, m))
            else:
                res = meta.post_instagram(video, caption=social_text(job, "instagram"), duration=duration,
                                          vertical=vertical, log=lambda m: store.log(job_id, m))
            posts[platform] = res
            store.update(job_id, social_posts=posts)
            store.log(job_id, f"Posted to {platform.title()}: {res['url']}")
        except Exception as e:
            errors.append(f"{platform.title()}: {e}")
            store.log(job_id, f"{platform.title()} post failed: {e}")
    store.update(job_id, social_uploading="", error=("Posting failed. " + " | ".join(errors)) if errors else None)


def narration_credit(script: dict) -> str:
    scenes = script.get("scenes") or []
    own = sum(1 for s in scenes if s.get("voice_file"))
    if scenes and own == len(scenes):
        return "Narration: recorded by the channel | आवाज़: चैनल द्वारा रिकॉर्ड की गई"
    engine = {"sarvam": "Sarvam Bulbul", "gemini": "Google Gemini TTS", "elevenlabs": "ElevenLabs"}.get(
        config.tts_engine(), "Microsoft Edge TTS")
    if own:
        return f"Narration: partly AI-generated voice ({engine}) | आवाज़: आंशिक रूप से AI-जनित"
    return f"Narration: AI-generated voice ({engine}) | आवाज़: AI-जनित"


def missing_personal(script: dict | None) -> list[int]:
    """1-based numbers of personal scenes the owner has not written yet."""
    return [i for i, s in enumerate((script or {}).get("scenes") or [], 1)
            if s.get("personal") and not s.get("narration", "").strip()]


def video_theme(job: dict) -> str:
    from . import slides
    return job.get("theme") if job.get("theme") in slides.THEMES else slides.default_theme_for(job["id"])


def compose_description(job: dict) -> str:
    meta = job.get("metadata") or {}
    text = (meta.get("description") or "").strip()
    footer = description_footer(job)
    return (text + "\n\n" + footer).strip() if footer else text


# ----------------------------------------------------------------- script ----
def run_generate_script(store: JobStore, job_id: str, feedback: str = "") -> None:
    job = store.get(job_id)
    store.update(job_id, status="generating_script", error=None, stage=f"Writing script with {llm.label()}", progress=0.0)
    store.log(job_id, "Generating script" + (" (revision)" if feedback else ""))
    llm.last_model_used = ""  # best-effort note only: two scripts writing at once may share it
    try:
        script = scriptgen.generate_script(
            job["topic"], job["kind"], job["format"], job["minutes"], job.get("notes", ""),
            previous=job.get("script") if feedback else None, feedback=feedback, repeat=job.get("repeat") or 108,
        )
    except Exception as e:
        store.update(job_id, status="script_ready" if job.get("script") else "new", stage="", error=str(e))
        store.log(job_id, f"Script generation failed: {e}")
        return
    used = llm.last_model_used
    if llm.provider() != "anthropic" and used and used != llm.model():
        store.log(job_id, f"{llm.model()} was overloaded, so {used} wrote this script.")
    _apply_script(store, job_id, script)


def set_script(store: JobStore, job_id: str, script: dict) -> None:
    job = store.get(job_id)
    clean = scriptgen.normalize_script(script, job["format"])
    _apply_script(store, job_id, clean, keep_metadata=bool(job.get("metadata")))
    if job.get("reviewed") and clean != job.get("script"):
        store.update(job_id, reviewed=None)  # the reviewed content changed


def _apply_script(store: JobStore, job_id: str, script: dict, keep_metadata: bool = False) -> None:
    job = store.get(job_id)
    words = sum(len(s["narration"].split()) for s in script["scenes"])
    store.log(job_id, f"Script ready: {len(script['scenes'])} scenes, about {words} words "
                      f"(~{words / config.WORDS_PER_MINUTE:.1f} min)")
    changes = dict(script=script, status="script_ready", stage="", progress=0.0, error=None)
    if not keep_metadata:
        changes["reviewed"] = None
    if not keep_metadata or not job.get("social"):
        changes["social"] = default_social(script)
    if not keep_metadata:
        changes["metadata"] = default_metadata(script, job)
    store.update(job_id, **changes)
    if script.get("verify_notes"):
        store.log(job_id, f"{len(script['verify_notes'])} items to verify before publishing (see Script tab).")


def derive_short(store: JobStore, job_id: str, indices: list[int]) -> dict:
    """New 9:16 Short job built from chosen scenes of a long video. Your recordings and any cached
    AI voice-over are copied across, so nothing is synthesized twice."""
    src = store.get(job_id)
    scenes = (src.get("script") or {}).get("scenes") or []
    picked = [scenes[i] for i in sorted(set(indices)) if 0 <= i < len(scenes)]
    picked = [s for s in picked if s["narration"].strip() and int(s.get("repeat") or 1) == 1]
    if not picked:
        raise ValueError("Pick at least one scene with narration (chant loops cannot go in a Short).")
    script = dict(src["script"], scenes=[{k: v for k, v in s.items() if k not in ("personal", "hint", "id")}
                                         for s in picked])
    script["verify_notes"] = list(src["script"].get("verify_notes") or [])
    url = (src.get("youtube") or {}).get("url")
    if url:
        script["description"] = (script.get("description", "") + f"\n\nपूरा वीडियो / Full video: {url}").strip()
    clean = scriptgen.normalize_script(script, "short")

    cfg = studio.normalize_studio(src["studio"]) if src.get("studio") is not None else studio.defaults_for_new()
    cfg["thumbnail"]["scene"] = ""  # scene ids differ in the new video
    new = store.create(topic=f"Short: {src['topic']}", kind=src["kind"], format="short", minutes=1,
                       voice=src.get("voice", ""), notes="", captions=src.get("captions", True),
                       music=src.get("music", True), theme=src.get("theme", ""), parent=job_id, studio=cfg,
                       storyboard=src.get("storyboard"))
    for c in (src.get("storyboard") or {}).get("characters", []):  # character sheets, for redrawing in 9:16
        if c.get("ref") and (store.job_dir(job_id) / "media" / c["ref"]).exists():
            (store.job_dir(new["id"]) / "media").mkdir(parents=True, exist_ok=True)
            shutil.copy(store.job_dir(job_id) / "media" / c["ref"], store.job_dir(new["id"]) / "media" / c["ref"])
    sd, nd = store.job_dir(job_id), store.job_dir(new["id"])
    voice = src.get("voice") or config.default_voice()
    rate, pitch = config.tts_rate(), config.tts_pitch()
    for old, sc in zip(picked, clean["scenes"]):
        if sc.get("voice_file") and (sd / "voice" / sc["voice_file"]).exists():
            (nd / "voice").mkdir(exist_ok=True)
            shutil.copy(sd / "voice" / sc["voice_file"], nd / "voice" / sc["voice_file"])
        pic = (src.get("scene_images") or {}).get(old["id"])
        if pic and pic.get("file") and (sd / "media" / pic["file"]).exists():
            (nd / "media").mkdir(exist_ok=True)
            shutil.copy(sd / "media" / pic["file"], nd / "media" / pic["file"])
            storyboard.set_scene_image(store, new["id"], sc["id"], pic["file"], by=pic.get("by", ""),
                                       prompt=pic.get("prompt", ""))
        bg = (sc.get("edit") or {}).get("bg_file")
        if bg and (sd / "media" / bg).exists():
            (nd / "media").mkdir(exist_ok=True)
            shutil.copy(sd / "media" / bg, nd / "media" / bg)
        key = tts.cache_key(sc["narration"], voice, rate, pitch, config.tts_engine())
        cached = sd / "work" / "audio" / f"{key}.mp3"
        if cached.exists():
            (nd / "work" / "audio").mkdir(parents=True, exist_ok=True)
            shutil.copy(cached, nd / "work" / "audio" / f"{key}.mp3")
    _apply_script(store, new["id"], clean)
    store.log(new["id"], f"Made from {len(clean['scenes'])} scene(s) of '{src['script'].get('title', src['topic'])}'")
    words = sum(len(s["narration"].split()) for s in clean["scenes"])
    if words / config.WORDS_PER_MINUTE * 60 > SHORT_MAX_SECONDS:
        store.warn(new["id"], f"About {words / config.WORDS_PER_MINUTE * 60:.0f}s of narration; Shorts must stay "
                              "under ~58s. Remove a scene or trim the text.")
    store.log(job_id, f"Short created from {len(clean['scenes'])} scene(s): {new['id']}")
    return store.get(new["id"])


# ----------------------------------------------------------------- render ----
BG_KEYS = ("bg_kind", "bg_path", "bg_src", "bg_query", "bg_id", "bg_credit", "bg_own", "bg_ai")


def run_render(store: JobStore, job_id: str) -> None:
    # A new render is a new video: the owner has to watch and confirm it again before upload.
    store.update(job_id, status="rendering", error=None, progress=0.0, stage="Starting", reviewed=None)
    store.log(job_id, "Render started")
    try:
        _render(store, job_id)
    except Exception as e:
        store.update(job_id, status="script_ready", stage="", error=f"Render failed: {e}")
        store.log(job_id, f"Render failed: {e}")


def run_preview(store: JobStore, job_id: str, scene_id: str) -> None:
    """Render one scene at half size into output/preview.mp4: a quick look while editing. The finished
    video and its confirmation are left alone."""
    store.update(job_id, previewing=scene_id, error=None)
    try:
        _render(store, job_id, only=scene_id)
    except Exception as e:
        store.update(job_id, error=f"Preview failed: {e}")
        store.log(job_id, f"Preview failed: {e}")
    finally:
        store.update(job_id, previewing="")


def _scene_audio(ctx, sc: dict) -> None:
    """The narration for one scene: the owner's recording (trimmed) or the AI voice, then polish and
    gain, then looped for a chant. Fills ctx.assets[scene id]."""
    store, job_id, work = ctx.store, ctx.job_id, ctx.work
    edit = sc.get("edit") or {}
    own = ctx.jd / "voice" / sc["voice_file"] if sc.get("voice_file") else None
    denoise = False
    if own and own.exists():
        v_in, v_out = edit.get("voice_in", 0.0), edit.get("voice_out", 0.0)
        tag = f"_{render.short_hash(v_in, v_out)}" if (v_in or v_out) else ""
        mp3 = work / "audio" / f"own_{own.stem}{tag}.mp3"
        if not mp3.exists():
            render.prepare_voice(own, mp3, start=v_in, end=v_out)
        denoise = True
    else:
        if own:
            store.warn(job_id, "A scene's own recording is missing, so the AI voice was used.")
        key = tts.cache_key(sc["narration"], ctx.voice, ctx.rate, ctx.pitch, config.tts_engine())
        mp3 = work / "audio" / f"{key}.mp3"
        if not mp3.exists():
            tts.synthesize(sc["narration"], mp3, voice=ctx.voice, rate=ctx.rate, pitch=ctx.pitch)
    polish = ctx.cfg["audio"]["voice_polish"]
    gain = edit.get("voice_gain", 0.0)
    chain = render.polish_chain(polish, gain, denoise)
    if chain:
        polished = work / "audio" / f"{mp3.stem}_p{render.short_hash(chain)}.mp3"
        if not polished.exists():
            render.polish_voice(mp3, polished, polish, gain, denoise)
        mp3 = polished
    speech = media.duration(mp3)
    rep = int(sc.get("repeat") or 1)
    info = {"repeat": rep, "period": 0.0, "single": speech}
    if rep > 1:  # mantra jaap: loop the chant
        looped = work / "audio" / f"{mp3.stem}_x{rep}.wav"
        if not looped.exists():
            render.repeat_audio(mp3, looped, rep, JAAP_GAP)
        info["period"] = media.duration(looped) / rep
        mp3, speech = looped, info["period"] * rep
        store.log(job_id, f"Chant repeated {rep} times ({speech / 60:.1f} min)")
    ctx.assets.setdefault(sc["id"], {}).update(audio=str(mp3), speech=speech, **info)


def _scene_picture(ctx, sc: dict, i: int, used_ids: set[int]) -> None:
    """The picture behind one scene: the owner's own footage or photo, a text slide, or stock footage
    (kept from an earlier render while the search words are unchanged). Fills ctx.assets[scene id]."""
    store, job_id, work, size = ctx.store, ctx.job_id, ctx.work, ctx.size
    v, a, edit = sc["visual"], ctx.assets[sc["id"]], sc.get("edit") or {}
    a["total"] = a["speech"] + (sc["pause_after"] if sc["pause_after"] > 0 else DEFAULT_GAP)
    start, speed = edit.get("bg_start", 0.0), edit.get("bg_speed", 1.0)

    def trimmed(src: Path) -> str:
        if not start and speed == 1.0:
            return str(src)
        out = work / "visuals" / f"clip_{render.short_hash(src.name, start, speed)}.mp4"
        if not out.exists():
            render.prepare_background(src, out, start, speed)
        return str(out)

    own = ctx.jd / "media" / edit["bg_file"] if edit.get("bg_file") else None
    if own and own.exists():
        if own.suffix.lstrip(".").lower() in studio.VIDEO_EXTS:
            a.update(bg_kind="video", bg_path=trimmed(own), bg_src=str(own))
        else:
            a.update(bg_kind="image", bg_path=str(own), bg_src="")
        a.update(bg_query="", bg_id=None, bg_credit="", bg_own=True, bg_ai="")
        return
    if own is not None:
        store.warn(job_id, "A scene's own footage or photo is missing, so the normal picture was used.")
    a["bg_own"] = False

    if v["type"] == "image":
        entry = ctx.scene_images.get(sc["id"]) or {}
        pic = ctx.jd / "media" / entry["file"] if entry.get("file") else None
        if pic and pic.exists():
            a.update(bg_kind="image", bg_path=str(pic), bg_src="", bg_query="", bg_id=None, bg_credit="",
                     bg_ai=entry.get("by") or "AI")
            return
        n = next((k + 1 for k, x in enumerate(ctx.all_scenes) if x["id"] == sc["id"]), "?")
        ctx.store.warn(ctx.job_id, f"Scene {n} has no AI picture yet, so a plain background was used. "
                                   "Draw it in the Studio.")
        variant = zlib.crc32(sc["id"].encode()) % 4
        png = work / "visuals" / f"bg{variant}_{size[0]}_{ctx.theme}.png"
        if not png.exists():
            slides.render_background(png, size, variant=variant, theme_name=ctx.theme)
        a.update(bg_kind="image", bg_path=str(png), bg_src="", bg_query="", bg_id=None, bg_credit="", bg_ai="")
        return
    a["bg_ai"] = ""

    if v["type"] == "slide":
        png = work / "visuals" / f"slide_{render.short_hash(v['heading'], v['text'], v['sub'], size, ctx.theme)}.png"
        if not png.exists():
            slides.render_slide(png, size, heading=v["heading"], text=v["text"], sub=v["sub"],
                                orientation=ctx.orientation, theme_name=ctx.theme)
        a.update(bg_kind="image", bg_path=str(png), bg_src="", bg_query="", bg_id=None, bg_credit="")
        return

    # stock: reuse the earlier pick if the query has not changed
    src = a.get("bg_src") or a.get("bg_path") or ""
    if a.get("bg_kind") == "video" and a.get("bg_id") and a.get("bg_query") == v["query"] and src and Path(src).exists():
        a.update(bg_src=src, bg_path=trimmed(Path(src)))
        return
    clip = None
    try:
        clip = visuals.fetch_clip(v["query"], size, ctx.orientation, used_ids, min_seconds=min(a["total"], 10))
    except Exception as e:
        store.log(job_id, f"Stock lookup failed for '{v['query']}': {e}")
    if clip:
        a.update(bg_kind="video", bg_src=clip["path"], bg_path=trimmed(Path(clip["path"])), bg_query=v["query"],
                 bg_id=clip["id"], bg_credit=clip["credit"])
        return
    variant = zlib.crc32(sc["id"].encode()) % 4  # stable for the scene, whatever its position
    png = work / "visuals" / f"bg{variant}_{size[0]}_{ctx.theme}.png"
    if not png.exists():
        slides.render_background(png, size, variant=variant, theme_name=ctx.theme)
    a.update(bg_kind="image", bg_path=str(png), bg_src="", bg_query=v["query"], bg_id=None, bg_credit="")
    if not ctx.warned_fallback:
        ctx.warned_fallback = True
        why = "no PEXELS_API_KEY set" if not visuals.available() else "no clip found / network problem"
        store.warn(job_id, f"Stock footage unavailable ({why}); using plain gradient backgrounds.")


def _scene_video(ctx, sc: dict, offset: float) -> tuple[Path, list]:
    """Render one scene (cached by content). Returns (scene file, subtitle entries)."""
    work = ctx.work
    a, edit, cfg = ctx.assets[sc["id"]], sc.get("edit") or {}, ctx.cfg
    W, H = ctx.out_size
    if not ctx.preview:
        a["start"] = offset  # where this scene begins in the final video (used for translated subtitles)
    srt: list[tuple[str, float, float]] = []
    counter = None
    if a.get("repeat", 1) > 1:
        # The chant is on the slide already; captions would only flicker. One SRT line per repetition.
        timed, counter = [], {"period": a["period"], "total": a["repeat"]}
        for k in range(a["repeat"]):
            srt.append((sc["narration"], offset + k * a["period"], offset + k * a["period"] + a["single"]))
    else:
        max_words = 6 if ctx.orientation == "portrait" else 8
        timed = captions.time_pages(captions.split_pages(sc["narration"], max_words), a["speech"])
    for text, t0, t1 in timed:
        srt.append((text, offset + t0, offset + t1))

    cap = cfg["captions"]
    scale = studio.CAPTION_SIZES[cap["size"]]
    overlays = []
    if ctx.use_captions:
        y = studio.caption_y(H, ctx.orientation, cap["position"])
        for text, t0, t1 in timed:
            png = work / "captions" / f"cap_{render.short_hash(text, (W, H), cap['style'], scale)}.png"
            _, cw, ch = captions.render_caption_png(text, png, W, H, ctx.orientation, style=cap["style"], scale=scale)
            overlays.append({"png": png, "w": cw, "h": ch, "start": t0, "end": t1, "y": y})
    texts = []
    ov = edit.get("overlay")
    if ov:
        png = work / "captions" / f"ov_{render.short_hash(ov['text'], (W, H), ov['pos'])}.png"
        _, _, _, y = captions.render_overlay_png(ov["text"], png, W, H, ctx.orientation, ov["pos"])
        texts.append({"png": png, "y": y, "start": ov["start"], "end": ov["end"]})
    logo = studio.effective_logo(cfg)

    is_video = a["bg_kind"] == "video"
    grade = render.grade_filter(edit.get("look", "none"), edit.get("brightness"), edit.get("saturation"), is_video)
    motion = "none" if is_video else edit.get("motion") or (
        storyboard.default_motion(sc["id"]) if a.get("bg_ai") else "none")  # AI stills always move a little
    fade, crf = cfg["transition"]["fade"], ctx.crf
    preset = "ultrafast" if ctx.preview else config.x264_preset()
    sh = render.short_hash(
        sc["narration"], sc["pause_after"], a["bg_path"], (W, H), ctx.use_captions,
        [(o["start"], o["end"], o["y"]) for o in overlays], preset, a["audio"], counter, grade, motion, fade, crf,
        [(t["png"].name, t["y"], t["start"], t["end"]) for t in texts],
        (logo["stamp"], logo["corner"], logo["size"], logo["opacity"]) if logo else None, n=12,
    )
    scene_file = work / "scenes" / f"{sh}.mp4"  # named by content, so moving a scene re-renders nothing
    if not scene_file.exists():
        render.render_scene(
            out=scene_file, size=(W, H), orientation=ctx.orientation, bg_kind=a["bg_kind"],
            bg_path=Path(a["bg_path"]), audio=Path(a["audio"]), total_dur=a["total"], caption_overlays=overlays,
            preset=preset, counter=counter, motion=motion, grade=grade, fade=fade, crf=crf, overlays=texts, logo=logo,
        )
    return scene_file, srt


def _render(store: JobStore, job_id: str, only: str | None = None) -> None:
    """Render the whole video, or (with `only`) just one scene at half size as a preview."""
    preview = only is not None
    job = store.get(job_id)
    script = job["script"]
    if not script:
        raise RuntimeError("No script yet.")
    fmt = config.FORMATS[job["format"]]
    size = (fmt["w"], fmt["h"])
    orientation = fmt["orientation"]
    scenes = [sc for sc in script["scenes"] if sc["id"] == only] if preview else script["scenes"]
    if not scenes:
        raise RuntimeError("That scene no longer exists.")
    n = len(scenes)

    if preview:
        if not scenes[0]["narration"].strip():
            raise RuntimeError("Write this scene's narration first.")
    else:
        todo = missing_personal(script)
        if todo:
            raise RuntimeError(f"Write your personal reflection first (scene {', '.join(map(str, todo))}).")
    if not media.have_ffmpeg():
        raise RuntimeError("ffmpeg/ffprobe not found on PATH. Install ffmpeg first.")
    if not textutil.raqm_available():
        store.warn(job_id, "Pillow has no Raqm text shaping, so Hindi conjuncts and matras on slides, captions and "
                           "the thumbnail may look wrong. See the README (Troubleshooting).")

    cfg = studio.effective(job)
    jd = store.job_dir(job_id)
    work, out_dir = jd / "work", jd / "output"
    out_dir.mkdir(exist_ok=True)
    assets = dict(job.get("assets") or {})
    ctx = SimpleNamespace(
        store=store, job_id=job_id, cfg=cfg, jd=jd, work=work, assets=assets, size=size, orientation=orientation,
        out_size=(size[0] // 2, size[1] // 2) if preview else size, preview=preview, theme=video_theme(job),
        voice=job.get("voice") or config.default_voice(), rate=config.tts_rate(), pitch=config.tts_pitch(),
        use_captions=job.get("captions", True), crf=28 if preview else studio.crf_for(cfg), warned_fallback=False,
        scene_images=job.get("scene_images") or {}, all_scenes=script["scenes"],
    )

    # 1) Voice-over ------------------------------------------------------------
    total_speech = 0.0
    for i, sc in enumerate(scenes):
        store.set_stage(job_id, f"Voice-over {i + 1}/{n}", 0.25 * i / n)
        _scene_audio(ctx, sc)
        total_speech += assets[sc["id"]]["speech"]
    store.log(job_id, f"Voice-over done: {total_speech:.0f}s of speech")
    if job["format"] == "short" and total_speech > SHORT_MAX_SECONDS and not preview:
        store.warn(job_id, f"Narration is {total_speech:.0f}s; Shorts should stay under ~58s. Trim the script.")

    # 2) Pictures --------------------------------------------------------------
    used_ids: set[int] = {a["bg_id"] for a in assets.values() if a.get("bg_id")}
    for i, sc in enumerate(scenes):
        store.set_stage(job_id, f"Visuals {i + 1}/{n}", 0.25 + 0.20 * i / n)
        _scene_picture(ctx, sc, i, used_ids)
    if not preview:
        store.update(job_id, assets=assets)

    # 3) Scenes ----------------------------------------------------------------
    scene_files: list[Path] = []
    srt: list[tuple[str, float, float]] = []
    offset = 0.0
    for i, sc in enumerate(scenes):
        store.set_stage(job_id, f"Rendering scene {i + 1}/{n}", 0.45 + 0.43 * i / n)
        scene_file, entries = _scene_video(ctx, sc, offset)
        scene_files.append(scene_file)
        srt += entries
        offset += assets[sc["id"]]["total"]

    if preview:
        shutil.copy(scene_files[0], out_dir / "preview.mp4")
        # Keep which picture was chosen (so the final render uses the same clip), not the timings.
        saved = dict(store.get(job_id).get("assets") or {})
        # ...and its voice-over file, so the Studio can draw the scene's sound.
        saved.setdefault(only, {}).update({k: assets[only][k] for k in (*BG_KEYS, "audio") if k in assets[only]})
        store.update(job_id, assets=saved, stage="", progress=0.0,
                     preview={"scene": only, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "duration": round(assets[only]["total"], 1)})
        store.log(job_id, "Scene preview ready")
        return

    # 4) Assemble, music, loudness ------------------------------------------------
    store.set_stage(job_id, "Assembling video", 0.90)
    silent = work / "concat.mp4"
    render.concat_scenes(scene_files, silent)
    final = out_dir / "video.mp4"
    au = cfg["audio"]
    music_label, stage_file = "none", silent
    if job.get("music", True):
        mu = au["music"]
        track, music_label = render.pick_music(job_id, mu["track"])
        vol = mu["volume"] if mu["volume"] is not None else float(
            job.get("music_volume") or config.KINDS[job["kind"]]["music_volume"])
        stage_file = work / "mixed.mp4"
        render.mix_music(silent, track, stage_file, offset, vol, ducking=mu["ducking"], fade_in=mu["fade_in"],
                         fade_out=mu["fade_out"], start=mu["start"])
        if "placeholder" in music_label:
            store.warn(job_id, "No music files in assets/music/, so a plain generated drone was used. "
                               "Add royalty-free tracks there for a better result.")
        elif mu["track"] and mu["track"] != music_label:
            store.warn(job_id, f"The music track '{mu['track']}' is no longer in the library, so '{music_label}' was used.")
        if track.parent == config.MUSIC_DIR and not studio.library_license(track.name):
            store.warn(job_id, f"No licence note for the music '{track.name}'. Add one in the Studio so you "
                               "remember the track is cleared for YouTube, Instagram and Facebook.")
    loudness = None
    if au["master"]:
        store.set_stage(job_id, "Setting the loudness", 0.93)
        loudness = render.master(stage_file, final, au["lufs"])
        if loudness["applied"]:
            store.log(job_id, f"Loudness {loudness['before']} LUFS to {loudness['after']} LUFS (target {au['lufs']:g})")
    else:
        shutil.copy(stage_file, final)
    total = media.duration(final)
    render.write_srt(srt, out_dir / "captions.srt")
    mp3_path = out_dir / "audio.mp3"
    if cfg["export"]["audio_mp3"]:
        render.audio_mp3(final, mp3_path)
    else:
        mp3_path.unlink(missing_ok=True)

    # 5) Thumbnail ---------------------------------------------------------------
    store.set_stage(job_id, "Thumbnail", 0.96)
    frame = None
    chosen = cfg["thumbnail"]["scene"]
    for sc in sorted(scenes, key=lambda s: s["id"] != chosen):  # the chosen scene first
        a = assets[sc["id"]]
        if a.get("bg_kind") == "video":
            frame = work / "thumb_frame.png"
            try:
                media.extract_frame(Path(a["bg_path"]), frame, at=1.0)
            except media.MediaError:
                frame = None
            break
        if sc["id"] == chosen or (not chosen and a.get("bg_ai")):
            frame = Path(a["bg_path"])
            break
    thumbnail.render_thumbnail(out_dir / "thumbnail.jpg", script["thumbnail_text"], frame,
                               config.channel_name(), theme_name=ctx.theme)

    outputs = {
        "video": "output/video.mp4", "thumbnail": "output/thumbnail.jpg", "srt": "output/captions.srt",
        "duration": round(total, 1), "music": music_label, "size_mb": round(final.stat().st_size / 1e6, 1),
    }
    if loudness and loudness["applied"]:
        outputs["loudness"] = {**loudness, "target": au["lufs"]}
    if cfg["export"]["audio_mp3"]:
        outputs["audio"] = "output/audio.mp3"
    store.update(job_id, status="rendered", stage="Done", progress=1.0, assets=assets, outputs=outputs)
    store.log(job_id, f"Render finished: {total:.0f}s, {final.stat().st_size / 1e6:.1f} MB")
    # Timings changed, so rebuild English subtitles from saved translations (no API call), or drop stale ones.
    try:
        ok = write_english_srt(store, job_id, allow_api=False)
    except Exception as e:  # never fail a finished render over subtitles
        store.log(job_id, f"English subtitles not rebuilt: {e}")
        ok = False
    if not ok:
        (out_dir / "captions.en.srt").unlink(missing_ok=True)
        outputs = dict(store.get(job_id).get("outputs") or {})
        if outputs.pop("srt_en", None):
            store.update(job_id, outputs=outputs)


# -------------------------------------------------------- English subtitles ----
def _tkey(text: str) -> str:
    return render.short_hash(text)


def write_english_srt(store: JobStore, job_id: str, allow_api: bool = True) -> bool:
    """Write output/captions.en.srt from the rendered scene timings. Translations are cached on the
    job, so after a re-render only new or edited narration goes to the AI writer. Returns False when it
    cannot (nothing rendered, or translations missing and allow_api is False)."""
    job = store.get(job_id)
    script, assets = job.get("script") or {}, job.get("assets") or {}
    scenes = script.get("scenes") or []
    if not scenes or not (job.get("outputs") or {}).get("video"):
        return False
    if any("start" not in assets.get(sc["id"], {}) for sc in scenes):
        return False
    cache = dict((job.get("translations") or {}).get("en") or {})
    todo = list(dict.fromkeys(sc["narration"] for sc in scenes if _tkey(sc["narration"]) not in cache))
    if todo:
        if not allow_api:
            return False
        for src, en in zip(todo, scriptgen.translate_lines(todo)):
            cache[_tkey(src)] = en
        store.update(job_id, translations={**(job.get("translations") or {}), "en": cache})

    entries: list[tuple[str, float, float]] = []
    for sc in scenes:
        a, en = assets[sc["id"]], cache[_tkey(sc["narration"])]
        if a.get("repeat", 1) > 1:
            for k in range(a["repeat"]):
                t0 = a["start"] + k * a["period"]
                entries.append((en, t0, t0 + a["single"]))
        else:
            for text, t0, t1 in captions.time_pages(captions.split_pages(en, 12), a["speech"]):
                entries.append((text, a["start"] + t0, a["start"] + t1))
    out = store.job_dir(job_id) / "output" / "captions.en.srt"
    render.write_srt(entries, out)
    outputs = dict(store.get(job_id).get("outputs") or {})
    outputs["srt_en"] = "output/captions.en.srt"
    store.update(job_id, outputs=outputs)
    return True


def run_translate(store: JobStore, job_id: str) -> None:
    store.update(job_id, translating=True, error=None)
    store.log(job_id, "Translating subtitles to English")
    try:
        if not write_english_srt(store, job_id):
            raise RuntimeError("Render the video first.")
        store.log(job_id, "English subtitles ready")
    except Exception as e:
        store.update(job_id, error=f"Translation failed: {e}")
        store.log(job_id, f"Translation failed: {e}")
    finally:
        store.update(job_id, translating=False)


# ----------------------------------------------------------------- upload ----
def run_upload(store: JobStore, job_id: str, privacy: str | None = None, publish_at: str | None = None) -> None:
    from . import uploader

    job = store.get(job_id)
    jd = store.job_dir(job_id)
    meta = dict(job["metadata"] or {})
    if privacy:
        meta["privacy"] = privacy
    if publish_at is not None:
        meta["publish_at"] = publish_at
    store.update(job_id, status="uploading", error=None, stage="Uploading to YouTube", progress=0.0, metadata=meta)
    store.log(job_id, f"Uploading ({meta.get('privacy')})")
    try:
        result = uploader.upload_video(
            jd / job["outputs"]["video"], jd / job["outputs"]["thumbnail"],
            title=meta["title"], description=compose_description(store.get(job_id)), tags=meta.get("tags", []),
            category=meta.get("category") or config.yt_category(), privacy=meta.get("privacy", "private"),
            publish_at=meta.get("publish_at") or None, made_for_kids=bool(meta.get("made_for_kids")),
            synthetic=bool(meta.get("synthetic", True)),
            on_progress=lambda p: store.set_stage(job_id, "Uploading to YouTube", p),
            on_log=lambda m: store.log(job_id, m),
        )
    except Exception as e:
        store.update(job_id, status="rendered", stage="", error=f"Upload failed: {e}")
        store.log(job_id, f"Upload failed: {e}")
        return
    store.update(job_id, status="uploaded", stage="Done", progress=1.0, youtube=result)
    store.log(job_id, f"Uploaded: {result['url']}")
    # Caption tracks help search and reach viewers outside India; a failure here never undoes the upload.
    tracks = [(jd / "output" / "captions.srt", "hi", "हिन्दी"), (jd / "output" / "captions.en.srt", "en", "English")]
    try:
        uploader.upload_captions(result["video_id"], tracks, lambda m: store.log(job_id, m))
    except Exception as e:
        store.log(job_id, f"Caption tracks not uploaded: {e}")
