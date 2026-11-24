"""Voice cloning (ElevenLabs), music generation (ElevenLabs Music or Stability Stable Audio) and the
voice-engine settings the Setup page edits. Keys live in .env, the cloned voices in config/cloned_voices.json.
"""
from __future__ import annotations

import json
import re

from . import config, llm

ELEVEN = "https://api.elevenlabs.io/v1"
STABILITY_AUDIO = "https://api.stability.ai/v2beta/audio/stable-audio-2/text-to-audio"

MUSIC_PROVIDERS = {
    "elevenlabs": {"label": "ElevenLabs Music", "key_env": "ELEVENLABS_API_KEY", "max_seconds": 300},
    "stability": {"label": "Stability Stable Audio", "key_env": "STABILITY_API_KEY", "max_seconds": 190},
}
KEYS = {"elevenlabs": "ELEVENLABS_API_KEY", "stability": "STABILITY_API_KEY", "sarvam": "SARVAM_API_KEY",
        "gemini": "GEMINI_API_KEY"}


class AudioError(RuntimeError):
    pass


def music_provider() -> str:
    p = config.env("MUSIC_PROVIDER", "elevenlabs").lower()
    return p if p in MUSIC_PROVIDERS else "elevenlabs"


def describe() -> dict:
    """What the Setup page shows. Keys are never returned, only whether they are set."""
    return {
        "engine": config.tts_engine(),
        "engines": {k: {"label": v, "has_key": _engine_has_key(k)} for k, v in config.TTS_ENGINES.items()},
        "voice": config.default_voice(),
        "voices": config.voices(),
        "cloned": config.cloned_voices(),
        "music_provider": music_provider(),
        "music_providers": {k: {"label": v["label"], "has_key": bool(config.env(v["key_env"]))}
                            for k, v in MUSIC_PROVIDERS.items()},
    }


def _engine_has_key(engine: str) -> bool:
    return {"edge": True, "sarvam": bool(config.sarvam_key()), "gemini": bool(config.gemini_key()),
            "elevenlabs": bool(config.elevenlabs_key())}[engine]


def save(engine: str, music: str = "", keys: dict | None = None) -> None:
    if engine not in config.TTS_ENGINES:
        raise AudioError(f"Unknown voice engine: {engine}")
    if music and music not in MUSIC_PROVIDERS:
        raise AudioError(f"Unknown music provider: {music}")
    for name, value in (keys or {}).items():
        if name in KEYS and (value or "").strip():
            try:
                llm.set_env_key(KEYS[name], value.strip())
            except llm.LLMError as e:
                raise AudioError(str(e)) from e
    llm.set_env_value("TTS_ENGINE", engine)
    import os
    os.environ["TTS_ENGINE"] = engine
    if music:
        llm.set_env_value("MUSIC_PROVIDER", music)
        os.environ["MUSIC_PROVIDER"] = music


def set_default_voice(voice_id: str) -> None:
    if voice_id not in config.voices():
        raise AudioError("Unknown voice for the current engine.")
    env_name = {"gemini": "GEMINI_TTS_VOICE", "elevenlabs": "ELEVENLABS_VOICE", "sarvam": "SARVAM_SPEAKER",
                "edge": "TTS_VOICE"}[config.tts_engine()]
    llm.set_env_value(env_name, voice_id)
    import os
    os.environ[env_name] = voice_id


# ------------------------------------------------------------ cloning ----
def _save_clones(clones: dict[str, str]) -> None:
    config.CLONED_VOICES_FILE.write_text(json.dumps(clones, indent=2, ensure_ascii=False), encoding="utf-8")


def _eleven_error(r) -> AudioError:
    detail = r.text[:300]
    try:
        d = r.json().get("detail")
        detail = d.get("message") if isinstance(d, dict) else (d or detail)
    except (ValueError, AttributeError):
        pass
    if r.status_code in (401, 403):
        return AudioError(f"ElevenLabs refused the request ({r.status_code}): {detail}. Check the key and that "
                          "your plan includes this feature (voice cloning needs a paid plan).")
    return AudioError(f"ElevenLabs error {r.status_code}: {detail}")


def clone_voice(name: str, sample: bytes, ext: str, consent: bool) -> dict:
    """Instant voice clone from one clean recording of a voice you have the right to clone."""
    import requests

    if not consent:
        raise AudioError("Confirm that this is your own voice, or that you have the speaker's permission.")
    if not config.elevenlabs_key():
        raise AudioError("Add your ElevenLabs API key first.")
    name = re.sub(r"[^\w\- ()ऀ-ॿ]", "", name).strip()[:40]
    if not name:
        raise AudioError("Give the voice a name.")
    if len(sample) < 20_000:
        raise AudioError("The recording is too short. Use at least 30 seconds, ideally 1 to 3 minutes, of clean speech.")
    ext = ext.lower().lstrip(".")
    r = requests.post(f"{ELEVEN}/voices/add", timeout=300, headers={"xi-api-key": config.elevenlabs_key()},
                      data={"name": name, "remove_background_noise": "true",
                            "description": "Cloned from the owner's own recording"},
                      files={"files": (f"sample.{ext}", sample, "application/octet-stream")})
    if r.status_code != 200:
        raise _eleven_error(r)
    vid = r.json().get("voice_id")
    if not vid:
        raise AudioError("ElevenLabs did not return a voice id.")
    clones = config.cloned_voices()
    clones[vid] = name
    _save_clones(clones)
    return {"voice_id": vid, "name": name}


def delete_clone(voice_id: str) -> None:
    import requests

    clones = config.cloned_voices()
    if voice_id not in clones:
        raise AudioError("That is not one of your cloned voices.")
    if config.elevenlabs_key():
        r = requests.delete(f"{ELEVEN}/voices/{voice_id}", timeout=60, headers={"xi-api-key": config.elevenlabs_key()})
        if r.status_code not in (200, 404):
            raise _eleven_error(r)
    clones.pop(voice_id)
    _save_clones(clones)


# -------------------------------------------------------------- music ----
def generate_music(prompt: str, seconds: int, provider: str | None = None) -> tuple[bytes, str]:
    """Returns (mp3 bytes, suggested file name). The prompt should describe the mood and say 'no vocals'."""
    import requests

    provider = provider or music_provider()
    info = MUSIC_PROVIDERS[provider]
    key = config.env(info["key_env"])
    if not key:
        raise AudioError(f"{info['label']} has no API key. Add it on the Setup page.")
    prompt = prompt.strip()
    if len(prompt) < 5:
        raise AudioError("Describe the music, for example: calm lo-fi, soft piano, no vocals.")
    seconds = max(10, min(int(seconds), info["max_seconds"]))
    if provider == "elevenlabs":
        r = requests.post(f"{ELEVEN}/music?output_format=mp3_44100_128", timeout=600,
                          headers={"xi-api-key": key, "Content-Type": "application/json"},
                          json={"prompt": prompt, "music_length_ms": seconds * 1000, "force_instrumental": True})
        if r.status_code != 200:
            raise _eleven_error(r)
    else:
        r = requests.post(STABILITY_AUDIO, timeout=600,
                          headers={"authorization": f"Bearer {key}", "accept": "audio/*"},
                          files={"none": ""}, data={"prompt": prompt, "duration": seconds, "output_format": "mp3"})
        if r.status_code != 200:
            raise AudioError(f"Stability error {r.status_code}: {r.text[:300]}")
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:30].strip("-") or "music"
    return r.content, f"ai-{slug}.mp3"
