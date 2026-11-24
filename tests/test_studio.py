"""Tests for the studio (docs/STUDIO_REQUIREMENTS.md). No network and no ffmpeg needed:
python tests/test_studio.py   (real-ffmpeg checks of the same features are in tests/smoke_render.py)"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_features as tf  # noqa: E402  (sets up an isolated data folder, settings and API client)
from test_features import H, _job_with_script, _stubbed_render, client, server  # noqa: E402

from app import captions, config, media, pipeline, render, scriptgen, studio, visuals  # noqa: E402

# Never touch the real branding, saved defaults, licence notes or music library.
_TMP = Path(tempfile.mkdtemp(prefix="yt_studio_"))
(_TMP / "music").mkdir()
studio.BRANDING_FILE, studio.LOGO_FILE = _TMP / "branding.json", _TMP / "logo.png"
studio.DEFAULTS_FILE, studio.LICENSE_FILE = _TMP / "studio_defaults.json", _TMP / "music" / "licenses.json"
config.MUSIC_DIR = _TMP / "music"


def _png(path: Path, size=(200, 100), color=(200, 30, 30, 255)) -> Path:
    from PIL import Image
    img = Image.new("RGBA", size, color)
    img.convert("RGB").save(path) if path.suffix.lower() in (".jpg", ".jpeg") else img.save(path)
    return path


def _scene(narration="नमस्ते", **kw):
    return {"narration": narration, "visual": {"type": "stock", "query": "river"}, **kw}


# ------------------------------------------------------------- validation ----
def test_edit_validation():
    ok = studio.normalize_edit({
        "bg_file": "0123456789ab.mp4", "bg_start": 12.345, "bg_speed": 1.5, "motion": "zoom_in", "look": "warm",
        "brightness": 0.1, "saturation": 1.4, "voice_gain": 3, "voice_in": 1, "voice_out": 5,
        "overlay": {"text": " अध्याय ", "pos": "middle", "start": 1, "end": 3}})
    assert ok == {"bg_file": "0123456789ab.mp4", "bg_start": 12.35, "bg_speed": 1.5, "motion": "zoom_in",
                  "look": "warm", "brightness": 0.1, "saturation": 1.4, "voice_gain": 3.0, "voice_in": 1.0,
                  "voice_out": 5.0, "overlay": {"text": "अध्याय", "pos": "middle", "start": 1.0, "end": 3.0}}
    # defaults are not stored; nonsense is dropped or clamped
    assert studio.normalize_edit({"bg_speed": 1, "motion": "none", "look": "none", "voice_gain": 0}) == {}
    bad = studio.normalize_edit({"bg_file": "../../x.mp4", "bg_speed": 99, "bg_start": -4, "motion": "spin",
                                 "brightness": "x", "saturation": 9, "voice_gain": float("nan"),
                                 "voice_in": 5, "voice_out": 4, "overlay": {"text": "  "}})
    assert bad == {"bg_speed": 2.0, "saturation": 2.0, "voice_in": 5.0}
    assert studio.normalize_edit("nope") == {} and studio.normalize_edit(None) == {}
    # trim end must be after the start; an end of 0 means "to the end"
    assert "voice_out" not in studio.normalize_edit({"voice_in": 2, "voice_out": 2.1})
    assert studio.normalize_edit({"overlay": {"text": "x", "start": 5, "end": 2}})["overlay"]["end"] == 0.0


def test_studio_settings_validation():
    d = studio.normalize_studio(None)
    assert d == studio.default_studio() and d["audio"]["master"] is True and d["audio"]["lufs"] == -14.0
    s = studio.normalize_studio({
        "captions": {"style": "highlight", "position": "top", "size": "large"}, "transition": {"fade": 0},
        "logo": {"show": True}, "thumbnail": {"scene": "s03"}, "export": {"quality": "high", "audio_mp3": 1},
        "audio": {"voice_polish": "studio", "lufs": -15.5, "master": False,
                  "music": {"track": "a.mp3", "volume": 0.2, "ducking": "off", "fade_in": 0, "fade_out": 99, "start": 7}}})
    assert s["captions"] == {"style": "highlight", "position": "top", "size": "large"}
    assert s["transition"]["fade"] == 0.0 and s["logo"]["show"] is True and s["thumbnail"]["scene"] == "s03"
    assert s["export"] == {"quality": "high", "audio_mp3": True} and studio.crf_for(s) == 17
    assert s["audio"]["lufs"] == -16.0 and s["audio"]["master"] is False and s["audio"]["voice_polish"] == "studio"
    assert s["audio"]["music"] == {"track": "a.mp3", "volume": 0.2, "ducking": "off", "fade_in": 0.0,
                                   "fade_out": 10.0, "start": 7.0}
    junk = studio.normalize_studio({"captions": {"style": "x"}, "audio": {"music": {"track": "../a.mp3", "volume": 5}},
                                    "thumbnail": {"scene": "../x"}, "export": {"quality": "x"}, "extra": 1})
    assert junk["captions"]["style"] == "box" and junk["audio"]["music"]["track"] == ""
    assert junk["audio"]["music"]["volume"] == 1.0 and junk["thumbnail"]["scene"] == "" and "extra" not in junk
    assert junk["export"]["quality"] == "standard" and studio.crf_for(junk) == 20
    # a video made before the studio existed keeps its old sound until settings are saved
    assert studio.effective({})["audio"]["master"] is False
    assert studio.effective({"studio": {}})["audio"]["master"] is True
    # captions keep their old place by default
    assert studio.caption_y(1080, "landscape", "bottom") == int(1080 * 0.80)
    assert studio.caption_y(1920, "portrait", "bottom") == int(1920 * 0.66)
    assert studio.caption_y(1080, "landscape", "top") < studio.caption_y(1080, "landscape", "middle") \
        < studio.caption_y(1080, "landscape", "bottom")


def test_edits_survive_script_save_and_studio_api():
    jid = _job_with_script({"title": "t", "scenes": [_scene(edit={"look": "warm", "motion": "pan_left", "x": 1}),
                                                     _scene("दो", edit={"look": "bad"})]})
    scenes = server.store.get(jid)["script"]["scenes"]
    assert scenes[0]["edit"] == {"look": "warm", "motion": "pan_left"} and "edit" not in scenes[1]
    r = client.put(f"/api/jobs/{jid}/script", json={"script": server.store.get(jid)["script"]}, headers=H)
    assert r.json()["script"]["scenes"][0]["edit"]["look"] == "warm"
    r = client.put(f"/api/jobs/{jid}/studio", json={"studio": {"captions": {"style": "outline"}}}, headers=H)
    assert r.status_code == 200 and r.json()["studio"]["captions"]["style"] == "outline"
    assert r.json()["studio_effective"]["audio"]["master"] is True  # saved settings use the new defaults
    # new videos start from the saved defaults
    client.post(f"/api/jobs/{jid}/studio/default", headers=H)
    new = client.post("/api/jobs", json={"topic": "x y", "kind": "quote", "format": "short",
                                         "script_json": json.dumps({"scenes": [{"narration": "ॐ"}]})}, headers=H).json()
    assert new["studio"]["captions"]["style"] == "outline"
    opts = client.get("/api/options").json()["studio"]
    assert "zoom_in" in opts["motions"] and opts["lufs"]["-14"] and "studio" in opts["polish"]


# ------------------------------------------------------- render: pictures ----
def test_pipeline_applies_picture_edits():
    tmp = Path(tempfile.mkdtemp())
    stock = tmp / "stock.mp4"; stock.write_bytes(b"s" * 3000)
    logo = _png(_TMP / "logo.png", (400, 400))
    studio.save_branding(corner="tl", size=0.1, opacity=0.7, auto=False)
    with _stubbed_render() as calls:
        visuals.fetch_clip = lambda *a, **k: calls["fetch"].append(a[0]) or {"path": str(stock), "id": 77, "credit": "c"}
        jid = _job_with_script({"title": "t", "scenes": [
            _scene("एक", edit={"bg_start": 4, "bg_speed": 1.5, "look": "cool", "saturation": 1.3}),
            {"narration": "दो", "visual": {"type": "slide", "text": "ॐ"},
             "edit": {"motion": "zoom_in", "voice_gain": 3,
                      "overlay": {"text": "अध्याय १", "pos": "middle", "start": 0.5, "end": 1.5}}},
            _scene("तीन", edit={"bg_file": "abcdef012345.jpg", "motion": "pan_left", "brightness": 0.1}),
        ]})
        own = server.store.job_dir(jid) / "media"; own.mkdir(); _png(own / "abcdef012345.jpg", (64, 64))
        server.store.update(jid, studio={
            "captions": {"style": "outline", "position": "top", "size": "large"}, "transition": {"fade": 0},
            "logo": {"show": True}, "thumbnail": {"scene": "s03"}, "export": {"quality": "small"},
            "audio": {"voice_polish": "studio"}})
        pipeline.run_render(server.store, jid)
        job = server.store.get(jid)
        assert job["status"] == "rendered", job.get("error")
        s1, s2, s3 = calls["scene"]

        # clip start and speed: a trimmed copy is made once, from the stock clip, and used by the scene
        assert calls["bgprep"] == [("stock.mp4", 4.0, 1.5)] and calls["fetch"] == ["river"]
        assert s1["bg_kind"] == "video" and Path(s1["bg_path"]).name.startswith("clip_")
        assert s1["grade"] == render.LOOK_FILTERS["cool"] + ",eq=brightness=-0.05:saturation=1.3"
        assert s1["motion"] == "none"
        # a slide: slow zoom, on-screen text in its time window, no colour change
        assert s2["motion"] == "zoom_in" and s2["grade"] == ""
        assert len(s2["overlays"]) == 1 and (s2["overlays"][0]["start"], s2["overlays"][0]["end"]) == (0.5, 1.5)
        assert s2["overlays"][0]["png"].exists() and 0 < s2["overlays"][0]["y"] < 1080
        # the owner's own photo: used as is, no stock lookup, still moves, brightness applied
        assert s3["bg_kind"] == "image" and str(s3["bg_path"]).endswith("abcdef012345.jpg") and s3["motion"] == "pan_left"
        assert s3["grade"] == "eq=brightness=0.1" + ":saturation=1" and job["assets"]["s03"]["bg_own"] is True
        assert "Pexels" not in pipeline.description_footer(job) or job["assets"]["s01"]["bg_id"] == 77
        # video-wide: hard cut, small file, captions at the top in the outlined style, logo top left
        for kw in (s1, s2, s3):
            assert kw["fade"] == 0.0 and kw["crf"] == 24
            cap = kw["caption_overlays"][0]
            assert cap["y"] == studio.caption_y(1080, "landscape", "top")
            from PIL import Image
            with Image.open(cap["png"]) as im:
                assert im.getpixel((3, im.height // 2))[3] == 0  # outline: no dark box
            assert kw["logo"]["corner"] == "tl" and kw["logo"]["path"] == studio.LOGO_FILE and kw["logo"]["size"] == 0.1
        big = captions.render_caption_png("नमस्ते दुनिया", _TMP / "c1.png", 1920, 1080, "landscape", "outline", 1.25)
        small = captions.render_caption_png("नमस्ते दुनिया", _TMP / "c2.png", 1920, 1080, "landscape", "outline", 0.8)
        assert big[1] > small[1]
        # the thumbnail uses the chosen scene's picture (a photo here)
        assert calls["thumb_args"][0][1] == Path(s3["bg_path"])
        # voice: studio polish on every scene, the owner's gain on scene 2
        assert [(c[1], c[2]) for c in calls["polish"]] == [("studio", 0.0), ("studio", 3.0), ("studio", 0.0)]

        # changing only the look re-renders only that scene
        sc = server.store.get(jid)["script"]
        sc["scenes"][0]["edit"]["look"] = "warm"
        pipeline.set_script(server.store, jid, sc)
        pipeline.run_render(server.store, jid)
        assert len(calls["scene"]) == 4 and calls["scene"][3]["grade"].startswith(render.LOOK_FILTERS["warm"])
        assert len(calls["bgprep"]) == 1 and len(calls["tts"]) == 3  # no new trimming, no new voice-over


def test_stock_clip_choice_is_kept_between_renders():
    tmp = Path(tempfile.mkdtemp()); stock = tmp / "stock.mp4"; stock.write_bytes(b"s" * 3000)
    with _stubbed_render() as calls:
        visuals.fetch_clip = lambda *a, **k: calls["fetch"].append(a[0]) or {"path": str(stock), "id": 5, "credit": "c"}
        jid = _job_with_script({"title": "t", "scenes": [_scene("एक", edit={"bg_start": 2})]})
        pipeline.run_render(server.store, jid)
        pipeline.run_render(server.store, jid)
        assert calls["fetch"] == ["river"]  # the clip is chosen once; the trimmed copy is not trimmed again
        assert calls["bgprep"] == [("stock.mp4", 2.0, 1.0)]
        sc = server.store.get(jid)["script"]
        sc["scenes"][0]["edit"] = {"bg_start": 6}
        pipeline.set_script(server.store, jid, sc)
        pipeline.run_render(server.store, jid)
        assert calls["fetch"] == ["river"] and calls["bgprep"][-1] == ("stock.mp4", 6.0, 1.0)  # still from the original


# --------------------------------------------------------- render: sound ----
def test_pipeline_applies_sound_settings():
    with _stubbed_render() as calls:
        jid = _job_with_script({"title": "t", "scenes": [
            _scene("एक", voice_file="aaaaaaaaaaaa.webm", edit={"voice_in": 1.5, "voice_out": 6, "voice_gain": -2}),
            _scene("दो")]})
        vdir = server.store.job_dir(jid) / "voice"; vdir.mkdir(); (vdir / "aaaaaaaaaaaa.webm").write_bytes(b"v" * 2000)
        server.store.update(jid, studio={"audio": {
            "voice_polish": "light", "lufs": -16, "master": True,
            "music": {"track": "calm.mp3", "volume": 0.2, "ducking": "gentle", "fade_in": 1, "fade_out": 4, "start": 5}},
            "export": {"audio_mp3": True}})
        pipeline.run_render(server.store, jid)
        job = server.store.get(jid)
        assert job["status"] == "rendered", job.get("error")
        # the owner's recording is trimmed, and polished with noise handling only for studio level
        assert calls["prepare"][0][1] == {"start": 1.5, "end": 6.0}
        assert calls["polish"][0] == ("own_aaaaaaaaaaaa_" + render.short_hash(1.5, 6.0) + ".mp3", "light", -2.0, True)
        assert calls["polish"][1][1:] == ("light", 0.0, False)
        # music settings reach the mixer
        assert calls["pick"] == ["calm.mp3"]
        args, kw = calls["mix"][0]
        assert args[4] == 0.2 and kw == {"ducking": "gentle", "fade_in": 1.0, "fade_out": 4.0, "start": 5.0}
        # loudness mastering and the audio export
        assert calls["master"] == [-16.0] and calls["mp3"] == ["audio.mp3"]
        assert job["outputs"]["loudness"] == {"before": -20.0, "after": -16.0, "applied": True, "target": -16.0}
        assert job["outputs"]["audio"] == "output/audio.mp3"
        # re-trimming re-runs only that voice; the same trim does not
        n = len(calls["prepare"])
        pipeline.run_render(server.store, jid)
        assert len(calls["prepare"]) == n
        sc = server.store.get(jid)["script"]
        sc["scenes"][0]["edit"]["voice_out"] = 7
        pipeline.set_script(server.store, jid, sc)
        pipeline.run_render(server.store, jid)
        assert len(calls["prepare"]) == n + 1 and calls["prepare"][-1][1]["end"] == 7.0
        # master off and no mp3: neither runs, the video is still produced
        calls["master"].clear(); calls["mp3"].clear()
        server.store.update(jid, studio={"audio": {"master": False}})
        pipeline.run_render(server.store, jid)
        assert calls["master"] == [] and calls["mp3"] == []
        j = server.store.get(jid)
        assert "loudness" not in j["outputs"] and "audio" not in j["outputs"]
        assert not (server.store.job_dir(jid) / "output" / "audio.mp3").exists()


def test_ffmpeg_command_builders():
    chain = render.polish_chain("studio", 3, True)
    assert chain.startswith("volume=3dB,highpass=f=80,afftdn") and "alimiter" in chain
    assert "afftdn" not in render.polish_chain("studio", 0, False) and render.polish_chain("off", 0) == ""
    assert render.polish_chain("off", -2) == "volume=-2dB" and render.polish_chain("light").count("acompressor") == 1
    assert render.grade_filter() == "eq=brightness=-0.05:saturation=1.05"  # what stock footage always had
    assert render.grade_filter(is_video=False) == "" and render.grade_filter("mono", is_video=False) == render.LOOK_FILTERS["mono"]
    z, x, y = render.zoom_exprs("zoom_out", 100)
    assert z.startswith("1.12-") and "on/100" in z
    assert render.zoom_exprs("pan_right", 90)[1] == "(iw-iw/zoom)*on/90"

    seen = []
    orig = media.run
    media.run = lambda cmd, timeout=0: seen.append(cmd) or SimpleNamespace(stderr="", stdout="")
    try:
        render.mix_music(Path("v.mp4"), Path("m.mp3"), Path("o.mp4"), 60.0, 0.12)  # the defaults are the old sound
        fc = seen[-1][seen[-1].index("-filter_complex") + 1]
        assert "sidechaincompress=threshold=0.04:ratio=6" in fc and "afade=t=in:st=0:d=2" in fc and "afade=t=out:st=57.00:d=3" in fc
        render.mix_music(Path("v.mp4"), Path("m.mp3"), Path("o.mp4"), 60.0, 0.3, ducking="off", fade_in=0, fade_out=0, start=12)
        fc = seen[-1][seen[-1].index("-filter_complex") + 1]
        assert "sidechain" not in fc and "afade" not in fc and "atrim=start=12:end=72.00" in fc
        render.prepare_voice(Path("r.webm"), Path("o.mp3"), start=1.5, end=6)
        cmd = seen[-1]
        assert cmd[cmd.index("-ss") + 1] == "1.50" and cmd[cmd.index("-t") + 1] == "4.50" and cmd.index("-ss") < cmd.index("-i")
        render.prepare_voice(Path("r.webm"), Path("o.mp3"))
        assert "-ss" not in seen[-1] and "-t" not in seen[-1]
        orig_duration, media.duration = media.duration, lambda p: 10.0
        try:
            render.prepare_background(Path("c.mp4"), Path("o.mp4"), start=3, speed=2)
            cmd = seen[-1]
            assert cmd[cmd.index("-ss") + 1] == "3.00" and "setpts=PTS/2.0000" in cmd[cmd.index("-vf") + 1]
            render.prepare_background(Path("c.mp4"), Path("o.mp4"), start=99, speed=1)  # past the end: stays inside the clip
            assert seen[-1][seen[-1].index("-ss") + 1] == "9.00"
        finally:
            media.duration = orig_duration
    finally:
        media.run = orig


def test_loudness_master():
    report = lambda i: SimpleNamespace(stderr='[Parsed_loudnorm_0 @ 0x1]\n{\n"input_i" : "%s",\n"input_tp" : "-3.0",\n'
                                       '"input_lra" : "4.0",\n"input_thresh" : "-30.0",\n"output_i" : "-14.0",\n'
                                       '"output_tp" : "-1.0",\n"output_lra" : "3.0",\n"output_thresh" : "-24.0",\n'
                                       '"normalization_type" : "dynamic",\n"target_offset" : "0.5"\n}' % i, stdout="")
    tmp = Path(tempfile.mkdtemp()); src = tmp / "in.mp4"; src.write_bytes(b"v" * 100); out = tmp / "out.mp4"
    orig, calls = media.run, []
    outs = iter([report("-22.3"), SimpleNamespace(stderr="", stdout=""), report("-14.1")])
    media.run = lambda cmd, timeout=0: calls.append(cmd) or next(outs)
    try:
        m = render.loudnorm_measure(src)
        assert m["input_i"] == -22.3 and m["target_offset"] == 0.5 and "normalization_type" not in m
        outs = iter([report("-22.3"), SimpleNamespace(stderr="", stdout=""), report("-14.1")])
        res = render.master(src, out, -14.0)
        assert res == {"before": -22.3, "after": -14.1, "applied": True}
        pass2 = calls[-2][calls[-2].index("-af") + 1]
        assert "measured_I=-22.3" in pass2 and "linear=true" in pass2 and "offset=0.5" in pass2
        assert calls[-2][calls[-2].index("-c:v") + 1] == "copy"  # the picture is not re-encoded
        # silent audio is left alone
        calls.clear(); outs = iter([report("-inf")])
        assert render.master(src, out, -14.0) == {"before": None, "after": None, "applied": False}
        assert len(calls) == 1 and out.read_bytes() == b"v" * 100
    finally:
        media.run = orig


# ---------------------------------------------------------------- preview ----
def test_scene_preview():
    with _stubbed_render() as calls:
        jid = _job_with_script({"title": "t", "scenes": [_scene("एक"), _scene("दो")]})
        pipeline.run_render(server.store, jid)
        before = server.store.get(jid)
        full_scenes = len(calls["scene"])
        starts = {k: v["start"] for k, v in before["assets"].items()}
        server.store.update(jid, reviewed={"at": "x"})
        sc = before["script"]; sc["scenes"][1]["edit"] = {"look": "mono"}
        pipeline.set_script(server.store, jid, sc)
        server.store.update(jid, reviewed={"at": "x"})

        status_before = server.store.get(jid)["status"]
        r = client.post(f"/api/jobs/{jid}/preview", json={"scene": "s02"}, headers=H)
        assert r.status_code == 200 and r.json()["previewing"] == "s02"
        for _ in range(100):  # the real server runs it on a worker thread
            if not server.store.get(jid).get("previewing"):
                break
            time.sleep(0.05)
        job = server.store.get(jid)
        assert not job.get("error") and job["previewing"] == "" and job["preview"]["scene"] == "s02"
        kw = calls["scene"][full_scenes]
        assert kw["size"] == (960, 540) and kw["crf"] == 28 and kw["preset"] == "ultrafast" and kw["grade"].startswith("hue=s=0")
        assert (server.store.job_dir(jid) / "output" / "preview.mp4").exists()
        # the finished video, its confirmation and the timings used for subtitles are untouched
        assert job["status"] == status_before and job["reviewed"] and job["outputs"]["video"] == "output/video.mp4"
        assert {k: v["start"] for k, v in job["assets"].items()} == starts
        assert len(calls["concat"]) == 1 and calls["master"] == []

        assert client.post(f"/api/jobs/{jid}/preview", json={"scene": "s09"}, headers=H).status_code == 404
        assert client.post(f"/api/jobs/{jid}/preview", json={"scene": "bad"}, headers=H).status_code == 422
        server.store.update(jid, previewing="s01")
        assert client.post(f"/api/jobs/{jid}/preview", json={"scene": "s01"}, headers=H).status_code == 409
        assert client.delete(f"/api/jobs/{jid}", headers=H).status_code == 409
        server.store.update(jid, previewing="")
        # an empty scene cannot be previewed
        sc = server.store.get(jid)["script"]; sc["scenes"][0]["narration"] = ""
        server.store.update(jid, script=sc)
        assert client.post(f"/api/jobs/{jid}/preview", json={"scene": "s01"}, headers=H).status_code == 400


def test_restart_clears_preview_flag():
    jid = server.store.create(topic="t", kind="quote", format="short", minutes=1)["id"]
    server.store.update(jid, previewing="s01")
    server.store.recover_interrupted()
    assert server.store.get(jid)["previewing"] == ""


# ----------------------------------------------------- media, music, logo ----
def test_own_media_upload():
    jid = _job_with_script({"title": "t", "scenes": [_scene()]})
    orig = media.probe
    media.probe = lambda p: {"streams": [{"codec_type": "video", "width": 640, "height": 360}],
                             "format": {"duration": "12.5"}}
    try:
        r = client.post(f"/api/jobs/{jid}/media?ext=mp4", content=b"v" * 5000, headers=H)
        assert r.status_code == 200 and r.json()["kind"] == "video" and r.json()["duration"] == 12.5
        name = r.json()["bg_file"]
        assert studio.MEDIA_FILE_RE.fullmatch(name)
        assert client.get(f"/media/{jid}/{name}").content == b"v" * 5000
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (80, 60), (5, 90, 40)).save(buf, "JPEG")
        r = client.post(f"/api/jobs/{jid}/media?ext=jpeg", content=buf.getvalue() + bytes(1200), headers=H)
        assert r.json()["bg_file"].endswith(".jpg") and (r.json()["kind"], r.json()["width"]) == ("image", 80)
        # a renamed non-picture is refused even though ffprobe would take the extension on trust
        r = client.post(f"/api/jobs/{jid}/media?ext=png", content=b"not a picture" * 200, headers=H)
        assert r.status_code == 400 and not list((server.store.job_dir(jid) / "media").glob("*.png"))
        for bad, msg in (("exe", "Use one"), ("mp4", "empty")):
            r = client.post(f"/api/jobs/{jid}/media?ext={bad}", content=b"x" * (5000 if bad == "exe" else 10), headers=H)
            assert r.status_code == 400 and msg in r.json()["detail"]
        assert client.get(f"/media/{jid}/..%2Fjob.json").status_code == 404
        assert client.get(f"/media/{jid}/ffffffffffff.mp4").status_code == 404
        # a file with no picture in it is refused and not kept
        media.probe = lambda p: {"streams": [{"codec_type": "audio"}], "format": {"duration": "9"}}
        r = client.post(f"/api/jobs/{jid}/media?ext=mov", content=b"a" * 5000, headers=H)
        assert r.status_code == 400 and not list((server.store.job_dir(jid) / "media").glob("*.mov"))
        media.probe = lambda p: (_ for _ in ()).throw(media.MediaError("bad"))
        assert client.post(f"/api/jobs/{jid}/media?ext=webm", content=b"b" * 5000, headers=H).status_code == 400
    finally:
        media.probe = orig


def test_music_library():
    orig = media.probe
    media.probe = lambda p: {"streams": [{"codec_type": "audio"}], "format": {"duration": "95.4"}}
    try:
        assert client.get("/api/music").json() == {"tracks": []}
        r = client.post("/api/music?name=..%2F..%2FCalm%20Raga!.MP3", content=b"m" * 5000, headers=H)
        assert r.status_code == 200 and r.json()["name"] == "Calm Raga_.mp3" and r.json()["duration"] == 95.4
        assert (_TMP / "music" / "Calm Raga_.mp3").exists()
        r = client.post("/api/music?name=Calm%20Raga_.mp3", content=b"m" * 5000, headers=H)  # never overwritten
        assert r.json()["name"] == "Calm Raga_-2.mp3"
        for name, data in (("song.exe", b"m" * 5000), ("x.mp3", b"m"), (".mp3", b"m" * 5000)):
            assert client.post(f"/api/music?name={name}", content=data, headers=H).status_code == 400
        # licence notes
        r = client.put("/api/music/Calm%20Raga_.mp3/license", json={"note": "YouTube Audio Library"}, headers=H)
        assert {t["name"]: t["license"] for t in r.json()["tracks"]} == {
            "Calm Raga_.mp3": "YouTube Audio Library", "Calm Raga_-2.mp3": ""}
        assert client.put("/api/music/nope.mp3/license", json={"note": "x"}, headers=H).status_code == 404
        assert client.get("/music/Calm%20Raga_.mp3").content == b"m" * 5000
        assert client.get("/music/..%2Fx.mp3").status_code == 404 and client.get("/music/licenses.json").status_code == 404
        # a track without a note is flagged when a render uses it
        with _stubbed_render() as calls:
            render.pick_music = lambda seed, track="": (_TMP / "music" / "Calm Raga_-2.mp3", "Calm Raga_-2.mp3")
            jid = _job_with_script({"title": "t", "scenes": [_scene()]})
            pipeline.run_render(server.store, jid)
            assert any("No licence note" in w for w in server.store.get(jid)["warnings"])
            render.pick_music = lambda seed, track="": (_TMP / "music" / "Calm Raga_.mp3", "Calm Raga_.mp3")
            jid2 = _job_with_script({"title": "t", "scenes": [_scene()]})
            pipeline.run_render(server.store, jid2)
            assert not any("licence" in w.lower() for w in server.store.get(jid2)["warnings"])
        assert client.delete("/api/music/Calm%20Raga_.mp3", headers=H).status_code == 200
        assert "Calm Raga_.mp3" not in json.loads(studio.LICENSE_FILE.read_text(encoding="utf-8"))
        assert client.delete("/api/music/Calm%20Raga_.mp3", headers=H).status_code == 404
        assert client.delete("/api/music/..%2Fapp.py", headers=H).status_code in (404, 405)
    finally:
        media.probe = orig
        for p in (_TMP / "music").glob("*.mp3"):
            p.unlink()


def test_branding_logo():
    assert studio.branding()["has_logo"] is False and studio.effective_logo(studio.default_studio()) is None
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (1500, 900), (10, 120, 60)).save(buf, "JPEG")
    r = client.post("/api/branding/logo", content=buf.getvalue(), headers=H)
    assert r.status_code == 200 and r.json()["has_logo"] is True
    with Image.open(studio.LOGO_FILE) as im:
        assert im.mode == "RGBA" and max(im.size) == 600  # shrunk, kept as PNG
    assert client.get("/branding/logo").status_code == 200
    assert client.post("/api/branding/logo", content=b"not an image", headers=H).status_code == 400
    assert client.put("/api/branding", json={"corner": "zz"}, headers=H).status_code == 400
    assert client.put("/api/branding", json={"size": 5}, headers=H).status_code == 422
    b = client.put("/api/branding", json={"corner": "br", "size": 0.2, "opacity": 0.5, "auto": True}, headers=H).json()
    assert (b["corner"], b["size"], b["opacity"], b["auto"]) == ("br", 0.2, 0.5, True)
    cfg = studio.default_studio()  # "follow the channel setting": auto is on, so the logo shows
    assert studio.effective_logo(cfg)["corner"] == "br"
    cfg["logo"]["show"] = False
    assert studio.effective_logo(cfg) is None
    assert client.delete("/api/branding/logo", headers=H).json()["has_logo"] is False
    cfg["logo"]["show"] = True
    assert studio.effective_logo(cfg) is None  # nothing to show without a file


def test_waveform_endpoint_and_peaks():
    import array
    import subprocess
    pcm = array.array("h", [0] * 4000 + [16384] * 4000 + [-32768] * 4000)  # 1.5 s at 8 kHz
    orig = subprocess.run
    studio.subprocess.run = lambda *a, **k: SimpleNamespace(returncode=0, stdout=pcm.tobytes(), stderr=b"")
    try:
        w = studio.waveform(Path("x.wav"), buckets=12)
        assert w["duration"] == 1.5 and len(w["peaks"]) == 12
        assert w["peaks"][:4] == [0.0] * 4 and w["peaks"][4:8] == [0.5] * 4 and w["peaks"][8:] == [1.0] * 4
        jid = _job_with_script({"title": "t", "scenes": [_scene(voice_file="aaaaaaaaaaaa.webm"), _scene("दो")]})
        r = client.get(f"/api/jobs/{jid}/waveform?scene=s02")
        assert r.status_code == 404 and "render" in r.json()["detail"].lower()  # nothing rendered yet
        assert client.get(f"/api/jobs/{jid}/waveform?scene=s09").status_code == 404
        rec = server.store.job_dir(jid) / "voice"; rec.mkdir(); (rec / "aaaaaaaaaaaa.webm").write_bytes(b"v" * 100)
        r = client.get(f"/api/jobs/{jid}/waveform?scene=s01")
        assert r.status_code == 200 and r.json()["source"] == "recording" and len(r.json()["peaks"]) > 10
        audio = server.store.job_dir(jid) / "a.mp3"; audio.write_bytes(b"a" * 50)
        server.store.update(jid, assets={"s02": {"audio": str(audio)}})
        assert client.get(f"/api/jobs/{jid}/waveform?scene=s02").json()["source"] == "voice-over"
        studio.subprocess.run = lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"x")
        assert client.get(f"/api/jobs/{jid}/waveform?scene=s02").status_code == 400
    finally:
        studio.subprocess.run = orig


def test_audio_export_endpoint_and_short_keeps_studio():
    with _stubbed_render() as calls:
        jid = _job_with_script({"title": "t", "scenes": [
            _scene("एक", edit={"bg_file": "abcdef012345.jpg", "look": "warm"}), _scene("दो")]})
        own = server.store.job_dir(jid) / "media"; own.mkdir(); _png(own / "abcdef012345.jpg")
        server.store.update(jid, studio={"captions": {"style": "highlight"}, "thumbnail": {"scene": "s01"}})
        assert client.post(f"/api/jobs/{jid}/audio-export", headers=H).status_code == 400  # nothing rendered yet
        pipeline.run_render(server.store, jid)
        r = client.post(f"/api/jobs/{jid}/audio-export", headers=H)
        assert r.status_code == 200 and r.json()["outputs"]["audio"] == "output/audio.mp3" and calls["mp3"]

        new = pipeline.derive_short(server.store, jid, [0, 1])
        assert new["studio"]["captions"]["style"] == "highlight" and new["studio"]["thumbnail"]["scene"] == ""
        sc = new["script"]["scenes"][0]
        assert sc["edit"]["bg_file"] == "abcdef012345.jpg" and sc["edit"]["look"] == "warm"
        assert (server.store.job_dir(new["id"]) / "media" / "abcdef012345.jpg").exists()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
