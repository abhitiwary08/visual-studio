"""Hindi script generation with the chosen AI writer (structured output via a forced tool call)."""
from __future__ import annotations

import json
import re
from typing import Any

from . import config, llm, studio


class ScriptError(RuntimeError):
    pass


# Uploaded/recorded narration lives in the job's voice/ folder, named by content hash.
MAX_REPEAT = 108  # jaap counts are traditionally 11, 21, 54 or 108
JAAP_COUNTS = (11, 21, 54, 108)
VOICE_FILE_RE = re.compile(r"[0-9a-f]{12}\.(?:webm|ogg|mp3|wav|m4a)")


SCRIPT_TOOL = {
    "name": "submit_script",
    "description": "Submit the finished video script and its YouTube metadata.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "YouTube title in Hindi, max 70 characters, no clickbait."},
            "description": {
                "type": "string",
                "description": "YouTube description in Hindi: 2-3 lines of summary, then 3-5 hashtags.",
            },
            "tags": {"type": "array", "items": {"type": "string"}, "description": "10-15 tags, Hindi and English."},
            "thumbnail_text": {"type": "string", "description": "3-5 Hindi words for the thumbnail."},
            "social_caption": {
                "type": "string",
                "description": "Instagram/Facebook caption in Hindi: a hook line, 2-4 short lines, a question that "
                               "invites comments, a gentle follow line. No hashtags here, under 900 characters.",
            },
            "hashtags": {"type": "array", "items": {"type": "string"},
                         "description": "10-15 Instagram/Facebook hashtags without #, Hindi and English, specific to the topic."},
            "sources": {
                "type": "array", "items": {"type": "string"},
                "description": "Texts the content is drawn from, e.g. 'Katha Upanishad, Prathama Adhyaya'. Only what you are sure of.",
            },
            "verify_notes": {
                "type": "array", "items": {"type": "string"},
                "description": "Every shloka, Sanskrit word, number or claim a human must double-check before publishing.",
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "narration": {"type": "string", "description": "Hindi (Devanagari) voice-over for this scene, 1-3 short sentences."},
                        "pause_after": {"type": "number", "description": "Seconds of silence after the narration (0 to 25)."},
                        "personal": {"type": "boolean", "description": "True only for the channel owner's personal reflection scene (narration left empty)."},
                        "hint": {"type": "string", "description": "For the personal scene: one line in Hindi suggesting what the owner could share."},
                        "repeat": {"type": "integer", "description": "Mantra jaap only: how many times the chant scene repeats (1-108). Omit otherwise."},
                        "visual": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["stock", "slide"]},
                                "query": {"type": "string", "description": "For stock: 2-4 English words for stock footage search."},
                                "heading": {"type": "string", "description": "For slide: small heading (Hindi)."},
                                "text": {"type": "string", "description": "For slide: main text, e.g. the shloka (use \\n between lines)."},
                                "sub": {"type": "string", "description": "For slide: meaning or transliteration under the main text."},
                            },
                            "required": ["type"],
                        },
                    },
                    "required": ["narration", "visual"],
                },
            },
        },
        "required": ["title", "description", "tags", "thumbnail_text", "verify_notes", "scenes"],
    },
}

IDEAS_TOOL = {
    "name": "submit_ideas",
    "description": "Submit video topic ideas.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Specific topic, in English with the Hindi title in brackets."},
                        "why": {"type": "string", "description": "One line on why people would search/watch this."},
                    },
                    "required": ["topic"],
                },
            }
        },
        "required": ["ideas"],
    },
}

SYSTEM = """You write scripts for a faceless Hindi spirituality YouTube channel (stories from the \
Upanishads, short moral stories, mantras with meanings, guided meditation). The audience is \
Indian, of all ages. The narrator is a calm AI voice, so the writing must sound natural when \
read aloud.

Language and tone
- Narration is in simple, warm, spoken Hindi in Devanagari, with correct matras and spelling. \
Use Sanskrit terms only where natural and explain them.
- Calm, respectful, non-sectarian, never preachy. No fear, no guilt, no sensationalism.
- Short sentences. One idea per scene (1-3 sentences, about 15-45 words).

Accuracy rules (these matter more than anything else)
- Be faithful to the actual texts and well-known traditional tellings. Do not invent plot, \
dialogue attributed to scripture, or shloka text.
- Quote a Sanskrit shloka or mantra ONLY if you are certain of the exact wording. If not \
certain, paraphrase in Hindi instead and add that point to verify_notes.
- Only name a specific source (Upanishad, chapter, verse number) if you are certain. Otherwise \
omit it. List in verify_notes every shloka, verse number, Sanskrit word, name or claim that a \
human must double-check before publishing.
- Never promise material results (wealth, cure, exam success, "this mantra will solve ...") \
and make no medical claims.

Visuals (each scene needs one)
- "stock": give 2-4 English words that a stock-footage library would match (sunrise over river, \
diya flame closeup, forest path mist, himalaya mountains clouds, temple bells, lotus pond, \
starry night sky). Concrete nature and ambiance, no people's faces, no deity images, no text, \
no brand or film references. Vary the queries across scenes.
- "slide": a generated text card, used for shlokas/mantras (put the Sanskrit text in `text` with \
\\n between lines, the meaning in `sub`), for word-by-word meanings, for the moral/teaching, \
and for the closing call to action.

Personal reflection
- Add exactly one scene with "personal": true, placed just before the closing call to action. \
Leave its narration empty: the channel owner writes it in their own words (a personal \
experience, how they understand the teaching, or how they practise it). Put a one-line \
suggestion in Hindi in `hint`, and give it a calm stock visual. Do not count it in the word budget.

Metadata
- Title: Hindi, under 70 characters, honest and searchable. Description: Hindi, 2-3 lines plus \
3-5 hashtags. Do not mention that the video is AI-generated (the system adds disclosure). \
Do not add source lines (the system adds them from `sources`).
- social_caption and hashtags are for Instagram and Facebook Reels: warm, personal, never clickbait.

Always answer by calling submit_script."""


def _target_words(minutes: float) -> int:
    return max(40, int(minutes * config.WORDS_PER_MINUTE))


def _user_prompt(topic: str, kind: str, fmt: str, minutes: float, notes: str,
                 previous: dict | None, feedback: str, repeat: int = 108) -> str:
    k = config.KINDS[kind]
    f = config.FORMATS[fmt]
    words = _target_words(minutes if fmt == "long" else min(minutes, 0.8))
    if k.get("jaap"):
        length = (f"Length: the introduction and closing together about 60-90 spoken Hindi words; "
                  f"the chant scene repeats the mantra {repeat} times (set its `repeat` to {repeat}).")
    else:
        length = (f"Length: about {words} spoken Hindi words in total "
                  f"(about {minutes if fmt == 'long' else 'under 1'} minute(s)).")
    parts = [
        f"Topic: {topic}",
        f"Content type: {k['label']}",
        f"Format: {f['label']}",
        length,
        f"Structure: {k['structure']}",
        f"Share of slide scenes: {k['slide_share']}.",
    ]
    if fmt == "short":
        parts.append("This is a vertical Short: strong hook in the first 3 seconds, at most 8-10 scenes, "
                     "and it must stay under 55 seconds of speech.")
    if notes:
        parts.append(f"Extra notes from the channel owner: {notes}")
    if previous and feedback:
        parts.append("Here is the previous draft (JSON):\n" + json.dumps(previous, ensure_ascii=False))
        parts.append(f"Revise it according to this feedback, keeping what already works:\n{feedback}")
    return "\n".join(parts)


def _client():
    key = llm.api_key("anthropic")
    if not key:
        raise ScriptError("No API key for Claude. Add one on the Setup page, pick another AI writer there, "
                          "or paste a script JSON manually.")
    import anthropic

    return anthropic.Anthropic(api_key=key)


def _call_tool(system: str, user: str, tool: dict, max_tokens: int) -> dict:
    """One forced tool call to the AI writer chosen on the Setup page; returns the tool arguments."""
    if llm.provider() != "anthropic":
        try:
            return llm.call_openai_compatible(system, user, tool, max_tokens)
        except llm.LLMError as e:
            raise ScriptError(str(e)) from e
        except Exception as e:  # network
            raise ScriptError(f"{llm.label()} call failed: {e}") from e
    client = _client()
    try:
        with client.messages.stream(
            model=llm.model("anthropic"),
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:
        raise ScriptError(f"Claude API call failed: {e}") from e
    for block in msg.content:
        if getattr(block, "type", "") == "tool_use" and block.name == tool["name"]:
            return dict(block.input)
    raise ScriptError("Claude did not return a structured script. Try again.")


SCENE_ID_RE = re.compile(r"s\d{2,5}")


def _assign_scene_ids(scenes: list[dict]) -> None:
    """Scene ids are stable: a scene keeps its id when moved, duplicated scenes and new scenes get the
    next free number. Rendered audio, pictures and edits are tied to the id, so this keeps them with
    the right scene. Duplicate or malformed ids are replaced."""
    seen: set[str] = set()
    for sc in scenes:
        if SCENE_ID_RE.fullmatch(sc["id"]) and sc["id"] not in seen:
            seen.add(sc["id"])
        else:
            sc["id"] = ""
    top = max((int(i[1:]) for i in seen), default=0)
    for sc in scenes:
        if not sc["id"]:
            top += 1
            sc["id"] = f"s{top:02d}"


def normalize_script(data: dict, fmt: str) -> dict:
    """Validate and clean a script dict (from Claude or pasted by the user)."""
    if not isinstance(data, dict):
        raise ScriptError("Script must be a JSON object.")
    scenes_in = data.get("scenes")
    if not isinstance(scenes_in, list) or not scenes_in:
        raise ScriptError("Script needs a non-empty 'scenes' list.")

    scenes = []
    for i, sc in enumerate(scenes_in, 1):
        if not isinstance(sc, dict):
            continue
        narration = str(sc.get("narration", "")).strip()
        personal = bool(sc.get("personal"))
        if not narration and not personal:  # a personal scene may stay empty until the owner writes it
            continue
        vis = sc.get("visual") or {}
        vtype = vis.get("type", "stock")
        if vtype not in ("stock", "slide", "image"):  # image: an AI picture (see storyboard.py)
            vtype = "stock"
        visual = {
            "type": vtype,
            "query": str(vis.get("query", "")).strip() or "nature sunrise",
            "heading": str(vis.get("heading", "")).strip(),
            "text": str(vis.get("text", "")).strip(),
            "sub": str(vis.get("sub", "")).strip(),
        }
        # the AI picture description is kept even when the scene shows something else, so switching back keeps it
        prompt = str(vis.get("prompt") or "").strip()[:1200]
        chars = [c for c in dict.fromkeys(str(c) for c in (vis.get("chars") or []) if isinstance(c, str))
                 if re.fullmatch(r"c\d{1,2}", c)][:8]
        if prompt:
            visual["prompt"] = prompt
        if chars:
            visual["chars"] = chars
        if vtype == "slide" and not (visual["text"] or visual["sub"] or visual["heading"]):
            visual["type"] = "stock"
        try:
            pause = float(sc.get("pause_after", 0) or 0)
        except (TypeError, ValueError):
            pause = 0.0
        scene = {
            "id": str(sc.get("id") or "").strip(),  # kept when valid, so edits follow the scene when it moves
            "narration": narration,
            "pause_after": max(0.0, min(25.0, pause)),
            "visual": visual,
        }
        if personal:
            scene["personal"] = True
            scene["hint"] = str(sc.get("hint", "")).strip()[:300]
        try:
            repeat = int(sc.get("repeat") or 1)
        except (TypeError, ValueError):
            repeat = 1
        if repeat > 1:
            scene["repeat"] = min(MAX_REPEAT, repeat)
        voice_file = str(sc.get("voice_file", "")).strip()
        if VOICE_FILE_RE.fullmatch(voice_file):
            scene["voice_file"] = voice_file
        edit = studio.normalize_edit(sc.get("edit"))
        if edit:
            scene["edit"] = edit
        scenes.append(scene)
    if not any(s["narration"] for s in scenes):
        raise ScriptError("No usable scenes (every scene needs narration).")
    _assign_scene_ids(scenes)

    title = str(data.get("title", "")).strip() or "Untitled"
    return {
        "title": title[:100],
        "description": str(data.get("description", "")).strip(),
        "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()][:25],
        "thumbnail_text": str(data.get("thumbnail_text", "")).strip() or title[:30],
        "social_caption": str(data.get("social_caption", "")).strip()[:2000],
        "hashtags": [h for h in (re.sub(r"[^\w\u0900-\u097F]", "", str(t).strip().lstrip("#"))
                                 for t in (data.get("hashtags") or [])) if h][:30],
        "sources": [str(s).strip() for s in (data.get("sources") or []) if str(s).strip()],
        "verify_notes": [str(s).strip() for s in (data.get("verify_notes") or []) if str(s).strip()],
        "scenes": scenes,
    }


def parse_pasted(text: str, fmt: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return normalize_script(json.loads(text), fmt)
    except json.JSONDecodeError as e:
        raise ScriptError(f"That is not valid JSON: {e}") from e


def generate_script(topic: str, kind: str, fmt: str, minutes: float, notes: str = "",
                    previous: dict | None = None, feedback: str = "", repeat: int = 108) -> dict:
    if kind not in config.KINDS:
        raise ScriptError(f"Unknown content type: {kind}")
    if fmt not in config.FORMATS:
        raise ScriptError(f"Unknown format: {fmt}")
    max_min = config.FORMATS[fmt]["max_minutes"]
    minutes = max(0.5, min(float(minutes), max_min))
    # Devanagari is token-hungry: allow generous output, scaled to length.
    max_tokens = int(min(30000, 4000 + minutes * 2600))
    data = _call_tool(SYSTEM, _user_prompt(topic, kind, fmt, minutes, notes, previous, feedback, repeat),
                      SCRIPT_TOOL, max_tokens)
    return normalize_script(data, fmt)


def generate_ideas(kind: str, count: int = 10, theme: str = "") -> list[dict]:
    k = config.KINDS.get(kind, config.KINDS["upanishad_story"])
    system = (
        "You plan content for a faceless Hindi spirituality YouTube channel. Suggest specific, "
        "searchable video topics that are faithful to real texts and traditions, evergreen (unless a "
        "festival is named, then timely for it), and not repetitive. Answer by calling submit_ideas."
    )
    user = f"Suggest {count} topics for the content type: {k['label']}."
    if theme:
        user += f" Theme or series: {theme}."
    data = _call_tool(system, user, IDEAS_TOOL, 3000)
    return [i for i in data.get("ideas", []) if i.get("topic")]


TRANSLATE_TOOL = {
    "name": "submit_translation",
    "description": "Submit the English subtitle text, one entry per input line, in the same order.",
    "input_schema": {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
        "required": ["lines"],
    },
}


def translate_lines(lines: list[str]) -> list[str]:
    """Hindi narration -> English subtitle lines (same count and order)."""
    if not lines:
        return []
    system = (
        "You translate the Hindi narration of a spirituality video into English subtitles for viewers "
        "outside India. Natural, simple English that keeps the calm tone. Keep Sanskrit terms in plain "
        "transliteration without diacritics (Atman, Brahman, dharma, Om) and keep mantras and shlokas "
        "transliterated, not translated. Do not add or drop meaning. Answer by calling submit_translation "
        "with exactly one entry per input line, in order."
    )
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
    user = f"Translate these {len(lines)} lines:\n{numbered}"
    data = _call_tool(system, user, TRANSLATE_TOOL, int(min(16000, 1000 + 60 * sum(len(t) for t in lines) / 10)))
    out = [re.sub(r"^\s*\d+\.\s*", "", str(t)).strip() for t in data.get("lines") or []]
    if len(out) != len(lines):
        raise ScriptError(f"Translation returned {len(out)} lines for {len(lines)}. Try again.")
    return out


SOCIAL_TOOL = {
    "name": "submit_social",
    "description": "Submit the Instagram/Facebook caption and hashtags.",
    "input_schema": {
        "type": "object",
        "properties": {
            "caption": {"type": "string", "description": "Hindi caption, no hashtags, under 900 characters."},
            "hashtags": {"type": "array", "items": {"type": "string"}, "description": "10-15 hashtags without #."},
        },
        "required": ["caption", "hashtags"],
    },
}


def generate_social(script: dict) -> dict:
    """Instagram/Facebook caption and hashtags for an existing script."""
    system = (
        "You write Instagram and Facebook Reels captions for a Hindi spirituality channel. Hindi in "
        "Devanagari, warm and personal, never clickbait, no fear or guilt, no promises of material "
        "results or medical claims. Structure: a first line that makes people stop scrolling (it shows "
        "before 'more'), 2-4 short lines with the essence, one question that invites comments, and a "
        "gentle line to follow for more. Then 10-15 specific hashtags (Hindi and English, no #, no "
        "banned or spammy tags). Answer by calling submit_social."
    )
    narration = " ".join(sc["narration"] for sc in script.get("scenes") or [])[:3000]
    user = f"Title: {script.get('title', '')}\nNarration: {narration}"
    data = _call_tool(system, user, SOCIAL_TOOL, 2000)
    caption = str(data.get("caption", "")).strip()
    if not caption:
        raise ScriptError("The AI writer returned an empty caption. Try again.")
    tags = [re.sub(r"[^\w\u0900-\u097F]", "", str(t).strip().lstrip("#")) for t in data.get("hashtags") or []]
    return {"caption": caption[:2000], "hashtags": [t for t in tags if t][:30]}
