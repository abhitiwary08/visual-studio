"""Festival calendar (config/festivals.json) for planning videos ahead of seasonal search spikes."""
from __future__ import annotations

import datetime as dt
import json

from . import config

FILE = config.CONFIG_DIR / "festivals.json"


def load() -> tuple[list[dict], int]:
    """Returns (festivals sorted by date, lead_days). Bad entries are skipped."""
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], 14
    out = []
    for f in data.get("festivals") or []:
        try:
            day = dt.date.fromisoformat(str(f["date"]))
        except (KeyError, ValueError):
            continue
        out.append({"date": day.isoformat(), "name": str(f.get("name", "")).strip() or "Festival",
                    "hindi": str(f.get("hindi", "")).strip(), "ideas": str(f.get("ideas", "")).strip()})
    try:
        lead = max(0, int(data.get("lead_days", 14)))
    except (TypeError, ValueError):
        lead = 14
    return sorted(out, key=lambda f: f["date"]), lead


def upcoming(today: dt.date | None = None, days: int = 60) -> list[dict]:
    """Festivals from today up to `days` ahead, with how long is left to prepare.

    `plan_by` is the date a video should be ready (lead_days before the festival), because search
    interest climbs in the days before a festival, not on the day."""
    today = today or dt.date.today()
    fests, lead = load()
    out = []
    for f in fests:
        day = dt.date.fromisoformat(f["date"])
        left = (day - today).days
        if 0 <= left <= days:
            plan_by = day - dt.timedelta(days=lead)
            out.append({**f, "days_left": left, "plan_by": plan_by.isoformat(),
                        "urgent": plan_by <= today})
    return out


def ideas_theme(f: dict) -> str:
    """Theme text handed to the topic-idea generator for one festival."""
    day = dt.date.fromisoformat(f["date"]).strftime("%d %B %Y")
    parts = [f"Festival: {f['name']} ({f['hindi']}) on {day}.",
             "Topics people search for in the days before this festival, faithful to the tradition."]
    if f.get("ideas"):
        parts.append(f"Starting points: {f['ideas']}.")
    return " ".join(parts)
