"""AI image generation for scenes, with reference pictures so characters look the same everywhere.

Providers (chosen on the Setup page, saved to config/images.json):
- "openai": GPT image models. With reference pictures it calls /v1/images/edits (several `image[]`).
- "gemini": Gemini image models via generateContent; reference pictures go in as inline images.
- "placeholder": free, offline cards with the scene description written on them, for trying the
  workflow (and for tests). IMAGES_FORCE_PROVIDER=placeholder forces it, e.g. for a test server.
API keys are the same ones the AI writer uses (OPENAI_API_KEY, GEMINI_API_KEY).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from pathlib import Path

from . import config, llm

PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI (GPT image)", "key": "openai", "est": 0.05, "default_model": "gpt-image-1.5",
        "note": "Keeps faces close to the character sheets (high input fidelity). Paid, roughly $0.03 to $0.07 "
                "per image at medium quality; check OpenAI's pricing.",
    },
    "gemini": {
        "label": "Google Gemini (image)", "key": "gemini", "est": 0.04, "default_model": "gemini-3.1-flash-image",
        "fallback_model": "gemini-2.5-flash-image",
        "note": "Strong character consistency (up to 5 character references). Paid per image on most "
                "accounts, roughly $0.04; images carry an invisible SynthID watermark.",
    },
    "placeholder": {
        "label": "Placeholder (free, offline)", "key": "", "est": 0, "default_model": "cards",
        "note": "Simple cards with the scene description, to try the workflow for free. Not for publishing.",
    },
}
QUALITIES = {"low": "Low (cheapest)", "medium": "Medium", "high": "High"}
MAX_REFS = {"openai": 8, "gemini": 5, "placeholder": 8}
SETTINGS_FILE = config.CONFIG_DIR / "images.json"


class ImageError(RuntimeError):
    pass


# ----------------------------------------------------------------- settings ----
def _settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def provider() -> str:
    forced = config.env("IMAGES_FORCE_PROVIDER").lower()
    if forced in PROVIDERS:
        return forced
    p = _settings().get("provider")
    if p in PROVIDERS:
        return p
    # first run: use whichever paid provider already has a key, else the free placeholder
    for cand in ("openai", "gemini"):
        if llm.api_key(cand):
            return cand
    return "placeholder"


def model(p: str | None = None) -> str:
    p = p or provider()
    return (_settings().get("models") or {}).get(p) or PROVIDERS[p]["default_model"]


def quality() -> str:
    q = _settings().get("quality")
    return q if q in QUALITIES else "medium"


def api_key(p: str | None = None) -> str:
    k = PROVIDERS[p or provider()]["key"]
    return llm.api_key(k) if k else "free"


def ready() -> bool:
    return bool(api_key())


def describe() -> dict:
    return {
        "provider": provider(), "model": model(), "quality": quality(), "ready": ready(),
        "label": PROVIDERS[provider()]["label"], "forced": bool(config.env("IMAGES_FORCE_PROVIDER")),
        "qualities": QUALITIES,
        "providers": {k: {"label": v["label"], "model": model(k), "default_model": v["default_model"],
                          "has_key": bool(api_key(k)), "note": v["note"], "est": v["est"]}
                      for k, v in PROVIDERS.items()},
    }


def save(p: str, model_name: str = "", qual: str = "") -> None:
    if p not in PROVIDERS:
        raise ImageError(f"Unknown image provider: {p}")
    data = _settings()
    data["provider"] = p
    models = dict(data.get("models") or {})
    model_name = model_name.strip()
    if model_name and model_name != PROVIDERS[p]["default_model"]:
        if not re.fullmatch(r"[A-Za-z0-9._\-/:]{2,80}", model_name):
            raise ImageError("That does not look like a model name.")
        models[p] = model_name
    else:
        models.pop(p, None)
    data["models"] = models
    if qual:
        if qual not in QUALITIES:
            raise ImageError("Unknown quality.")
        data["quality"] = qual
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_models(p: str) -> list[str]:
    import requests

    if p == "placeholder":
        return ["cards"]
    if not api_key(p):
        return []
    if p == "openai":
        r = requests.get("https://api.openai.com/v1/models", timeout=30,
                         headers={"Authorization": f"Bearer {api_key(p)}"})
        if r.status_code != 200:
            raise ImageError(_explain(p, r.status_code, r.text))
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        return sorted(i for i in ids if re.match(r"(gpt-image|chatgpt-image|dall-e-3)", i))
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=30,
                     params={"pageSize": 1000}, headers={"x-goog-api-key": api_key(p)})
    if r.status_code != 200:
        raise ImageError(_explain(p, r.status_code, r.text))
    ids = [m.get("name", "").removeprefix("models/") for m in r.json().get("models", [])]
    return sorted(i for i in ids if "image" in i and i.startswith("gemini"))


# --------------------------------------------------------------------- http ----
def _request(method: str, url: str, **kw):
    """The single place that talks to image providers (tests replace it)."""
    import requests

    return requests.request(method, url, timeout=kw.pop("timeout", 300), **kw)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _with_retry(send):
    """Retry an overloaded provider (5xx) and short rate-limit waits, like the AI writer does."""
    import requests

    r = None
    for i, delay in enumerate((3, 8, 20, None)):
        try:
            r = send()
        except (requests.ConnectionError, requests.Timeout):
            if delay is None:
                raise
            _sleep(delay)
            continue
        if r.status_code == 429:
            try:
                wait = float((getattr(r, "headers", None) or {}).get("retry-after", "") or delay or 0)
            except ValueError:
                wait = delay or 0
            if delay is None or wait > 60:
                return r
            _sleep(max(wait, delay))
            continue
        if r.status_code in (500, 502, 503, 504) and delay is not None:
            _sleep(delay)
            continue
        return r
    return r


def _explain(p: str, status: int, text: str) -> str:
    detail = text[:400]
    try:
        err = json.loads(text)
        err = err[0] if isinstance(err, list) else err
        detail = (err.get("error") or {}).get("message") or detail
    except (ValueError, AttributeError):
        pass
    label = PROVIDERS[p]["label"]
    low = detail.lower()
    if status in (401, 403) and "safety" not in low and "policy" not in low:
        return f"{label} rejected the API key ({status}), or this key may not use image models. ({detail})"
    if status == 429:
        return f"{label} rate limit or quota reached. Wait a little and try again. ({detail})"
    if status == 404:
        return f"Image model '{model(p)}' was not found on {label}. Pick another on the Setup page. ({detail})"
    if "safety" in low or "policy" in low or "moderation" in low or "blocked" in low:
        return f"{label} refused this picture (content rules). Reword the scene description and try again. ({detail})"
    if status in (500, 502, 503, 504):
        return f"{label} is overloaded right now ({status}), even after retrying. Try again in a few minutes."
    return f"{label} error {status}: {detail}"


# ------------------------------------------------------------------ helpers ----
def _ref_png(path: Path, max_side: int = 1024) -> bytes:
    """A reference picture as PNG bytes, at most `max_side` pixels (smaller uploads, same likeness)."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()


def _save(data: bytes, out: Path) -> Path:
    """Check the bytes really are a picture, then store them as PNG."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            out.parent.mkdir(parents=True, exist_ok=True)
            im.convert("RGB").save(out, "PNG")
    except Exception as e:
        raise ImageError("The provider sent back something that is not a picture.") from e
    return out


def _find_image(obj) -> bytes | None:
    """First base64 image anywhere in a JSON response (Gemini's inlineData / inline_data)."""
    if isinstance(obj, dict):
        for k in ("inlineData", "inline_data"):
            blob = obj.get(k)
            if isinstance(blob, dict) and blob.get("data") and \
                    str(blob.get("mimeType") or blob.get("mime_type") or "image/").startswith("image/"):
                return base64.b64decode(blob["data"])
        for v in obj.values():
            found = _find_image(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_image(v)
            if found:
                return found
    return None


# ---------------------------------------------------------------- providers ----
def _openai(prompt: str, refs: list[Path], aspect: str, out: Path) -> Path:
    p, m = "openai", model("openai")
    size = {"16:9": "1536x1024", "9:16": "1024x1536"}.get(aspect, "1024x1024")
    head = {"Authorization": f"Bearer {api_key(p)}"}
    if refs:
        data = {"model": m, "prompt": prompt, "size": size, "quality": quality(), "n": "1"}
        if m in ("gpt-image-1", "gpt-image-1.5"):
            data["input_fidelity"] = "high"  # keep faces close to the character sheets
        files = [("image[]", (f"ref{i}.png", _ref_png(r), "image/png")) for i, r in enumerate(refs)]
        r = _with_retry(lambda: _request("POST", "https://api.openai.com/v1/images/edits", headers=head,
                                         data=data, files=files))
    else:
        body = {"model": m, "prompt": prompt, "size": size, "quality": quality(), "n": 1}
        r = _with_retry(lambda: _request("POST", "https://api.openai.com/v1/images/generations",
                                         headers={**head, "Content-Type": "application/json"}, json=body))
    if r.status_code != 200:
        raise ImageError(_explain(p, r.status_code, r.text))
    items = r.json().get("data") or []
    if not items or not items[0].get("b64_json"):
        raise ImageError("OpenAI returned no image. Try again.")
    return _save(base64.b64decode(items[0]["b64_json"]), out)


def _gemini(prompt: str, refs: list[Path], aspect: str, out: Path) -> Path:
    p = "gemini"
    parts = [{"text": prompt}] + [
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(_ref_png(r)).decode()}} for r in refs]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}
    head = {"x-goog-api-key": api_key(p), "Content-Type": "application/json"}

    def call(m: str):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        return _with_retry(lambda: _request("POST", url, headers=head, json=body))

    m = model(p)
    r = call(m)
    fb = PROVIDERS[p].get("fallback_model")
    if r.status_code == 404 and fb and m != fb and not (_settings().get("models") or {}).get(p):
        r = call(fb)  # the default model is not on this account yet: use the established one
    if r.status_code != 200:
        raise ImageError(_explain(p, r.status_code, r.text))
    data = r.json()
    img = _find_image(data)
    if img is None:
        cand = (data.get("candidates") or [{}])[0]
        reason = cand.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason") or ""
        if reason and reason not in ("STOP",):
            raise ImageError(f"Gemini refused this picture ({reason}). Reword the scene description and try again.")
        raise ImageError("Gemini returned no image. Try again, or pick another image model.")
    return _save(img, out)


def _placeholder(prompt: str, refs: list[Path], aspect: str, out: Path) -> Path:
    """A plain card: a colour from the prompt, the reference pictures as small portraits, and the text."""
    from PIL import Image, ImageDraw

    from . import textutil

    w, h = {"16:9": (1280, 720), "9:16": (720, 1280)}.get(aspect, (1024, 1024))
    hue = int(hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:6], 16)
    bg = (40 + hue % 90, 50 + (hue >> 8) % 80, 80 + (hue >> 16) % 90)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    x = 30
    for r in refs[:4]:
        with Image.open(r) as ref:
            ref = ref.convert("RGB")
            ref.thumbnail((h // 4, h // 4))
            img.paste(ref, (x, 30))
            x += ref.width + 16
    font, lines, lh = textutil.fit_block(prompt[:420], w - 80, h // 2, bold=False, start=max(18, h // 26), minimum=14)
    y = h // 3 + 40
    for line in lines:
        d.text((40, y), line, font=font, fill=(255, 250, 240))
        y += lh
    d.text((40, h - 50), "PLACEHOLDER IMAGE", font=textutil.load_font(max(16, h // 30)), fill=(255, 220, 120))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


def generate(prompt: str, refs: list[Path], aspect: str, out: Path) -> Path:
    """Make one picture from `prompt`, keeping the people in `refs` (reference pictures) the same."""
    p = provider()
    if not ready():
        raise ImageError(f"{PROVIDERS[p]['label']} has no API key. Add it on the Setup page (AI writer section).")
    refs = [r for r in refs if r and Path(r).exists()][:MAX_REFS[p]]
    if p == "openai":
        return _openai(prompt, refs, aspect, out)
    if p == "gemini":
        return _gemini(prompt, refs, aspect, out)
    return _placeholder(prompt, refs, aspect, out)
