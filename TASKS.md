# Roadmap and task tracker

Built from the October 2026 market research. The main risk is YouTube's July 2026 "inauthentic content"
guidance, which names "automated voiceovers over stock images" and "template-based videos" as
non-monetizable. Phase 1 adds human contribution so videos are not just automated output.

Status: `[ ]` to do, `[~]` in progress, `[x]` done, `[-]` deferred

## Phase 0: Housekeeping
- [x] **T0 Fix restart status bug.** `recover_interrupted` sets interrupted renders to `approved`, a status nothing else uses.

## Phase 1: Protect monetization (human value)
- [x] **T1 Your own voice.** Upload or record (in the browser) narration for any scene; it replaces the AI voice for that scene.
- [x] **T2 Personal reflection scene.** Scenes marked `personal` must be written by the channel owner; rendering is blocked until they are. Claude adds an empty one to each script.
- [x] **T3 Visual variety.** Several slide/background themes; each video gets one (rotating by default, selectable per job).
- [x] **T4 Review gate.** Upload requires a "I reviewed the script and checked every verify note" confirmation, recorded in the job.

## Phase 2: Traffic
- [x] **T5 Festival calendar.** Upcoming Hindu festivals on the New video page (editable `config/festivals.json`), with one click to suggest topics for a festival.
- [x] **T6 Mantra jaap format.** New content type: a mantra repeated N times (11/21/108) with a counter on screen. Built on a generic per-scene `repeat` field.
- [x] **T7 Short from a long video.** Pick scenes from a long video and create a 9:16 Short job from them (reuses cached voice-over).

## Phase 3: Revenue
- [x] **T8 English subtitles.** Translate captions to English with Claude (`captions.en.srt`), downloadable and uploaded to YouTube as a caption track.
- [x] **T9 Description links.** Editable `config/description_links.txt` (membership, affiliate, social links) appended to every description.

## Phase 4: Quality and risk
- [x] **T10 Licensed voice + pronunciation.** `TTS_ENGINE=sarvam` (Sarvam Bulbul, commercial terms) alongside Edge TTS, and `config/pronunciation.json` word replacements for both.
- [x] **T11 Upload pacing.** Daily upload cap (`MAX_UPLOADS_PER_DAY`, default 2) to avoid the bulk-upload pattern.

## Phase 5: Feedback loop
- [x] **T12 Performance stats.** Views, likes and comments for uploaded videos on a Channel stats page, averages per content type, and the best videos fed into topic suggestions.

- [x] **T13 Hindi text shaping on Windows.** `tools/get_fribidi.py` puts FriBiDi in `vendor/`; `app/__init__.py` loads it so Pillow's Raqm works.

## Phase 6: More platforms (added 2026-10-13)
- [x] **T14 AI writer choice.** Claude, Gemini, Groq or OpenAI, chosen on the Setup page; retries and backup models when a provider is overloaded.
- [x] **T15 Facebook and Instagram.** Captions and hashtags per video; post as Facebook Reel or Page video and Instagram Reel through the Graph API (v25.0), connected with a long-lived token.

## Phase 7: Studio, video and audio editing (added 2026-10-13; requirements in docs/STUDIO_REQUIREMENTS.md)
- [x] **M0 Research and requirements.**
- [x] **M1 Stable scene ids and content-named caches** (reorder and duplicate without re-rendering scenes).
- [x] **M2 Studio settings:** model, validation, defaults for new videos, API.
- [x] **M3 Picture:** clip start and speed, own footage or photo, slow zoom and pan, looks, on-screen text, fade or cut, caption styles, channel logo, thumbnail scene.
- [x] **M4 Sound:** voice polish and gain, own-recording trim with waveform, master loudness (two-pass), music library and mix controls, MP3 export.
- [x] **M5 Scene preview** at half size.
- [x] **M6 Studio tab:** scene strip with reorder, duplicate, split, merge and delete; scene editor; video-wide panels; undo, redo and autosave.
- [x] **M7 Tests:** unit tests, real-ffmpeg smoke test, browser test of the dashboard.
- [x] **M8 Documentation.**

## Phase 8: AI pictures with consistent characters (added 2026-10-13)
- [x] **P1 Providers:** OpenAI GPT image (reference images via /v1/images/edits) and Gemini image (generateContent with inline references), plus a free placeholder. Retries, clear refusal and key messages.
- [x] **P2 Character bible and scene descriptions** planned by the AI writer; editable characters and setting; art styles.
- [x] **P3 Character sheets, then scene pictures with the sheets as references**; versions per scene; stop; per-scene errors.
- [x] **P4 Video:** pictures used in the render with automatic slow movement; credit line; thumbnail; Make a Short keeps them.
- [x] **P5 Studio and Setup UI**, New video option, tests (test_images.py 13/13, browser_pictures.js 17/17).

## Verification
- [x] `tests/test_features.py` covers every task (15 tests) and passes; `tests/test_logic.py` 7/7 passes; JS checked with `node --check`
- [x] `tests/smoke_render.py` passes (long, short, mantra jaap with own voice) with a portable ffmpeg 9.0.2

## Still needs a real-world check (no keys or accounts on this machine)
- [ ] Sarvam TTS against the live API (`SARVAM_API_KEY`)
- [ ] Caption track upload and stats with a real YouTube login (reconnect needed for the new scope)
- [ ] Browser recording (MediaRecorder) in the actual browser you use
- [ ] Claude translation and festival topic ideas with a real `ANTHROPIC_API_KEY`
- [ ] Facebook Reel, Page video and Instagram Reel posting with a real Meta app and Page
- [ ] AI pictures with a real OpenAI or Gemini image model (only the free placeholder was used in tests)

## Log
- 2026-10-13: Phase 7 (Studio) done. All requirements in docs/STUDIO_REQUIREMENTS.md are built. Tests: test_studio.py 15/15, test_features.py 22/22, test_logic.py 7/7, smoke_render.py with a real-ffmpeg Studio scenario (loudness measured at -14.0 LUFS after mastering), and a 17-check browser test in Edge. Found and fixed on the way: ffprobe accepted renamed non-pictures as photos (now decoded with Pillow); the page overflowed sideways on phones.
- 2026-10-12: Phases 3-5 done (T8, T9, T12) and T13. Uploads now ask for the `youtube.force-ssl` scope (captions, stats); older logins keep working for uploads and are asked to reconnect for the new features.
- 2026-10-12: Phase 2 done (T5-T7). Festival dates cover Oct 2026 to Dec 2027; some 2027 dates differ by a day between sources, so the UI says to confirm with a panchang. The jaap counter uses ffmpeg `drawtext`, not yet run against a real ffmpeg.
- 2026-10-12: Phase 1 done (T0-T4) plus T10 and T11. New `tests/test_features.py`. T10's Sarvam call follows the official `sarvamai` SDK v0.1.37 (`language_code`, falling back to `target_language_code`); not yet tried against the live API (no key).
- 2026-10-12: Task list created. Baseline `test_logic.py` 7/7 passing. Python 3.12 venv via uv. ffmpeg not installed on this machine.
