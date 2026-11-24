"""AI pictures for every scene, with the same characters throughout.

How consistency is kept (the 2026 practice for AI storyboards):
1. A character bible: the AI writer reads the script once and fixes each character's look (age, face,
   hair, clothing and colours) in words that are then reused verbatim, plus one art style and setting.
2. A character sheet per character: one reference picture, generated (or uploaded by the owner).
3. Every scene picture is generated with the sheets of the characters in it as reference images, the
   same style text and the same character descriptions. Only the action and framing change.

Data: job["storyboard"] (style, setting, characters), scene["visual"] of type "image" with "prompt" and
"chars", job["scene_images"][scene id] (the chosen picture and earlier versions), job["imaging"]
(progress of a running batch).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path

from . import imagegen, scriptgen
from .jobs import _LOCK, JobStore

STYLES = {
    "storybook": {
        "label": "Storybook watercolour",
        "prompt": "Soft watercolour storybook illustration, warm earthy palette, gentle light, hand-painted texture, "
                  "calm and devotional mood",
    },
    "miniature": {
        "label": "Indian miniature painting",
        "prompt": "Traditional Indian miniature painting style (Pahari and Rajput schools), flat perspective, fine "
                  "detailed brushwork, rich mineral colours, ornamental borders inside the frame",
    },
    "devotional": {
        "label": "Classic devotional art",
        "prompt": "Classic Indian devotional oil painting, luminous skin tones, glowing divine light, rich saffron, "
                  "gold and deep blue palette, reverent and serene",
    },
    "cinematic": {
        "label": "Cinematic digital painting",
        "prompt": "Cinematic digital painting, ancient India, volumetric golden-hour light, atmospheric depth, "
                  "painterly detail, epic but peaceful",
    },
    "animation": {
        "label": "Soft 3D animation",
        "prompt": "Soft 3D animated film style, rounded friendly shapes, warm lighting, expressive faces, "
                  "family-friendly, ancient Indian setting",
    },
}
DEFAULT_STYLE = "storybook"
CHAR_ID_RE = re.compile(r"c\d{1,2}")
MAX_CHARS = 8
MOTION_CYCLE = ("zoom_in", "pan_right", "zoom_out", "pan_left")

PLAN_TOOL = {
    "name": "submit_storyboard",
    "description": "Submit the character bible and one picture description per listed scene.",
    "input_schema": {
        "type": "object",
        "properties": {
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "c1, c2, ... in order of importance."},
                        "name": {"type": "string", "description": "Name as written in the script (Hindi)."},
                        "name_en": {"type": "string", "description": "Name in English letters."},
                        "look": {"type": "string",
                                 "description": "English, 30-60 words, fixed for the whole video: age, build, skin "
                                                "tone, face, hair, clothing with exact colours, one or two "
                                                "distinctive items. Visual facts only."},
                    },
                    "required": ["id", "name", "look"],
                },
            },
            "setting": {"type": "string",
                        "description": "English, 1-2 sentences: era, landscape, architecture and colour palette "
                                       "shared by all pictures."},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene": {"type": "integer", "description": "The scene number from the list."},
                        "prompt": {"type": "string",
                                   "description": "English, 25-60 words: what happens, who is where, framing "
                                                  "(wide, medium or close-up), light and mood. Refer to people "
                                                  "by name; do not repeat their looks."},
                        "characters": {"type": "array", "items": {"type": "string"},
                                       "description": "Ids of the characters visible in this picture."},
                    },
                    "required": ["scene", "prompt", "characters"],
                },
            },
        },
        "required": ["characters", "setting", "scenes"],
    },
}

PLAN_SYSTEM = """You are the storyboard artist for a Hindi spirituality YouTube channel. You turn a narrated \
script into one picture per scene, and the same characters must look identical in every picture.

Rules
- Characters: list each person (or deity) who appears in more than one picture, max 8. Their `look` is \
written once and never changes: be concrete (e.g. "saffron dhoti with a thin red border, rudraksha mala, \
grey beard to the chest"), and never use synonyms later.
- Scene prompts describe action, place, framing and light only; refer to people by name and list their ids \
in `characters`. Vary framing across scenes (wide, medium, close-up).
- Abstract or philosophical narration: show a fitting symbolic image (a lamp, a river at dawn, a lotus, \
a starry sky) or a character in contemplation.
- Depict deities, sages and sacred symbols respectfully, in traditional iconography, serene and dignified. \
No violence or gore (show consequences symbolically), no nudity, nothing frightening for children.
- Never ask for text, letters, captions, signs or logos in a picture.
- Write prompts and looks in English.
Answer by calling submit_storyboard."""


def style_text(sb: dict) -> str:
    base = STYLES.get(sb.get("style"), STYLES[DEFAULT_STYLE])["prompt"]
    note = str(sb.get("style_note") or "").strip()
    return f"{base}. {note}" if note else base


def aspect_for(fmt: str) -> str:
    return "9:16" if fmt == "short" else "16:9"


def normalize_storyboard(raw, keep: dict | None = None) -> dict:
    """Clean a storyboard (from the AI writer or the Studio). Reference pictures of characters whose id or
    name is unchanged are kept from `keep`."""
    raw = raw if isinstance(raw, dict) else {}
    old = {c["id"]: c for c in (keep or {}).get("characters") or []}
    old_by_name = {c["name"]: c for c in old.values()}
    chars, seen = [], set()
    for c in raw.get("characters") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        if not CHAR_ID_RE.fullmatch(cid) or cid in seen:
            cid = next(f"c{n}" for n in range(1, 100) if f"c{n}" not in seen)
        name = str(c.get("name") or "").strip()[:60]
        look = str(c.get("look") or "").strip()[:600]
        if not name or not look:
            continue
        seen.add(cid)
        prev = old.get(cid) if old.get(cid, {}).get("name") == name else old_by_name.get(name)
        ref = str(c.get("ref") or (prev or {}).get("ref") or "")
        chars.append({"id": cid, "name": name, "name_en": str(c.get("name_en") or "").strip()[:60], "look": look,
                      "ref": ref if re.fullmatch(r"[0-9a-f]{12}\.(?:png|jpg|jpeg|webp)", ref) else ""})
        if len(chars) >= MAX_CHARS:
            break
    style = raw.get("style") if raw.get("style") in STYLES else (keep or {}).get("style") or DEFAULT_STYLE
    return {"style": style, "style_note": str(raw.get("style_note") or "").strip()[:300],
            "setting": str(raw.get("setting") or "").strip()[:500], "characters": chars}


# ------------------------------------------------------------------- planning ----
def targets(script: dict, include_slides: bool = False) -> list[int]:
    """0-based indexes of scenes that should get an AI picture: not the owner's own footage, not chant loops,
    and text slides only when asked."""
    out = []
    for i, sc in enumerate(script.get("scenes") or []):
        if (sc.get("edit") or {}).get("bg_file") or int(sc.get("repeat") or 1) > 1:
            continue
        if sc["visual"]["type"] == "slide" and not include_slides:
            continue
        out.append(i)
    return out


def plan(store: JobStore, job_id: str, style: str = "", include_slides: bool = False, style_note: str = "") -> dict:
    """Ask the AI writer for the character bible and a picture description per scene, and switch those scenes
    to AI pictures. Returns the updated job."""
    job = store.get(job_id)
    script = job.get("script")
    if not script:
        raise scriptgen.ScriptError("Write the script first.")
    idx = targets(script, include_slides)
    if not idx:
        raise scriptgen.ScriptError("No scenes to illustrate (only slides, chants or your own footage).")
    sb_old = job.get("storyboard") or {}
    style = style if style in STYLES else sb_old.get("style") or DEFAULT_STYLE
    lines = []
    for i in idx:
        sc = script["scenes"][i]
        extra = f" [currently a text slide: {sc['visual']['text'] or sc['visual']['heading']}]" if sc["visual"]["type"] == "slide" else ""
        lines.append(f"{i + 1}. {sc['narration']}{extra}")
    context = "\n".join(f"{n + 1}. {s['narration']}" for n, s in enumerate(script["scenes"]) if n not in idx)
    user = (f"Title: {script.get('title', '')}\nArt style: {STYLES[style]['prompt']}\n"
            f"Picture shape: {'vertical 9:16' if job['format'] == 'short' else 'wide 16:9'}\n"
            + (f"Existing characters to keep (same ids and looks): "
               + "; ".join(f"{c['id']} {c['name']}: {c['look']}" for c in sb_old.get("characters", [])) + "\n"
               if sb_old.get("characters") else "")
            + f"\nScenes to illustrate (number. narration):\n" + "\n".join(lines)
            + (f"\n\nOther scenes, for context only:\n{context}" if context else ""))
    data = scriptgen._call_tool(PLAN_SYSTEM, user, PLAN_TOOL, int(min(16000, 2500 + 320 * len(idx))))
    sb = normalize_storyboard({**data, "style": style, "style_note": style_note or sb_old.get("style_note", "")},
                              keep=sb_old)
    known = {c["id"] for c in sb["characters"]}
    by_num = {}
    for s in data.get("scenes") or []:
        try:
            by_num[int(s.get("scene")) - 1] = s
        except (TypeError, ValueError):
            continue
    with _LOCK:
        job = store.get(job_id)
        script = job["script"]
        done = 0
        for i in idx:
            s = by_num.get(i)
            if not s or not str(s.get("prompt") or "").strip() or i >= len(script["scenes"]):
                continue
            v = script["scenes"][i]["visual"]
            v["type"] = "image"
            v["prompt"] = str(s["prompt"]).strip()[:1200]
            v["chars"] = [c for c in (s.get("characters") or []) if c in known][:MAX_CHARS]
            done += 1
        clean = scriptgen.normalize_script(script, job["format"])
        store.update(job_id, script=clean, storyboard=sb)
    store.log(job_id, f"Pictures planned for {done} scene(s) with {len(sb['characters'])} character(s)")
    if done < len(idx):
        store.warn(job_id, f"The AI writer described {done} of {len(idx)} scenes. Plan again, or write the missing "
                           "descriptions in the Studio.")
    return store.get(job_id)


# -------------------------------------------------------------------- prompts ----
def character_prompt(sb: dict, c: dict) -> str:
    who = c.get("name_en") or c["name"]
    return (f"Character reference sheet of {who}. {c['look']} Full-body view and a close-up of the face side by "
            f"side, front view, calm neutral expression, plain light grey background, soft even lighting. "
            f"{style_text(sb)}. No text, labels or letters.")


def scene_prompt(sb: dict, scene: dict, fmt: str) -> tuple[str, list[dict]]:
    """The full prompt for one scene and the characters (in reference-image order) it shows."""
    v = scene["visual"]
    by_id = {c["id"]: c for c in sb.get("characters", [])}
    chars = [by_id[i] for i in (v.get("chars") or []) if i in by_id]  # in the scene's order: main character first
    with_ref = [c for c in chars if c.get("ref")]
    parts = [f"{style_text(sb)}."]
    if sb.get("setting"):
        parts.append(f"Setting of the whole story: {sb['setting']}")
    parts.append(f"Scene: {v.get('prompt') or scene['narration']}")
    if chars:
        parts.append("Characters in this picture: " + "; ".join(
            f"{c.get('name_en') or c['name']}: {c['look']}" for c in chars) + ".")
    if with_ref:
        parts.append("Each character must look exactly like their reference image (same face, hair, body, clothing "
                     "and colours): " + ", ".join(f"reference image {n + 1} is {c.get('name_en') or c['name']}"
                                                  for n, c in enumerate(with_ref)) + ".")
    parts.append("No text, letters, captions, signs, logos or watermarks anywhere in the picture.")
    parts.append(("Vertical 9:16 composition" if fmt == "short" else "Wide 16:9 composition")
                 + "; keep faces and the main subject out of the bottom fifth, where captions go.")
    return " ".join(parts), with_ref


def default_motion(scene_id: str) -> str:
    """A slow move for a still AI picture, varied from scene to scene but stable for each scene."""
    import zlib
    return MOTION_CYCLE[zlib.crc32(scene_id.encode()) % len(MOTION_CYCLE)]


# ------------------------------------------------------------------ generation ----
def _store_file(job_dir: Path, tmp: Path) -> str:
    data = tmp.read_bytes()
    name = f"{hashlib.sha1(data).hexdigest()[:12]}.png"
    dest = job_dir / "media" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.move(str(tmp), dest)
    else:
        tmp.unlink(missing_ok=True)
    return name


def set_scene_image(store: JobStore, job_id: str, scene_id: str, name: str, by: str = "", prompt: str = "") -> None:
    """Make `name` (a file in media/) the scene's picture; earlier pictures stay selectable (last 6)."""
    with _LOCK:
        imgs = dict(store.get(job_id).get("scene_images") or {})
        cur = dict(imgs.get(scene_id) or {})
        hist = [h for h in cur.get("history", []) if h != name]
        if cur.get("file") and cur["file"] != name:
            hist = [cur["file"]] + [h for h in hist if h != cur["file"]]
        imgs[scene_id] = {"file": name, "history": hist[:6], "by": by or cur.get("by", ""),
                          "prompt": prompt or cur.get("prompt", ""), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        store.update(job_id, scene_images=imgs)


def _set_ref(store: JobStore, job_id: str, cid: str, name: str) -> None:
    with _LOCK:
        sb = dict(store.get(job_id).get("storyboard") or {})
        sb["characters"] = [dict(c, ref=name) if c["id"] == cid else c for c in sb.get("characters", [])]
        store.update(job_id, storyboard=sb)


def _progress(store: JobStore, job_id: str, **changes) -> dict:
    with _LOCK:
        cur = dict(store.get(job_id).get("imaging") or {})
        errors = dict(cur.get("errors") or {})
        errors.update(changes.pop("error", {}) or {})
        cur.update(changes, errors=errors)
        store.update(job_id, imaging=cur)
        return cur


def run_images(store: JobStore, job_id: str, scene_ids: list[str] | None = None, only_missing: bool = True,
               chars: list[str] | None = None) -> None:
    """Background task: make the missing character sheets (or the `chars` asked for), then the pictures of the
    chosen scenes (all AI-picture scenes when scene_ids is None). One failure does not stop the rest."""
    job = store.get(job_id)
    jd = store.job_dir(job_id)
    label = imagegen.PROVIDERS[imagegen.provider()]["label"]
    try:
        sb = job.get("storyboard") or {}
        scenes = [s for s in (job.get("script") or {}).get("scenes", []) if s["visual"]["type"] == "image"]
        if scene_ids is not None:
            scenes = [s for s in scenes if s["id"] in scene_ids]
        if only_missing and scene_ids is None:
            have = job.get("scene_images") or {}
            scenes = [s for s in scenes if not ((have.get(s["id"]) or {}).get("file")
                                                and (jd / "media" / have[s["id"]]["file"]).exists())]
        used = {c for s in scenes for c in (s["visual"].get("chars") or [])}
        sheets = [c for c in sb.get("characters", [])
                  if (chars is not None and c["id"] in chars)
                  or (chars is None and c["id"] in used and not (c.get("ref") and (jd / "media" / c["ref"]).exists()))]
        total = len(sheets) + len(scenes)
        if not total:
            store.log(job_id, "Nothing to draw: every scene already has its picture")
            return
        _progress(store, job_id, total=total, done=0, current="", errors={})
        store.log(job_id, f"Drawing {len(sheets)} character sheet(s) and {len(scenes)} scene picture(s) with {label}")
        done = 0
        for c in sheets:
            if store.get(job_id).get("imaging_cancel"):
                break
            _progress(store, job_id, current=f"Character sheet: {c['name']}")
            try:
                sb_now = store.get(job_id).get("storyboard") or sb
                c_now = next((x for x in sb_now.get("characters", []) if x["id"] == c["id"]), c)
                tmp = jd / "work" / f"char_{c['id']}.png"
                imagegen.generate(character_prompt(sb_now, c_now), [], "16:9", tmp)
                _set_ref(store, job_id, c["id"], _store_file(jd, tmp))
            except Exception as e:
                _progress(store, job_id, error={c["id"]: str(e)})
                store.log(job_id, f"Character sheet for {c['name']} failed: {e}")
            done += 1
            _progress(store, job_id, done=done)
        for s in scenes:
            fresh = store.get(job_id)
            if fresh.get("imaging_cancel"):
                store.log(job_id, "Picture generation stopped")
                break
            sc = next((x for x in (fresh.get("script") or {}).get("scenes", []) if x["id"] == s["id"]), None)
            if sc is None or sc["visual"]["type"] != "image":
                done += 1
                continue
            n = fresh["script"]["scenes"].index(sc) + 1
            _progress(store, job_id, current=f"Scene {n}")
            try:
                prompt, refs = scene_prompt(fresh.get("storyboard") or sb, sc, fresh["format"])
                tmp = jd / "work" / f"img_{sc['id']}.png"
                imagegen.generate(prompt, [jd / "media" / c["ref"] for c in refs], aspect_for(fresh["format"]), tmp)
                set_scene_image(store, job_id, sc["id"], _store_file(jd, tmp), by=label, prompt=sc["visual"].get("prompt", ""))
            except Exception as e:
                _progress(store, job_id, error={sc["id"]: str(e)})
                store.log(job_id, f"Scene {n} picture failed: {e}")
            done += 1
            _progress(store, job_id, done=done)
        errs = (store.get(job_id).get("imaging") or {}).get("errors") or {}
        store.log(job_id, f"Pictures done: {done - len(errs)} made" + (f", {len(errs)} failed" if errs else ""))
    finally:
        last = store.get(job_id).get("imaging") or {}
        store.update(job_id, imaging=None, imaging_cancel=False,
                     imaging_last={"errors": last.get("errors") or {}, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
