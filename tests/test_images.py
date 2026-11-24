"""Tests for AI scene pictures with consistent characters (app/storyboard.py, app/imagegen.py).
No network, no API keys, no ffmpeg:   python tests/test_images.py"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_features as tf  # noqa: E402  (isolated data folder, settings and API client)
from test_features import H, _job_with_script, _stubbed_render, client, server  # noqa: E402

from app import imagegen, pipeline, scriptgen, storyboard  # noqa: E402

imagegen.SETTINGS_FILE = Path(tempfile.mkdtemp(prefix="yt_img_")) / "images.json"


def _png_bytes(color=(10, 120, 60), size=(64, 40)) -> bytes:
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", size, color).save(buf, "PNG"); return buf.getvalue()


class _Resp:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}
        self.text = data if isinstance(data, str) else json.dumps(data)

    def json(self):
        return self._data


SCRIPT = {"title": "नचिकेता", "scenes": [
    {"narration": "नचिकेता एक जिज्ञासु बालक था।", "visual": {"type": "stock", "query": "boy"}},
    {"narration": "उसके पिता वाजश्रवा ने यज्ञ किया।", "visual": {"type": "stock", "query": "fire"}},
    {"narration": "उत्तिष्ठत जाग्रत", "visual": {"type": "slide", "text": "उत्तिष्ठत जाग्रत"}},
    {"narration": "मेरा अपना दृश्य।", "visual": {"type": "stock"}, "edit": {"bg_file": "abcdef012345.png"}},
    {"narration": "यम ने तीन वर दिए।", "visual": {"type": "stock", "query": "river"}},
]}
PLAN = {
    "characters": [
        {"id": "c1", "name": "नचिकेता", "name_en": "Nachiketa", "look": "A 10-year-old boy, slim, dark wavy hair tied in a small knot, saffron dhoti, rudraksha bead."},
        {"id": "c2", "name": "वाजश्रवा", "name_en": "Vajashravas", "look": "A 50-year-old sage, grey beard to the chest, white dhoti, sacred thread."},
        {"id": "c3", "name": "यम", "name_en": "Yama", "look": "A calm dark-skinned god, golden crown, deep green robes, kind eyes."},
    ],
    "setting": "Vedic India, forest ashram by a river, warm earthy palette.",
    "scenes": [
        {"scene": 1, "prompt": "Nachiketa sits under a banyan tree, wide shot, morning light.", "characters": ["c1"]},
        {"scene": 2, "prompt": "Vajashravas pours ghee into the sacred fire while Nachiketa watches.", "characters": ["c2", "c1", "c9"]},
        {"scene": 5, "prompt": "Yama offers three boons to Nachiketa beside a misty river.", "characters": ["c3", "c1"]},
    ],
}


def _with_plan(plan=PLAN):
    seen = {}
    orig = scriptgen._call_tool
    scriptgen._call_tool = lambda system, user, tool, mt: seen.update(system=system, user=user, tool=tool["name"]) or json.loads(json.dumps(plan))
    return seen, orig


def _wait(jid, key="imaging", timeout=10):
    for _ in range(int(timeout / 0.05)):
        if not server.store.get(jid).get(key):
            return server.store.get(jid)
        time.sleep(0.05)
    raise AssertionError(f"{key} did not finish")


# --------------------------------------------------------------- validation ----
def test_script_keeps_image_scenes():
    s = scriptgen.normalize_script({"scenes": [
        {"narration": "a", "visual": {"type": "image", "prompt": "  a boy  ", "chars": ["c1", "c1", "x", "c22", 5]}},
        {"narration": "b", "visual": {"type": "stock", "prompt": "kept for later"}},
        {"narration": "c", "visual": {"type": "image"}}]}, "long")
    v = [x["visual"] for x in s["scenes"]]
    assert v[0]["type"] == "image" and v[0]["prompt"] == "a boy" and v[0]["chars"] == ["c1", "c22"]
    assert v[1]["type"] == "stock" and v[1]["prompt"] == "kept for later" and "chars" not in v[1]
    assert v[2]["type"] == "image" and "prompt" not in v[2]


def test_storyboard_validation():
    sb = storyboard.normalize_storyboard({"style": "nope", "characters": [
        {"id": "c1", "name": "A", "look": "x", "ref": "../evil.png"}, {"id": "c1", "name": "B", "look": "y"},
        {"id": "zz", "name": "C", "look": "z"}, {"name": "", "look": "q"}, "junk"]})
    assert sb["style"] == storyboard.DEFAULT_STYLE
    assert [(c["id"], c["name"], c["ref"]) for c in sb["characters"]] == [("c1", "A", ""), ("c2", "B", ""), ("c3", "C", "")]
    keep = {"style": "miniature", "characters": [{"id": "c1", "name": "A", "look": "x", "ref": "0123456789ab.png"},
                                                 {"id": "c2", "name": "B", "look": "y", "ref": "ba9876543210.png"}]}
    again = storyboard.normalize_storyboard({"characters": [{"id": "c5", "name": "B", "look": "new"},
                                                            {"id": "c1", "name": "A", "look": "x2"}]}, keep=keep)
    assert again["style"] == "miniature" and {c["name"]: c["ref"] for c in again["characters"]} == {
        "B": "ba9876543210.png", "A": "0123456789ab.png"}  # sheets follow the character by id or name


# ----------------------------------------------------------------- planning ----
def test_plan_switches_scenes_and_keeps_others():
    jid = _job_with_script(json.loads(json.dumps(SCRIPT)))
    seen, orig = _with_plan()
    try:
        r = client.post(f"/api/jobs/{jid}/storyboard/plan", json={"style": "miniature"}, headers=H)
        assert r.status_code == 200, r.text
    finally:
        scriptgen._call_tool = orig
    job = r.json()
    types = [s["visual"]["type"] for s in job["script"]["scenes"]]
    assert types == ["image", "image", "slide", "stock", "image"]  # slide and own footage untouched
    assert job["script"]["scenes"][1]["visual"]["chars"] == ["c2", "c1"]  # unknown id dropped
    assert job["script"]["scenes"][0]["visual"]["prompt"].startswith("Nachiketa sits")
    assert job["storyboard"]["style"] == "miniature" and len(job["storyboard"]["characters"]) == 3
    assert "Scenes to illustrate" in seen["user"] and "\n3. " not in seen["user"].split("Other scenes")[0]
    assert "Other scenes, for context only" in seen["user"] and seen["tool"] == "submit_storyboard"
    assert "respectfully" in seen["system"]
    # scene ids are unchanged, so studio edits and rendered pieces stay attached
    assert [s["id"] for s in job["script"]["scenes"]] == ["s01", "s02", "s03", "s04", "s05"]
    # slides can be included on request
    seen, orig = _with_plan({**PLAN, "scenes": PLAN["scenes"] + [{"scene": 3, "prompt": "A rising sun.", "characters": []}]})
    try:
        job = client.post(f"/api/jobs/{jid}/storyboard/plan", json={"include_slides": True}, headers=H).json()
    finally:
        scriptgen._call_tool = orig
    assert job["script"]["scenes"][2]["visual"]["type"] == "image" and job["storyboard"]["style"] == "miniature"
    assert "Existing characters to keep" in seen["user"]
    # an incomplete answer is reported
    seen, orig = _with_plan({**PLAN, "scenes": PLAN["scenes"][:1]})
    try:
        jid2 = _job_with_script(json.loads(json.dumps(SCRIPT)))
        client.post(f"/api/jobs/{jid2}/storyboard/plan", json={}, headers=H)
    finally:
        scriptgen._call_tool = orig
    assert any("described 1 of 3" in w for w in server.store.get(jid2)["warnings"])
    assert client.post(f"/api/jobs/{jid2}/storyboard/plan", json={"style": "bad"}, headers=H).status_code == 400


def test_new_video_with_ai_pictures_is_planned_after_the_script():
    seen, orig = _with_plan()
    orig_gen = scriptgen.generate_script
    scriptgen.generate_script = lambda *a, **k: scriptgen.normalize_script(json.loads(json.dumps(SCRIPT)), "long")
    try:
        r = client.post("/api/jobs", json={"topic": "Nachiketa", "kind": "upanishad_story", "format": "long",
                                           "minutes": 2, "ai_images": True}, headers=H)
        jid = r.json()["id"]
        for _ in range(200):
            j = server.store.get(jid)
            if j["status"] == "script_ready" and j.get("storyboard"):
                break
            time.sleep(0.05)
    finally:
        scriptgen._call_tool, scriptgen.generate_script = orig, orig_gen
    assert j.get("storyboard") and j["script"]["scenes"][0]["visual"]["type"] == "image"
    assert not j.get("scene_images")  # pictures cost money: drawing waits for the owner


# ------------------------------------------------------------------ prompts ----
def test_prompts_carry_style_characters_and_references():
    sb = storyboard.normalize_storyboard({**PLAN, "style": "storybook", "style_note": "soft pastel"})
    sb["characters"][0]["ref"] = "0123456789ab.png"
    sc = {"id": "s02", "narration": "x", "visual": {"type": "image", "prompt": "Vajashravas pours ghee.", "chars": ["c2", "c1"]}}
    prompt, refs = storyboard.scene_prompt(sb, sc, "long")
    assert prompt.startswith(storyboard.STYLES["storybook"]["prompt"] + ". soft pastel.")
    assert "Setting of the whole story: Vedic India" in prompt and "Scene: Vajashravas pours ghee." in prompt
    assert "Vajashravas: A 50-year-old sage" in prompt and "Nachiketa: A 10-year-old boy" in prompt
    assert [c["id"] for c in refs] == ["c1"] and "reference image 1 is Nachiketa" in prompt
    assert "No text, letters" in prompt and "Wide 16:9" in prompt
    assert "Vertical 9:16" in storyboard.scene_prompt(sb, sc, "short")[0]
    cp = storyboard.character_prompt(sb, sb["characters"][1])
    assert cp.startswith("Character reference sheet of Vajashravas. A 50-year-old sage") and "plain light grey" in cp
    assert storyboard.default_motion("s01") == storyboard.default_motion("s01")
    assert len({storyboard.default_motion(f"s{i:02d}") for i in range(1, 30)}) == 4


# ---------------------------------------------------------------- providers ----
def test_provider_choice_and_settings():
    os.environ.pop("IMAGES_FORCE_PROVIDER", None)
    assert imagegen.provider() == "placeholder"  # no keys: the free placeholder
    os.environ["OPENAI_API_KEY"] = "k" * 20
    try:
        assert imagegen.provider() == "openai" and imagegen.model() == "gpt-image-1.5"
        r = client.put("/api/images/settings", json={"provider": "gemini", "model": "", "quality": "high"}, headers=H)
        assert r.status_code == 200 and r.json()["provider"] == "gemini" and r.json()["ready"] is False
        assert r.json()["quality"] == "high"
        assert client.put("/api/images/settings", json={"provider": "x"}, headers=H).status_code == 400
        assert client.put("/api/images/settings", json={"provider": "openai", "model": "bad model!"}, headers=H).status_code == 400
        os.environ["IMAGES_FORCE_PROVIDER"] = "placeholder"
        assert imagegen.provider() == "placeholder" and imagegen.describe()["forced"]
    finally:
        os.environ.pop("OPENAI_API_KEY", None); os.environ.pop("IMAGES_FORCE_PROVIDER", None)
        imagegen.SETTINGS_FILE.unlink(missing_ok=True)


def test_openai_requests():
    calls = []
    good = _Resp(200, {"data": [{"b64_json": base64.b64encode(_png_bytes()).decode()}]})
    orig, orig_sleep = imagegen._request, imagegen._sleep
    imagegen._request = lambda method, url, **kw: calls.append((url, kw)) or good
    imagegen._sleep = lambda s: None
    os.environ["OPENAI_API_KEY"] = "k" * 20
    imagegen.save("openai", "", "medium")
    tmp = Path(tempfile.mkdtemp()); ref = tmp / "ref.png"; ref.write_bytes(_png_bytes((200, 0, 0), (2000, 1000)))
    try:
        out = imagegen.generate("a boy", [ref, ref], "16:9", tmp / "o.png")
        url, kw = calls[-1]
        assert url.endswith("/v1/images/edits") and [f[0] for f in kw["files"]] == ["image[]", "image[]"]
        assert kw["data"]["size"] == "1536x1024" and kw["data"]["input_fidelity"] == "high"
        assert kw["data"]["model"] == "gpt-image-1.5" and kw["headers"]["Authorization"].startswith("Bearer ")
        from PIL import Image
        with Image.open(io.BytesIO(kw["files"][0][1][1])) as sent:
            assert max(sent.size) == 1024  # big references are shrunk before upload
        with Image.open(out) as im:
            assert im.format == "PNG"
        imagegen.generate("a sunrise", [], "9:16", tmp / "p.png")
        url, kw = calls[-1]
        assert url.endswith("/v1/images/generations") and kw["json"]["size"] == "1024x1536" and kw["json"]["quality"] == "medium"
        imagegen.save("openai", "gpt-image-2")
        imagegen.generate("a boy", [ref], "16:9", tmp / "q.png")
        assert "input_fidelity" not in calls[-1][1]["data"]  # only the 1.x models take it
        # overloaded twice, then fine
        seq = iter([_Resp(503, "busy"), _Resp(503, "busy"), good])
        imagegen._request = lambda *a, **k: next(seq)
        imagegen.generate("x", [], "16:9", tmp / "r.png")
        # a refusal is explained, not retried
        n = []
        imagegen._request = lambda *a, **k: n.append(1) or _Resp(400, {"error": {"message": "Your request was rejected by the safety system."}})
        try:
            imagegen.generate("x", [], "16:9", tmp / "s.png"); raise AssertionError
        except imagegen.ImageError as e:
            assert "refused" in str(e) and len(n) == 1
        imagegen._request = lambda *a, **k: _Resp(200, {"data": [{"b64_json": base64.b64encode(b"not a picture").decode()}]})
        try:
            imagegen.generate("x", [], "16:9", tmp / "t.png"); raise AssertionError
        except imagegen.ImageError as e:
            assert "not a picture" in str(e)
    finally:
        imagegen._request, imagegen._sleep = orig, orig_sleep
        os.environ.pop("OPENAI_API_KEY", None)
        imagegen.SETTINGS_FILE.unlink(missing_ok=True)


def test_gemini_requests():
    calls = []
    img = base64.b64encode(_png_bytes()).decode()
    ok = _Resp(200, {"candidates": [{"content": {"parts": [{"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": img}}]}}]})
    orig, orig_sleep = imagegen._request, imagegen._sleep
    imagegen._sleep = lambda s: None
    os.environ["GEMINI_API_KEY"] = "g" * 20
    imagegen.save("gemini")
    tmp = Path(tempfile.mkdtemp()); ref = tmp / "ref.png"; ref.write_bytes(_png_bytes())
    try:
        imagegen._request = lambda method, url, **kw: calls.append((url, kw)) or ok
        imagegen.generate("a boy", [ref], "9:16", tmp / "o.png")
        url, kw = calls[-1]
        assert url.endswith("/models/gemini-3.1-flash-image:generateContent") and kw["headers"]["x-goog-api-key"]
        parts = kw["json"]["contents"][0]["parts"]
        assert parts[0] == {"text": "a boy"} and parts[1]["inlineData"]["mimeType"] == "image/png"
        assert kw["json"]["generationConfig"] == {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}}
        # the newest default model is not on every account: fall back to the established one
        calls.clear()
        seq = iter([_Resp(404, {"error": {"message": "not found"}}), ok])
        imagegen._request = lambda method, url, **kw: calls.append(url) or next(seq)
        imagegen.generate("a boy", [], "16:9", tmp / "p.png")
        assert calls[1].endswith("/models/gemini-2.5-flash-image:generateContent")
        # a blocked prompt is explained
        imagegen._request = lambda *a, **k: _Resp(200, {"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]})
        try:
            imagegen.generate("x", [], "16:9", tmp / "q.png"); raise AssertionError
        except imagegen.ImageError as e:
            assert "IMAGE_SAFETY" in str(e)
        assert len(imagegen._find_image({"a": [{"b": {"inline_data": {"mime_type": "image/png", "data": img}}}]})) > 10
    finally:
        imagegen._request, imagegen._sleep = orig, orig_sleep
        os.environ.pop("GEMINI_API_KEY", None)
        imagegen.SETTINGS_FILE.unlink(missing_ok=True)


# ----------------------------------------------------------------- drawing ----
def _planned_job():
    jid = _job_with_script(json.loads(json.dumps(SCRIPT)))
    seen, orig = _with_plan()
    try:
        storyboard.plan(server.store, jid, "storybook")
    finally:
        scriptgen._call_tool = orig
    return jid


def test_drawing_sheets_then_scenes_with_references():
    os.environ["IMAGES_FORCE_PROVIDER"] = "placeholder"
    calls = []
    real = imagegen.generate
    imagegen.generate = lambda prompt, refs, aspect, out: calls.append((prompt, [Path(r).name for r in refs], aspect)) or real(prompt, refs, aspect, out)
    try:
        jid = _planned_job()
        assert client.post(f"/api/jobs/{_job_with_script()}/images", json={}, headers=H).status_code == 400  # no plan yet
        r = client.post(f"/api/jobs/{jid}/images", json={}, headers=H)
        assert r.status_code == 200 and r.json()["imaging"]
        job = _wait(jid)
        sheets, scenes = calls[:3], calls[3:]
        assert all(p.startswith("Character reference sheet") and refs == [] for p, refs, _ in sheets)
        refs = {c["id"]: c["ref"] for c in job["storyboard"]["characters"]}
        assert all(refs.values())
        # every scene is drawn with the sheets of the characters in it, in the order the prompt names them
        assert [s[1] for s in scenes] == [[refs["c1"]], [refs["c2"], refs["c1"]], [refs["c3"], refs["c1"]]]
        assert all(s[2] == "16:9" for s in scenes)
        imgs = job["scene_images"]
        assert set(imgs) == {"s01", "s02", "s05"} and all((server.store.job_dir(jid) / "media" / v["file"]).exists() for v in imgs.values())
        assert imgs["s01"]["by"] == "Placeholder (free, offline)" and job["imaging"] is None and job["imaging_last"]["errors"] == {}

        # only missing: nothing more to draw
        calls.clear()
        client.post(f"/api/jobs/{jid}/images", json={}, headers=H); _wait(jid)
        assert calls == []
        # redraw one scene: a new version, the old one kept to go back to
        sc = server.store.get(jid)["script"]; sc["scenes"][0]["visual"]["prompt"] = "Nachiketa walks to the river."
        pipeline.set_script(server.store, jid, sc)
        first = imgs["s01"]["file"]
        client.post(f"/api/jobs/{jid}/images", json={"scenes": ["s01"], "only_missing": False}, headers=H)
        job = _wait(jid)
        assert len(calls) == 1 and "Nachiketa walks to the river." in calls[0][0]
        assert job["scene_images"]["s01"]["file"] != first and job["scene_images"]["s01"]["history"] == [first]
        r = client.post(f"/api/jobs/{jid}/images/s01/use", json={"file": first}, headers=H)
        assert r.json()["scene_images"]["s01"]["file"] == first
        assert client.post(f"/api/jobs/{jid}/images/s01/use", json={"file": "ffffffffffff.png"}, headers=H).status_code == 404
        assert client.post(f"/api/jobs/{jid}/images/s01/use", json={"file": "../x.png"}, headers=H).status_code == 422
        # redraw a character sheet on request
        calls.clear()
        client.post(f"/api/jobs/{jid}/images", json={"scenes": [], "characters": ["c2"]}, headers=H); _wait(jid)
        assert len(calls) == 1 and calls[0][0].startswith("Character reference sheet of Vajashravas")
    finally:
        imagegen.generate = real
        os.environ.pop("IMAGES_FORCE_PROVIDER", None)


def test_failures_cancel_and_gates():
    os.environ["IMAGES_FORCE_PROVIDER"] = "placeholder"
    real = imagegen.generate
    n = []

    def flaky(prompt, refs, aspect, out):
        n.append(prompt)
        if "Vajashravas pours" in prompt:
            raise imagegen.ImageError("refused this picture")
        return real(prompt, refs, aspect, out)
    imagegen.generate = flaky
    try:
        jid = _planned_job()
        client.post(f"/api/jobs/{jid}/images", json={}, headers=H)
        job = _wait(jid)
        assert set(job["scene_images"]) == {"s01", "s05"} and "refused" in job["imaging_last"]["errors"]["s02"]
        assert len(n) == 6  # one failure did not stop the rest
        # cancel between pictures
        jid2 = _planned_job()
        imagegen.generate = lambda prompt, refs, aspect, out: (server.store.update(jid2, imaging_cancel=True), real(prompt, refs, aspect, out))[1]
        client.post(f"/api/jobs/{jid2}/images", json={}, headers=H)
        job = _wait(jid2)
        assert len(job.get("scene_images") or {}) == 0 and job["imaging_cancel"] is False
        # while drawing: no render, no preview, no second batch, no delete
        server.store.update(jid2, imaging={"total": 3, "done": 1})
        for method, url, body in (("post", f"/api/jobs/{jid2}/render", None), ("post", f"/api/jobs/{jid2}/images", {}),
                                  ("post", f"/api/jobs/{jid2}/preview", {"scene": "s01"}), ("delete", f"/api/jobs/{jid2}", None)):
            r = getattr(client, method)(url, headers=H, **({"json": body} if body is not None else {}))
            assert r.status_code == 409, (url, r.status_code)
        assert client.post(f"/api/jobs/{jid2}/images/cancel", headers=H).json()["imaging_cancel"] is True
        server.store.recover_interrupted()
        assert server.store.get(jid2)["imaging"] is None
        # without a key for a paid provider, drawing explains what to do
        os.environ.pop("IMAGES_FORCE_PROVIDER", None)
        imagegen.save("openai")
        r = client.post(f"/api/jobs/{jid2}/images", json={}, headers=H)
        assert r.status_code == 400 and "no API key" in r.json()["detail"]
    finally:
        imagegen.generate = real
        os.environ.pop("IMAGES_FORCE_PROVIDER", None)
        imagegen.SETTINGS_FILE.unlink(missing_ok=True)


def test_own_character_picture_and_storyboard_edits():
    jid = _planned_job()
    r = client.put(f"/api/jobs/{jid}/storyboard", json={"storyboard": {
        "style": "devotional", "setting": "Kashi ghats", "characters": [
            {"id": "c1", "name": "नचिकेता", "name_en": "Nachiketa", "look": "A boy in a saffron dhoti."}]}}, headers=H)
    sb = r.json()["storyboard"]
    assert sb["style"] == "devotional" and sb["setting"] == "Kashi ghats" and len(sb["characters"]) == 1
    r = client.post(f"/api/jobs/{jid}/characters/c1/ref?ext=png", content=_png_bytes(size=(300, 300)) + bytes(1200), headers=H)
    assert r.status_code == 200, r.text
    ref = r.json()["storyboard"]["characters"][0]["ref"]
    assert (server.store.job_dir(jid) / "media" / ref).exists()
    assert client.post(f"/api/jobs/{jid}/characters/c9/ref?ext=png", content=_png_bytes(), headers=H).status_code == 404
    assert client.post(f"/api/jobs/{jid}/characters/c1/ref?ext=mp4", content=_png_bytes(), headers=H).status_code == 400
    # an uploaded sheet counts: drawing does not replace it
    os.environ["IMAGES_FORCE_PROVIDER"] = "placeholder"
    try:
        client.post(f"/api/jobs/{jid}/images", json={}, headers=H)
        job = _wait(jid)
        assert job["storyboard"]["characters"][0]["ref"] == ref
    finally:
        os.environ.pop("IMAGES_FORCE_PROVIDER", None)


# ------------------------------------------------------------ in the video ----
def test_render_uses_pictures_with_movement_and_credit():
    os.environ["IMAGES_FORCE_PROVIDER"] = "placeholder"
    try:
        jid = _planned_job()
        client.post(f"/api/jobs/{jid}/images", json={"scenes": ["s01", "s02"]}, headers=H)
        _wait(jid)
    finally:
        os.environ.pop("IMAGES_FORCE_PROVIDER", None)
    own = server.store.job_dir(jid) / "media" / "abcdef012345.png"; own.write_bytes(_png_bytes())
    sc = server.store.get(jid)["script"]
    sc["scenes"][1]["edit"] = {"motion": "pan_left"}
    pipeline.set_script(server.store, jid, sc)
    with _stubbed_render() as calls:
        pipeline.run_render(server.store, jid)
        job = server.store.get(jid)
        assert job["status"] == "rendered", job.get("error")
        s1, s2, s3, s4, s5 = calls["scene"]
        pics = job["scene_images"]
        assert Path(s1["bg_path"]).name == pics["s01"]["file"] and s1["bg_kind"] == "image"
        assert s1["motion"] == storyboard.default_motion("s01")  # AI stills move even without a setting
        assert s2["motion"] == "pan_left"  # the owner's choice wins
        assert s3["motion"] == "none" and "slide_" in Path(s3["bg_path"]).name
        assert Path(s4["bg_path"]).name == "abcdef012345.png"  # own footage wins over everything
        assert "bg" in Path(s5["bg_path"]).name and any("Scene 5 has no AI picture yet" in w for w in job["warnings"])
        assert "Pictures: AI-generated (Placeholder (free, offline))" in pipeline.description_footer(job)
        assert calls["thumb_args"][-1][1] == Path(s1["bg_path"])  # the thumbnail uses the first picture

        # a Short made from these scenes brings its pictures and characters along
        new = pipeline.derive_short(server.store, jid, [0, 1])
        assert set(new["scene_images"]) == {s["id"] for s in new["script"]["scenes"]}
        for v in new["scene_images"].values():
            assert (server.store.job_dir(new["id"]) / "media" / v["file"]).exists()
        assert new["storyboard"]["characters"] == job["storyboard"]["characters"]
        for c in new["storyboard"]["characters"]:
            if c["ref"]:
                assert (server.store.job_dir(new["id"]) / "media" / c["ref"]).exists()


def test_placeholder_card():
    tmp = Path(tempfile.mkdtemp()); ref = tmp / "r.png"; ref.write_bytes(_png_bytes((250, 0, 0), (400, 400)))
    out = imagegen._placeholder("नचिकेता एक नदी के किनारे बैठा है", [ref], "9:16", tmp / "o.png")
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (720, 1280) and im.getpixel((40, 40))[0] > 200  # the reference portrait is on the card


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
