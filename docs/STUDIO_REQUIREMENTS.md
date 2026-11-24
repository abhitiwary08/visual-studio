# Studio: requirements

Written 2026-10-13 from market research, before development. **Status: all M and S requirements built and tested (2026-10-13).** Status of each item is tracked in
[../TASKS.md](../TASKS.md) (Phase 7).

## 1. Goal

Let the channel owner edit the video and the sound of a generated video inside the dashboard, so that
every video can carry the owner's own choices, footage, voice and branding. This matters twice: it makes
better videos, and YouTube's July 2026 guidance only pays for videos with human contribution
("automated voiceovers over stock images" and template-based videos are named as not monetizable).

## 2. What the research says

| Finding | Source | Consequence for the studio |
|---|---|---|
| Creators in 2026 use CapCut, InShot, VN, VEED, Descript and Clipchamp, often several at once. Beginners need trim, split, merge, speed, crop, undo/redo and autosave; templates and obvious next steps matter for the first session. | [Splice](https://spliceapp.com/blog/video-editors-new-features-2026), [beginner guide](https://ipsnews.net/business/2026/05/08/best-online-video-editors-for-beginners-in-2026/) | Keep the editor simple and scene-based, with undo/redo, autosave and looks you can apply in one click. |
| CapCut wins on caption styles, templates, background removal and beat sync; Descript wins on transcript editing, filler-word removal and Studio Sound. | [clipspeed comparison](https://www.clipspeed.ai/compare/descript-vs-capcut.html), [Ramp](https://ramp.com/vendors/capcut/alternatives/capcut-vs-descript) | Caption styles and one-click voice polish are worth building. Beat sync and background removal are not (see 7). |
| Platforms normalise to about -14 LUFS integrated with -1 dBTP true peak and 11 LU range (YouTube, Instagram, TikTok); podcasts use -16 LUFS. Two-pass measuring then correcting avoids pumping. | [loudness targets](https://instagit.com/browser-use/video-use/loudness-normalization-targets-video-use.md), [LUFS guide](https://cleanvoice.ai/blog/lufs-for-podcast/) | A master loudness step with a -14 / -16 choice, using two passes, and a before/after readout. |
| Standard voice clean-up is noise reduction, hum removal, EQ, compression, de-essing, silence trimming and level matching. | [Cleanvoice](https://cleanvoice.ai/blog/lufs-for-podcast/) | Voice polish presets (light, studio), per-scene gain, and trimming of the owner's own recordings. |
| Hindi speech recognition: Whisper large-v3 scores about 31% word error on Common Voice Hindi, against about 12% for Sarvam's recogniser and a fine-tuned Whisper. Whisper's text normaliser also strips Hindi vowel signs and virama. | [benchmark](https://arxiv.org/pdf/2602.03868), [Sarvam Saarika](https://docs.sarvam.ai/api/getting-started/models/saarika.md), [whisper-hinglish](https://friendli.ai/models/Trelis/whisper-hinglish-preview) | Do not build transcript-based editing on Whisper. It is not needed: the script already is the transcript and captions are timed from it. |
| Browser editors either run ffmpeg in WebAssembly or WebCodecs in the page, or render on a server and show a low-resolution proxy for preview. | [Videola](https://github.com/fgilde/videola), [local-first editor](https://dev.to/martindelophy/how-i-built-a-local-first-ai-video-editor-with-webcodecs-webgpu-and-wasm-4lkn) | This app already runs locally with ffmpeg, so edit on the server and preview a single scene at half size. |
| Background music brings Content ID claims; "royalty-free" still has licence terms (monetization, Shorts, platforms). YouTube's own Audio Library is not claimed. | [Foxi Music guide](https://www.foximusic.com/blog/what-background-music-can-i-use-on-youtube-guide/) | A music library with a licence note per track and a warning for tracks without one. |
| Devotional listeners often listen without watching (bhajans, mantras, meditation). | market research, Phase 0 | Export the mix as an MP3 for audio platforms. |

## 3. How this tool differs from a general editor

- The unit of editing is the **scene** (one narration passage with one picture). A free-form multi-track
  timeline would be a different product and a poor fit for script-driven videos.
- The script is the transcript, so captions, subtitles and "edit by text" already work without speech recognition.
- Everything is **non-destructive**: edits are settings saved with the job; the original footage and recordings are never changed.
- It runs on the owner's computer, so there is no upload step and no quota.

## 4. Jobs to be done

1. "Make this scene look like mine": my own footage or photo, a calmer look, slow movement, my logo.
2. "Make it sound professional": clean voice, even levels, music that stays under the voice, correct loudness.
3. "Fix the order and the pacing": move, split, duplicate, merge and remove scenes; pause and trim.
4. "Check before I wait for a full render": preview one scene quickly.
5. "Don't lose my work": undo, redo, autosave.

## 5. Requirements

Priority: **M** must have, **S** should have. Acceptance criteria are what the tests check.

### Workflow (W)
| ID | Requirement | Pri | Acceptance |
|---|---|---|---|
| W1 | A **Studio** tab on every video with a script, showing scenes as a strip (type, length, marks for own voice, own footage, reflection, chant). | M | Strip renders all scenes; selecting one opens its editor. |
| W2 | Reorder (drag and arrows), duplicate, split at the cursor, merge with next, delete scenes. | M | Order changes persist; a reorder re-renders nothing except joining the scenes. |
| W3 | Undo and redo for every studio change (at least 100 steps), and autosave within 2 seconds of the last change with a visible saved state. | M | Undo restores the previous script and settings exactly. |
| W4 | Preview one scene at half resolution with its voice, picture, captions and effects. | M | A preview file is produced and plays; the final video is untouched. |
| W5 | Settings can be saved as the default for new videos. | S | A new video starts with the saved caption style, looks, audio and logo settings. |

### Picture (V)
| ID | Requirement | Pri | Acceptance |
|---|---|---|---|
| V1 | Choose where a stock clip starts and its speed (0.5x to 2x). | M | Rendered scene uses the chosen part at the chosen speed. |
| V2 | Use the owner's own video or photo as a scene's picture (upload; checked for being playable). | M | Own file is used; no stock lookup; no Pexels credit for it. |
| V3 | Slow movement on still pictures (zoom in, zoom out, pan left/right). | M | Frames differ across the scene; no jitter at 1080p. |
| V4 | Looks: warm, cool, soft, vivid, black and white, plus brightness and saturation. | M | The look changes the rendered frames; "none" matches today's output. |
| V5 | On-screen text per scene (top, middle or bottom, with start and end), rendered with correct Hindi shaping. | M | Text is visible only in its time window. |
| V6 | Caption style (box, outline, bold yellow), position (top, middle, bottom) and size. | M | Caption images differ by style; position follows the setting in both orientations. |
| V7 | Transition between scenes: fade length 0 to 1 s (0 = hard cut). | S | Zero removes the fades. |
| V8 | Channel logo: uploaded once, shown in a corner with size and opacity, switchable per video. | S | Logo appears in the chosen corner on every scene. |
| V9 | Thumbnail: edit the thumbnail text and pick which scene's picture it uses. | S | Thumbnail uses the chosen scene. |

### Sound (A)
| ID | Requirement | Pri | Acceptance |
|---|---|---|---|
| A1 | Voice polish: off, light (EQ and gentle compression), studio (adds noise reduction for the owner's recordings). | M | Polished audio differs from the original; off is bit-identical to today. |
| A2 | Per-scene voice gain (-12 to +12 dB). | M | Measured level changes by the set amount. |
| A3 | Trim the start and end of the owner's own recording, with a waveform to see where. | M | Output length follows the trim; waveform endpoint returns peaks. |
| A4 | Master loudness: -14 LUFS (YouTube, Instagram, Facebook) or -16 LUFS (softer, meditation), true peak -1 dB, two passes, with measured before/after shown. Silent audio is left alone. | M | Measured integrated loudness of the result is within 1 LU of target. |
| A5 | Music: choose the track (or automatic), volume, ducking (off, gentle, strong), fade in and out, start offset. | M | The mix honours each setting; ducking off keeps music level under speech. |
| A6 | Music library: list, play, upload, delete tracks, with a licence note per track and a warning when a used track has none. | M | Upload rejects non-audio and path tricks; note is saved. |
| A7 | Export the final mix as an MP3. | S | An MP3 is produced from the rendered video. |

### Export (X)
| ID | Requirement | Pri | Acceptance |
|---|---|---|---|
| X1 | Quality choice: small file, standard, high. | S | CRF 24, 20 and 17 are used. |
| X2 | Re-rendering after audio-only or ordering changes reuses all rendered scenes. | M | Scene files are not re-encoded when only joining or mixing changed. |

## 6. Non-functional

- **Safe by default:** uploads are limited in size and type, stored under the job folder with content-hash names, checked with ffprobe, and never executed. No path from a request reaches outside the job or music folder.
- **Compatible:** videos made before the studio render the same as before when no studio setting is used.
- **Fast enough:** a scene preview should finish in well under a minute; slow zoom on a 1080p still must render faster than real time.
- **Accessible:** every control is keyboard reachable with a visible label; drag and drop has button alternatives.
- **Honest:** the UI states what is estimated (lengths) and what is measured (loudness).

## 7. Out of scope, and why

| Not building | Reason |
|---|---|
| Free-form multi-track timeline, keyframes | Different product; scene-based editing covers script-driven videos. |
| Transcript editing and filler-word removal | Needs Hindi speech recognition; Whisper is weak at Hindi (see 2). The script already is the transcript. Revisit with Sarvam. |
| Beat sync, background removal, AI effects | Little value for calm devotional content; heavy dependencies. |
| Sound-effect library | Licensing burden; low demand in this niche. |
| Cloud rendering or sharing | The app is deliberately local. |

## 8. Decisions

1. Scene ids become **stable** (kept across reordering) so rendered pictures and audio stay attached to the right scene. Cached audio, slides and scene videos are named by their content, not their position.
2. Per-scene edits are saved inside the script (`scene.edit`), so they travel with Make a Short and with undo. Video-wide settings are saved on the job (`studio`).
3. Channel branding (logo) is stored once in `config/`, not per video.
4. Previews render one scene at half size into `output/preview.mp4`.
5. Loudness uses ffmpeg `loudnorm` in two passes with linear correction.
