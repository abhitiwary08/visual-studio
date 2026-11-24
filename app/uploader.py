"""YouTube upload via the Data API v3 (OAuth 'Desktop app' client)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import config

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# Needed for caption tracks (captions.insert) and reading your videos' statistics.
MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = [UPLOAD_SCOPE, MANAGE_SCOPE]
CLIENT_SECRET = config.CONFIG_DIR / "client_secret.json"
TOKEN_FILE = config.CONFIG_DIR / "youtube_token.json"


class UploadError(RuntimeError):
    pass


def _token() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def has_scope(scope: str) -> bool:
    """Logins made before captions/stats were added only have the upload scope."""
    return scope in (_token().get("scopes") or [])


def status() -> dict:
    data = _token()
    connected = bool(data.get("refresh_token"))
    return {"configured": CLIENT_SECRET.exists(), "connected": connected,
            "manage": connected and has_scope(MANAGE_SCOPE)}


def connect() -> None:
    """Run the OAuth browser flow on this machine and store the token. Blocks until done."""
    if not CLIENT_SECRET.exists():
        raise UploadError(f"Missing {CLIENT_SECRET}. Download an OAuth 'Desktop app' client JSON from Google Cloud Console.")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", authorization_prompt_message="")
    TOKEN_FILE.write_text(creds.to_json())
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        raise UploadError("YouTube is not connected yet. Use 'Connect YouTube' in the dashboard.")
    # Use the scopes the login actually granted (older logins have upload only).
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if not creds.valid:
        if creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise UploadError(
                    f"Could not refresh the YouTube login ({e}). Reconnect YouTube. "
                    "If your OAuth consent screen is in 'Testing' mode, tokens expire after 7 days."
                ) from e
            TOKEN_FILE.write_text(creds.to_json())
        else:
            raise UploadError("YouTube login expired. Reconnect YouTube.")
    return creds


def _clean(text: str) -> str:
    return text.replace("<", "").replace(">", "")


def _tags(tags: list[str]) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = _clean(str(t)).strip().replace(",", " ")
        if not t:
            continue
        if total + len(t) + 1 > 480:  # API limit is 500 characters in total
            break
        out.append(t)
        total += len(t) + 1
    return out


def upload_video(
    video: Path, thumb: Path | None, *, title: str, description: str, tags: list[str], category: str,
    privacy: str, publish_at: str | None, made_for_kids: bool, synthetic: bool,
    on_progress: Callable[[float], None] | None = None, on_log: Callable[[str], None] | None = None,
) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    log = on_log or (lambda m: None)
    if not Path(video).exists():
        raise UploadError(f"Video file not found: {video}")
    if privacy not in ("private", "unlisted", "public"):
        raise UploadError(f"Invalid privacy setting: {privacy}")

    yt = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)

    status_body = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status_body["privacyStatus"] = "private"  # scheduled videos must start private
        status_body["publishAt"] = publish_at
    body = {
        "snippet": {
            "title": _clean(title)[:100], "description": _clean(description)[:4900], "tags": _tags(tags),
            "categoryId": str(category), "defaultLanguage": "hi", "defaultAudioLanguage": "hi",
        },
        "status": status_body,
    }

    def attempt(with_synthetic: bool) -> dict:
        b = json.loads(json.dumps(body))
        if with_synthetic:
            b["status"]["containsSyntheticMedia"] = True
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=b, media_body=media)
        resp = None
        while resp is None:
            st, resp = req.next_chunk(num_retries=5)
            if st and on_progress:
                on_progress(st.progress())
        return resp

    try:
        try:
            resp = attempt(synthetic)
        except HttpError as e:
            if synthetic and e.resp.status == 400 and "ynthetic" in str(e):
                log("YouTube rejected the 'synthetic media' flag; retrying without it. "
                    "Set 'Altered content' manually in YouTube Studio.")
                resp = attempt(False)
            else:
                raise
    except HttpError as e:
        reason = ""
        try:
            reason = json.loads(e.content.decode())["error"]["errors"][0]["reason"]
        except Exception:
            pass
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise UploadError("YouTube API daily quota reached (each upload costs 1,600 of 10,000 units). Try tomorrow.") from e
        if reason == "uploadLimitExceeded":
            raise UploadError("YouTube upload limit reached for this channel. Try again later.") from e
        raise UploadError(f"YouTube API error {e.resp.status}: {reason or e}") from e

    vid = resp["id"]
    log(f"Video uploaded (id {vid}).")

    if thumb and Path(thumb).exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
            log("Thumbnail set.")
        except HttpError as e:
            log(f"Thumbnail not set ({e.resp.status}). Custom thumbnails need a verified channel; set it in Studio.")

    return {
        "video_id": vid,
        "url": f"https://youtu.be/{vid}",
        "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
        "privacy": status_body["privacyStatus"],
        "publish_at": publish_at or "",
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def upload_captions(video_id: str, tracks: list[tuple[Path, str, str]], log: Callable[[str], None]) -> int:
    """Add caption tracks [(srt_path, language, name)] to an uploaded video. Each costs 400 quota units.
    Returns how many were added; failures are logged, never raised (the video is already up)."""
    if not has_scope(MANAGE_SCOPE):
        log("Caption tracks not uploaded: reconnect YouTube once to allow captions and stats.")
        return 0
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)
    added = 0
    for path, lang, name in tracks:
        if not Path(path).exists():
            continue
        try:
            yt.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": lang, "name": name, "isDraft": False}},
                media_body=MediaFileUpload(str(path), mimetype="application/octet-stream"),
            ).execute()
            added += 1
            log(f"Caption track added: {name}")
        except HttpError as e:
            log(f"Caption track '{name}' not added ({e.resp.status}). You can add the .srt in YouTube Studio.")
    return added


def video_stats(video_ids: list[str]) -> dict[str, dict]:
    """Views, likes and comments for up to 50 of your videos per call (1 quota unit per call)."""
    if not video_ids:
        return {}
    if not has_scope(MANAGE_SCOPE):
        raise UploadError("Reconnect YouTube once to allow reading video statistics.")
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    yt = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        try:
            resp = yt.videos().list(part="statistics,status", id=",".join(video_ids[i:i + 50])).execute()
        except HttpError as e:
            raise UploadError(f"YouTube API error {e.resp.status} while reading statistics.") from e
        for item in resp.get("items", []):
            st = item.get("statistics", {})
            out[item["id"]] = {
                "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "privacy": item.get("status", {}).get("privacyStatus", ""),
            }
    return out
