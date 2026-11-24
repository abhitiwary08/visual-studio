"""Hindi voice-over. Engines, chosen with TTS_ENGINE in .env (edge, sarvam, gemini, elevenlabs):

- "gemini": Google Gemini TTS (GEMINI_API_KEY), prebuilt voices steered by a style prompt.
- "elevenlabs": ElevenLabs (ELEVENLABS_API_KEY), including voices you cloned (config/cloned_voices.json).

- "edge" (default): Microsoft Edge read-aloud voices. Free, no key, but no published commercial terms.
- "sarvam": Sarvam AI Bulbul (https://www.sarvam.ai), a paid API with commercial terms and Indian voices.

config/pronunciation.json (optional) maps words to how they should be spoken, for both engines.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import time
import wave
from pathlib import Path

from . import config, media


class TTSError(RuntimeError):
    pass


SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_MAX_CHARS = 2000  # API limit is 2500 (v3) / 1500 (v2); stay under it with room to spare
PRONUNCIATION_FILE = config.CONFIG_DIR / "pronunciation.json"
# Python's \b does not treat Devanagari matras as word characters, so build our own boundary.
_WORD = r"[\wऀ-ॿ]"


def pronunciation_map() -> dict[str, str]:
    try:
        data = json.loads(PRONUNCIATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in data.items()
            if str(k).strip() and not str(k).startswith("_")} if isinstance(data, dict) else {}


def apply_pronunciation(text: str, mapping: dict[str, str] | None = None) -> str:
    mapping = pronunciation_map() if mapping is None else mapping
    for word in sorted(mapping, key=len, reverse=True):  # longest first, so phrases win over parts
        text = re.sub(rf"(?<!{_WORD}){re.escape(word)}(?!{_WORD})", lambda _m, r=mapping[word]: r, text)
    return text


def clean_for_speech(text: str, mapping: dict[str, str] | None = None) -> str:
    """Remove things a voice should not read aloud, then apply pronunciation fixes."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*stage[^)]*\)", " ", text)  # [stage directions]
    text = re.sub(r"[*_#>`~]+", " ", text)  # stray markdown
    text = re.sub(r"\s+", " ", text).strip()
    return apply_pronunciation(text, mapping).strip()


def cache_key(text: str, voice: str, rate: str, pitch: str, engine: str = "edge") -> str:
    # Pronunciation fixes change the audio, so they are part of the key.
    fixes = json.dumps(pronunciation_map(), sort_keys=True, ensure_ascii=False)
    extra = "" if engine == "edge" else f"|{engine}"
    if fixes != "{}":
        extra += "|" + fixes
    return hashlib.sha1(f"{voice}|{rate}|{pitch}|{text}{extra}".encode("utf-8")).hexdigest()[:10]


# ------------------------------------------------------------------ edge ----
async def _edge(text: str, voice: str, rate: str, pitch: str, out: Path) -> None:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(out))


def _synth_edge(text: str, out: Path, voice: str, rate: str, pitch: str) -> None:
    asyncio.run(_edge(text, voice, rate, pitch, out))


# ---------------------------------------------------------------- sarvam ----
def rate_to_pace(rate: str) -> float:
    """Edge-style '-8%' -> Sarvam pace 0.92 (allowed range 0.5 to 2.0)."""
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    pace = 1.0 + float(m.group(1)) / 100 if m else 1.0
    return round(max(0.5, min(2.0, pace)), 2)


def split_for_api(text: str, limit: int = SARVAM_MAX_CHARS) -> list[str]:
    """Split long narration at sentence ends (। . ? !) so each request stays under the limit."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[।॥.?!])\s+", text):
        while len(sent) > limit:  # one enormous sentence: hard-cut at a space
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > limit:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return [p for p in parts if p]


def _sarvam_request(text: str, speaker: str, pace: float) -> bytes:
    import requests

    body = {"text": text, "language_code": "hi-IN", "speaker": speaker, "pace": pace,
            "model": config.sarvam_model(), "speech_sample_rate": 44100}
    headers = {"api-subscription-key": config.sarvam_key(), "Content-Type": "application/json"}
    r = requests.post(SARVAM_URL, json=body, headers=headers, timeout=120)
    if r.status_code in (400, 422) and "language_code" in r.text:
        # Older API versions call this field target_language_code.
        body["target_language_code"] = body.pop("language_code")
        r = requests.post(SARVAM_URL, json=body, headers=headers, timeout=120)
    if r.status_code != 200:
        raise TTSError(f"Sarvam TTS error {r.status_code}: {r.text[:300]}")
    audios = r.json().get("audios") or []
    if not audios:
        raise TTSError("Sarvam TTS returned no audio.")
    return base64.b64decode(audios[0])


def join_wavs(chunks: list[bytes]) -> bytes:
    """Concatenate WAV byte strings that share one format."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        for i, data in enumerate(chunks):
            with wave.open(io.BytesIO(data), "rb") as r:
                if i == 0:
                    w.setparams(r.getparams())
                w.writeframes(r.readframes(r.getnframes()))
    return out.getvalue()


def _synth_sarvam(text: str, out: Path, voice: str, rate: str) -> None:
    if not config.sarvam_key():
        raise TTSError("TTS_ENGINE is 'sarvam' but SARVAM_API_KEY is not set in .env.")
    speaker = voice if voice in config.SARVAM_VOICES else config.sarvam_speaker()
    pace = rate_to_pace(rate)
    wav = join_wavs([_sarvam_request(part, speaker, pace) for part in split_for_api(text)])
    tmp = out.with_suffix(".wav")
    tmp.write_bytes(wav)
    try:
        media.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-c:a", "libmp3lame", "-q:a", "2", str(out)])
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- gemini ----
GEMINI_TTS_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MAX_CHARS = 3000


def _pcm_to_mp3(pcm: bytes, out: Path, atempo: float = 1.0, rate_hz: int = 24000) -> None:
    tmp = out.with_suffix(".wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate_hz)
        w.writeframes(pcm)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(tmp)]
    if abs(atempo - 1.0) > 0.01:
        cmd += ["-filter:a", f"atempo={atempo}"]
    try:
        media.run(cmd + ["-c:a", "libmp3lame", "-q:a", "2", str(out)])
    finally:
        tmp.unlink(missing_ok=True)


def _synth_gemini(text: str, out: Path, voice: str, rate: str) -> None:
    import requests

    key = config.gemini_key()
    if not key:
        raise TTSError("TTS_ENGINE is 'gemini' but GEMINI_API_KEY is not set in .env.")
    voice = voice if voice in config.GEMINI_VOICES else config.default_voice()
    style = config.env("GEMINI_TTS_STYLE", "Read this in clear, natural Hindi, in a calm, warm storyteller voice")
    model = config.env("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    pcm = b""
    for part in split_for_api(text, GEMINI_MAX_CHARS):
        body = {
            "contents": [{"parts": [{"text": f"{style}: {part}" if style else part}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
        }
        r = requests.post(GEMINI_TTS_URL.format(model=model), json=body, timeout=180,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        if r.status_code != 200:
            raise TTSError(f"Gemini TTS error {r.status_code}: {r.text[:300]}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            pcm += base64.b64decode(next(p["inlineData"]["data"] for p in parts if "inlineData" in p))
        except (KeyError, IndexError, StopIteration, ValueError):
            raise TTSError("Gemini TTS returned no audio. Try again, or shorten the narration.")
    _pcm_to_mp3(pcm, out, atempo=rate_to_pace(rate))


# ------------------------------------------------------------ elevenlabs ----
ELEVEN_URL = "https://api.elevenlabs.io/v1"
ELEVEN_MAX_CHARS = 4500


def _synth_elevenlabs(text: str, out: Path, voice: str, rate: str) -> None:
    import requests

    key = config.elevenlabs_key()
    if not key:
        raise TTSError("TTS_ENGINE is 'elevenlabs' but ELEVENLABS_API_KEY is not set in .env.")
    if voice not in config.voices():
        voice = config.default_voice()
    model = config.env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    speed = max(0.7, min(1.2, rate_to_pace(rate)))
    chunks = []
    for part in split_for_api(text, ELEVEN_MAX_CHARS):
        r = requests.post(f"{ELEVEN_URL}/text-to-speech/{voice}?output_format=mp3_44100_128", timeout=180,
                          headers={"xi-api-key": key, "Content-Type": "application/json"},
                          json={"text": part, "model_id": model, "language_code": "hi",
                                "voice_settings": {"speed": speed, "stability": 0.5, "similarity_boost": 0.8}})
        if r.status_code != 200:
            raise TTSError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")
        chunks.append(r.content)
    out.write_bytes(b"".join(chunks))  # mp3 frames concatenate cleanly


# ------------------------------------------------------------------ main ----
def synthesize(text: str, out: Path, *, voice: str | None = None, rate: str | None = None,
               pitch: str | None = None, retries: int = 3) -> Path:
    """Write an mp3 of `text`. Retries on transient network errors."""
    engine = config.tts_engine()
    voice = voice or config.tts_voice()
    rate = rate or config.tts_rate()
    pitch = pitch or config.tts_pitch()
    spoken = clean_for_speech(text)
    if not spoken:
        raise TTSError("Narration is empty after cleaning.")
    out.parent.mkdir(parents=True, exist_ok=True)

    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if engine == "sarvam":
                _synth_sarvam(spoken, out, voice, rate)
            elif engine == "gemini":
                _synth_gemini(spoken, out, voice, rate)
            elif engine == "elevenlabs":
                _synth_elevenlabs(spoken, out, voice, rate)
            else:
                _synth_edge(spoken, out, voice, rate, pitch)
            if out.exists() and out.stat().st_size > 1000:
                return out
            raise TTSError("The voice service returned no audio.")
        except Exception as e:  # network, websocket, throttling
            last = e
            if out.exists():
                out.unlink(missing_ok=True)
            if isinstance(e, TTSError) and "not set" in str(e):
                break
            time.sleep(1.5 * attempt)
    if engine != "edge":
        raise TTSError(f"{config.TTS_ENGINES[engine]} failed after {retries} attempts: {last}")
    raise TTSError(
        f"Edge TTS failed after {retries} attempts: {last}. "
        "It needs internet access to Microsoft's speech service."
    )
