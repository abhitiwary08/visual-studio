/* Create page: thumbnail, speech, music, scene picture and video clip generators. Loaded after index.html's script. */
const CR = {
  tool: "thumbnail", info: null, files: [], busy: "", note: "", filter: "all", latest: null, err: "",
  f: { title: "", tprompt: "", text: "", voice: "", mprompt: "", mseconds: 60, sprompt: "", vprompt: "", aspect: "16:9" },
  bg: null,
};
const CR_ICON = {
  thumbnail: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>',
  speech: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  scene: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M5 19l1-2M19 19l-1-2"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/>',
};
const CR_TOOLS = {
  thumbnail: ["Thumbnail", "Title text on an AI, uploaded or plain background."],
  speech: ["Speech", "Turn any text into a voice-over."],
  music: ["Background music", "Instrumental tracks from a text prompt."],
  scene: ["Scene picture", "One AI picture in any shape."],
  video: ["Video clip", "An 8-second AI clip from Google Veo."],
};
const CR_KIND_OF = { thumbnail: "thumbnail", speech: "speech", music: "music", scene: "scene", video: "video" };

async function createEnter() {
  renderCreate();
  try { const r = await api("/api/create"); CR.info = r; CR.files = r.files; if (!CR.f.voice) CR.f.voice = r.voice; }
  catch (e) { toast(e.message, true); }
  if (S.view === "create") renderCreate();
}

function crField(label, id, html, hint) {
  return `<div class="field"><label class="f" for="${id}">${label}</label>${html}${hint ? `<p class="hint">${hint}</p>` : ""}</div>`;
}
function crInput(key, id, ph, rows) {
  return rows
    ? `<textarea id="${id}" data-cr="${key}" rows="${rows}" placeholder="${esc(ph)}">${esc(CR.f[key])}</textarea>`
    : `<input id="${id}" type="text" data-cr="${key}" value="${esc(CR.f[key])}" placeholder="${esc(ph)}">`;
}
function crAspect(opts) {
  return `<select id="crAsp" data-cr="aspect">${opts.map(([v, l]) => `<option value="${v}" ${CR.f.aspect === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
}
function crGo(act, label, disabled) {
  const b = CR.busy === act;
  return `<div class="actions"><button class="btn primary ${b ? "busy" : ""}" type="button" data-act="${act}" ${CR.busy || disabled ? "disabled" : ""}>${label}</button>
    ${b ? `<div class="progress-inline" role="status"><span class="bar"></span><span id="crNote">${esc(CR.note || "Working...")}</span></div>` : ""}</div>
    ${CR.err && !CR.busy ? `<div class="banner err" role="alert">${esc(CR.err)}</div>` : ""}`;
}
function crForm() {
  const i = CR.info || {}, f = CR.f;
  switch (CR.tool) {
    case "thumbnail": return `
      ${crField("Title on the thumbnail", "crTitle", crInput("title", "crTitle", "आत्मा क्या है?"))}
      ${crField("AI background (optional)", "crTprompt", crInput("tprompt", "crTprompt", "A glowing diya on a river at dawn, cinematic, no text", 2),
        i.images_ready ? `Drawn with ${esc(i.image_label || "the image maker")}.` : "No image key yet, so a placeholder card is used. Set one up under Setup and checks.")}
      <div class="field"><label class="f">Or use your own picture</label>
        <label class="btn secondary file">${CR.bg ? esc(CR.bg.name) : "Choose a picture"}<input type="file" accept="image/*" data-cr-bg></label>
        ${CR.bg ? `<button class="btn quiet" type="button" data-act="crClearBg">Remove</button>` : ""}</div>
      ${crGo("crThumb", "Make the thumbnail")}`;
    case "speech": return `
      ${crField("Words to speak", "crText", crInput("text", "crText", "यहाँ हिंदी में लिखें...", 6), "Uses the voice engine chosen in Setup and checks.")}
      ${crField("Voice", "crVoice", `<select id="crVoice" data-cr="voice">${Object.entries(i.voices || {}).map(([k, v]) => `<option value="${esc(k)}" ${f.voice === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`)}
      ${crGo("crSpeech", "Make the voice-over")}`;
    case "music": return `
      ${crField("Describe the music", "crMprompt", crInput("mprompt", "crMprompt", "Calm devotional instrumental, soft flute and tanpura, no vocals", 3), "Uses credits with the music maker chosen in Setup and checks.")}
      ${crField("Length (seconds)", "crMsecs", `<input id="crMsecs" type="number" min="10" max="300" step="5" data-cr="mseconds" value="${f.mseconds}">`)}
      ${crGo("crMusic", "Generate the music")}`;
    case "scene": return `
      ${crField("Describe the picture", "crSprompt", crInput("sprompt", "crSprompt", "A sage meditating under a banyan tree at sunrise, warm painterly light", 3),
        `Drawn with ${esc(i.image_label || "the image maker")}.`)}
      ${crField("Shape", "crAsp", crAspect([["16:9", "Wide 16:9 (YouTube)"], ["9:16", "Tall 9:16 (Shorts, Reels)"], ["1:1", "Square 1:1"]]))}
      ${crGo("crScene", "Draw the picture")}`;
    default: return `
      ${i.video_ready === false ? `<div class="banner">Add your Gemini key under Setup and checks first. Veo needs a paid Gemini API plan.</div>` : ""}
      ${crField("Describe the clip", "crVprompt", crInput("vprompt", "crVprompt", "Slow aerial shot over the Ganga at dawn, mist, temple bells, golden light", 3), "Takes a minute or two. You can leave this page and come back; finished clips appear in your creations.")}
      ${crField("Shape", "crAsp", crAspect([["16:9", "Wide 16:9"], ["9:16", "Tall 9:16"]]))}
      ${crGo("crVideo", "Make the clip", i.video_ready === false)}`;
  }
}
function crMedia(name, big) {
  const url = `/creations/${enc(name)}`;
  if (name.endsWith(".mp4")) return `<video controls preload="metadata" src="${url}"></video>`;
  if (name.endsWith(".mp3")) return `<audio controls ${big ? "autoplay" : 'preload="none"'} src="${url}"></audio>`;
  return `<img src="${url}" alt="${esc(name)}" loading="lazy">`;
}
function crPreview() {
  const n = CR.latest && CR.files.find((x) => x.name === CR.latest);
  if (!n) return `<div class="preview-pane"><div><strong style="display:block;color:var(--ink);margin-bottom:4px">Your result appears here</strong>Fill in the form and press the button.</div></div>`;
  const url = `/creations/${enc(n.name)}`;
  return `<div class="preview-pane has"><div style="width:100%">${crMedia(n.name, true)}
    <div class="actions" style="justify-content:center;margin-top:12px"><a class="btn secondary sm" href="${url}" download="${esc(n.name)}">Download</a></div></div></div>`;
}
function crGallery() {
  const kinds = ["all", "thumbnail", "scene", "speech", "music", "video"];
  const list = CR.files.filter((x) => CR.filter === "all" || x.kind === CR.filter);
  const label = { all: "All", thumbnail: "Thumbnails", scene: "Pictures", speech: "Voice", music: "Music", video: "Clips" };
  return `<div class="filters" role="group" aria-label="Filter">${kinds.map((k) => `<button class="chip" type="button" aria-pressed="${CR.filter === k}" data-act="crFilter" data-k="${k}">${label[k]}</button>`).join("")}</div>
  ${list.length ? `<ul class="gallery">${list.map((x) => `<li class="g-item"><div class="m">${crMedia(x.name, false)}</div>
    <div class="b"><div class="n"><strong title="${esc(x.name)}">${esc(x.name)}</strong><span>${x.size_mb} MB</span></div>
    <span><a class="btn quiet sm" href="/creations/${enc(x.name)}" download="${esc(x.name)}">Save</a><button class="btn quiet danger sm m0" type="button" data-act="crDelete" data-name="${esc(x.name)}">Delete</button></span></div></li>`).join("")}</ul>`
    : `<div class="empty-state"><strong>${CR.files.length ? "Nothing in this filter" : "Nothing created yet"}</strong>Everything you make is saved in <code>data/creations</code>.</div>`}`;
}
function renderCreate() {
  if (!CR.info) { $("#main").innerHTML = `<header class="page-head"><h1>Create</h1></header><div class="skeleton" aria-busy="true"><i></i><i></i><i class="big"></i></div>`; return; }
  $("#main").innerHTML = `
  <header class="page-head"><h1>Create</h1><p class="muted">Make a single thumbnail, voice-over, music track, picture or video clip without starting a whole video.</p></header>
  <div class="tool-grid" role="tablist" aria-label="What to make">${Object.entries(CR_TOOLS).map(([k, [t, d]]) =>
    `<button class="tool-card" type="button" role="tab" aria-selected="${CR.tool === k}" data-act="crTool" data-tool="${k}">
      <span class="ico"><svg viewBox="0 0 24 24" aria-hidden="true">${CR_ICON[k]}</svg></span><strong>${t}</strong><span class="d">${d}</span></button>`).join("")}</div>
  <div class="create-layout">
    <section class="card" style="margin:0" aria-label="${esc(CR_TOOLS[CR.tool][0])}"><h2 style="margin-bottom:14px">${esc(CR_TOOLS[CR.tool][0])}</h2>${crForm()}</section>
    <aside aria-label="Result">${crPreview()}</aside>
  </div>
  <h2 class="mt-3 mb-2">Your creations</h2>
  ${crGallery()}`;
}

main.addEventListener("input", (e) => {
  const k = e.target.dataset && e.target.dataset.cr; if (!k || S.view !== "create") return;
  CR.f[k] = k === "mseconds" ? Number(e.target.value) : e.target.value;
});
main.addEventListener("change", (e) => {
  const t = e.target; if (S.view !== "create" || !t.dataset) return;
  if (t.dataset.crBg !== undefined && t.files && t.files[0]) { CR.bg = t.files[0]; renderCreate(); }
});

async function crRun(act, work, done) {
  CR.busy = act; CR.note = ""; CR.err = ""; renderCreate();
  try {
    const r = await work();
    if (r && r.files) CR.files = r.files;
    CR.latest = (r && (r.name || r.file)) || (CR.files[0] && CR.files[0].name) || null;
    toast(done);
  } catch (e) { CR.err = e.message; throw e; }
  finally { CR.busy = ""; CR.note = ""; if (S.view === "create") renderCreate(); }
}
async function crPollVideo(tid) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 6000));
    if (S.view !== "create") return null;
    const t = await api(`/api/create/task/${tid}`);
    CR.note = t.note || ""; const n = $("#crNote"); if (n) n.textContent = CR.note;
    if (t.status === "done") return t;
    if (t.status === "error") throw new Error(t.error);
  }
}
Object.assign(actions, {
  crTool: (t) => { CR.tool = t.dataset.tool; CR.err = ""; renderCreate(); },
  crFilter: (t) => { CR.filter = t.dataset.k; renderCreate(); },
  crClearBg: () => { CR.bg = null; renderCreate(); },
  crThumb: guard(() => crRun("crThumb", () => CR.bg
    ? stPostRaw(`/api/create/thumbnail-upload?title=${enc(CR.f.title.trim())}`, CR.bg)
    : api("/api/create/thumbnail", { method: "POST", body: { text: CR.f.title, prompt: CR.f.tprompt } }), "Thumbnail ready")),
  crSpeech: guard(() => crRun("crSpeech", () => api("/api/create/speech", { method: "POST", body: { text: CR.f.text, voice: CR.f.voice } }), "Voice-over ready")),
  crMusic: guard(() => crRun("crMusic", () => api("/api/create/music", { method: "POST", body: { prompt: CR.f.mprompt, seconds: CR.f.mseconds } }), "Music ready")),
  crScene: guard(() => crRun("crScene", () => api("/api/create/scene", { method: "POST", body: { prompt: CR.f.sprompt, aspect: CR.f.aspect } }), "Picture ready")),
  crVideo: guard(() => crRun("crVideo", async () => {
    const { task } = await api("/api/create/video", { method: "POST", body: { prompt: CR.f.vprompt, aspect: CR.f.aspect } });
    return crPollVideo(task);
  }, "Clip ready")),
  crDelete: guard(async (t) => {
    if (!confirm(`Delete ${t.dataset.name}?`)) return;
    CR.files = (await api(`/api/create/files/${enc(t.dataset.name)}`, { method: "DELETE" })).files;
    if (CR.latest === t.dataset.name) CR.latest = null;
    renderCreate();
  }),
});
