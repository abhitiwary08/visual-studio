"""Which AI writes the scripts: Claude (Anthropic), Google Gemini, Groq or OpenAI.

The choice and model are set in the dashboard (Setup page) and saved to config/llm.json; API keys
go to .env. Gemini and Groq are called through their OpenAI-compatible chat completions APIs with a
forced function call, so every provider returns the same structured script.

"""
from __future__ import annotations

import json
import os
import re

from . import config

PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "label": "Claude (Anthropic)", "key_env": "ANTHROPIC_API_KEY", "model_env": "CLAUDE_MODEL",
        "default_model": "claude-sonnet-5-5", "key_url": "https://console.anthropic.com",
        "note": "Best Hindi and accuracy. Paid; new accounts get $5 of starter credit.",
    },
    "gemini": {
        "label": "Google Gemini", "key_env": "GEMINI_API_KEY", "model_env": "GEMINI_MODEL",
        "default_model": "gemini-3.8-flash", "key_url": "https://aistudio.google.com/apikey",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "max_tokens": 30000,
        "note": "Free tier for Flash models (rate-limited). Good Hindi. Free-tier prompts may be used by Google.",
    },
    "groq": {
        "label": "Groq", "key_env": "GROQ_API_KEY", "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b", "key_url": "https://console.groq.com/keys",
        # The free tier allows about 8,000 tokens per minute including the reply, so keep requests small.
        "base_url": "https://api.groq.com/openai/v1", "max_tokens": 6000,
        "note": "Free and very fast, but weaker Hindi. The free tier limits output: keep videos to about 3 minutes.",
    },
    "openai": {
        "label": "OpenAI (GPT)", "key_env": "OPENAI_API_KEY", "model_env": "OPENAI_MODEL",
        "default_model": "gpt-5-mini", "key_url": "https://platform.openai.com/api-keys",
        # Newer GPT models take max_completion_tokens and only the default temperature.
        "base_url": "https://api.openai.com/v1", "max_tokens": 30000, "completion_tokens_param": True,
        "note": "Paid API (cheap small models). A ChatGPT subscription does not include API use.",
    },
    "bedrock": {
        "label": "AWS Bedrock", "key_env": "AWS_BEARER_TOKEN_BEDROCK", "model_env": "BEDROCK_MODEL",
        "default_model": "us.amazon.nova-pro-v1:0", "key_url": "https://console.aws.amazon.com/bedrock/home#/api-keys",
        # Called through the Converse API, so any Bedrock chat model with tool use works (Nova, Claude, Llama...).
        "max_tokens": 8000, "converse": True,
        "note": "Nova, Claude, Llama and more via an AWS Bedrock API key. Set AWS_REGION in .env and enable model access first.",
    },
}
SETTINGS_FILE = config.CONFIG_DIR / "llm.json"
ENV_FILE = config.ROOT / ".env"


class LLMError(RuntimeError):
    pass


# ----------------------------------------------------------------- settings ----
def _settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def provider() -> str:
    p = _settings().get("provider") or config.env("LLM_PROVIDER", "anthropic").lower()
    return p if p in PROVIDERS else "anthropic"


def model(p: str | None = None) -> str:
    p = p or provider()
    info = PROVIDERS[p]
    return (_settings().get("models") or {}).get(p) or config.env(info["model_env"]) or info["default_model"]


def api_key(p: str | None = None) -> str:
    return config.env(PROVIDERS[p or provider()]["key_env"])


def ready() -> bool:
    return bool(api_key())


def label() -> str:
    return PROVIDERS[provider()]["label"]


def describe() -> dict:
    """Everything the Setup page needs. Keys are never returned, only whether they are set."""
    return {
        "provider": provider(), "model": model(), "ready": ready(), "label": label(),
        "providers": {k: {"label": v["label"], "model": model(k), "default_model": v["default_model"],
                          "has_key": bool(api_key(k)), "key_url": v["key_url"], "note": v["note"]}
                      for k, v in PROVIDERS.items()},
    }


def save(p: str, model_name: str = "", key: str = "") -> None:
    if p not in PROVIDERS:
        raise LLMError(f"Unknown provider: {p}")
    data = _settings()
    data["provider"] = p
    models = dict(data.get("models") or {})
    model_name = model_name.strip()
    if model_name and model_name != PROVIDERS[p]["default_model"]:
        models[p] = model_name
    else:
        models.pop(p, None)
    data["models"] = models
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if key.strip():
        set_env_key(PROVIDERS[p]["key_env"], key.strip())


def set_env_key(name: str, value: str) -> None:
    """Write NAME=value into .env (replacing an existing line) and apply it without a restart."""
    if not re.fullmatch(r"[A-Za-z0-9_\-\.\+/=]{8,2000}", value):  # Bedrock keys are long base64
        raise LLMError("That does not look like an API key.")
    set_env_value(name, value)


def set_env_value(name: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(name)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value


# --------------------------------------------------- OpenAI-compatible call ----
RETRY_STATUS = {500, 502, 503, 504}  # provider overloaded or briefly down
RETRY_DELAYS = (2, 5, 10)  # seconds between attempts
last_model_used = ""  # the model that actually answered (after any fallback), for the Setup page test


class _Overloaded(LLMError):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


def _post(url: str, key: str, body: dict):
    import requests

    return requests.post(url, json=body, timeout=300,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})


def _sleep(seconds: float) -> None:  # separate so tests can skip the waiting
    import time

    time.sleep(seconds)


def _post_retry(url: str, key: str, body: dict):
    """POST, retrying when the provider is overloaded (5xx), on a short rate-limit wait (429 with a
    Retry-After of 30 s or less), and on network errors. Returns the last response."""
    import requests

    for i, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            r = _post(url, key, body)
        except (requests.ConnectionError, requests.Timeout):
            if delay is None:
                raise
            _sleep(delay)
            continue
        wait = delay
        if r.status_code == 429:
            try:
                wait = float((getattr(r, "headers", None) or {}).get("retry-after", ""))
            except ValueError:
                wait = None
            if wait is None or wait > 30 or i > 0:
                return r
        elif r.status_code not in RETRY_STATUS:
            return r
        if delay is None:
            return r
        _sleep(wait)
    return r


def _explain(p: str, status: int, text: str) -> str:
    detail = text[:400]
    try:
        err = json.loads(text)
        err = err[0] if isinstance(err, list) else err
        detail = (err.get("error") or {}).get("message") or detail
    except (ValueError, AttributeError):
        pass
    if status in (401, 403):
        return f"{PROVIDERS[p]['label']} rejected the API key ({status}). Check it on the Setup page."
    if status == 429:
        return (f"{PROVIDERS[p]['label']} rate limit reached (free tiers allow only a few requests per minute "
                f"and a daily total). Wait a minute and try again. Details: {detail}")
    if status == 404:
        return f"Model '{model(p)}' was not found on {PROVIDERS[p]['label']}. Pick another on the Setup page. ({detail})"
    if status in RETRY_STATUS:
        return (f"{PROVIDERS[p]['label']} is overloaded right now ({status}), even after retrying. This is on their "
                f"side and usually passes in a few minutes. Try again later, or pick another model or provider "
                f"on the Setup page. Details: {detail}")
    return f"{PROVIDERS[p]['label']} error {status}: {detail}"


def fallback_models(p: str, current: str) -> list[str]:
    """Up to two other models to try when the chosen one is overloaded: same family (e.g. other
    Gemini Flash models), stable releases before previews. Empty if the list cannot be fetched."""
    try:
        ids = [m for m in list_models(p) if m != current]
    except Exception:
        return []
    if p == "gemini":
        family = "flash" if "flash" in current else "pro" if "pro" in current else "gemini"
        ids = [m for m in ids if family in m]
    elif p == "openai":
        ids = [m for m in ids if "mini" in m or "nano" in m]
    stable = sorted((m for m in ids if not re.search(r"preview|exp", m)), reverse=True)  # newest first
    rest = sorted((m for m in ids if m not in stable), reverse=True)
    return (stable + rest)[:2]


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)  # some models wrap the JSON in prose
        if not m:
            raise
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise json.JSONDecodeError("not an object", text, 0)
    return data


def call_openai_compatible(system: str, user: str, tool: dict, max_tokens: int, p: str | None = None) -> dict:
    """Force one function call and return its arguments. Overloaded requests are retried, then other
    models of the same family are tried before giving up."""
    global last_model_used
    p = p or provider()
    info = PROVIDERS[p]
    if not api_key(p):
        raise LLMError(f"{info['label']} has no API key. Add it on the Setup page.")
    chosen = model(p)
    call = _call_converse if info.get("converse") else _call_model
    try:
        out = call(system, user, tool, max_tokens, p, chosen)
        last_model_used = chosen
        return out
    except _Overloaded as first:
        for alt in fallback_models(p, chosen):
            try:
                out = call(system, user, tool, max_tokens, p, alt)
                last_model_used = alt
                return out
            except _Overloaded:
                continue
        raise LLMError(_explain(p, first.status, first.text)) from None


def _call_model(system: str, user: str, tool: dict, max_tokens: int, p: str, model_name: str) -> dict:
    """One model: falls back from naming the function, to 'required', to 'auto' with a JSON-only
    instruction, because providers differ in what they accept."""
    info = PROVIDERS[p]
    key = api_key(p)
    fn = {"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                           "parameters": tool["input_schema"]}}
    base = {
        "model": model_name,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "tools": [fn],
    }
    limit = int(min(max_tokens, info.get("max_tokens", max_tokens)))
    if info.get("completion_tokens_param"):
        base["max_completion_tokens"] = limit
    else:
        base["max_tokens"] = limit
        base["temperature"] = 0.7
    choices = [{"type": "function", "function": {"name": tool["name"]}}, "required", "auto"]
    url = info["base_url"] + "/chat/completions"
    last = ""
    for choice in choices:
        body = dict(base, tool_choice=choice)
        if choice == "auto":
            body["messages"] = base["messages"][:1] + [{"role": "user", "content": user + (
                f"\n\nAnswer only by calling the {tool['name']} function.")}]
        r = _post_retry(url, key, body)
        if r.status_code == 400 and "tool_choice" in r.text.lower():
            last = r.text
            continue  # this provider does not accept that tool_choice form; try the next
        if r.status_code in RETRY_STATUS:
            raise _Overloaded(r.status_code, r.text)
        if r.status_code != 200:
            raise LLMError(_explain(p, r.status_code, r.text))
        choice0 = (r.json().get("choices") or [{}])[0]
        msg = choice0.get("message") or {}
        calls = msg.get("tool_calls") or []
        raw = next((c["function"].get("arguments") for c in calls
                    if (c.get("function") or {}).get("name") == tool["name"]), None)
        if raw is None and calls:
            raw = calls[0]["function"].get("arguments")
        if raw is None:
            raw = msg.get("content")  # a model that answered in plain JSON instead
        try:
            return _parse_arguments(raw)
        except (json.JSONDecodeError, TypeError):
            if choice0.get("finish_reason") == "length":
                raise LLMError(f"{info['label']} ran out of output space before finishing. Try a shorter "
                               "video, or a provider with a larger limit.")
            raise LLMError(f"{info['label']} did not return a usable structured answer. Try again, or pick "
                           "another model on the Setup page.")
    raise LLMError(_explain(p, 400, last))


def _call_converse(system: str, user: str, tool: dict, max_tokens: int, p: str, model_name: str) -> dict:
    """AWS Bedrock Converse API with a Bedrock API key (bearer token). Forces the tool where the
    model allows it, then 'any', then plain 'auto' with a JSON-only instruction."""
    info = PROVIDERS[p]
    region = config.env("AWS_REGION") or config.env("AWS_DEFAULT_REGION") or "us-east-1"
    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_name}/converse"
    spec = {"toolSpec": {"name": tool["name"], "description": tool["description"],
                         "inputSchema": {"json": tool["input_schema"]}}}
    limit = int(min(max_tokens, info.get("max_tokens", max_tokens)))
    last = ""
    for choice in ({"tool": {"name": tool["name"]}}, {"any": {}}, {"auto": {}}):
        text = user + (f"\n\nAnswer only by calling the {tool['name']} tool." if "auto" in choice else "")
        body = {
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": text}]}],
            "inferenceConfig": {"maxTokens": limit, "temperature": 0.7},
            "toolConfig": {"tools": [spec], "toolChoice": choice},
        }
        r = _post_retry(url, api_key(p), body)
        if r.status_code == 400 and "toolchoice" in r.text.lower().replace("_", ""):
            last = r.text
            continue
        if r.status_code in RETRY_STATUS:
            raise _Overloaded(r.status_code, r.text)
        if r.status_code != 200:
            raise LLMError(_explain(p, r.status_code, r.text))
        data = r.json()
        parts = ((data.get("output") or {}).get("message") or {}).get("content") or []
        raw = next(((c["toolUse"].get("input")) for c in parts
                    if (c.get("toolUse") or {}).get("name") == tool["name"]), None)
        if raw is None:
            raw = "".join(c.get("text", "") for c in parts)  # answered in plain JSON instead
        try:
            return _parse_arguments(raw)
        except (json.JSONDecodeError, TypeError):
            if data.get("stopReason") == "max_tokens":
                raise LLMError(f"{info['label']} ran out of output space before finishing. Try a shorter "
                               "video, or a model with a larger limit.")
            raise LLMError(f"{info['label']} did not return a usable structured answer. Try again, or pick "
                           "another model on the Setup page.")
    raise LLMError(_explain(p, 400, last))


def list_models(p: str) -> list[str]:
    """Model ids the provider offers this key (OpenAI-compatible providers only)."""
    info = PROVIDERS[p]
    if p in ("anthropic", "bedrock") or not api_key(p):
        return []
    import requests

    r = requests.get(info["base_url"] + "/models", timeout=30,
                     headers={"Authorization": f"Bearer {api_key(p)}"})
    if r.status_code != 200:
        raise LLMError(_explain(p, r.status_code, r.text))
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    ids = [i.removeprefix("models/") for i in ids if i]
    if p == "gemini":  # hide embedding/image/audio-only models
        ids = [i for i in ids if i.startswith("gemini") and not re.search(r"embed|image|tts|audio|live", i)]
    if p == "groq":
        ids = [i for i in ids if not re.search(r"whisper|tts|guard|orpheus|playai", i)]
    if p == "openai":  # chat models only
        ids = [i for i in ids if re.match(r"(gpt|o\d|chatgpt)", i) and not re.search(r"audio|realtime|image|transcribe|tts|search", i)]
    return sorted(set(ids))
