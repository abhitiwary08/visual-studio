# Video studio

A local dashboard that turns a topic into a finished Hindi spirituality video
(Upanishad stories, mantras with meanings, short stories, guided meditation) and uploads it to YouTube.

```
topic -> Hindi script (Claude) -> you review -> voice-over (Edge TTS) -> stock footage + text slides
      -> captions + music -> rendered video + thumbnail -> YouTube upload
```

It runs on your own computer. Nothing is published until you approve the script and press Upload.

## Quick start

1. Install **Python 3.10+** and **ffmpeg** (`brew install ffmpeg`, `sudo apt install ffmpeg`, or ffmpeg.org on Windows).
2. In this folder:
   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env               # then add your keys (see below)
   python run.py
   ```
3. The dashboard opens at http://127.0.0.1:8765. Open **Setup and checks** to see what is missing.

Keys (all optional to start; the Setup page tells you what each one unlocks):

| Key in `.env` | What for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Writing scripts and topic ideas | console.anthropic.com |
| `PEXELS_API_KEY` | Free stock footage | pexels.com/api |
| `TTS_ENGINE=sarvam` + `SARVAM_API_KEY` | Licensed Hindi voice (optional; default is Edge TTS) | sarvam.ai |
| `MAX_UPLOADS_PER_DAY` | Daily upload cap before the dashboard asks you to confirm (default 2) | |

Without Pexels, scenes get plain gradient backgrounds. Without Claude, you can paste your own script JSON.

## Daily workflow

1. **New video**: enter a topic, press *Suggest topics*, or pick a festival under **Coming up** to get topics
   timed for it (`config/festivals.json`; confirm dates with your panchang).
2. **Script tab**: read the script. Fix narration, change a scene's footage search words, edit shlokas on text slides.
   The box *Check these before you publish* lists every shloka, name and claim the writer was unsure about.
   - **Your reflection**: each script has one scene for you to write in your own words. Rendering waits for it.
   - **Record my voice / Upload audio** on any scene replaces the AI voice for it. *Your contribution* shows how much
     of the video is yours.
   - **Look** picks the slide and thumbnail theme (rotates automatically).
3. **Approve and render**. The flame on the progress line moves as it works. Re-rendering after an edit only
   rebuilds the scenes you changed.
4. **Video tab**: watch it all the way through. Optionally **Create English subtitles**, or **Make a Short** from
   scenes that stand on their own.
5. **Publish tab**: check title, description and tags, tick *I watched this video*, then **Upload to YouTube**
   (private by default). Hindi and English caption tracks are added automatically.
6. **Channel stats**: views, likes and comments per video and per content type. Topic suggestions use your best videos.

**Mantra jaap** is its own content type: one chant scene repeats 11, 21, 54 or 108 times with a counter on screen.
Record the chant once in your own voice for the best result.

## Folders

| Path | Purpose |
|---|---|
| `assets/music/` | Put royalty-free `.mp3/.wav` tracks here. One is picked per video and ducks under the voice. |
| `assets/fonts/` | Devanagari fonts used on slides, captions and thumbnails (Noto Sans Devanagari is included). |
| `config/` | `client_secret.json` and the saved YouTube login (never share these); `festivals.json`, `description_links.txt` (added to every description), `pronunciation.json` (fixes for words the voice says wrongly). |
| `assets/music/licenses.json` | Licence notes you wrote for music tracks (Studio). |
| `config/logo.png`, `branding.json`, `studio_defaults.json` | Channel logo and its placement; Studio settings for new videos. |
| `vendor/` | Windows only: `libfribidi-0.dll` so Hindi text is shaped correctly (`python tools/get_fribidi.py`). |
| `data/jobs/<id>/` | Each video: `job.json`, `work/` (cache), `output/` (`video.mp4`, `thumbnail.jpg`, `captions.srt`). |
| `app/` | The pipeline. `tts.py` is the one place to change the voice engine. |

## YouTube setup (one time)

1. Go to console.cloud.google.com, create a project, and enable **YouTube Data API v3**.
2. **OAuth consent screen**: choose External, add yourself as a test user.
3. **Credentials > Create credentials > OAuth client ID > Desktop app**. Download the JSON and save it as
   `config/client_secret.json`.
4. In the dashboard: **Publish tab > Connect YouTube**, and sign in in the window that opens. The login asks for
   upload and caption/statistics access. If you connected before captions and stats were added, disconnect and
   connect once more.

Things to know:
- **Private-only until audited.** YouTube restricts API uploads from unverified projects to *private*. You can
  switch each video to public in YouTube Studio, or apply for Google's API compliance audit.
- **Testing mode logins expire after 7 days.** Reconnect, or publish the consent screen.
- **Quota:** each upload costs 1,600 of your 10,000 free daily units, plus 400 per caption track, so about 4 uploads a day with both tracks.
- Uploads are marked as containing synthetic media (AI voice) by default. Keep that checked.
- Facebook and Instagram: see *Facebook and Instagram* below.

## Studio: edit the picture and sound

The **Studio** edits a video scene by scene. Open it with **Studio: edit a video** in the sidebar, the **Studio** tab on any video, or **Edit in Studio** on the Script and Video tabs. Nothing you upload is changed; every edit is a setting
that is applied when you export, and everything saves itself (Undo and Redo go back up to 100 steps).

- **Scenes:** pick a scene in the strip; drag chips (or use Move earlier/later) to reorder; duplicate,
  split at the cursor, merge with the next, add or delete. Moving scenes re-renders nothing.
- **Picture:** your own video or photo instead of stock footage; where a clip starts and its speed;
  colour looks (warm, cool, soft, vivid, black and white), brightness and saturation; slow zoom or pan
  on still pictures; on-screen text with a position and time window.
- **Sound:** voice level per scene; trim your own recording with the waveform markers; voice polish
  (light or studio, which also reduces noise in your recordings); the music track, level, how far it dips
  while you speak, fades and start point; loudness set to -14 LUFS (YouTube, Instagram, Facebook) or
  -16 LUFS (meditation), measured in two passes with a -1 dB peak limit.
- **Music library:** add tracks, play them, write a licence note for each (a render warns about tracks without one).
- **Whole video:** caption style, position and size; fade length or hard cuts; channel logo (uploaded once,
  for every video or per video); which scene the thumbnail uses; file size (small, standard, high); an MP3 of the sound.
- **Preview scene** makes a quick half-size video of one scene so you can check it without a full export.
  **Use these settings for new videos** saves the current settings as the starting point.

Videos made before the Studio existed export exactly as before until you change their settings.
Requirements and research: [docs/STUDIO_REQUIREMENTS.md](docs/STUDIO_REQUIREMENTS.md).

## AI pictures with the same characters

Every scene can get an AI-drawn picture in which the characters stay the same from scene to scene.
In the Studio, open **AI pictures**:

1. Pick an art style and press **Plan the pictures**. The AI writer reads the script once, writes a fixed
   description of every character (age, face, hair, clothing and colours, in words that never change),
   a setting shared by all pictures, and a description of each scene's picture.
2. Check the characters. Edit a look if needed, or **Use my own picture** to give a character your drawing.
3. Press **Draw missing**. A character sheet is drawn for each character first; then every scene is drawn
   with the sheets of the characters in it as reference pictures, plus the same style and descriptions.
4. In a scene: change what the picture shows, tick who is in it, **Redraw**, or go back to an earlier
   version. Still pictures get a slow zoom or pan automatically.

Providers (Setup and checks, AI pictures): **OpenAI** (GPT image, default `gpt-image-1.5` with high input
fidelity), **Google Gemini** (default `gemini-3.1-flash-image`, falling back to `gemini-2.5-flash-image`), or
the free **Placeholder** cards to try the workflow. They use the AI writer's API keys. Pictures cost money
on both paid providers (very roughly $0.04 to $0.07 each; the Studio shows an estimate before drawing).
Scenes with your own footage, text slides (unless you ask) and chant loops are not replaced. The description
credits "Pictures: AI-generated". Stylised pictures need no "altered content" label on YouTube; photo-realistic
ones may get one automatically, so the styles offered are illustrated ones.
You can also tick **AI pictures for every scene** on the New video form to have them planned with the script.

## Facebook and Instagram

Each video gets an Instagram/Facebook caption and hashtags (written with the script; **Write caption with AI**
rewrites them). The **Facebook & Instagram** tab posts the rendered video:

- **Facebook Page**: vertical videos of 3 to 90 seconds go up as Reels; anything else as a normal Page video.
- **Instagram**: Reels only, so the video must be vertical (use **Make a Short** on a long video first).

Connect once on **Setup and checks**: create a Meta app (type Business) at developers.facebook.com, generate a
User token in Graph API Explorer with `pages_show_list, pages_read_engagement, pages_manage_posts,
instagram_basic, instagram_content_publish, business_management`, and paste App ID, App Secret and token.
The dashboard swaps it for a long-lived login (about 60 days; reconnect when the Setup page says it expired).
Your Instagram account must be a Business or Creator account linked to the Facebook Page. Posts are public
as soon as they finish; Meta allows about 100 Instagram API posts per 24 hours.

## Before you monetize

- **YouTube's July 2026 "inauthentic content" guidance** names automated voice-overs over stock footage and
  template-based videos as not monetizable. Use the reflection scene, your own voice and varied looks, and space uploads out.
- **Review every script.** The tool is built around a human review step because shloka wording, Sanskrit
  meanings and story details must be right. Do not turn on *Render as soon as the script is written* for
  videos you publish unchecked.
- **Add your own value.** YouTube's "inauthentic content" policy targets mass-produced, templated
  videos. Edit scripts, vary formats, add your own perspective.
- **Voice licence.** `edge-tts` is an unofficial client for Microsoft Edge's online read-aloud voices. It is free,
  but Microsoft does not publish commercial-use terms for that route. Before you rely on it for a monetized channel,
  switch `app/tts.py` to an official service with clear terms (Azure AI Speech has the same Hindi voices,
  Google Cloud Text-to-Speech is another option).
- **Music.** Only use tracks you have the right to use on YouTube. The generated drone used when
  `assets/music/` is empty is a placeholder.
- **Stock footage.** Pexels footage is free to use commercially without attribution; the description lists it anyway.

## Tests

```bash
pip install -r requirements-dev.txt
python tests/test_logic.py      # fast checks, no network
python tests/test_features.py   # roadmap features (TASKS.md) and API gates, no network or ffmpeg
python tests/test_studio.py     # Studio settings, editing and API, no network or ffmpeg
python tests/smoke_render.py    # renders long, short, mantra-jaap and Studio-edited videos with real ffmpeg
node tests/browser_studio.js    # clicks through the Studio in Edge/Chrome (setup steps at the top of the file)
python tests/test_images.py     # AI pictures: planning, providers, drawing, consistency references
node tests/browser_pictures.js  # AI pictures in the browser, with free placeholder pictures
```

## Troubleshooting

- **Hindi letters look broken (matras in the wrong place) on slides/captions:** Pillow needs Raqm text shaping.
  On Linux/macOS `pip install -U pillow` normally includes it. On Windows run `pip install zstandard` and
  `python tools/get_fribidi.py`, then restart. The Setup page shows whether Raqm is available.
- **"Edge TTS failed":** the voice service needs internet access to Microsoft. Check your network or proxy.
- **Pexels returns nothing:** rephrase the scene's search words in the Script tab (2 to 4 concrete English words work best).
- **Port in use:** `python run.py --port 8800`.
- The dashboard only listens on `127.0.0.1`. Do not expose it to a network; it can upload to your channel.
