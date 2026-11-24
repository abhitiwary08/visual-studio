"""Fast logic tests (no network, no ffmpeg):  python tests/test_logic.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["YT_DATA_DIR"] = tempfile.mkdtemp(prefix="yt_logic_")

from app import captions, media, pipeline, scriptgen, tts  # noqa: E402
from app.jobs import JobStore  # noqa: E402
from app import llm as _llm  # noqa: E402

# Never use the real AI-writer choice or API keys from config/llm.json and .env.
_llm.SETTINGS_FILE = Path(tempfile.mkdtemp(prefix="yt_llm_")) / "llm.json"
for _k in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)


def test_normalize():
    s = scriptgen.normalize_script({
        "title": "T", "scenes": [
            {"narration": "  एक  ", "visual": {"type": "slide", "text": "ॐ"}, "pause_after": 99},
            {"narration": "", "visual": {"type": "stock"}},                      # dropped: no narration
            {"narration": "दो", "visual": {"type": "weird"}},                    # bad type -> stock
            {"narration": "तीन", "visual": {"type": "slide"}},                   # empty slide -> stock
        ]}, "long")
    assert [x["id"] for x in s["scenes"]] == ["s01", "s02", "s03"]
    assert s["scenes"][0]["pause_after"] == 25.0
    assert s["scenes"][1]["visual"]["type"] == "stock" and s["scenes"][2]["visual"]["type"] == "stock"
    assert s["thumbnail_text"] == "T"
    for bad in ({}, {"scenes": []}, {"scenes": [{"narration": ""}]}, []):
        try:
            scriptgen.normalize_script(bad, "long")
            raise AssertionError("should have failed")
        except scriptgen.ScriptError:
            pass


def test_parse_pasted():
    raw = "```json\n" + json.dumps({"scenes": [{"narration": "नमस्ते", "visual": {"type": "stock", "query": "river"}}]}) + "\n```"
    assert scriptgen.parse_pasted(raw, "long")["scenes"][0]["visual"]["query"] == "river"


def test_captions():
    pages = captions.split_pages("पहला वाक्य यहाँ है। दूसरा वाक्य थोड़ा लंबा है, इसलिए वह दो भागों में बँटेगा।", 5)
    assert all(len(p.split()) <= 5 for p in pages) and len(pages) >= 3
    timed = captions.time_pages(pages, 10.0)
    assert timed[0][1] > 0 and abs(timed[-1][2] - 10.0) < 1e-6
    assert all(a[2] <= b[1] + 1e-9 for a, b in zip(timed, timed[1:]))  # no overlap


def test_tts_helpers():
    assert tts.clean_for_speech("नमस्ते [pause] **दुनिया**") == "नमस्ते दुनिया"
    assert tts.cache_key("a", "v", "-8%", "+0Hz") != tts.cache_key("a", "v", "-10%", "+0Hz")


def test_srt_time():
    assert media.fmt_srt_time(3725.5) == "01:02:05,500"


def test_metadata_and_footer():
    store = JobStore()
    job = store.create(topic="t", kind="quote", format="short", minutes=1)
    script = scriptgen.normalize_script({"title": "शीर्षक", "description": "विवरण", "sources": ["Gita 2.47"],
                                         "scenes": [{"narration": "x", "visual": {"type": "stock"}}]}, "short")
    pipeline._apply_script(store, job["id"], script)
    job = store.get(job["id"])
    assert job["metadata"]["title"].endswith("#Shorts") and job["status"] == "script_ready"
    footer = pipeline.description_footer(job)
    assert "Gita 2.47" in footer and "AI-generated" in footer
    # keep_metadata=True must not overwrite edits
    store.update(job["id"], metadata={**job["metadata"], "title": "मेरा शीर्षक"})
    pipeline._apply_script(store, job["id"], script, keep_metadata=True)
    assert store.get(job["id"])["metadata"]["title"] == "मेरा शीर्षक"


def test_call_tool_parsing():
    """The Claude call returns a forced tool_use block; make sure we read it correctly."""
    payload = {"title": "T", "description": "D", "tags": [], "thumbnail_text": "x", "verify_notes": [],
               "scenes": [{"narration": "नमस्ते", "visual": {"type": "stock", "query": "sunrise"}}]}

    class FakeStream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="hi"),
                                            SimpleNamespace(type="tool_use", name="submit_script", input=payload)])

    seen = {}
    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kw):
                seen.update(kw); return FakeStream()

    scriptgen._client = lambda: FakeClient()
    out = scriptgen.generate_script("topic", "upanishad_story", "long", 5, notes="gentle")
    assert out["scenes"][0]["narration"] == "नमस्ते"
    assert seen["tool_choice"] == {"type": "tool", "name": "submit_script"}
    assert "gentle" in seen["messages"][0]["content"] and seen["max_tokens"] >= 4000


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
