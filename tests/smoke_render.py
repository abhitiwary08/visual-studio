"""Offline smoke test of the render pipeline.

Replaces the network pieces (Edge TTS, Pexels) with ffmpeg-generated stand-ins, so it
needs no API keys or internet:   python tests/smoke_render.py [workdir]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="yt_smoke_"))
os.environ["YT_DATA_DIR"] = str(WORK / "data")
os.environ["X264_PRESET"] = "ultrafast"

from app import config, media, pipeline, tts, visuals  # noqa: E402
from app.jobs import JobStore  # noqa: E402


def fake_tts(text, out, **kw):
    words = max(3, len(text.split()))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    media.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        f"sine=frequency=180:sample_rate=44100:duration={words * 0.42:.2f}",
        "-af", "volume=0.4", "-c:a", "libmp3lame", str(out),
    ])
    return out


_clip = WORK / "fake_stock.mp4"


def fake_fetch_clip(query, size, orientation, used, min_seconds=0):
    if not _clip.exists():
        media.run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "gradients=s=1280x720:d=6:speed=0.03:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(_clip),
        ])
    used.add(len(used) + 1000)
    return {"path": str(_clip), "id": 1000 + len(used), "credit": "Test (pexels.com)"}


tts.synthesize = fake_tts
visuals.fetch_clip = fake_fetch_clip

SCRIPT = {
    "title": "नचिकेता और यमराज की कथा",
    "description": "एक बालक की निडर जिज्ञासा। #उपनिषद #नचिकेता",
    "tags": ["upanishad", "नचिकेता", "katha upanishad"],
    "thumbnail_text": "नचिकेता और यमराज का रहस्य",
    "sources": ["Katha Upanishad"],
    "verify_notes": ["Check the story order against a trusted translation."],
    "scenes": [
        {"narration": "क्या आप जानते हैं कि एक छोटे बालक ने मृत्यु के देवता से सबसे बड़ा प्रश्न पूछा था?",
         "visual": {"type": "stock", "query": "sunrise over river"}},
        {"narration": "यह कथा कठोपनिषद में आती है। नचिकेता के पिता ने एक यज्ञ किया था।",
         "visual": {"type": "slide", "heading": "कठोपनिषद", "text": "नचिकेता की कथा",
                    "sub": "सत्य की खोज करने वाला बालक"}},
        {"narration": "नचिकेता तीन दिन तक यमराज के द्वार पर प्रतीक्षा करता रहा। उसने भोजन और जल का भी त्याग कर दिया।",
         "pause_after": 2, "visual": {"type": "stock", "query": "forest path mist"}},
        {"narration": "ऐसी और कथाओं के लिए चैनल को सब्सक्राइब करें।",
         "visual": {"type": "slide", "heading": "सब्सक्राइब करें", "text": "ॐ शांति शांति शांति"}},
    ],
}


def run(fmt: str) -> Path:
    store = JobStore()
    job = store.create(topic="Nachiketa", kind="upanishad_story", format=fmt, minutes=1, notes="", voice="")
    pipeline.set_script(store, job["id"], SCRIPT)
    pipeline.run_render(store, job["id"])
    job = store.get(job["id"])
    print(f"[{fmt}] status={job['status']} error={job['error']} warnings={job['warnings']}")
    assert job["status"] == "rendered", job["error"]
    jd = store.job_dir(job["id"])
    video = jd / job["outputs"]["video"]
    info = media.probe(video)
    kinds = {s["codec_type"]: s["codec_name"] for s in info["streams"]}
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    expected = sum(a["total"] for a in job["assets"].values())
    dur = float(info["format"]["duration"])
    print(f"[{fmt}] {kinds} {vs['width']}x{vs['height']} dur={dur:.2f}s expected~{expected:.2f}s "
          f"size={job['outputs']['size_mb']}MB music={job['outputs']['music']}")
    assert abs(dur - expected) < 0.6, (dur, expected)
    assert (jd / job["outputs"]["thumbnail"]).stat().st_size < 2_000_000
    # frames for visual inspection: stock scene with caption, shloka slide with caption
    t_stock = 1.5
    t_slide = job["assets"]["s01"]["total"] + 1.2
    for name, t in (("stock", t_stock), ("slide", t_slide)):
        media.extract_frame(video, WORK / f"frame_{fmt}_{name}.png", at=t)
    print(pipeline.compose_description(job).splitlines()[-4:])
    return video


def run_jaap() -> Path:
    """Mantra jaap with the on-screen counter, plus a personal scene in the owner's own (stand-in) voice."""
    store = JobStore()
    job = store.create(topic="Om Namah Shivaya", kind="mantra_jaap", format="long", minutes=1, repeat=5,
                       theme="saffron_dawn")
    jd = store.job_dir(job["id"])
    # stand-in recording with 1.5 s of silence at both ends, which prepare_voice should trim
    own = jd / "voice" / "0123456789ab.wav"
    own.parent.mkdir(parents=True)
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.5",
               "-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.5",
               "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(own)])
    pipeline.set_script(store, job["id"], {"title": "ॐ नमः शिवाय जाप", "scenes": [
        {"narration": "आइए शांत होकर जाप करें।", "visual": {"type": "stock", "query": "temple"}},
        {"narration": "ॐ नमः शिवाय", "repeat": 5, "visual": {"type": "slide", "text": "ॐ नमः शिवाय", "sub": "शिव को नमन"}},
        {"narration": "मेरे अनुभव में यह जाप मन को शांत करता है।", "personal": True, "voice_file": own.name,
         "visual": {"type": "stock", "query": "lotus"}},
    ]})
    pipeline.run_render(store, job["id"])
    job = store.get(job["id"])
    print(f"[jaap] status={job['status']} error={job['error']}")
    assert job["status"] == "rendered", job["error"]
    a = job["assets"]
    assert a["s02"]["repeat"] == 5 and abs(a["s02"]["speech"] - 5 * a["s02"]["period"]) < 0.01
    assert 2.6 < a["s03"]["speech"] < 3.4, a["s03"]["speech"]  # silence trimmed from the 6 s recording
    video = jd / job["outputs"]["video"]
    dur = media.duration(video)
    expected = sum(x["total"] for x in a.values())
    print(f"[jaap] dur={dur:.2f}s expected~{expected:.2f}s period={a['s02']['period']:.2f}s own={a['s03']['speech']:.2f}s")
    assert abs(dur - expected) < 0.6, (dur, expected)
    srt = (jd / "output" / "captions.srt").read_text(encoding="utf-8")
    assert srt.count("ॐ नमः शिवाय") == 5
    t = a["s02"]["start"] + 2.5 * a["s02"]["period"]  # during the 3rd repetition
    media.extract_frame(video, WORK / "frame_jaap_counter.png", at=t)
    print("[jaap]", pipeline.description_footer(job).splitlines()[-1])
    return video


def run_studio() -> Path:
    """Every studio feature through real ffmpeg: own photo with movement, trimmed and sped-up footage,
    a trimmed recording, on-screen text, logo, caption style, voice polish, music, loudness, MP3, preview."""
    from PIL import Image, ImageDraw

    from app import render, studio

    # keep branding and the music library inside the work folder, never the real project
    studio.LOGO_FILE, studio.BRANDING_FILE = WORK / "logo.png", WORK / "branding.json"
    studio.LICENSE_FILE = WORK / "music" / "licenses.json"
    (WORK / "music").mkdir(exist_ok=True)
    config.MUSIC_DIR = WORK / "music"

    # real upload validation: a photo, a clip, a track and a logo, all checked with ffprobe / Pillow
    photo = Image.new("RGB", (1600, 900), (30, 90, 160))
    d = ImageDraw.Draw(photo)
    for k in range(8):
        d.ellipse([100 + k * 170, 150 + (k % 3) * 160, 300 + k * 170, 350 + (k % 3) * 160], fill=(240, 180 - k * 12, 40 + k * 20))
    photo_path = WORK / "photo.png"; photo.save(photo_path)
    logo = Image.new("RGBA", (300, 300), (255, 0, 255, 255)); logo.save(WORK / "logo_src.png")
    studio.save_logo((WORK / "logo_src.png").read_bytes())
    studio.save_branding(corner="tr", size=0.12, opacity=0.85, auto=True)
    track = WORK / "calm.wav"
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=20", "-ar", "44100", str(track)])
    saved = studio.save_music("calm.wav", track.read_bytes())
    studio.set_license("calm.wav", "test tone, made for the smoke test")
    assert saved["duration"] > 19 and studio.library()[0]["license"].startswith("test tone")

    store = JobStore()
    job = store.create(topic="studio", kind="upanishad_story", format="long", minutes=1)
    jd = store.job_dir(job["id"])
    up = studio.save_job_media(jd, photo_path.read_bytes(), "png")
    assert up["kind"] == "image" and up["width"] == 1600
    clip_info = studio.save_job_media(jd, (WORK / "fake_stock.mp4").read_bytes(), "mp4") if (WORK / "fake_stock.mp4").exists() else None
    for bad, ext in ((b"x" * 5000, "mp4"), (b"x" * 5000, "png")):
        try:
            studio.save_job_media(jd, bad, ext); raise AssertionError("garbage accepted")
        except ValueError:
            pass
    # a recording: 1.2 s of silence, a 4 s tone, 1.2 s of silence; the scene keeps seconds 1.0 to 4.0
    rec = jd / "voice" / "0123456789ab.wav"; rec.parent.mkdir(parents=True)
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.2",
               "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.2",
               "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(rec)])
    wf = studio.waveform(rec, 100)
    assert abs(wf["duration"] - 6.4) < 0.05 and wf["peaks"][0] < 0.01 and max(wf["peaks"][30:70]) > 0.1

    store.update(job["id"], studio={
        "captions": {"style": "outline", "position": "middle", "size": "large"}, "transition": {"fade": 0.5},
        "logo": {"show": None}, "thumbnail": {"scene": "s03"}, "export": {"quality": "small", "audio_mp3": True},
        "audio": {"voice_polish": "studio", "master": True, "lufs": -14,
                  "music": {"track": "calm.wav", "volume": 0.15, "ducking": "gentle", "fade_in": 1, "fade_out": 2}}})
    pipeline.set_script(store, job["id"], {"title": "स्टूडियो", "scenes": [
        {"narration": "यह पहला दृश्य है, जिसमें फुटेज तेज़ चलता है।",
         "visual": {"type": "stock", "query": "river"},
         "edit": {"bg_start": 1, "bg_speed": 2, "look": "warm",
                  "overlay": {"text": "अध्याय एक", "pos": "top", "start": 0.3, "end": 2.0}}},
        {"narration": "यह दूसरा दृश्य एक स्लाइड है।", "visual": {"type": "slide", "heading": "उपनिषद", "text": "सत्यमेव जयते"},
         "edit": {"motion": "zoom_in", "voice_gain": 3}},
        {"narration": "मेरा अपना अनुभव।", "visual": {"type": "stock"}, "voice_file": rec.name,
         "edit": {"bg_file": up["bg_file"], "motion": "pan_right", "look": "mono", "voice_in": 1.0, "voice_out": 4.0}},
    ]})
    pipeline.run_render(store, job["id"])
    job = store.get(job["id"])
    print(f"[studio] status={job['status']} error={job['error']}")
    assert job["status"] == "rendered", job["error"]
    a = job["assets"]
    assert a["s02"]["speech"] > 0 and 2.2 < a["s03"]["speech"] < 3.3, a["s03"]["speech"]  # silence cut, 3 s kept
    video = jd / job["outputs"]["video"]
    dur = media.duration(video)
    expected = sum(x["total"] for x in a.values())
    loud = job["outputs"]["loudness"]
    measured = render.loudnorm_measure(video, -14)["input_i"]
    print(f"[studio] dur={dur:.2f}s expected~{expected:.2f}s loudness {loud['before']} -> {loud['after']} LUFS "
          f"(measured again {measured:.1f}) size={job['outputs']['size_mb']}MB")
    assert abs(dur - expected) < 0.8, (dur, expected)
    assert abs(measured - (-14)) < 1.5, measured
    mp3 = jd / job["outputs"]["audio"]
    assert mp3.exists() and abs(media.duration(mp3) - dur) < 0.6

    # frames: overlay text and look on scene 1, zoom on scene 2, mono photo on scene 3, logo everywhere
    t1, t2, t3 = a["s01"]["start"], a["s02"]["start"], a["s03"]["start"]
    media.extract_frame(video, WORK / "studio_s01.png", at=t1 + 1.0)
    media.extract_frame(video, WORK / "studio_s02_early.png", at=t2 + 0.7)
    media.extract_frame(video, WORK / "studio_s02_late.png", at=t2 + a["s02"]["total"] - 0.7)
    media.extract_frame(video, WORK / "studio_s03.png", at=t3 + 1.2)
    early, late = Image.open(WORK / "studio_s02_early.png").convert("L"), Image.open(WORK / "studio_s02_late.png").convert("L")
    from PIL import ImageChops
    assert ImageChops.difference(early, late).getbbox() is not None, "no zoom happened"
    mono = Image.open(WORK / "studio_s03.png").convert("RGB")
    px = [mono.getpixel((x, y)) for x in range(200, 1500, 130) for y in range(250, 800, 120)]
    assert sum(1 for r, g, b in px if max(r, g, b) - min(r, g, b) < 14) > 0.8 * len(px), "photo is not black and white"
    for name in ("s01", "s02_early", "s03"):
        im = Image.open(WORK / f"studio_{name}.png").convert("RGB")
        r, g, b = im.getpixel((1748, 172))
        assert r > 150 and b > 150 and g < 110, (name, (r, g, b))  # the magenta logo, top right

    # the scene preview: half size, fast, and the finished video is left alone
    before = store.get(job["id"])["outputs"]
    pipeline.run_preview(store, job["id"], "s01")
    j = store.get(job["id"])
    assert not j["error"] and j["outputs"] == before and j["preview"]["scene"] == "s01"
    pv = media.probe(jd / "output" / "preview.mp4")
    vs = next(x for x in pv["streams"] if x["codec_type"] == "video")
    print(f"[studio] preview {vs['width']}x{vs['height']} {float(pv['format']['duration']):.1f}s")
    assert (vs["width"], vs["height"]) == (960, 540)
    return video


if __name__ == "__main__":
    for f in ("long", "short"):
        run(f)
    run_jaap()
    run_studio()
    print("WORKDIR", WORK)
