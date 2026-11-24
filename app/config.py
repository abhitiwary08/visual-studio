"""Central configuration: paths, formats, content kinds and env-driven settings."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("YT_DATA_DIR") or ROOT / "data")
JOBS_DIR = DATA_DIR / "jobs"
CACHE_DIR = DATA_DIR / "cache"
ASSETS_DIR = ROOT / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "web"

for _d in (JOBS_DIR, CACHE_DIR, FONT_DIR, MUSIC_DIR, CONFIG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Output formats. "short" is vertical 9:16 for Shorts / Reels / Facebook Reels.
FORMATS = {
    "long": {"w": 1920, "h": 1080, "orientation": "landscape", "max_minutes": 20, "label": "Long video (16:9)"},
    "short": {"w": 1080, "h": 1920, "orientation": "portrait", "max_minutes": 1, "label": "Short / Reel (9:16)"},
}

# Content kinds. `structure` is handed to the script writer; `music_volume` is the
# bed level under the narration (0..1).
KINDS = {
    "upanishad_story": {
        "label": "Upanishad story",
        "default_minutes": 8,
        "music_volume": 0.10,
        "slide_share": "about 15%",
        "structure": (
            "Hook in the first 5 seconds (a question or the central dilemma). Name the Upanishad and "
            "who the characters are. Tell the story faithfully, scene by scene. State the teaching "
            "(the Upanishadic idea) in simple Hindi. Close with one practical life lesson and a "
            "gentle call to subscribe."
        ),
    },
    "mantra_meaning": {
        "label": "Mantra with meaning",
        "default_minutes": 6,
        "music_volume": 0.12,
        "slide_share": "about 40% (the mantra, word-by-word meaning, and overall meaning on slides)",
        "structure": (
            "Hook (why this mantra matters). Show the mantra on a slide. Give word-by-word meaning "
            "(on slides, a few words per slide). Give the overall meaning. Explain origin/source "
            "only if you are certain. Explain how and when it is traditionally chanted. Close "
            "with a gentle call to subscribe. No promises of material results."
        ),
    },
    "short_story": {
        "label": "Short moral story",
        "default_minutes": 4,
        "music_volume": 0.10,
        "slide_share": "about 10%",
        "structure": (
            "Hook. A simple story with a clear beginning, a turn, and an ending. State the moral "
            "in one line (on a slide). Close with a gentle call to subscribe."
        ),
    },
    "meditation": {
        "label": "Guided meditation",
        "default_minutes": 10,
        "music_volume": 0.28,
        "slide_share": "about 10%",
        "structure": (
            "A calm guided meditation. Short sentences, slow pace. Settling in, breath awareness, "
            "the main practice, gentle return. Use `pause_after` generously (8 to 25 seconds) so "
            "the listener can practise in silence with the music. Visuals should be slow, "
            "natural, and soft."
        ),
    },
    "mantra_jaap": {
        "label": "Mantra jaap (repeated chant)",
        "default_minutes": 10,
        "music_volume": 0.22,
        "slide_share": "most scenes (the chant scene must be a slide)",
        "jaap": True,
        "structure": (
            "A short, calm introduction (2-4 scenes, about 60-90 words): which mantra this is, its "
            "meaning in one or two lines, and how to sit and chant along. Then exactly ONE chant scene: "
            "its narration is only the mantra itself in Devanagari, exactly as chanted, nothing else; its "
            "visual is a slide with the mantra in `text` and a short meaning in `sub`; set `repeat` to the "
            "requested count. Then one short closing scene (a line of blessing, a gentle call to subscribe). "
            "Use a mantra only if you are certain of the exact wording, and list it in verify_notes."
        ),
    },
    "quote": {
        "label": "Shloka / quote (Short)",
        "default_minutes": 1,
        "music_volume": 0.14,
        "slide_share": "about 50%",
        "structure": (
            "Hook line. The shloka or quote on a slide (only if you are certain of the exact "
            "text). Its meaning in simple Hindi. One line of reflection. Under 50 seconds total."
        ),
    },
}

# Hindi narration pace used for word budgets (words per minute at the default TTS rate).
WORDS_PER_MINUTE = 118


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def anthropic_key() -> str:
    return env("ANTHROPIC_API_KEY")


def claude_model() -> str:
    return env("CLAUDE_MODEL", "claude-sonnet-5-5")


def pexels_key() -> str:
    return env("PEXELS_API_KEY")


def channel_name() -> str:
    return env("CHANNEL_NAME")


def tts_voice() -> str:
    return env("TTS_VOICE", "hi-IN-MadhurNeural")


def tts_rate() -> str:
    return env("TTS_RATE", "-8%")


def tts_pitch() -> str:
    return env("TTS_PITCH", "+0Hz")


def x264_preset() -> str:
    return env("X264_PRESET", "medium")


def default_privacy() -> str:
    return env("YT_DEFAULT_PRIVACY", "private")


def yt_category() -> str:
    return env("YT_CATEGORY_ID", "27")  # 27 = Education


TTS_ENGINES = {
    "edge": "Microsoft Edge (free)",
    "sarvam": "Sarvam Bulbul",
    "gemini": "Google Gemini TTS",
    "elevenlabs": "ElevenLabs (incl. your cloned voices)",
}


def tts_engine() -> str:
    e = env("TTS_ENGINE", "edge").lower()
    return e if e in TTS_ENGINES else "edge"


def elevenlabs_key() -> str:
    return env("ELEVENLABS_API_KEY")


def stability_key() -> str:
    return env("STABILITY_API_KEY")


def gemini_key() -> str:
    return env("GEMINI_API_KEY")


def sarvam_key() -> str:
    return env("SARVAM_API_KEY")


def sarvam_model() -> str:
    return env("SARVAM_MODEL", "bulbul:v3")


def sarvam_speaker() -> str:
    return env("SARVAM_SPEAKER", "shubh")


def max_uploads_per_day() -> int:
    try:
        return max(0, int(env("MAX_UPLOADS_PER_DAY", "2")))
    except ValueError:
        return 2


VOICES = {
    "hi-IN-MadhurNeural": "Madhur (male, Hindi)",
    "hi-IN-SwaraNeural": "Swara (female, Hindi)",
}

# A selection of Sarvam bulbul:v3 speakers (lowercase names are what the API expects).
SARVAM_VOICES = {
    "shubh": "Shubh (male)",
    "aditya": "Aditya (male)",
    "rahul": "Rahul (male)",
    "anand": "Anand (male)",
    "ritu": "Ritu (female)",
    "priya": "Priya (female)",
    "neha": "Neha (female)",
    "kavya": "Kavya (female)",
}


# Gemini TTS prebuilt voices (they speak Hindi when the text is Hindi).
GEMINI_VOICES = {
    "Kore": "Kore (female, firm)", "Puck": "Puck (male, upbeat)", "Charon": "Charon (male, informative)",
    "Aoede": "Aoede (female, breezy)", "Fenrir": "Fenrir (male, excitable)", "Leda": "Leda (female, youthful)",
    "Orus": "Orus (male, firm)", "Zephyr": "Zephyr (female, bright)", "Sulafat": "Sulafat (female, warm)",
    "Gacrux": "Gacrux (female, mature)", "Algenib": "Algenib (male, gravelly)", "Achird": "Achird (male, friendly)",
}
CLONED_VOICES_FILE = CONFIG_DIR / "cloned_voices.json"


def cloned_voices() -> dict[str, str]:
    """Voices you cloned on ElevenLabs: {voice_id: name}."""
    import json

    try:
        data = json.loads(CLONED_VOICES_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def voices() -> dict[str, str]:
    """Voices for the active TTS engine."""
    e = tts_engine()
    if e == "sarvam":
        return SARVAM_VOICES
    if e == "gemini":
        return GEMINI_VOICES
    if e == "elevenlabs":
        return {**{k: f"{v} (your clone)" for k, v in cloned_voices().items()}, **ELEVENLABS_VOICES}
    return VOICES


# A few ElevenLabs premade voices (multilingual v2 speaks Hindi with any of them).
ELEVENLABS_VOICES = {
    "JBFqnCBsd6RMkjVDRZzb": "George (male)", "EXAVITQu4vr4xnSDxMaL": "Sarah (female)",
    "pNInz6obpgDQGcFmaJgB": "Adam (male)", "21m00Tcm4TlvDq8ikWAM": "Rachel (female)",
}


def default_voice() -> str:
    e = tts_engine()
    if e == "sarvam":
        return sarvam_speaker()
    if e == "gemini":
        return env("GEMINI_TTS_VOICE", "Kore")
    if e == "elevenlabs":
        return env("ELEVENLABS_VOICE") or next(iter(voices()))
    return tts_voice()
