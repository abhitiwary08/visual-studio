"""Tests for the roadmap features in TASKS.md (no network, no ffmpeg):  python tests/test_features.py"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["YT_DATA_DIR"] = tempfile.mkdtemp(prefix="yt_feat_")
os.environ["MAX_UPLOADS_PER_DAY"] = "2"

from fastapi.testclient import TestClient  # noqa: E402

from app import config, pipeline, scriptgen, server, slides, tts  # noqa: E402
from app import llm as _llm  # noqa: E402

# Never use the real AI-writer choice or API keys from config/llm.json and .env.
_llm.SETTINGS_FILE = Path(tempfile.mkdtemp(prefix="yt_llm_")) / "llm.json"
from app import meta as _meta  # noqa: E402
_meta.TOKEN_FILE = Path(tempfile.mkdtemp(prefix="yt_meta_")) / "meta_token.json"
for _k in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)

H = {"X-Dashboard": "1"}
client = TestClient(server.app, base_url="http://127.0.0.1")


def _script(**extra):
    base = {"title": "शीर्षक", "description": "विवरण", "tags": ["a"], "thumbnail_text": "x", "verify_notes": ["v1"],
            "scenes": [{"narration": "पहला दृश्य", "visual": {"type": "stock", "query": "river"}},
                       {"narration": "", "personal": True, "hint": "अपना अनुभव", "visual": {"type": "stock"}},
                       {"narration": "अंत", "visual": {"type": "slide", "text": "ॐ"}}]}
    base.update(extra)
    return base


def _job_with_script(script=None, fmt="long"):
    job = server.store.create(topic="t", kind="upanishad_story", format=fmt, minutes=5)
    pipeline.set_script(server.store, job["id"], script or _script())
    return job["id"]


# ------------------------------------------------------------ T0 restart ----
def test_t0_recover_interrupted_render():
    job = server.store.create(topic="t", kind="quote", format="short", minutes=1)
    server.store.update(job["id"], status="rendering")
    server.store.recover_interrupted()
    assert server.store.get(job["id"])["status"] == "script_ready"


# --------------------------------------------------------- T1 own voice ----
def test_t1_voice_file_validated():
    s = scriptgen.normalize_script({"scenes": [
        {"narration": "a", "voice_file": "0123456789ab.webm", "visual": {"type": "stock"}},
        {"narration": "b", "voice_file": "../../etc/passwd", "visual": {"type": "stock"}},
    ]}, "long")
    assert s["scenes"][0]["voice_file"] == "0123456789ab.webm"
    assert "voice_file" not in s["scenes"][1]


def test_t1_voice_upload_endpoint_and_credit():
    jid = _job_with_script()
    r = client.post(f"/api/jobs/{jid}/voice?ext=webm", content=b"x" * 5000, headers=H)
    assert r.status_code == 200, r.text
    name = r.json()["voice_file"]
    assert scriptgen.VOICE_FILE_RE.fullmatch(name)
    assert client.get(f"/voice/{jid}/{name}").content == b"x" * 5000
    assert client.post(f"/api/jobs/{jid}/voice?ext=exe", content=b"x" * 5000, headers=H).status_code == 400
    assert client.post(f"/api/jobs/{jid}/voice?ext=mp3", content=b"x", headers=H).status_code == 400
    assert client.get(f"/voice/{jid}/..%2F..%2Fjob.json").status_code == 404

    sc = _script()
    sc["scenes"][1]["narration"] = "मेरा अनुभव"
    assert "AI-generated" in pipeline.narration_credit(scriptgen.normalize_script(sc, "long"))
    sc["scenes"][0]["voice_file"] = name
    assert "partly" in pipeline.narration_credit(scriptgen.normalize_script(sc, "long"))
    for s in sc["scenes"]:
        s["voice_file"] = name
    assert "recorded by the channel" in pipeline.narration_credit(scriptgen.normalize_script(sc, "long"))


# ------------------------------------------------------ T2 personal scene ----
def test_t2_personal_scene_kept_and_blocks_render():
    s = scriptgen.normalize_script(_script(), "long")
    assert len(s["scenes"]) == 3 and s["scenes"][1]["personal"] and s["scenes"][1]["hint"] == "अपना अनुभव"
    assert pipeline.missing_personal(s) == [2]
    # a script made only of an empty personal scene is still unusable
    try:
        scriptgen.normalize_script({"scenes": [{"narration": "", "personal": True}]}, "long")
        raise AssertionError("should have failed")
    except scriptgen.ScriptError:
        pass
    jid = _job_with_script()
    r = client.post(f"/api/jobs/{jid}/render", headers=H)
    assert r.status_code == 400 and "reflection" in r.json()["detail"]
    assert "personal" in scriptgen.SCRIPT_TOOL["input_schema"]["properties"]["scenes"]["items"]["properties"]
    assert '"personal": true' in scriptgen.SYSTEM


# ---------------------------------------------------------- T3 themes ----
def test_t3_themes(tmp=Path(tempfile.mkdtemp())):
    assert slides.default_theme_for("20261012-abc") == slides.default_theme_for("20261012-abc")
    seen = {slides.default_theme_for(f"job-{i}") for i in range(40)}
    assert len(seen) == len(slides.THEMES)  # rotation reaches every theme
    assert slides.theme("nope") is slides.THEMES[slides.DEFAULT_THEME]
    a = slides.render_slide(tmp / "a.png", (640, 360), heading="ह", text="ॐ", sub="अर्थ", theme_name="forest_ashram")
    b = slides.render_slide(tmp / "b.png", (640, 360), heading="ह", text="ॐ", sub="अर्थ", theme_name="lotus_dusk")
    assert a.read_bytes() != b.read_bytes()
    from app import thumbnail
    thumbnail.render_thumbnail(tmp / "t.jpg", "नचिकेता", None, "चैनल", theme_name="ganga_river")
    jid = _job_with_script()
    r = client.put(f"/api/jobs/{jid}/settings", json={"theme": "saffron_dawn", "music": False}, headers=H)
    assert r.status_code == 200 and r.json()["theme_effective"] == "saffron_dawn" and r.json()["music"] is False
    assert client.put(f"/api/jobs/{jid}/settings", json={"theme": "bad"}, headers=H).status_code == 400
    assert set(client.get("/api/options").json()["themes"]) == set(slides.THEMES)


# ------------------------------------------------------ T4 review gate ----
def test_t4_review_gate_and_reset():
    jid = _job_with_script()
    assert client.post(f"/api/jobs/{jid}/review", json={"reviewed": True}, headers=H).status_code == 400  # no video yet
    server.store.update(jid, status="rendered", outputs={"video": "output/video.mp4", "thumbnail": "output/thumbnail.jpg"})
    r = client.post(f"/api/jobs/{jid}/upload", json={"privacy": "private"}, headers=H)
    assert r.status_code == 400 and "watched" in r.json()["detail"]
    r = client.post(f"/api/jobs/{jid}/review", json={"reviewed": True}, headers=H)
    assert r.json()["reviewed"]["verify_notes"] == 1
    # editing the script clears the confirmation
    sc = server.store.get(jid)["script"]
    sc["scenes"][0]["narration"] = "बदला हुआ"
    client.put(f"/api/jobs/{jid}/script", json={"script": sc}, headers=H)
    assert not server.store.get(jid)["reviewed"]
    # saving an unchanged script keeps it
    client.post(f"/api/jobs/{jid}/review", json={"reviewed": True}, headers=H)
    client.put(f"/api/jobs/{jid}/script", json={"script": server.store.get(jid)["script"]}, headers=H)
    assert server.store.get(jid)["reviewed"]


# ------------------------------------------------- T10 voice engine ----
def test_t10_pronunciation_and_sarvam_helpers():
    m = {"ब्रह्म": "ब्रह्-म", "ॐ": "ओम"}
    assert tts.apply_pronunciation("ब्रह्म सत्य है", m) == "ब्रह्-म सत्य है"
    assert tts.apply_pronunciation("ब्रह्मा जी", m) == "ब्रह्मा जी"  # no partial-word match across a matra
    assert tts.clean_for_speech("**ॐ** शांति", m) == "ओम शांति"
    assert tts.rate_to_pace("-8%") == 0.92 and tts.rate_to_pace("+300%") == 2.0 and tts.rate_to_pace("x") == 1.0
    long = "यह एक वाक्य है। " * 400
    parts = tts.split_for_api(long, 500)
    assert all(len(p) <= 500 for p in parts) and "".join(parts).replace(" ", "") == long.replace(" ", "")
    assert tts.split_for_api("छोटा") == ["छोटा"]

    def wav(n):
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b"\0\0" * n)
        return b.getvalue()
    with wave.open(io.BytesIO(tts.join_wavs([wav(100), wav(250)]))) as w:
        assert w.getnframes() == 350
    assert tts.cache_key("a", "v", "r", "p") != tts.cache_key("a", "v", "r", "p", "sarvam")
    os.environ["TTS_ENGINE"] = "sarvam"
    try:
        assert config.voices() is config.SARVAM_VOICES and config.default_voice() == "shubh"
    finally:
        os.environ["TTS_ENGINE"] = "edge"


# ------------------------------------------------- T11 upload pacing ----
def test_t11_upload_limit():
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    before = server.uploads_last_24h()
    for _ in range(2):
        j = server.store.create(topic="u", kind="quote", format="short", minutes=1)
        server.store.update(j["id"], status="uploaded", youtube={"uploaded_at": stamp})
    old = server.store.create(topic="u", kind="quote", format="short", minutes=1)
    server.store.update(old["id"], status="uploaded", youtube={"uploaded_at": "2020-01-01 00:00:00"})
    assert server.uploads_last_24h() == before + 2
    jid = _job_with_script()
    server.store.update(jid, status="rendered", outputs={"video": "output/video.mp4"}, reviewed={"at": stamp})
    r = client.post(f"/api/jobs/{jid}/upload", json={"privacy": "private"}, headers=H)
    assert r.status_code == 429
    # with the override it gets past the limit (and stops at the YouTube setup check instead)
    r = client.post(f"/api/jobs/{jid}/upload", json={"privacy": "private", "override_limit": True}, headers=H)
    assert r.status_code == 400 and "client_secret" in r.json()["detail"]


# --------------------------------------------------- T5 festivals ----
def test_t5_festivals():
    import datetime as dt

    from app import festivals
    fests, lead = festivals.load()
    assert len(fests) > 20 and lead == 14 and fests == sorted(fests, key=lambda f: f["date"])
    up = festivals.upcoming(dt.date(2026, 10, 25), days=20)
    names = [f["name"] for f in up]
    assert names[0] == "Sharad Purnima" and "Diwali" in names and "Gita Jayanti" not in names
    diwali = next(f for f in up if f["name"] == "Diwali")
    assert diwali["days_left"] == 14 and diwali["plan_by"] == "2026-10-25" and diwali["urgent"]
    assert not next(f for f in up if f["name"] == "Bhai Dooj")["urgent"]
    assert "दीपावली" in festivals.ideas_theme(diwali)
    assert "festivals" in client.get("/api/festivals?days=400").json()

    seen = {}
    orig = scriptgen.generate_ideas
    scriptgen.generate_ideas = lambda kind, count, theme: seen.update(theme=theme) or [{"topic": "x"}]
    try:
        r = client.post("/api/ideas", json={"kind": "mantra_meaning", "festival": "2026-11-08"}, headers=H)
        assert r.status_code == 200 and "Diwali" in seen["theme"]
        assert client.post("/api/ideas", json={"festival": "1999-01-01"}, headers=H).status_code == 400
    finally:
        scriptgen.generate_ideas = orig


# --------------------------------------------------- T6 mantra jaap ----
def test_t6_jaap_script_and_job():
    s = scriptgen.normalize_script({"scenes": [
        {"narration": "ॐ नमः शिवाय", "repeat": 500, "visual": {"type": "slide", "text": "ॐ नमः शिवाय"}},
        {"narration": "अंत", "repeat": "x", "visual": {"type": "stock"}}]}, "long")
    assert s["scenes"][0]["repeat"] == 108 and "repeat" not in s["scenes"][1]
    prompt = scriptgen._user_prompt("Om Namah Shivaya", "mantra_jaap", "long", 10, "", None, "", 21)
    assert "repeats the mantra 21 times" in prompt and "`repeat` to 21" in prompt
    from app import render
    f = render.counter_filter(3.5, 108, 1920, 1080)
    assert "eif\\:floor(t/3.5000)+1\\:d" in f and "/ 108" in f and "lt(t,378.000)" in f
    assert "\\:/" in f or os.name != "nt"  # Windows drive colon escaped for ffmpeg
    r = client.post("/api/jobs", json={"topic": "jaap", "kind": "mantra_jaap", "format": "short",
                                       "script_json": '{"scenes":[{"narration":"ॐ"}]}'}, headers=H)
    assert r.status_code == 400 and "long video" in r.json()["detail"]
    r = client.post("/api/jobs", json={"topic": "jaap", "kind": "mantra_jaap", "format": "long", "repeat": 21,
                                       "script_json": '{"scenes":[{"narration":"ॐ"}]}'}, headers=H)
    assert r.status_code == 200 and r.json()["repeat"] == 21
    assert client.get("/api/options").json()["kinds"]["mantra_jaap"]["jaap"] is True


import contextlib  # noqa: E402


@contextlib.contextmanager
def _stubbed_render():
    """ffmpeg, voices and stock footage replaced by recorders; yields the recorded calls."""
    from app import media, render, thumbnail, visuals
    calls: dict[str, list] = {k: [] for k in (
        "scene", "prepare", "repeat", "tts", "mix", "thumb", "thumb_args", "polish", "master", "bgprep", "mp3",
        "concat", "pick", "fetch")}

    def touch(path, *_a, **_k):
        Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_bytes(b"x" * 2000); return Path(path)

    def polish(src, out, level, gain=0.0, denoise=False):
        chain = render.polish_chain(level, gain, denoise)
        if not chain:
            return src
        calls["polish"].append((Path(src).name, level, gain, denoise)); return touch(out)

    def master(src, out, lufs=-14.0, **k):
        calls["master"].append(lufs); touch(out)
        return {"before": -20.0, "after": lufs, "applied": True}

    stubs = {
        (media, "have_ffmpeg"): lambda: True,
        (media, "duration"): lambda p: 30.0 if "_x" in Path(p).stem else 2.0,
        (tts, "synthesize"): lambda text, out, **k: calls["tts"].append(text) or touch(out),
        (render, "prepare_voice"): lambda src, out, **k: calls["prepare"].append((src.name, k)) or touch(out),
        (render, "polish_voice"): polish,
        (render, "prepare_background"): lambda src, out, start=0.0, speed=1.0: calls["bgprep"].append((src.name, start, speed)) or touch(out),
        (render, "repeat_audio"): lambda src, out, n, gap: calls["repeat"].append((n, gap)) or (touch(out), 0),
        (render, "render_scene"): lambda **k: calls["scene"].append(k) or touch(k["out"]),
        (render, "concat_scenes"): lambda files, out: calls["concat"].append(list(files)) or touch(out),
        (render, "pick_music"): lambda seed, track="": calls["pick"].append(track) or (Path("m.wav"), "test track"),
        (render, "mix_music"): lambda *a, **k: calls["mix"].append((a, k)) or touch(a[2]),
        (render, "master"): master,
        (render, "audio_mp3"): lambda video, out: calls["mp3"].append(out.name) or touch(out),
        (visuals, "fetch_clip"): lambda *a, **k: calls["fetch"].append(a[0]) or None,
        (thumbnail, "render_thumbnail"): lambda out, *a, **k: (calls["thumb"].append(k), calls["thumb_args"].append(a),
                                                                touch(out))[-1],
    }
    saved = {k: getattr(*k) for k in stubs}
    for (mod, name), fn in stubs.items():
        setattr(mod, name, fn)
    try:
        yield calls
    finally:
        for (mod, name), fn in saved.items():
            setattr(mod, name, fn)


def test_m1_stable_ids_and_cheap_reorder():
    mk = lambda **k: {"narration": "x", "visual": {"type": "stock"}, **k}
    ids = lambda sc: [s["id"] for s in scriptgen.normalize_script({"scenes": sc}, "long")["scenes"]]
    assert ids([mk(), mk(), mk()]) == ["s01", "s02", "s03"]
    assert ids([mk(id="s03"), mk(id="s01"), mk(id="s02")]) == ["s03", "s01", "s02"]  # moving keeps ids
    assert ids([mk(id="s05"), mk(), mk(id="s02"), mk(id="s05"), mk(id="../x")]) == ["s05", "s06", "s02", "s07", "s08"]

    with _stubbed_render() as calls:
        jid = _job_with_script({"title": "t", "scenes": [
            {"narration": "एक", "visual": {"type": "stock", "query": "river"}},
            {"narration": "दो", "visual": {"type": "slide", "text": "ॐ"}},
            {"narration": "तीन", "visual": {"type": "stock", "query": "temple"}}]})
        pipeline.run_render(server.store, jid)
        assert server.store.get(jid)["status"] == "rendered"
        assert len(calls["scene"]) == 3 and len(calls["tts"]) == 3

        script = server.store.get(jid)["script"]
        script["scenes"] = [script["scenes"][2], script["scenes"][0], script["scenes"][1]]
        pipeline.set_script(server.store, jid, script)
        assert [s["id"] for s in server.store.get(jid)["script"]["scenes"]] == ["s03", "s01", "s02"]
        pipeline.run_render(server.store, jid)
        assert server.store.get(jid)["status"] == "rendered"
        assert len(calls["scene"]) == 3 and len(calls["tts"]) == 3  # nothing re-rendered, nothing re-voiced

        # a duplicate of scene 1 inserted in the middle: new id, no new voice-over, at most one new scene file
        script = server.store.get(jid)["script"]
        dup = {k: v for k, v in script["scenes"][1].items() if k != "id"}
        script["scenes"].insert(1, dup)
        pipeline.set_script(server.store, jid, script)
        assert [s["id"] for s in server.store.get(jid)["script"]["scenes"]] == ["s03", "s04", "s01", "s02"]
        pipeline.run_render(server.store, jid)
        assert len(calls["tts"]) == 3  # same narration, same cached voice-over
        assert len(calls["scene"]) <= 4  # at most the duplicate itself (it gets its own background)


def test_render_pipeline_with_stubs():
    """Runs the full _render with ffmpeg/network stubbed: own voice, chant loop, counter, theme, SRT."""
    with _stubbed_render() as calls:
        jid = _job_with_script({"title": "जाप", "scenes": [
            {"narration": "परिचय", "visual": {"type": "stock", "query": "temple"}},
            {"narration": "ॐ नमः शिवाय", "repeat": 15, "visual": {"type": "slide", "text": "ॐ नमः शिवाय"}},
            {"narration": "मेरा अनुभव", "personal": True, "voice_file": "aaaaaaaaaaaa.webm", "visual": {"type": "stock"}}]})
        vdir = server.store.job_dir(jid) / "voice"; vdir.mkdir(); (vdir / "aaaaaaaaaaaa.webm").write_bytes(b"v" * 2000)
        server.store.update(jid, theme="forest_ashram")
        pipeline.run_render(server.store, jid)
        job = server.store.get(jid)
        assert job["status"] == "rendered", job.get("error")
        assert [c[0] for c in calls["prepare"]] == ["aaaaaaaaaaaa.webm"] and calls["tts"] == ["परिचय", "ॐ नमः शिवाय"]
        assert calls["repeat"] == [(15, pipeline.JAAP_GAP)]
        chant = calls["scene"][1]
        assert chant["counter"] == {"period": 2.0, "total": 15} and chant["caption_overlays"] == []
        assert calls["scene"][0]["counter"] is None
        assert calls["thumb"][0]["theme_name"] == "forest_ashram"
        srt = (server.store.job_dir(jid) / "output" / "captions.srt").read_text(encoding="utf-8")
        assert srt.count("ॐ नमः शिवाय") == 15
        assert "partly AI-generated" in pipeline.description_footer(job)
        # videos from before the studio keep their old sound: no mastering, no polish
        assert calls["master"] == [] and calls["polish"] == []


# ------------------------------------------------------ T7 make a Short ----
def test_t7_derive_short():
    sc = _script()
    sc["scenes"][1]["narration"] = "मेरा अनुभव"
    sc["scenes"][0]["voice_file"] = "bbbbbbbbbbbb.mp3"
    sc["scenes"].append({"narration": "ॐ", "repeat": 108, "visual": {"type": "slide", "text": "ॐ"}})
    jid = _job_with_script(sc)
    sd = server.store.job_dir(jid)
    (sd / "voice").mkdir(); (sd / "voice" / "bbbbbbbbbbbb.mp3").write_bytes(b"v" * 2000)
    key = tts.cache_key("अंत", config.default_voice(), config.tts_rate(), config.tts_pitch(), config.tts_engine())
    (sd / "work" / "audio").mkdir(parents=True); (sd / "work" / "audio" / f"{key}.mp3").write_bytes(b"a" * 2000)
    server.store.update(jid, youtube={"url": "https://youtu.be/abc", "uploaded_at": "2020-01-01 00:00:00"})

    r = client.post(f"/api/jobs/{jid}/short", json={"scenes": [0, 1, 2, 3]}, headers=H)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["format"] == "short" and new["parent"] == jid and new["status"] == "script_ready"
    scenes = new["script"]["scenes"]
    assert len(scenes) == 3 and not any(s.get("personal") or s.get("repeat") for s in scenes)  # chant dropped
    assert new["metadata"]["title"].endswith("#Shorts") and "youtu.be/abc" in new["script"]["description"]
    nd = server.store.job_dir(new["id"])
    assert (nd / "voice" / "bbbbbbbbbbbb.mp3").exists()
    assert (nd / "work" / "audio" / f"{key}.mp3").exists()  # cached voice-over reused under the new id
    assert client.post(f"/api/jobs/{jid}/short", json={"scenes": [3]}, headers=H).status_code == 400
    assert client.post(f"/api/jobs/{new['id']}/short", json={"scenes": [0]}, headers=H).status_code == 400


# --------------------------------------------------- T8 English subtitles ----
def test_t8_english_subtitles():
    jid = _job_with_script({"title": "t", "scenes": [
        {"narration": "पहला वाक्य। दूसरा वाक्य।", "visual": {"type": "stock"}},
        {"narration": "ॐ नमः शिवाय", "repeat": 3, "visual": {"type": "slide", "text": "ॐ"}}]})
    assets = {"s01": {"start": 0.0, "speech": 4.0, "repeat": 1},
              "s02": {"start": 4.6, "speech": 9.0, "repeat": 3, "period": 3.0, "single": 2.2}}
    (server.store.job_dir(jid) / "output").mkdir(parents=True, exist_ok=True)
    server.store.update(jid, assets=assets, outputs={"video": "output/video.mp4"})
    assert pipeline.write_english_srt(server.store, jid, allow_api=False) is False  # nothing cached yet

    asked = []
    orig = scriptgen.translate_lines
    scriptgen.translate_lines = lambda lines: asked.append(list(lines)) or [f"EN {i}" for i in range(len(lines))]
    try:
        r = client.post(f"/api/jobs/{jid}/translate", headers=H)
        assert r.status_code == 200
        for _ in range(100):
            if not server.store.get(jid).get("translating"):
                break
            time.sleep(0.05)
        job = server.store.get(jid)
        assert job["outputs"]["srt_en"] == "output/captions.en.srt" and not job.get("error"), job.get("error")
        srt = (server.store.job_dir(jid) / "output" / "captions.en.srt").read_text(encoding="utf-8")
        assert srt.count("EN 1") == 3 and "EN 0" in srt and "00:00:10,600" in srt  # 3rd chant starts at 4.6+6
        # cached: rebuilding (as after a re-render) makes no new API call
        assert pipeline.write_english_srt(server.store, jid, allow_api=False) is True and len(asked) == 1
    finally:
        scriptgen.translate_lines = orig
    assert client.post(f"/api/jobs/{_job_with_script()}/translate", headers=H).status_code == 400

    from app import uploader
    logs = []
    assert uploader.upload_captions("vid", [], logs.append) == 0 and "reconnect" in logs[0].lower()
    assert uploader.status()["manage"] is False


# --------------------------------------------------- T9 description links ----
def test_t9_description_links():
    orig = config.CONFIG_DIR
    tmp = Path(tempfile.mkdtemp())
    (tmp / "description_links.txt").write_text("# comment\nJoin: https://example.com/join\n\n# x\n", encoding="utf-8")
    config.CONFIG_DIR = tmp
    try:
        job = server.store.get(_job_with_script())
        footer = pipeline.description_footer(job)
        assert footer.startswith("Join: https://example.com/join") and "comment" not in footer
        assert "Join:" in pipeline.compose_description(job)
    finally:
        config.CONFIG_DIR = orig


# --------------------------------------------------- T12 stats ----
def test_t12_stats_and_ideas():
    from app import uploader
    a = _job_with_script(); b = _job_with_script()
    server.store.update(a, youtube={"video_id": "AAA", "url": "u", "uploaded_at": "2026-10-01 10:00:00"})
    server.store.update(b, kind="mantra_meaning", youtube={"video_id": "BBB", "url": "u", "uploaded_at": "2026-10-02 10:00:00"})
    saved = uploader.video_stats, uploader.status
    uploader.video_stats = lambda ids: {"AAA": {"views": 50, "likes": 5, "comments": 1, "privacy": "public"},
                                        "BBB": {"views": 900, "likes": 80, "comments": 9, "privacy": "public"}}
    uploader.status = lambda: {"configured": True, "connected": True, "manage": True}
    seen = {}
    orig_ideas = scriptgen.generate_ideas
    scriptgen.generate_ideas = lambda kind, count, theme: seen.update(theme=theme) or []
    try:
        st = client.get("/api/stats?refresh=1").json()
        assert st["can_read"] and st["videos"][0]["views"] == 900 and st["by_kind"]["mantra_meaning"]["avg_views"] == 900
        assert server.store.get(b)["youtube"]["stats"]["views"] == 900  # saved for offline use
        assert client.get("/api/stats").json()["videos"][0]["views"] == 900
        client.post("/api/ideas", json={"kind": "short_story"}, headers=H)
        assert "best-performing" in seen["theme"] and "900 views" in seen["theme"]
    finally:
        uploader.video_stats, uploader.status = saved
        scriptgen.generate_ideas = orig_ideas


# --------------------------------------------------- AI writer providers ----
class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.text = data if isinstance(data, str) else __import__("json").dumps(data)

    def json(self):
        return self._data


def _tool_reply(args, as_content=False):
    import json as _j
    msg = {"content": _j.dumps(args)} if as_content else {
        "tool_calls": [{"function": {"name": "submit_ideas", "arguments": _j.dumps(args)}}]}
    return _Resp(200, {"choices": [{"message": msg, "finish_reason": "stop"}]})


def test_llm_settings_and_keys():
    from app import llm
    tmp = Path(tempfile.mkdtemp())
    saved = llm.SETTINGS_FILE, llm.ENV_FILE
    llm.SETTINGS_FILE, llm.ENV_FILE = tmp / "llm.json", tmp / ".env"
    (tmp / ".env").write_text("PEXELS_API_KEY=abc\nGEMINI_API_KEY=\n", encoding="utf-8")
    try:
        assert llm.provider() == "anthropic"
        r = client.put("/api/llm", json={"provider": "gemini", "model": "", "api_key": "AIzaTESTKEY123456"}, headers=H)
        assert r.status_code == 200 and r.json()["provider"] == "gemini" and r.json()["ready"]
        env = (tmp / ".env").read_text(encoding="utf-8")
        assert "GEMINI_API_KEY=AIzaTESTKEY123456" in env and "PEXELS_API_KEY=abc" in env and env.count("GEMINI_API_KEY") == 1
        assert "AIzaTESTKEY123456" not in r.text  # keys never go back to the browser
        client.put("/api/llm", json={"provider": "groq", "model": "qwen/qwen3.6-27b", "api_key": "gsk_TESTKEY123456"}, headers=H)
        assert llm.provider() == "groq" and llm.model() == "qwen/qwen3.6-27b"
        assert client.get("/api/health").json()["llm"]["providers"]["groq"]["has_key"]
        assert client.put("/api/llm", json={"provider": "nope"}, headers=H).status_code == 400
        assert client.put("/api/llm", json={"provider": "groq", "api_key": "bad key with spaces"}, headers=H).status_code == 400
    finally:
        llm.SETTINGS_FILE, llm.ENV_FILE = saved
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY"):
            os.environ.pop(k, None)


def test_llm_openai_compatible_calls():
    from app import llm
    tool = scriptgen.IDEAS_TOOL
    sent = []
    orig = llm._post
    os.environ.update(GEMINI_API_KEY="k" * 20, GROQ_API_KEY="k" * 20, OPENAI_API_KEY="k" * 20)
    try:
        # Gemini rejects naming the function, so it falls back to "required"
        replies = [_Resp(400, {"error": {"message": "tool_choice object not supported"}}), _tool_reply({"ideas": [{"topic": "x"}]})]
        llm._post = lambda url, key, body: sent.append((url, body)) or replies.pop(0)
        out = llm.call_openai_compatible("sys", "user", tool, 20000, "gemini")
        assert out == {"ideas": [{"topic": "x"}]} and sent[1][1]["tool_choice"] == "required"
        assert sent[0][0].startswith("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        # Groq caps output to fit the free tier; plain-JSON answers in content are accepted
        sent.clear(); llm._post = lambda url, key, body: sent.append((url, body)) or _tool_reply({"ideas": []}, as_content=True)
        assert llm.call_openai_compatible("s", "u", tool, 20000, "groq") == {"ideas": []}
        assert sent[0][1]["max_tokens"] == 6000 and sent[0][1]["tool_choice"]["function"]["name"] == "submit_ideas"
        # OpenAI: max_completion_tokens, no temperature
        sent.clear(); llm.call_openai_compatible("s", "u", tool, 5000, "openai")
        b = sent[0][1]
        assert b["max_completion_tokens"] == 5000 and "max_tokens" not in b and "temperature" not in b
        # helpful errors
        llm._post = lambda *a: _Resp(429, {"error": {"message": "quota"}})
        try:
            llm.call_openai_compatible("s", "u", tool, 100, "gemini"); raise AssertionError
        except llm.LLMError as e:
            assert "rate limit" in str(e)
        llm._post = lambda *a: _Resp(200, {"choices": [{"message": {"content": "{\"ideas\": [{\"to"}, "finish_reason": "length"}]})
        try:
            llm.call_openai_compatible("s", "u", tool, 100, "groq"); raise AssertionError
        except llm.LLMError as e:
            assert "ran out of output space" in str(e)
        # scriptgen routes through the selected provider
        llm._post = lambda *a: _tool_reply({"ideas": [{"topic": "गीता", "why": "w"}]})
        saved_provider = llm.provider
        llm.provider = lambda: "gemini"
        try:
            assert scriptgen.generate_ideas("short_story", 3) == [{"topic": "गीता", "why": "w"}]
        finally:
            llm.provider = saved_provider
    finally:
        llm._post = orig
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(k, None)


def test_llm_overload_retry_and_fallback():
    from app import llm
    saved = llm._post, llm._sleep, llm.list_models
    waits = []
    llm._sleep = waits.append
    os.environ["GEMINI_API_KEY"] = "k" * 20
    busy = _Resp(503, {"error": {"message": "This model is currently experiencing high demand."}})
    try:
        # 503 twice, then success: retried with backoff on the chosen model
        replies = [busy, busy, _tool_reply({"ideas": []})]
        llm._post = lambda url, key, body: replies.pop(0)
        assert llm.call_openai_compatible("s", "u", scriptgen.IDEAS_TOOL, 100, "gemini") == {"ideas": []}
        assert waits == [2, 5] and llm.last_model_used == llm.model("gemini")

        # chosen model stays overloaded: a stable model of the same family answers instead
        llm.list_models = lambda p: ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.9-flash-preview",
                                     "gemini-3.8-pro", "gemini-3.6-flash"]
        assert llm.fallback_models("gemini", "gemini-3.8-flash") == ["gemini-3.7-flash", "gemini-3.6-flash"]
        tried = []
        llm._post = lambda url, key, body: tried.append(body["model"]) or (
            _tool_reply({"ideas": [{"topic": "x"}]}) if body["model"] == "gemini-3.7-flash" else busy)
        assert llm.call_openai_compatible("s", "u", scriptgen.IDEAS_TOOL, 100, "gemini")["ideas"][0]["topic"] == "x"
        assert tried[:4] == ["gemini-3.8-flash"] * 4 and llm.last_model_used == "gemini-3.7-flash"

        # everything overloaded: one clear message
        llm._post = lambda *a: busy
        try:
            llm.call_openai_compatible("s", "u", scriptgen.IDEAS_TOOL, 100, "gemini"); raise AssertionError
        except llm.LLMError as e:
            assert "overloaded right now (503)" in str(e) and "high demand" in str(e)

        # 429 with a long wait is not retried; a 400 is not retried either
        calls = []
        r429 = _Resp(429, {"error": {"message": "quota"}}); r429.headers = {"retry-after": "3600"}
        llm._post = lambda *a: calls.append(1) or r429
        try:
            llm.call_openai_compatible("s", "u", scriptgen.IDEAS_TOOL, 100, "gemini"); raise AssertionError
        except llm.LLMError as e:
            assert "rate limit" in str(e) and len(calls) == 1
    finally:
        llm._post, llm._sleep, llm.list_models = saved
        os.environ.pop("GEMINI_API_KEY", None)


# --------------------------------------------------- Facebook / Instagram ----
class _FakeMeta:
    """Records calls to meta._request and answers like the Graph API."""

    def __init__(self, ig_status=("IN_PROGRESS", "FINISHED")):
        self.calls, self.ig_status = [], list(ig_status)

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if "oauth/access_token" in url:
            return _Resp(200, {"access_token": "LONG_USER", "expires_in": 5184000})
        if url.endswith("/me"):
            return _Resp(200, {"id": "1", "name": "Abhi"})
        if url.endswith("/me/accounts"):
            return _Resp(200, {"data": [
                {"id": "P1", "name": "Shanti Page", "access_token": "PAGE1", "tasks": ["CREATE_CONTENT"],
                 "instagram_business_account": {"id": "IG1", "username": "shanti.reels"}},
                {"id": "P2", "name": "Other Page", "access_token": "PAGE2"}]})
        if url.endswith("/video_reels"):
            return _Resp(200, {"video_id": "V1", "upload_url": "u"} if kw["data"]["upload_phase"] == "start" else {"success": True})
        if "rupload.facebook.com" in url:
            return _Resp(200, {"success": True, "message": "Upload successful."})
        if "graph-video.facebook.com" in url:
            return _Resp(200, {"id": "LV1"})
        if url.endswith("/IG1/media"):
            return _Resp(200, {"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/C1"})
        if url.endswith("/C1"):
            return _Resp(200, {"status_code": self.ig_status.pop(0) if self.ig_status else "FINISHED"})
        if url.endswith("/IG1/media_publish"):
            return _Resp(200, {"id": "M1"})
        if url.endswith("/M1"):
            return _Resp(200, {"permalink": "https://www.instagram.com/reel/abc/"})
        return _Resp(404, {"error": {"message": "unknown " + url}})


def test_meta_connect_and_status():
    from app import meta
    fake = _FakeMeta()
    orig = meta._request
    meta._request = fake
    try:
        r = client.post("/api/meta/connect", json={"app_id": "123456789", "app_secret": "sec", "user_token": "SHORT"}, headers=H)
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["connected"] and st["page_name"] == "Shanti Page" and st["instagram"] == "shanti.reels"
        assert "PAGE1" not in r.text and "LONG_USER" not in r.text and "sec" not in meta.TOKEN_FILE.read_text()
        assert fake.calls[0][2]["params"]["client_secret"] == "sec"
        assert client.post("/api/meta/page", json={"page_id": "P2"}, headers=H).json()["instagram"] == ""
        assert client.post("/api/meta/page", json={"page_id": "nope"}, headers=H).status_code == 400
        client.post("/api/meta/page", json={"page_id": "P1"}, headers=H)
        assert client.post("/api/meta/connect", json={"app_id": "abc", "app_secret": "s", "user_token": "t"}, headers=H).status_code == 400
        meta._request = lambda *a, **k: _Resp(400, {"error": {"code": 190, "message": "expired"}})
        r = client.post("/api/meta/connect", json={"app_id": "123456789", "app_secret": "s", "user_token": "t"}, headers=H)
        assert r.status_code == 400 and "expired" in r.json()["detail"]
    finally:
        meta._request = orig


def test_meta_captions():
    from app import meta
    assert meta.clean_hashtags("#शिव, upanishad  #Upanishad bad-tag!") == ["शिव", "upanishad", "badtag"]
    assert len(meta.clean_hashtags([f"t{i}" for i in range(50)])) == 30
    cap = meta.compose_caption("क" * 3000, ["शिव", "ॐ"], "आवाज़: AI-जनित")
    assert len(cap) <= meta.CAPTION_LIMIT and cap.endswith("#शिव #ॐ\n\nआवाज़: AI-जनित") and "…" in cap
    s = scriptgen.normalize_script({"social_caption": "  कैप्शन ", "hashtags": ["#शिव", "", "a b"],
                                    "scenes": [{"narration": "x", "visual": {"type": "stock"}}]}, "short")
    assert s["social_caption"] == "कैप्शन" and s["hashtags"] == ["शिव", "ab"]
    assert pipeline.default_social(s) == {"caption": "कैप्शन", "hashtags": ["शिव", "ab"]}
    # without the writer's caption, fall back to the YouTube description minus its hashtags
    fb = pipeline.default_social({"description": "विवरण #a #b", "tags": ["Gita Path", "गीता"]})
    assert fb == {"caption": "विवरण", "hashtags": ["GitaPath", "गीता"]}
    orig = scriptgen._call_tool
    scriptgen._call_tool = lambda *a: {"caption": "नई पंक्ति", "hashtags": ["#ध्यान", "meditation"]}
    try:
        jid = _job_with_script()
        r = client.post(f"/api/jobs/{jid}/social/write", headers=H)
        assert r.json()["social"] == {"caption": "नई पंक्ति", "hashtags": ["ध्यान", "meditation"]}
        assert "#ध्यान #meditation" in r.json()["social_preview"]["instagram"]
        r = client.put(f"/api/jobs/{jid}/social", json={"caption": "मेरा", "hashtags": "#a b,c"}, headers=H)
        assert r.json()["social"]["hashtags"] == ["a", "b", "c"]
    finally:
        scriptgen._call_tool = orig


def test_meta_posting():
    from app import meta
    fake = _FakeMeta()
    orig = meta._request, meta.time.sleep
    meta._request, meta.time.sleep = fake, lambda s: None
    try:
        client.post("/api/meta/connect", json={"app_id": "123456789", "app_secret": "s", "user_token": "t"}, headers=H)
        client.post("/api/meta/page", json={"page_id": "P1"}, headers=H)
        long_id = _job_with_script()
        vid = server.store.job_dir(long_id) / "output" / "video.mp4"
        vid.parent.mkdir(parents=True, exist_ok=True); vid.write_bytes(b"v" * 4000)
        server.store.update(long_id, status="rendered", outputs={"video": "output/video.mp4", "duration": 300})
        r = client.post(f"/api/jobs/{long_id}/social/post", json={"platforms": ["facebook"]}, headers=H)
        assert r.status_code == 400 and "watched" in r.json()["detail"]
        server.store.update(long_id, reviewed={"at": "x"})
        r = client.post(f"/api/jobs/{long_id}/social/post", json={"platforms": ["instagram"]}, headers=H)
        assert r.status_code == 400 and "vertical" in r.json()["detail"]

        # long 16:9 video -> Facebook Page video
        pipeline.run_social_post(server.store, long_id, ["facebook"])
        job = server.store.get(long_id)
        assert job["social_posts"]["facebook"]["kind"] == "video" and not job.get("error")
        assert any("graph-video.facebook.com" in c[1] for c in fake.calls)

        # vertical 40 s Short -> Facebook Reel + Instagram Reel
        short_id = _job_with_script(fmt="short")
        sv = server.store.job_dir(short_id) / "output" / "video.mp4"
        sv.parent.mkdir(parents=True, exist_ok=True); sv.write_bytes(b"v" * 4000)
        server.store.update(short_id, status="rendered", reviewed={"at": "x"},
                            outputs={"video": "output/video.mp4", "duration": 40})
        fake.calls.clear()
        pipeline.run_social_post(server.store, short_id, ["facebook", "instagram"])
        job = server.store.get(short_id)
        assert not job.get("error"), job.get("error")
        assert job["social_posts"]["facebook"]["url"] == "https://www.facebook.com/reel/V1"
        assert job["social_posts"]["instagram"]["url"] == "https://www.instagram.com/reel/abc/"
        urls = [c[1] for c in fake.calls]
        assert any(u.endswith("/rupload.facebook.com/video-upload/v25.0/V1") or "video-upload/v25.0/V1" in u for u in urls)
        ig_box = next(c for c in fake.calls if c[1].endswith("/IG1/media"))
        assert ig_box[2]["json"]["media_type"] == "REELS" and ig_box[2]["json"]["upload_type"] == "resumable"
        assert "#" in ig_box[2]["json"]["caption"] and "AI voice" in ig_box[2]["json"]["caption"]
        assert ig_box[2]["headers"]["Authorization"] == "Bearer LONG_USER"
        up = next(c for c in fake.calls if "ig-api-upload" in c[1])
        assert up[2]["headers"]["file_size"] == "4000" and up[2]["headers"]["Authorization"] == "OAuth LONG_USER"
        finish = next(c for c in fake.calls if c[1].endswith("/video_reels") and c[2]["data"]["upload_phase"] == "finish")
        assert finish[2]["data"]["video_state"] == "PUBLISHED" and finish[2]["data"]["access_token"] == "PAGE1"
        # posting again needs confirmation
        r = client.post(f"/api/jobs/{short_id}/social/post", json={"platforms": ["instagram"]}, headers=H)
        assert r.status_code == 409

        # Instagram processing error is reported, Facebook still succeeds
        bad = _FakeMeta(ig_status=("ERROR",))
        meta._request = bad
        pipeline.run_social_post(server.store, short_id, ["instagram", "facebook"])
        job = server.store.get(short_id)
        assert "Instagram: Instagram could not process" in job["error"] and job["social_uploading"] == ""
    finally:
        meta._request, meta.time.sleep = orig
        meta.disconnect()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
