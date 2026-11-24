"use strict";
/* Studio tab: edit the pictures and sound of a video, scene by scene (docs/STUDIO_REQUIREMENTS.md).
   Loaded after the main script and uses its globals (S, api, esc, clone, toast, guard, actions, main, ...).
   Scene edits live in S.draft (scene.edit); video-wide settings in S.studioDraft. Everything autosaves. */

const ST = {
  scene: 0, hist: null, timer: null, version: 0, studioDirty: false, status: "", statusErr: false,
  wave: {}, lib: null, brand: null, logoV: 1, drag: -1, brandTimer: null,
  gen: { prompt: "", seconds: 60, busy: false },
  open: new Set(["pictures", "captions", "sound"]),
  sbTimer: null, planning: false, planSlides: false, planStyle: "", lastImgKey: "",
};
const enc = encodeURIComponent;
const stOpts = () => (S.options && S.options.studio) || {};
const stScene = () => (S.draft && S.draft.scenes[ST.scene]) || null;
const stCap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const EDIT_DEFAULTS = { motion: "none", look: "none", bg_start: 0, bg_speed: 1, voice_gain: 0, voice_in: 0, voice_out: 0 };

/* ----------------------------------------------------------------- small builders --- */
function stSelect(path, map, value, attrs = "") {
  // map: {value: label}, a list of [value, label] pairs, or a list of values (labelled with a capital)
  const entries = Array.isArray(map) ? map.map((k) => (Array.isArray(k) ? k : [k, stCap(String(k))])) : Object.entries(map);
  return `<select data-st="${path}" ${attrs}>${entries.map(([k, v]) =>
    `<option value="${esc(k)}" ${String(k) === String(value) ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
}
function stFmt(v, { dec = 0, sign = false, unit = "", zero = "" } = {}) {
  if (zero && Number(v) === 0) return zero;
  let s = Number(v).toFixed(dec);
  if (sign && Number(v) > 0) s = "+" + s;
  return s + unit;
}
function stRange(path, { min, max, step, value, dec = 0, sign = false, unit = "", zero = "", auto = false, disabled = false }) {
  const fmt = { dec, sign, unit, zero };
  return `<span class="st-range"><input type="range" data-st="${path}" min="${min}" max="${max}" step="${step}" value="${value}"
    data-dec="${dec}" data-sign="${sign ? 1 : 0}" data-unit="${esc(unit)}" data-zero="${esc(zero)}" ${disabled ? "disabled" : ""}>
    <output>${auto ? "auto" : esc(stFmt(value, fmt))}</output></span>`;
}
const stNum = (path, value, attrs = "") => `<input type="number" data-st="${path}" value="${value ?? ""}" ${attrs}>`;
const stSecs = (sc) => {
  const rep = sc.repeat || 1;
  return Math.max(1, Math.round(words(sc.narration) / 1.97 * rep + (rep > 1 ? 0.8 * rep : 0) + (sc.pause_after > 0 ? sc.pause_after : 0.6)));
};

/* ----------------------------------------------------------------------- rendering --- */
function studioPanel(j) {
  if (!S.draft) return `<div class="empty"><p>Write the script first, then edit the video here.</p></div>`;
  if (!S.studioDraft) S.studioDraft = clone(j.studio_effective || {});
  if (!ST.hist) stHistInit();
  ST.scene = Math.max(0, Math.min(ST.scene, S.draft.scenes.length - 1));
  const sc = stScene(), total = S.draft.scenes.reduce((a, s) => a + stSecs(s), 0);
  const h = ST.hist, busy = isBusy(j);
  return `
  <div class="st-bar">
    <button class="btn secondary" type="button" data-act="stUndo" ${h.undo.length ? "" : "disabled"}>Undo</button>
    <button class="btn secondary" type="button" data-act="stRedo" ${h.redo.length ? "" : "disabled"}>Redo</button>
    <span class="st-status ${ST.statusErr ? "err" : ""}" id="stStatus" role="status">${esc(ST.status)}</span>
    <span class="grow"></span>
    <span class="muted">${S.draft.scenes.length} scenes, about ${mmss(total)}</span>
    <button class="btn primary" type="button" data-act="stExport" ${busy ? "disabled" : ""}>${j.outputs && j.outputs.video ? "Export the video again" : "Export the video"}</button>
  </div>
  <div class="st-strip" role="group" aria-label="Scenes. Drag to change the order.">
    ${S.draft.scenes.map(stChip).join("")}
    <button class="st-add" type="button" data-act="stAddScene" aria-label="Add a scene after this one">+ Add scene</button>
  </div>
  <div class="st-layout">
    <section aria-label="Scene ${ST.scene + 1}">${stSceneEditor(sc, ST.scene, j)}</section>
    <aside aria-label="Settings for the whole video">${stGlobal(S.studioDraft, j)}</aside>
  </div>`;
}

function stChip(sc, i) {
  const e = sc.edit || {}, own = !!e.bg_file, slide = sc.visual.type === "slide", ai = sc.visual.type === "image";
  const pic = ai && !own && stPic(sc.id);
  const tags = [own ? "Own file" : slide ? "Slide" : ai ? (pic ? "AI picture" : "AI picture: not drawn") : "Stock"];
  if (sc.voice_file) tags.push("your voice");
  if (sc.personal) tags.push("reflection");
  if ((sc.repeat || 1) > 1) tags.push(`chant x${sc.repeat}`);
  return `<button type="button" class="st-chip ${own ? "own" : slide ? "slide" : ""}" draggable="true" data-act="stSelect" data-i="${i}" aria-pressed="${i === ST.scene}" aria-label="Scene ${i + 1}">
    ${pic ? `<img class="st-thumb" alt="" src="/media/${enc(S.jobId)}/${enc(pic)}">` : ""}
    <span class="n"><span>${i + 1}</span><span>${stSecs(sc)}s</span></span>
    <span class="t hi">${esc(sc.narration.trim() || "(empty)")}</span>
    <span class="m">${esc(tags.join(" · "))}</span></button>`;
}

function stSceneEditor(sc, i, j) {
  const o = stOpts(), n = S.draft.scenes.length, e = sc.edit || {}, v = sc.visual;
  const own = !!e.bg_file, ownVideo = own && /\.(mp4|mov|webm|mkv)$/i.test(e.bg_file);
  const chant = (sc.repeat || 1) > 1, stockVideo = !own && v.type === "stock";
  const still = own ? !ownVideo : v.type === "slide";
  const busy = isBusy(j), recording = S.rec && S.rec.i === i;
  const pv = j.preview && j.preview.scene === sc.id ? j.preview : null;
  const ov = e.overlay || {};

  const head = `
  <div class="st-head">
    <h2>Scene ${i + 1} of ${n}</h2>
    <button class="btn secondary" type="button" data-act="stMove" data-d="-1" ${i === 0 ? "disabled" : ""}>Move earlier</button>
    <button class="btn secondary" type="button" data-act="stMove" data-d="1" ${i === n - 1 ? "disabled" : ""}>Move later</button>
    <button class="btn secondary" type="button" data-act="stDup" ${sc.personal ? "disabled" : ""}>Duplicate</button>
    <button class="btn secondary" type="button" data-act="stSplit" ${chant || sc.personal ? "disabled" : ""}>Split at cursor</button>
    <button class="btn secondary" type="button" data-act="stMerge" ${i === n - 1 || chant || sc.personal || (S.draft.scenes[i + 1].personal) || (S.draft.scenes[i + 1].repeat || 1) > 1 ? "disabled" : ""}>Merge with next</button>
    <button class="btn secondary danger" type="button" data-act="stDelete" ${n <= 1 ? "disabled" : ""}>Delete</button>
  </div>`;

  const words_ = `
  <div class="st-card"><h3>Words</h3>
    ${sc.personal ? `<p class="st-note">Your reflection: write this in your own words. ${sc.hint ? `<span class="hi">Idea: ${esc(sc.hint)}</span>` : ""}</p>` : ""}
    <label class="sr" for="stNarr">Narration</label>
    <textarea id="stNarr" class="hi" rows="3" lang="hi" data-st="scene.narration">${esc(sc.narration)}</textarea>
    <div class="st-row" style="margin-top:10px">
      <div><label class="f">Pause after (seconds)</label>${stNum("scene.pause_after", sc.pause_after || 0, 'min="0" max="25" step="0.5"')}</div>
      ${chant ? `<div><label class="f">Chant repeats</label>${stNum("scene.repeat", sc.repeat, 'min="1" max="108" step="1"')}</div>` : ""}
    </div>
  </div>`;

  const picture = `
  <div class="st-card"><h3>Picture</h3>
    <div class="st-own">
      ${own ? (ownVideo ? `<video src="/media/${enc(S.jobId)}/${enc(e.bg_file)}#t=0.5" muted preload="metadata"></video>`
                        : `<img alt="Your photo" src="/media/${enc(S.jobId)}/${enc(e.bg_file)}">`) : ""}
      <div>
        <label class="btn secondary file">${own ? "Replace my video or photo" : "Use my own video or photo"}<input type="file" accept="video/*,image/*" data-st-media></label>
        ${own ? `<button class="btn quiet" type="button" data-act="stRemoveMedia">Remove it</button>` : ""}
        <p class="st-note">Your own footage makes the video yours. Videos up to 500 MB, photos up to 25 MB.</p>
      </div>
    </div>
    ${own ? `<p class="st-note">Stock search and the text slide are not used while your own file is set.</p>` : `
    <div class="seg" role="radiogroup" aria-label="Picture type">
      <label><input type="radio" name="stvt" value="stock" data-st="scene.visual.type" data-rerender="1" ${v.type === "stock" ? "checked" : ""}>Stock footage</label>
      <label><input type="radio" name="stvt" value="slide" data-st="scene.visual.type" data-rerender="1" ${v.type === "slide" ? "checked" : ""}>Text slide</label>
      <label><input type="radio" name="stvt" value="image" data-st="scene.visual.type" data-rerender="1" ${v.type === "image" ? "checked" : ""}>AI picture</label>
    </div>
    ${v.type === "image" ? stImageEditor(sc, j) : v.type === "stock" ? `<div style="margin-top:10px"><label class="f" for="stQuery">Search words (English)</label>
        <input id="stQuery" type="text" data-st="scene.visual.query" value="${esc(v.query)}" placeholder="sunrise over river"></div>` : `
    <div class="st-row" style="margin-top:10px">
      <div><label class="f">Heading</label><input type="text" class="hi" data-st="scene.visual.heading" value="${esc(v.heading)}"></div>
      <div><label class="f">Meaning or note under the text</label><input type="text" class="hi" data-st="scene.visual.sub" value="${esc(v.sub)}"></div>
    </div>
    <label class="f" for="stText">Main text (one line per row)</label>
    <textarea id="stText" class="hi" rows="3" data-st="scene.visual.text">${esc(v.text)}</textarea>`}`}
    ${stockVideo || ownVideo ? `
    <div class="st-row" style="margin-top:10px">
      <div><label class="f">Start the clip at (seconds)</label>${stNum("scene.edit.bg_start", e.bg_start || 0, 'min="0" step="0.5"')}</div>
      <div><label class="f">Speed</label>${stRange("scene.edit.bg_speed", { min: 0.5, max: 2, step: 0.05, value: e.bg_speed || 1, dec: 2, unit: "x" })}</div>
    </div>` : ""}
  </div>`;

  const look = `
  <div class="st-card"><h3>Look and movement</h3>
    <div class="st-row">
      <div><label class="f">Colour look</label>${stSelect("scene.edit.look", o.looks || {}, e.look || "none")}</div>
      ${still ? `<div><label class="f">Slow movement</label>${stSelect("scene.edit.motion", o.motions || {}, e.motion || "none")}</div>` : ""}
    </div>
    <div class="st-row">
      <div><label class="f">Brightness</label>${stRange("scene.edit.brightness", { min: -0.3, max: 0.3, step: 0.01, value: e.brightness ?? 0, dec: 2, sign: true, auto: e.brightness == null })}</div>
      <div><label class="f">Saturation</label>${stRange("scene.edit.saturation", { min: 0, max: 2, step: 0.05, value: e.saturation ?? 1, dec: 2, auto: e.saturation == null })}</div>
    </div>
    <p class="st-note">${still ? "" : "Slow movement is for still pictures (slides and photos). "}Sliders you leave alone keep the standard look.</p>
    <button class="btn quiet" type="button" data-act="stResetLook">Reset look and movement</button>
  </div>`;

  const text = `
  <div class="st-card"><h3>On-screen text</h3>
    <label class="sr" for="stOv">On-screen text</label>
    <input id="stOv" type="text" class="hi" maxlength="120" data-st="scene.edit.overlay.text" value="${esc(ov.text || "")}" placeholder="A title or chapter name">
    <div class="st-row" style="margin-top:8px">
      <div><label class="f">Where</label>${stSelect("scene.edit.overlay.pos", o.overlay_positions || {}, ov.pos || "top")}</div>
      <div><label class="f">Shows from (seconds)</label>${stNum("scene.edit.overlay.start", ov.start || 0, 'min="0" step="0.5"')}</div>
      <div><label class="f">Until (0 = scene end)</label>${stNum("scene.edit.overlay.end", ov.end || 0, 'min="0" step="0.5"')}</div>
    </div>
  </div>`;

  let voice;
  if (recording) {
    voice = `<p class="st-note" role="status"><strong style="color:var(--brick)">Recording...</strong> read the narration above.</p>
      <div class="st-own"><button class="btn primary" type="button" data-act="recStop">Stop and use</button><button class="btn quiet" type="button" data-act="recCancel">Cancel</button></div>`;
  } else if (sc.voice_file) {
    voice = `<p class="st-note">Your recording. Drag the markers to keep only the part you want.</p>
      ${stWaveBox(i)}
      <div class="st-row">
        <div><label class="f">Start (seconds)</label>${stNum("scene.edit.voice_in", e.voice_in || 0, 'min="0" step="0.1" id="stIn"')}</div>
        <div><label class="f">End (seconds, 0 = to the end)</label>${stNum("scene.edit.voice_out", e.voice_out || 0, 'min="0" step="0.1" id="stOut"')}</div>
      </div>
      <audio controls preload="none" src="/voice/${enc(S.jobId)}/${enc(sc.voice_file)}" style="width:100%"></audio>
      <div class="st-own"><button class="btn quiet" type="button" data-act="recStart" data-i="${i}" ${S.rec ? "disabled" : ""}>Record again</button>
      <button class="btn quiet danger" type="button" data-act="voiceRemove" data-i="${i}">Use the AI voice instead</button></div>`;
  } else {
    voice = `${stWaveBox(i)}
      <div class="st-own"><button class="btn secondary" type="button" data-act="recStart" data-i="${i}" ${S.rec ? "disabled" : ""}>Record my voice</button>
      <label class="btn secondary file">Upload audio<input type="file" accept="audio/*" data-voice-file="${i}"></label>
      <span class="st-note" style="margin:0">The AI voice is used until you add yours.</span></div>`;
  }
  const sound = `
  <div class="st-card"><h3>Sound</h3>
    <div class="st-row"><div><label class="f">Voice level</label>${stRange("scene.edit.voice_gain", { min: -12, max: 12, step: 0.5, value: e.voice_gain || 0, dec: 1, sign: true, unit: " dB" })}</div></div>
    ${voice}
  </div>`;

  const preview = `
  <div class="st-card st-preview"><h3>Preview this scene</h3>
    <p class="st-note">A quick half-size preview with this scene's voice, picture, captions and effects. Your finished video is not changed.</p>
    <button class="btn primary" type="button" data-act="stPreview" ${j.previewing || busy ? "disabled" : ""}>${j.previewing ? "Making the preview..." : "Preview scene"}</button>
    ${pv ? `<video controls src="/files/${enc(j.id)}/output/preview.mp4?v=${enc(pv.at)}" class="${j.format === "short" ? "v" : ""}" style="margin-top:12px"></video>` : ""}
  </div>`;

  return head + words_ + picture + look + text + sound + preview;
}

function stWaveBox(i) {
  return `<div class="st-wavewrap" data-wrap="${i}"><canvas data-wave="${i}" aria-hidden="true"></canvas><div class="st-wavenote">Loading the sound...</div>
    <div class="st-handle" data-h="in" tabindex="0" role="slider" aria-label="Start of the part to keep"></div>
    <div class="st-handle" data-h="out" tabindex="0" role="slider" aria-label="End of the part to keep"></div></div>`;
}

function stGlobal(g, j) {
  const o = stOpts(), a = g.audio, mu = a.music, lib = ST.lib, b = ST.brand;
  const open = (k) => (ST.open.has(k) ? "open" : "");
  const kindVol = ((S.options.kinds || {})[j.kind] || {}).music_volume ?? 0.12;
  const known = new Set((lib || []).map((t) => t.name));
  const trackOpts = [["", "Automatic (picked for each video)"], ...(lib || []).map((t) => [t.name, t.name]), ["drone", "Generated drone (placeholder)"]];
  if (mu.track && mu.track !== "drone" && lib && !known.has(mu.track)) trackOpts.push([mu.track, `${mu.track} (missing)`]);
  const loud = j.outputs && j.outputs.loudness;
  const scenesOpt = [["", "Automatic (first video scene)"], ...S.draft.scenes.map((s, i) => [s.id || "", `Scene ${i + 1}: ${s.narration.trim().slice(0, 28)}`]).filter(([id]) => id)];
  return `
  ${stPicturesPanel(j, open("pictures"))}
  <details class="st-card" data-panel="captions" ${open("captions")}><summary>Captions and transitions</summary>
    <label class="check"><input type="checkbox" data-st-job="captions" ${j.captions !== false ? "checked" : ""}><span>Burn captions into the video</span></label>
    <div class="st-row">
      <div><label class="f">Caption style</label>${stSelect("g.captions.style", o.caption_styles || {}, g.captions.style)}</div>
      <div><label class="f">Position</label>${stSelect("g.captions.position", o.caption_positions || {}, g.captions.position)}</div>
      <div><label class="f">Size</label>${stSelect("g.captions.size", o.caption_sizes || [], g.captions.size)}</div>
    </div>
    <label class="f">Fade between scenes</label>
    ${stRange("g.transition.fade", { min: 0, max: 1, step: 0.05, value: g.transition.fade, dec: 2, unit: " s", zero: "Hard cut" })}
  </details>

  <details class="st-card" data-panel="sound" ${open("sound")}><summary>Voice and loudness</summary>
    <div class="st-row"><div><label class="f">Voice polish</label>${stSelect("g.audio.voice_polish", o.polish || {}, a.voice_polish)}</div></div>
    <p class="st-note">Light evens out the level. Studio also warms the tone and, for your own recordings, reduces background noise.</p>
    <label class="check"><input type="checkbox" data-st="g.audio.master" data-rerender="1" ${a.master ? "checked" : ""}><span>Set the loudness of the whole video</span></label>
    <div class="st-row"><div><label class="f">Target</label>${stSelect("g.audio.lufs", o.lufs || {}, String(Math.round(a.lufs)), `data-cast="num" ${a.master ? "" : "disabled"}`)}</div></div>
    ${loud ? `<p class="st-note">Last export: ${loud.before} to ${loud.after} LUFS (target ${loud.target}).</p>` : `<p class="st-note">Measured and corrected when you export, with a peak limit of -1 dB.</p>`}
  </details>

  <details class="st-card" data-panel="music" ${open("music")}><summary>Music</summary>
    <label class="check"><input type="checkbox" data-st-job="music" ${j.music !== false ? "checked" : ""}><span>Add background music</span></label>
    <div class="st-row"><div><label class="f">Track</label>${stSelect("g.audio.music.track", trackOpts, mu.track)}</div></div>
    <label class="check"><input type="checkbox" data-st-volauto ${mu.volume == null ? "checked" : ""}><span>Automatic level (${kindVol} for this type of video)</span></label>
    ${stRange("g.audio.music.volume", { min: 0, max: 0.5, step: 0.01, value: mu.volume ?? kindVol, dec: 2, disabled: mu.volume == null })}
    <div class="st-row" style="margin-top:10px">
      <div><label class="f">Lower the music while I speak</label>${stSelect("g.audio.music.ducking", o.ducking || {}, mu.ducking)}</div>
      <div><label class="f">Fade in (s)</label>${stNum("g.audio.music.fade_in", mu.fade_in, 'min="0" max="10" step="0.5"')}</div>
      <div><label class="f">Fade out (s)</label>${stNum("g.audio.music.fade_out", mu.fade_out, 'min="0" max="10" step="0.5"')}</div>
      <div><label class="f">Start the track at (s)</label>${stNum("g.audio.music.start", mu.start, 'min="0" max="600" step="1"')}</div>
    </div>
    <h3 style="font-size:.9375rem;margin:14px 0 4px">Music library</h3>
    ${lib == null ? `<p class="st-note">Loading...</p>` : `
    ${lib.length ? `<ul class="st-lib">${lib.map((t) => `<li>
      <div class="top"><strong>${esc(t.name)}</strong><span class="st-note" style="margin:0">${mmss(t.duration)} · ${t.size_mb} MB</span></div>
      <audio controls preload="none" src="/music/${enc(t.name)}"></audio>
      <input type="text" data-st-license="${esc(t.name)}" value="${esc(t.license)}" aria-label="Licence note for ${esc(t.name)}" placeholder="Licence: where it is from, and may you use it on YouTube, Instagram and Facebook?">
      <div class="top">${t.license ? "<span></span>" : `<span class="st-warn">No licence note yet</span>`}<button class="btn quiet danger" type="button" data-act="stMusicDelete" data-name="${esc(t.name)}" style="margin:0">Delete</button></div></li>`).join("")}</ul>`
      : `<p class="st-note">No tracks yet, so a plain generated drone is used.</p>`}
    <label class="btn secondary file">Add a track<input type="file" accept="audio/*" data-st-music></label>
    <h3 style="font-size:.9375rem;margin:14px 0 4px">Make music with AI</h3>
    <div class="st-row"><div style="flex:1"><label class="f" for="stGenPrompt">Describe it</label>
      <input id="stGenPrompt" type="text" data-st-gen="prompt" value="${esc(ST.gen.prompt)}" placeholder="Calm devotional instrumental, soft flute and tanpura, no vocals" maxlength="600"></div>
      <div><label class="f" for="stGenSecs">Length (s)</label><input id="stGenSecs" type="number" min="10" max="300" step="5" data-st-gen="seconds" value="${ST.gen.seconds}"></div></div>
    <button class="btn secondary" type="button" data-act="stGenMusic" ${ST.gen.busy ? "disabled" : ""}>${ST.gen.busy ? "Making the track (up to a minute)..." : "Generate a track"}</button>
    <p class="st-note">Uses the music maker chosen in Setup and checks. Costs credits with that provider. Check its terms before monetizing.</p>
    <p class="st-note">Only add music you are allowed to use on YouTube, Instagram and Facebook. Content ID can claim music even when a site calls it royalty-free.</p>`}
  </details>

  <details class="st-card" data-panel="brand" ${open("brand")}><summary>Channel logo</summary>
    ${b == null ? `<p class="st-note">Loading...</p>` : b.has_logo ? `
    <div class="st-logo"><img src="/branding/logo?v=${ST.logoV}" alt="Your logo">
      <div><label class="btn secondary file">Replace the logo<input type="file" accept="image/*" data-st-logo></label>
      <button class="btn quiet danger" type="button" data-act="stLogoDelete">Remove</button></div></div>
    <div class="st-row">
      <div><label class="f">Corner</label><select data-st-brand="corner">${Object.entries(o.corners || {}).map(([k, v]) => `<option value="${k}" ${b.corner === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      <div><label class="f">Show on this video</label>${stSelect("g.logo.show", { "": "Follow the setting below", true: "Show", false: "Hide" }, g.logo.show == null ? "" : String(g.logo.show), 'data-cast="tri"')}</div>
    </div>
    <label class="f">Size</label><span class="st-range"><input type="range" data-st-brand="size" min="0.05" max="0.3" step="0.01" value="${b.size}" data-dec="2"><output>${stFmt(b.size, { dec: 2 })}</output></span>
    <label class="f">Opacity</label><span class="st-range"><input type="range" data-st-brand="opacity" min="0.3" max="1" step="0.05" value="${b.opacity}" data-dec="2"><output>${stFmt(b.opacity, { dec: 2 })}</output></span>
    <label class="check" style="margin-top:8px"><input type="checkbox" data-st-brand="auto" ${b.auto ? "checked" : ""}><span>Add the logo to every new video</span></label>`
    : `<label class="btn secondary file">Upload your logo<input type="file" accept="image/*" data-st-logo></label><p class="st-note">A PNG with a transparent background works best. It is saved once for the whole channel.</p>`}
  </details>

  <details class="st-card" data-panel="thumb" ${open("thumb")}><summary>Thumbnail</summary>
    <label class="f" for="stThumbText">Thumbnail text</label>
    <input id="stThumbText" type="text" class="hi" data-st="script.thumbnail_text" value="${esc(S.draft.thumbnail_text || "")}">
    <div class="st-row" style="margin-top:10px"><div><label class="f">Picture from</label>${stSelect("g.thumbnail.scene", scenesOpt, g.thumbnail.scene)}</div></div>
  </details>

  <details class="st-card" data-panel="export" ${open("export")}><summary>Export</summary>
    <div class="st-row"><div><label class="f">Quality</label>${stSelect("g.export.quality", o.quality || {}, g.export.quality)}</div></div>
    <label class="check"><input type="checkbox" data-st="g.export.audio_mp3" ${g.export.audio_mp3 ? "checked" : ""}><span>Also make an MP3 of the sound (for audio-only listeners)</span></label>
    <div class="st-own">
      ${j.outputs && j.outputs.video ? `<button class="btn secondary" type="button" data-act="stMakeMp3">Make an MP3 now</button>` : ""}
      ${j.outputs && j.outputs.audio ? `<a class="btn quiet" href="/files/${enc(j.id)}/output/audio.mp3?download=1">Download the MP3</a>` : ""}
    </div>
    <button class="btn quiet" type="button" data-act="stSaveDefault">Use these settings for new videos</button>
  </details>`;
}

/* -------------------------------------------------------------------- AI pictures --- */
const stPic = (id) => { const e = ((S.job && S.job.scene_images) || {})[id]; return e && e.file; };
const stImgOpts = () => (S.health && S.health.images) || {};
function stImageEditor(sc, j) {
  const v = sc.visual, sb = S.sbDraft || j.storyboard, entry = (j.scene_images || {})[sc.id] || {};
  const err = ((j.imaging_last || {}).errors || {})[sc.id], busy = !!j.imaging;
  const chars = (sb && sb.characters) || [];
  const versions = [entry.file, ...(entry.history || [])].filter(Boolean);
  return `
    <div style="margin-top:10px">
      ${entry.file ? `<img class="st-bigpic ${j.format === "short" ? "v" : ""}" alt="The picture for this scene" src="/media/${enc(j.id)}/${enc(entry.file)}">` : `<p class="st-note">${sb ? "No picture drawn yet." : "Plan the pictures first (AI pictures, in the right-hand column)."}</p>`}
      ${versions.length > 1 ? `<div class="st-versions" role="group" aria-label="Earlier versions">${versions.map((f) => `<button type="button" data-act="stUseImg" data-file="${esc(f)}" aria-pressed="${f === entry.file}" title="Use this version"><img alt="" src="/media/${enc(j.id)}/${enc(f)}"></button>`).join("")}</div>` : ""}
      ${err ? `<p class="st-warn" role="alert">Last try failed: ${esc(err)}</p>` : ""}
      <label class="f" for="stPrompt" style="margin-top:8px">What the picture shows (English works best)</label>
      <textarea id="stPrompt" rows="3" data-st="scene.visual.prompt" placeholder="Nachiketa waits at Yama's door, wide shot, misty morning light">${esc(v.prompt || "")}</textarea>
      ${chars.length ? `<p class="f" style="margin:8px 0 4px">Characters in this picture</p><div class="st-chars">${chars.map((c) => `
        <label class="check" style="margin:0"><input type="checkbox" data-st-char="${esc(c.id)}" ${(v.chars || []).includes(c.id) ? "checked" : ""}><span class="hi">${esc(c.name)}</span></label>`).join("")}</div>` : ""}
      <div class="st-own">
        <button class="btn secondary" type="button" data-act="stDrawScene" ${busy || !sb || !sc.id ? "disabled" : ""}>${entry.file ? "Redraw this picture" : "Draw this picture"}</button>
        <span class="st-note" style="margin:0">Keeps the characters the same using their character sheets.</span>
      </div>
    </div>`;
}
function stPicturesPanel(j, open) {
  if (!S.sbDraft && j.storyboard) S.sbDraft = clone(j.storyboard);
  if (S.sbDraft && j.storyboard) {  // character sheets are made by the server: always show its latest ones
    for (const c of S.sbDraft.characters) {
      const n = j.storyboard.characters.find((x) => x.id === c.id);
      if (n) c.ref = n.ref;
    }
  }
  const io = stImgOpts(), sb = S.sbDraft || j.storyboard, styles = (S.options && S.options.image_styles) || {};
  const im = j.imaging, last = j.imaging_last || {};
  const scenes = S.draft.scenes.filter((s) => s.visual.type === "image" && !(s.edit || {}).bg_file);
  const missing = scenes.filter((s) => !stPic(s.id)).length;
  const est = (io.providers || {})[io.provider] ? ((io.providers[io.provider] || {}).est || 0) : 0;
  const needSheets = sb ? sb.characters.filter((c) => !c.ref).length : 0;
  const cost = (n) => (est ? ` (roughly $${(est * (n + needSheets)).toFixed(2)})` : " (free)");
  const errs = Object.keys(last.errors || {}).length;
  const style = ST.planStyle || (sb && sb.style) || "storybook";
  let body = `
    <p class="st-note" style="margin-top:0">One picture per scene, with the same characters throughout. Drawing with <strong>${esc(io.label || "?")}</strong>${io.provider === "placeholder" ? " (free test cards)" : ""}. <button class="btn quiet" type="button" data-act="goSetup" style="padding:0">Change</button></p>
    <div class="st-row"><div><label class="f">Art style</label>${stSelect("sb.style", styles, style, 'data-sb="style"')}</div></div>
    <label class="f" for="sbNote">Style notes (optional)</label>
    <input id="sbNote" type="text" data-sb="style_note" value="${esc((sb && sb.style_note) || "")}" placeholder="e.g. soft pastel colours, no modern objects">
    <label class="check" style="margin-top:8px"><input type="checkbox" data-st-planslides ${ST.planSlides ? "checked" : ""}><span>Also replace text slides with pictures</span></label>
    <button class="btn ${sb ? "secondary" : "primary"}" type="button" data-act="stPlan" ${ST.planning || im ? "disabled" : ""}>${ST.planning ? "The AI writer is planning..." : sb ? "Plan the pictures again" : "Plan the pictures"}</button>
    <p class="st-note">The AI writer describes each scene and fixes every character's look, in words that never change.</p>`;
  if (sb) {
    body += `
    <h3 style="font-size:.9375rem;margin:14px 0 6px">Characters</h3>
    ${sb.characters.length ? sb.characters.map((c, n) => `
    <div class="st-charcard">
      ${c.ref ? `<img alt="Character sheet of ${esc(c.name)}" src="/media/${enc(j.id)}/${enc(c.ref)}">` : `<div class="st-noref">No sheet yet</div>`}
      <div class="grow">
        <div class="st-row" style="margin-bottom:6px">
          <div><label class="f">Name</label><input type="text" class="hi" data-sb="characters.${n}.name" value="${esc(c.name)}"></div>
          <div><label class="f">In English</label><input type="text" data-sb="characters.${n}.name_en" value="${esc(c.name_en || "")}"></div>
        </div>
        <label class="f">Look (keep it exact: clothing, colours, hair)</label>
        <textarea rows="3" data-sb="characters.${n}.look">${esc(c.look)}</textarea>
        <div class="st-own" style="margin:6px 0 0">
          <button class="btn quiet" type="button" data-act="stDrawSheet" data-cid="${esc(c.id)}" ${im ? "disabled" : ""} style="padding:0">${c.ref ? "Redraw sheet" : "Draw sheet"}</button>
          <label class="btn quiet file" style="padding:0">Use my own picture<input type="file" accept="image/*" data-sb-ref="${esc(c.id)}"></label>
          <button class="btn quiet danger" type="button" data-act="stDelChar" data-n="${n}" style="padding:0">Remove</button>
        </div>
      </div>
    </div>`).join("") : `<p class="st-note">No recurring characters (the pictures show places and symbols).</p>`}
    <button class="btn quiet" type="button" data-act="stAddChar">+ Add a character</button>
    <label class="f" for="sbSetting" style="margin-top:10px">Setting shared by all pictures</label>
    <textarea id="sbSetting" rows="2" data-sb="setting">${esc(sb.setting || "")}</textarea>
    <h3 style="font-size:.9375rem;margin:14px 0 6px">Draw</h3>
    ${im ? `<div class="st-progress" role="status"><div style="width:${im.total ? Math.round(im.done / im.total * 100) : 3}%"></div></div>
      <p class="st-note" id="stImgProgress">${esc(im.current || "Starting")}: ${im.done} of ${im.total || "?"} done.</p>
      <button class="btn secondary" type="button" data-act="stStopDraw">Stop after this picture</button>`
    : `<p class="st-note">${scenes.length} scenes use AI pictures; ${missing} not drawn yet${needSheets ? `, plus ${needSheets} character sheet(s)` : ""}.</p>
      <div class="st-own">
        <button class="btn primary" type="button" data-act="stDraw" data-all="0" ${missing || needSheets ? "" : "disabled"}>Draw missing (${missing})${missing || needSheets ? cost(missing) : ""}</button>
        <button class="btn quiet" type="button" data-act="stDraw" data-all="1" ${scenes.length ? "" : "disabled"} style="padding:0">Redraw all (${scenes.length})${cost(scenes.length)}</button>
      </div>
      ${errs ? `<p class="st-warn">${errs} picture(s) failed last time. Open those scenes to see why, adjust the description and redraw.</p>` : ""}
      ${est ? `<p class="st-note">Costs are rough estimates; your provider bills the real amount.</p>` : ""}`}`;
  }
  return `<details class="st-card" data-panel="pictures" ${open}><summary>AI pictures</summary>${body}</details>`;
}
function stSaveSb() {
  clearTimeout(ST.sbTimer);
  ST.sbTimer = setTimeout(guard(async () => {
    if (!S.sbDraft || !S.jobId) return;
    const j = await api(`/api/jobs/${S.jobId}/storyboard`, { method: "PUT", body: { storyboard: S.sbDraft } });
    S.job = j;
  }), 800);
}
function stSbAssign(path, value) {
  if (!S.sbDraft) return;
  const keys = path.split("."); let o = S.sbDraft;
  for (let k = 0; k < keys.length - 1; k++) o = o[keys[k]];
  o[keys[keys.length - 1]] = value;
}
/* while pictures are being drawn, update the progress and pictures without disturbing typing */
function studioLive(j) {
  if (S.tab !== "studio") return;
  const im = j.imaging, el = $("#stImgProgress");
  if (im && el) {
    el.textContent = `${im.current || "Starting"}: ${im.done} of ${im.total || "?"} done.`;
    const bar = $(".st-progress div"); if (bar && im.total) bar.style.width = Math.round(im.done / im.total * 100) + "%";
  }
  const key = JSON.stringify([j.scene_images || {}, ((j.storyboard || {}).characters || []).map((c) => c.ref)]);
  if (key !== ST.lastImgKey) {
    const first = !ST.lastImgKey; ST.lastImgKey = key;
    if (!first) {
      if (S.sbDraft && j.storyboard) S.sbDraft.characters.forEach((c) => { const n = j.storyboard.characters.find((x) => x.id === c.id); if (n) c.ref = n.ref; });
      const a = document.activeElement;
      if (!(a && (a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && a.type === "text")))) stRender();
    }
  }
}

/* -------------------------------------------------------------- editing and history --- */
function stTarget(path) {
  const [root, ...keys] = path.split(".");
  return [root === "scene" ? stScene() : root === "g" ? S.studioDraft : S.draft, keys, root];
}
function stAssign(path, value) {
  const [base, keys, root] = stTarget(path);
  if (!base) return;
  if (path === "scene.pause_after" || path === "scene.repeat") value = value === null ? (path === "scene.repeat" ? 1 : 0) : value;
  let o = base;
  for (let i = 0; i < keys.length - 1; i++) {
    if (o[keys[i]] == null || typeof o[keys[i]] !== "object") o[keys[i]] = {};
    o = o[keys[i]];
  }
  const k = keys[keys.length - 1];
  if (value === null && path.startsWith("scene.edit")) delete o[k]; else o[k] = value;
  if (root === "scene") stTidy(base);
}
function stTidy(sc) {
  const e = sc.edit; if (!e) return;
  for (const [k, d] of Object.entries(EDIT_DEFAULTS)) if (e[k] === d) delete e[k];
  if (e.overlay && !String(e.overlay.text || "").trim()) delete e.overlay;
  if (!Object.keys(e).length) delete sc.edit;
}
function stCast(t) {
  if (t.type === "checkbox") return t.checked;
  if (t.type === "number" || t.type === "range") return t.value === "" ? null : Number(t.value);
  if (t.dataset.cast === "num") return Number(t.value);
  if (t.dataset.cast === "tri") return t.value === "" ? null : t.value === "true";
  return t.value;
}
function stOut(t) {
  const o = t.parentElement && t.parentElement.querySelector("output"); if (!o) return;
  o.textContent = stFmt(t.value, { dec: +(t.dataset.dec || 0), sign: t.dataset.sign === "1", unit: t.dataset.unit || "", zero: t.dataset.zero || "" });
}
const stSnap = () => JSON.stringify({ s: S.draft, g: S.studioDraft, t: ST.scene });
function stHistInit() { ST.hist = { undo: [], redo: [], cur: stSnap(), at: 0 }; }
function stRecord(mode) {
  if (!ST.hist) stHistInit();
  const h = ST.hist, now = stSnap();
  if (now === h.cur) return;
  const t = Date.now();
  if (mode === "typing" && t - h.at < 900) { h.cur = now; h.at = t; return; } // the same burst of typing: one undo step
  h.undo.push(h.cur); if (h.undo.length > 100) h.undo.shift();
  h.cur = now; h.redo = []; h.at = t;
}
function stSyncUndo() {
  const h = ST.hist; if (!h) return;
  const u = $('[data-act="stUndo"]'), r = $('[data-act="stRedo"]');
  if (u) u.disabled = !h.undo.length;
  if (r) r.disabled = !h.redo.length;
}
function stChanged(kind, mode) {
  ST.version++;
  if (kind === "studio") ST.studioDirty = true; else S.dirtyScript = true;
  stRecord(mode); stSyncUndo(); stSchedule();
}
function stStatus(text, err = false) {
  ST.status = text; ST.statusErr = err;
  const el = $("#stStatus"); if (el) { el.textContent = text; el.classList.toggle("err", err); }
}
function stRender() {
  const el = $("#panel");
  if (!el || S.tab !== "studio" || S.view !== "job" || !S.job) return;
  const y = window.scrollY;
  el.innerHTML = studioPanel(S.job);
  window.scrollTo(0, y);
  studioAfterRender();
}
function stUndo() {
  const h = ST.hist; if (!h || !h.undo.length) return;
  h.redo.push(h.cur); h.cur = h.undo.pop(); stApply(h.cur);
}
function stRedo() {
  const h = ST.hist; if (!h || !h.redo.length) return;
  h.undo.push(h.cur); h.cur = h.redo.pop(); stApply(h.cur);
}
function stApply(snap) {
  const o = JSON.parse(snap);
  S.draft = o.s; S.studioDraft = o.g; ST.scene = o.t;
  S.dirtyScript = true; ST.studioDirty = true; ST.version++;
  stSchedule(); stRender();
}
function stUpdateChip() {
  const sc = stScene(), chip = $('.st-chip[aria-pressed="true"]'); if (!sc || !chip) return;
  chip.querySelector(".t").textContent = sc.narration.trim() || "(empty)";
  chip.querySelector(".n span:last-child").textContent = stSecs(sc) + "s";
}

/* ----------------------------------------------------------------------- autosave --- */
let stChain = Promise.resolve();
function stSchedule() { clearTimeout(ST.timer); ST.timer = setTimeout(stSave, 1500); stStatus("Unsaved changes"); }
function stSave() { stChain = stChain.then(stDoSave, stDoSave); return stChain; }
async function stFlush() { clearTimeout(ST.timer); await stSave(); }
async function stDoSave() {
  const jobId = S.jobId;
  if (!S.draft || !jobId) return;
  if (!S.dirtyScript && !ST.studioDirty) { if (ST.status.startsWith("Unsaved")) stStatus("All changes saved"); return; }
  const bad = S.draft.scenes.findIndex((sc) => !sc.narration.trim() && !sc.personal);
  if (S.dirtyScript && bad >= 0) { stStatus(`Not saved yet: scene ${bad + 1} needs words (or delete it)`, true); return; }
  const v = ST.version;
  stStatus("Saving...");
  try {
    if (S.dirtyScript) {
      const j = await api(`/api/jobs/${jobId}/script`, { method: "PUT", body: { script: S.draft } });
      if (S.jobId !== jobId) return;
      S.job = j;
      if (ST.version === v) {
        const grew = j.script.scenes.length !== S.draft.scenes.length;
        S.draft = clone(j.script); S.dirtyScript = false;
        if (ST.hist) ST.hist.cur = stSnap();
        if (grew) stRender();
      }
    }
    if (ST.studioDirty) {
      const j = await api(`/api/jobs/${jobId}/studio`, { method: "PUT", body: { studio: S.studioDraft } });
      if (S.jobId !== jobId) return;
      S.job = j;
      if (ST.version === v) { S.studioDraft = clone(j.studio_effective); ST.studioDirty = false; if (ST.hist) ST.hist.cur = stSnap(); }
    }
    if (S.dirtyScript || ST.studioDirty) stSchedule(); else stStatus("All changes saved");
  } catch (e) {
    if (e.status === 409) { stStatus("Waiting for the current job to finish..."); ST.timer = setTimeout(stSave, 4000); }
    else stStatus("Not saved: " + e.message, true);
  }
}

/* --------------------------------------------------------------- scene operations --- */
function stStructural(select) {
  if (select != null) ST.scene = select;
  stChanged("script", "step");
  stRender();
}
function stBlankScene() {
  return { id: "", narration: "", pause_after: 0, visual: { type: "stock", query: "nature sunrise", heading: "", text: "", sub: "" } };
}
function stReorder(from, to) {
  const list = S.draft.scenes; if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return;
  const [m] = list.splice(from, 1); list.splice(to, 0, m);
  stStructural(to);
}
function stSplitScene() {
  const sc = stScene(), ta = $("#stNarr"); if (!sc || !ta) return;
  // Never cut through a word (that would break Hindi letters apart): move to the nearest space.
  const text = sc.narration, at = ta.selectionStart;
  const back = text.lastIndexOf(" ", at), fwd = text.indexOf(" ", at);
  const pos = /\s/.test(text[at] || "") || /\s/.test(text[at - 1] || "") ? at
    : back < 0 ? (fwd < 0 ? at : fwd) : fwd < 0 ? back : (at - back <= fwd - at ? back : fwd);
  const left = text.slice(0, pos).trim(), right = text.slice(pos).trim();
  if (!left || !right) { toast("Click inside the words where the scene should split, then press Split.", true); return; }
  if (sc.voice_file && !confirm("This scene has your recording. After splitting, both parts use the AI voice. Continue?")) return;
  const second = clone(sc);
  delete second.id; delete second.voice_file; delete second.personal; delete second.hint;
  if (second.edit) { delete second.edit.voice_in; delete second.edit.voice_out; delete second.edit.overlay; }
  delete sc.voice_file;
  if (sc.edit) { delete sc.edit.voice_in; delete sc.edit.voice_out; }
  second.narration = right; sc.narration = left; sc.pause_after = 0;
  S.draft.scenes.splice(ST.scene + 1, 0, second);
  stStructural(ST.scene);
}
function stMergeScene() {
  const list = S.draft.scenes, a = list[ST.scene], b = list[ST.scene + 1]; if (!a || !b) return;
  if ((a.voice_file || b.voice_file) && !confirm("A recording of yours is in one of these scenes. After merging, the scene uses the AI voice. Continue?")) return;
  a.narration = `${a.narration.trim()} ${b.narration.trim()}`.trim(); a.pause_after = b.pause_after;
  if (a.voice_file || b.voice_file) { delete a.voice_file; if (a.edit) { delete a.edit.voice_in; delete a.edit.voice_out; stTidy(a); } }
  list.splice(ST.scene + 1, 1);
  stStructural(ST.scene);
}

/* ---------------------------------------------------------------------- waveforms --- */
async function stDrawWaves() {
  for (const c of document.querySelectorAll("canvas[data-wave]")) {
    const i = +c.dataset.wave, sc = S.draft && S.draft.scenes[i], wrap = c.parentElement, note = wrap.querySelector(".st-wavenote");
    if (!sc) continue;
    if (!sc.id) { note.textContent = "Saving the scene first..."; continue; }
    const key = [S.jobId, sc.id, sc.voice_file || "", ((S.job.assets || {})[sc.id] || {}).audio || ""].join("|");
    if (!(key in ST.wave)) {
      try { ST.wave[key] = await api(`/api/jobs/${S.jobId}/waveform?scene=${enc(sc.id)}`); }
      catch (e) { ST.wave[key] = { error: e.message }; }
    }
    const w = ST.wave[key];
    if (!c.isConnected) continue;
    if (w.error) { note.textContent = sc.voice_file ? w.error : "Make a preview to see this scene's sound."; continue; }
    note.style.display = "none";
    wrap.dataset.dur = w.duration;
    if (c.getContext) stPaintWave(c, w, sc);
    stPlaceHandles(wrap, sc, w.duration);
  }
}
function stPaintWave(c, w, sc) {
  const dpr = window.devicePixelRatio || 1, W = c.clientWidth * dpr, H = c.clientHeight * dpr;
  c.width = W; c.height = H;
  const ctx = c.getContext("2d"), n = w.peaks.length, e = sc.edit || {};
  const from = (e.voice_in || 0) / w.duration, to = (e.voice_out || w.duration) / w.duration;
  for (let k = 0; k < n; k++) {
    const x = k / n * W, h = Math.max(1, w.peaks[k] * H * 0.9), inside = !sc.voice_file || (k / n >= from && k / n <= to);
    ctx.fillStyle = inside ? "#a85505" : "#aab4cb";
    ctx.fillRect(x, (H - h) / 2, Math.max(1, W / n - 1), h);
  }
}
function stPlaceHandles(wrap, sc, dur) {
  if (!sc.voice_file) return;
  const e = sc.edit || {}, a = wrap.querySelector('[data-h="in"]'), b = wrap.querySelector('[data-h="out"]');
  a.style.left = ((e.voice_in || 0) / dur * 100) + "%"; b.style.left = ((e.voice_out || dur) / dur * 100) + "%";
  a.setAttribute("aria-valuetext", `${(e.voice_in || 0).toFixed(1)} seconds`); b.setAttribute("aria-valuetext", `${(e.voice_out || dur).toFixed(1)} seconds`);
  wrap.classList.add("ready");
}
function stSetTrim(kind, secs, dur) {
  const sc = stScene(); if (!sc) return;
  const e = sc.edit || {}, lo = e.voice_in || 0, hi = e.voice_out || dur;
  secs = Math.round(Math.max(0, Math.min(dur, secs)) * 20) / 20;
  if (kind === "in") secs = Math.min(secs, hi - 0.3); else secs = Math.max(secs, lo + 0.3);
  stAssign(kind === "in" ? "scene.edit.voice_in" : "scene.edit.voice_out", kind === "out" && secs >= dur - 0.05 ? null : secs);
  const inp = $(kind === "in" ? "#stIn" : "#stOut"); if (inp) inp.value = kind === "out" && secs >= dur - 0.05 ? 0 : secs;
  const wrap = $(".st-wavewrap"); if (wrap) stPlaceHandles(wrap, sc, dur);
  const c = $("canvas[data-wave]"); const key = c && [S.jobId, sc.id, sc.voice_file || "", ((S.job.assets || {})[sc.id] || {}).audio || ""].join("|");
  if (c && c.getContext && ST.wave[key] && !ST.wave[key].error) stPaintWave(c, ST.wave[key], sc);
}
let stDragH = null;
document.addEventListener("pointerdown", (e) => {
  const h = e.target.closest && e.target.closest(".st-handle"); if (!h) return;
  const wrap = h.parentElement; stDragH = { kind: h.dataset.h, wrap, dur: +wrap.dataset.dur };
  h.setPointerCapture && h.setPointerCapture(e.pointerId); e.preventDefault();
});
document.addEventListener("pointermove", (e) => {
  if (!stDragH) return;
  const r = stDragH.wrap.getBoundingClientRect();
  stSetTrim(stDragH.kind, (e.clientX - r.left) / r.width * stDragH.dur, stDragH.dur);
});
document.addEventListener("pointerup", () => { if (stDragH) { stDragH = null; stChanged("script", "step"); } });
document.addEventListener("keydown", (e) => {
  const h = e.target.closest && e.target.closest(".st-handle"); if (!h || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
  const wrap = h.parentElement, dur = +wrap.dataset.dur, sc = stScene(), ed = (sc && sc.edit) || {};
  const cur = h.dataset.h === "in" ? (ed.voice_in || 0) : (ed.voice_out || dur);
  stSetTrim(h.dataset.h, cur + (e.key === "ArrowRight" ? 0.1 : -0.1), dur); stChanged("script", "typing"); e.preventDefault();
});

/* ------------------------------------------------------------- library and branding --- */
async function stLoadSide() {
  try { const [m, b] = await Promise.all([api("/api/music"), api("/api/branding")]); ST.lib = m.tracks; ST.brand = b; }
  catch (e) { toast(e.message, true); }
}
async function stPostRaw(url, file) {
  const r = await fetch(url, { method: "POST", headers: { "X-Dashboard": "1", "Content-Type": "application/octet-stream" }, body: file });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Upload failed (${r.status})`);
  return data;
}
const stExt = (f) => audioExt(f.type) || (f.name.split(".").pop() || "").toLowerCase();
function stSaveBrand() {
  clearTimeout(ST.brandTimer);
  ST.brandTimer = setTimeout(guard(async () => {
    const b = ST.brand;
    ST.brand = await api("/api/branding", { method: "PUT", body: { corner: b.corner, size: b.size, opacity: b.opacity, auto: b.auto } });
  }), 500);
}

/* ------------------------------------------------------------------------- events --- */
function stOnInput(e) {
  const t = e.target; if (!t.dataset || !S.draft) return;
  if (t.dataset.stGen !== undefined) { ST.gen[t.dataset.stGen] = t.dataset.stGen === "seconds" ? Number(t.value) : t.value; return; }
  if (t.dataset.stBrand !== undefined && ST.brand) {
    const k = t.dataset.stBrand;
    ST.brand[k] = t.type === "checkbox" ? t.checked : t.tagName === "SELECT" ? t.value : Number(t.value);
    if (t.type === "range") stOut(t);
    stSaveBrand(); return;
  }
  if (t.dataset.sb !== undefined) {
    if (t.dataset.sb === "style") ST.planStyle = t.value;
    if (S.sbDraft) { stSbAssign(t.dataset.sb, t.value); stSaveSb(); }
    return;
  }
  if (t.dataset.stChar !== undefined) {
    if (e.type === "input") return;
    const sc = stScene(), id = t.dataset.stChar, list = new Set(sc.visual.chars || []);
    t.checked ? list.add(id) : list.delete(id);
    sc.visual.chars = (S.sbDraft || S.job.storyboard).characters.map((c) => c.id).filter((c) => list.has(c));
    stChanged("script", "step"); return;
  }
  if (t.dataset.stPlanslides !== undefined) { ST.planSlides = t.checked; return; }
  if (t.dataset.stLicense !== undefined) return; // saved on change
  if (t.dataset.st === undefined) return;
  if (e.type === "input" && (t.tagName === "SELECT" || t.type === "checkbox" || t.type === "radio")) return;
  stAssign(t.dataset.st, stCast(t));
  if (t.type === "range") stOut(t);
  if (t.dataset.st === "scene.narration" || t.dataset.st === "scene.pause_after" || t.dataset.st === "scene.repeat") stUpdateChip();
  if (t.dataset.rerender) stRender();
  const typing = t.tagName === "TEXTAREA" || ["text", "range", "number"].includes(t.type);
  stChanged(t.dataset.st.startsWith("g.") ? "studio" : "script", typing ? "typing" : "step");
  if (t.dataset.st === "scene.edit.voice_in" || t.dataset.st === "scene.edit.voice_out") {
    const wrap = $(".st-wavewrap"), sc = stScene(); if (wrap && wrap.dataset.dur) stPlaceHandles(wrap, sc, +wrap.dataset.dur);
  }
}
main.addEventListener("input", stOnInput);
main.addEventListener("change", guard(async (e) => {
  const t = e.target; if (!t.dataset) return;
  if (t.dataset.stJob) {  // captions and music on/off are stored with the job, not the studio settings
    await stFlush();
    S.job = await api(`/api/jobs/${S.jobId}/settings`, { method: "PUT", body: { [t.dataset.stJob]: t.checked } }); toast("Saved");
    return;
  }
  if (t.dataset.stVolauto !== undefined) {
    const kindVol = ((S.options.kinds || {})[S.job.kind] || {}).music_volume ?? 0.12;
    stAssign("g.audio.music.volume", t.checked ? null : kindVol); stChanged("studio", "step"); stRender(); return;
  }
  if (t.dataset.stLicense !== undefined) {
    const r = await api(`/api/music/${enc(t.dataset.stLicense)}/license`, { method: "PUT", body: { note: t.value } });
    ST.lib = r.tracks; stRender(); toast("Licence note saved"); return;
  }
  if (t.dataset.stMusic !== undefined && t.files && t.files[0]) {
    const f = t.files[0]; toast("Adding the track...");
    await stPostRaw(`/api/music?name=${enc(f.name)}`, f);
    ST.lib = (await api("/api/music")).tracks; stRender(); toast("Track added. Add a licence note below it."); return;
  }
  if (t.dataset.sbRef !== undefined && t.files && t.files[0]) {
    const f = t.files[0]; toast("Adding the picture...");
    const j = await stPostRaw(`/api/jobs/${S.jobId}/characters/${enc(t.dataset.sbRef)}/ref?ext=${enc(stExt(f))}`, f);
    S.job = j; S.sbDraft = clone(j.storyboard); stRender(); toast("Character sheet set. New pictures will follow it."); return;
  }
  if (t.dataset.stLogo !== undefined && t.files && t.files[0]) {
    ST.brand = await stPostRaw("/api/branding/logo", t.files[0]); ST.logoV++; stRender(); toast("Logo saved"); return;
  }
  if (t.dataset.stMedia !== undefined && t.files && t.files[0]) {
    const f = t.files[0]; toast("Adding your file...");
    const ext = stExt(f), r = await stPostRaw(`/api/jobs/${S.jobId}/media?ext=${enc(ext)}`, f);
    stAssign("scene.edit.bg_file", r.bg_file); stChanged("script", "step"); stRender(); toast("Your file is the picture for this scene"); return;
  }
  stOnInput(e);
}));
Object.assign(actions, {
  openStudio: () => openStudio(),
  stPlan: guard(async () => {
    const sb = S.sbDraft || S.job.storyboard;
    if (sb && !confirm("Plan the pictures again? Scene descriptions are rewritten; characters with the same name keep their sheets.")) return;
    await stFlush();
    if (S.dirtyScript) return;
    ST.planning = true; stRender();
    try {
      const j = await api(`/api/jobs/${S.jobId}/storyboard/plan`, { method: "POST", body: { style: ST.planStyle || (sb && sb.style) || "", include_slides: ST.planSlides, style_note: (S.sbDraft && S.sbDraft.style_note) || "" } });
      S.job = j; S.draft = clone(j.script); S.dirtyScript = false; S.sbDraft = clone(j.storyboard); stHistInit();
      toast("Pictures planned. Check the characters, then press Draw.");
    } finally { ST.planning = false; stRender(); }
  }),
  stDraw: guard(async (t) => {
    await stFlush();
    if (S.dirtyScript) return;
    const all = t.dataset.all === "1";
    if (all && !confirm("Redraw every AI picture? The current pictures stay available as earlier versions.")) return;
    S.job = await api(`/api/jobs/${S.jobId}/images`, { method: "POST", body: { only_missing: !all, scenes: all ? S.draft.scenes.filter((s) => s.visual.type === "image").map((s) => s.id) : null } });
    stRender(); toast("Drawing started. You can keep editing.");
  }),
  stDrawScene: guard(async () => {
    await stFlush();
    if (S.dirtyScript) return;
    const sc = stScene();
    S.job = await api(`/api/jobs/${S.jobId}/images`, { method: "POST", body: { scenes: [sc.id], only_missing: false } });
    stRender(); toast("Drawing this scene...");
  }),
  stDrawSheet: guard(async (t) => {
    clearTimeout(ST.sbTimer);
    if (S.sbDraft) S.job = await api(`/api/jobs/${S.jobId}/storyboard`, { method: "PUT", body: { storyboard: S.sbDraft } });
    S.job = await api(`/api/jobs/${S.jobId}/images`, { method: "POST", body: { scenes: [], characters: [t.dataset.cid] } });
    stRender(); toast("Drawing the character sheet. Redraw the scenes afterwards so they follow it.");
  }),
  stStopDraw: guard(async () => { S.job = await api(`/api/jobs/${S.jobId}/images/cancel`, { method: "POST" }); toast("Stopping after the current picture"); }),
  stUseImg: guard(async (t) => {
    S.job = await api(`/api/jobs/${S.jobId}/images/${enc(stScene().id)}/use`, { method: "POST", body: { file: t.dataset.file } }); stRender();
  }),
  stAddChar: () => {
    const used = new Set(S.sbDraft.characters.map((c) => c.id));
    const id = Array.from({ length: 99 }, (_, k) => `c${k + 1}`).find((x) => !used.has(x));
    S.sbDraft.characters.push({ id, name: "नया पात्र", name_en: "", look: "Describe the look: age, face, hair, clothing and colours.", ref: "" });
    stSaveSb(); stRender();
  },
  stDelChar: (t) => {
    const c = S.sbDraft.characters[+t.dataset.n]; if (!c || !confirm(`Remove ${c.name}?`)) return;
    S.sbDraft.characters.splice(+t.dataset.n, 1); stSaveSb(); stRender();
  },
  stSelect: (t) => { ST.scene = +t.dataset.i; stRender(); },
  stUndo, stRedo,
  stMove: (t) => stReorder(ST.scene, ST.scene + +t.dataset.d),
  stDup: () => {
    const copy = clone(stScene()); delete copy.id; delete copy.personal; delete copy.hint;
    S.draft.scenes.splice(ST.scene + 1, 0, copy); stStructural(ST.scene + 1);
  },
  stSplit: stSplitScene, stMerge: stMergeScene,
  stDelete: () => {
    if (S.draft.scenes.length <= 1) return;
    S.draft.scenes.splice(ST.scene, 1); stStructural(Math.min(ST.scene, S.draft.scenes.length - 1));
  },
  stAddScene: () => { S.draft.scenes.splice(ST.scene + 1, 0, stBlankScene()); stStructural(ST.scene + 1); },
  stRemoveMedia: () => { stAssign("scene.edit.bg_file", null); stChanged("script", "step"); stRender(); },
  stResetLook: () => {
    for (const k of ["look", "motion", "brightness", "saturation"]) stAssign("scene.edit." + k, null);
    stChanged("script", "step"); stRender();
  },
  stPreview: guard(async () => {
    await stFlush();
    if (S.dirtyScript) return;
    const sc = stScene(); if (!sc || !sc.id) { toast("Save the scene first (add some words).", true); return; }
    S.job = await api(`/api/jobs/${S.jobId}/preview`, { method: "POST", body: { scene: sc.id } });
    stRender(); toast("Making the preview. This takes a few seconds.");
  }),
  stExport: guard(async () => {
    await stFlush();
    if (S.dirtyScript || ST.studioDirty) { toast("Some changes are not saved yet. Fix the message next to Undo first.", true); return; }
    await actions.approve();
  }),
  stMakeMp3: guard(async () => {
    await stFlush();
    S.job = await api(`/api/jobs/${S.jobId}/audio-export`, { method: "POST" }); toast("MP3 ready"); stRender();
  }),
  stSaveDefault: guard(async () => {
    await stFlush();
    await api(`/api/jobs/${S.jobId}/studio/default`, { method: "POST" }); toast("New videos will start with these settings");
  }),
  stGenMusic: guard(async () => {
    const g = ST.gen; if (g.busy) return;
    g.busy = true; stRender();
    try {
      const r = await api("/api/music/generate", { method: "POST", body: { prompt: g.prompt, seconds: g.seconds } });
      ST.lib = r.tracks; toast("Track added to the library. Pick it under Track.");
    } finally { g.busy = false; stRender(); }
  }),
  stMusicDelete: guard(async (t) => {
    const name = t.dataset.name; if (!confirm(`Delete "${name}" from the music library?`)) return;
    ST.lib = (await api(`/api/music/${enc(name)}`, { method: "DELETE" })).tracks;
    if (S.studioDraft.audio.music.track === name) { stAssign("g.audio.music.track", ""); stChanged("studio", "step"); }
    stRender();
  }),
  stLogoDelete: guard(async () => {
    if (!confirm("Remove the channel logo?")) return;
    ST.brand = await api("/api/branding/logo", { method: "DELETE" }); stRender();
  }),
});
// keep the panels the owner opened open
main.addEventListener("toggle", (e) => {
  const d = e.target; if (!d.dataset || !d.dataset.panel) return;
  d.open ? ST.open.add(d.dataset.panel) : ST.open.delete(d.dataset.panel);
}, true);
// drag the scene chips to reorder them
const stChipOf = (e) => e.target.closest && e.target.closest(".st-chip");
main.addEventListener("dragstart", (e) => {
  const c = stChipOf(e); if (!c) return;
  ST.drag = +c.dataset.i; e.dataTransfer.effectAllowed = "move";
  try { e.dataTransfer.setData("text/plain", String(ST.drag)); } catch (_) { /* some browsers need this */ }
});
main.addEventListener("dragover", (e) => { const c = stChipOf(e); if (c && ST.drag >= 0) { e.preventDefault(); c.classList.add("drop"); } });
main.addEventListener("dragleave", (e) => { const c = stChipOf(e); if (c) c.classList.remove("drop"); });
main.addEventListener("drop", (e) => {
  const c = stChipOf(e); if (!c || ST.drag < 0) return;
  e.preventDefault(); const from = ST.drag; ST.drag = -1; stReorder(from, +c.dataset.i);
});
main.addEventListener("dragend", () => { ST.drag = -1; document.querySelectorAll(".st-chip.drop").forEach((c) => c.classList.remove("drop")); });

/* --------------------------------------------------------------- hooks for the page --- */
function studioAfterRender() {
  const chip = $('.st-chip[aria-pressed="true"]'); if (chip && chip.scrollIntoView) chip.scrollIntoView({ block: "nearest", inline: "nearest" });
  stDrawWaves();
}
function studioRefresh() { if (S.tab === "studio") stRender(); }
function studioReset() {
  clearTimeout(ST.timer); ST.scene = 0; ST.hist = null; ST.studioDirty = false; ST.version = 0; ST.status = ""; ST.statusErr = false;
  S.studioDraft = null; S.sbDraft = null; clearTimeout(ST.sbTimer); ST.planning = false; ST.lastImgKey = "";
}
async function studioEnter() {
  if (!S.draft) return;
  if (!S.sbDraft && S.job && S.job.storyboard) S.sbDraft = clone(S.job.storyboard);
  await stLoadSide();
  stRender();
}
