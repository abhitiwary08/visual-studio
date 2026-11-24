"""Facebook Page and Instagram publishing through Meta's Graph API.

Connecting (one time, no app review needed for your own Page):
1. Create an app at developers.facebook.com (type "Business"), note its App ID and App Secret.
2. In Graph API Explorer, pick the app, add the permissions in PERMISSIONS, and generate a User token.
3. Paste App ID, App Secret and that token on the Setup page. We exchange it for a long-lived token
   (about 60 days) and read your Pages; Page tokens made from it do not expire. The App Secret is
   used once for the exchange and not stored.

Videos: Facebook Reels (9:16, 3-90 s) via /video_reels, longer or 16:9 videos as a normal Page video;
Instagram Reels (9:16) via a resumable container upload. Both upload the local file, so nothing
needs to be hosted publicly.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

from . import config

GRAPH = "https://graph.facebook.com"
RUPLOAD = "https://rupload.facebook.com"
TOKEN_FILE = config.CONFIG_DIR / "meta_token.json"
PERMISSIONS = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
               "instagram_basic", "instagram_content_publish", "business_management"]
CAPTION_LIMIT = 2200  # Instagram caption limit (characters)
HASHTAG_LIMIT = 30  # Instagram allows at most 30 hashtags
REEL_MIN, REEL_MAX = 3.0, 90.0  # Facebook Reels duration limits (seconds)


class MetaError(RuntimeError):
    pass


def version() -> str:
    return config.env("META_GRAPH_VERSION", "v25.0")


# ------------------------------------------------------------------- http ----
def _request(method: str, url: str, **kw):
    """The single place that talks to Meta (tests replace it)."""
    import requests

    return requests.request(method, url, timeout=kw.pop("timeout", 120), **kw)


def _json(r) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        err = (data or {}).get("error") or {}
        msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
        code = err.get("code")
        if code == 190:
            msg = "The Facebook/Instagram login has expired or was revoked. Connect again on the Setup page."
        elif code in (10, 200) or (isinstance(code, int) and 200 <= code < 300):
            msg = f"Permission missing ({msg}). Generate the token again with all permissions listed on the Setup page."
        elif code in (4, 17, 32, 613) or r.status_code == 429:
            msg = f"Meta rate limit reached. Try again later. ({msg})"
        raise MetaError(msg)
    return data


def _get(path: str, token: str, **params) -> dict:
    return _json(_request("GET", f"{GRAPH}/{version()}/{path}", params={**params, "access_token": token}))


def _post(path: str, token: str, **data) -> dict:
    return _json(_request("POST", f"{GRAPH}/{version()}/{path}", data={**data, "access_token": token}))


# ------------------------------------------------------------- connection ----
def _token() -> dict:
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def connect(app_id: str, app_secret: str, user_token: str) -> dict:
    """Exchange a short-lived User token for a long-lived one and store the Pages it can post to."""
    app_id, app_secret, user_token = app_id.strip(), app_secret.strip(), user_token.strip()
    if not re.fullmatch(r"\d{5,25}", app_id):
        raise MetaError("The App ID is the number shown at the top of your app's dashboard.")
    if not app_secret or not user_token:
        raise MetaError("Fill in the App Secret and the User token.")
    ex = _json(_request("GET", f"{GRAPH}/{version()}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": user_token}))
    long_token = ex.get("access_token")
    if not long_token:
        raise MetaError("Meta did not return a long-lived token.")
    expires = int(time.time()) + int(ex.get("expires_in") or 60 * 86400)
    me = _get("me", long_token, fields="id,name")
    data = _get("me/accounts", long_token,
                fields="id,name,access_token,tasks,instagram_business_account{id,username}", limit=100)
    pages = []
    for p in data.get("data") or []:
        ig = p.get("instagram_business_account") or {}
        pages.append({"id": p["id"], "name": p.get("name", ""), "token": p.get("access_token", ""),
                      "can_post": "CREATE_CONTENT" in (p.get("tasks") or ["CREATE_CONTENT"]),
                      "ig_id": ig.get("id", ""), "ig_username": ig.get("username", "")})
    if not pages:
        raise MetaError("No Facebook Pages found for this login. The token needs pages_show_list, and you "
                        "must be an admin of a Page (Instagram must be a Business/Creator account linked to it).")
    stored = {"user_name": me.get("name", ""), "user_token": long_token, "user_expires": expires,
              "pages": pages, "page_id": pages[0]["id"], "connected_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save(stored)
    return status()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def select_page(page_id: str) -> dict:
    data = _token()
    if not any(p["id"] == page_id for p in data.get("pages") or []):
        raise MetaError("Unknown Page.")
    data["page_id"] = page_id
    _save(data)
    return status()


def _page() -> dict:
    data = _token()
    return next((p for p in data.get("pages") or [] if p["id"] == data.get("page_id")), {})


def status() -> dict:
    """Safe to send to the browser: no tokens."""
    data = _token()
    page = _page()
    expires = data.get("user_expires") or 0
    return {
        "connected": bool(page),
        "user_name": data.get("user_name", ""),
        "pages": [{"id": p["id"], "name": p["name"], "ig_username": p.get("ig_username", ""),
                   "can_post": p.get("can_post", True)} for p in data.get("pages") or []],
        "page_id": page.get("id", ""), "page_name": page.get("name", ""),
        "instagram": page.get("ig_username", ""),
        "expires": time.strftime("%Y-%m-%d", time.localtime(expires)) if expires else "",
        "expired": bool(expires) and expires < time.time(),
        "permissions": PERMISSIONS,
    }


# ------------------------------------------------------------------ caption ----
def clean_hashtags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    out = []
    for t in tags or []:
        t = re.sub(r"[^\wऀ-ॿ]", "", str(t).strip().lstrip("#"))
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:HASHTAG_LIMIT]


def compose_caption(caption: str, hashtags: list[str], footer: str = "") -> str:
    """Caption + hashtags + short disclosure, trimmed to Instagram's limit (hashtags kept whole)."""
    tags = " ".join("#" + t for t in clean_hashtags(hashtags))
    tail = "\n\n".join(x for x in (tags, footer.strip()) if x)
    room = CAPTION_LIMIT - len(tail) - 2
    body = caption.strip()
    if len(body) > room:
        body = body[:max(0, room - 1)].rstrip() + "…"
    return "\n\n".join(x for x in (body, tail) if x)


# ------------------------------------------------------------------ upload ----
def _rupload(url: str, token: str, video: Path) -> None:
    size = video.stat().st_size
    with open(video, "rb") as fh:
        r = _request("POST", url, data=fh, timeout=1800, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
            "Content-Type": "application/octet-stream"})
    data = _json(r)
    if data.get("success") is False:
        raise MetaError(f"Video upload was not accepted: {data}")


def post_facebook(video: Path, *, description: str, title: str, duration: float, vertical: bool,
                  log: Callable[[str], None] = lambda m: None) -> dict:
    page = _page()
    if not page:
        raise MetaError("Facebook is not connected. Connect it on the Setup page.")
    token = page["token"]
    if vertical and REEL_MIN <= duration <= REEL_MAX:
        start = _post(f"{page['id']}/video_reels", token, upload_phase="start")
        vid = start["video_id"]
        log("Facebook: uploading the Reel")
        _rupload(f"{RUPLOAD}/video-upload/{version()}/{vid}", token, video)
        _post(f"{page['id']}/video_reels", token, upload_phase="finish", video_id=vid,
              video_state="PUBLISHED", description=description, title=title[:250])
        log("Facebook: Reel published (Facebook may take a few minutes to process it)")
        return {"id": vid, "kind": "reel", "url": f"https://www.facebook.com/reel/{vid}",
                "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    # Longer or 16:9: a normal Page video (single request; fine for files under 1 GB).
    log("Facebook: uploading the video")
    with open(video, "rb") as fh:
        r = _request("POST", f"https://graph-video.facebook.com/{version()}/{page['id']}/videos", timeout=3600,
                     data={"access_token": token, "title": title[:250], "description": description},
                     files={"source": (video.name, fh, "video/mp4")})
    vid = _json(r)["id"]
    log("Facebook: video published")
    return {"id": vid, "kind": "video", "url": f"https://www.facebook.com/{page['id']}/videos/{vid}",
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def post_instagram(video: Path, *, caption: str, duration: float, vertical: bool,
                   log: Callable[[str], None] = lambda m: None, poll_seconds: float = 5,
                   max_wait: float = 600) -> dict:
    page = _page()
    data = _token()
    if not page or not page.get("ig_id"):
        raise MetaError("No Instagram account is linked to the selected Facebook Page. In Instagram, switch to "
                        "a Business or Creator account and link it to the Page, then connect again.")
    if not vertical:
        raise MetaError("Instagram Reels must be vertical (9:16). Use 'Make a Short' on the Video tab first.")
    if duration < REEL_MIN:
        raise MetaError("Instagram Reels must be at least 3 seconds long.")
    token = data.get("user_token") or page["token"]
    ig = page["ig_id"]
    box = _json(_request("POST", f"{GRAPH}/{version()}/{ig}/media", headers={"Authorization": f"Bearer {token}"},
                         json={"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                               "share_to_feed": True, "thumb_offset": 1000}))
    cid = box["id"]
    log("Instagram: uploading the Reel")
    _rupload(box.get("uri") or f"{RUPLOAD}/ig-api-upload/{version()}/{cid}", token, video)
    waited = 0.0
    while True:  # Instagram processes the video before it can be published
        st = _get(cid, token, fields="status_code,status")
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise MetaError(f"Instagram could not process the video: {st.get('status') or code}")
        if waited >= max_wait:
            raise MetaError("Instagram is still processing the video after 10 minutes. Try posting again later.")
        time.sleep(poll_seconds)
        waited += poll_seconds
    media = _post(f"{ig}/media_publish", token, creation_id=cid)["id"]
    link = ""
    try:
        link = _get(media, token, fields="permalink").get("permalink", "")
    except MetaError:
        pass
    log("Instagram: Reel published")
    return {"id": media, "kind": "reel", "url": link or f"https://www.instagram.com/{page.get('ig_username', '')}/",
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}
