// Browser test of the Studio tab. Needs Node, the playwright-core package and Edge or Chrome.
//
//   1. Start a separate dashboard with its own data folder so test videos stay out of your list:
//        set YT_DATA_DIR=%TEMP%yt_ui_test   (Linux/macOS: export YT_DATA_DIR=/tmp/yt_ui_test)
//        python run.py --no-browser --port 8799
//   2. npm install playwright-core   (in any folder), then:
//        node tests/browser_studio.js <folder for screenshots>
//   BROWSER can point to Chrome or Edge; tests/photo.png is used as the uploaded photo.
const { chromium } = require(process.env.PLAYWRIGHT || "playwright-core");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:8799";
const OUT = process.argv[2] || require("os").tmpdir();
const H = { "X-Dashboard": "1", "Content-Type": "application/json" };
let failures = 0;
const check = (ok, msg) => { console.log((ok ? "PASS " : "FAIL ") + msg); if (!ok) failures++; };
const api = async (p, opts = {}) => {
  const r = await fetch(BASE + p, { ...opts, headers: { ...H, ...(opts.headers || {}) } });
  return r.json();
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const script = { title: "स्टूडियो परीक्षण", description: "d", tags: ["t"], thumbnail_text: "परीक्षण", scenes: [
    { narration: "पहला दृश्य यहाँ है।", visual: { type: "stock", query: "river" } },
    { narration: "दूसरा दृश्य एक स्लाइड है।", visual: { type: "slide", heading: "उपनिषद", text: "सत्यमेव जयते" } },
    { narration: "तीसरा दृश्य अंत है।", visual: { type: "stock", query: "temple" } },
  ] };
  const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ topic: "studio browser test", kind: "upanishad_story", format: "long", minutes: 1, script_json: JSON.stringify(script) }) });
  check(job.id && job.studio && job.studio.audio.master === true, "new video starts with studio settings");

  const browser = await chromium.launch({ executablePath: process.env.BROWSER || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "/usr/bin/google-chrome"), headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/404|favicon/.test(m.text())) errors.push("console: " + m.text()); });
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.locator(`.job[data-id="${job.id}"]`).click();
  await page.locator('.tab[data-tab="studio"]').click();
  await page.waitForSelector(".st-chip");
  check(await page.locator(".st-chip").count() === 3, "scene strip shows 3 scenes");
  await page.screenshot({ path: path.join(OUT, "studio_1.png"), fullPage: true });

  // scene 2: a look, slow zoom and on-screen text; then autosave
  await page.locator('.st-chip[data-i="1"]').click();
  check(await page.locator(".st-head h2").innerText() === "Scene 2 of 3", "selecting a scene opens its editor");
  await page.selectOption('select[data-st="scene.edit.look"]', "warm");
  await page.selectOption('select[data-st="scene.edit.motion"]', "zoom_in");
  await page.fill('input[data-st="scene.edit.overlay.text"]', "अध्याय दो");
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  let j = await api(`/api/jobs/${job.id}`);
  const e2 = j.script.scenes[1].edit || {};
  check(e2.look === "warm" && e2.motion === "zoom_in" && e2.overlay && e2.overlay.text === "अध्याय दो", "scene edits autosaved to the server");

  // undo the overlay text, redo it
  await page.locator('[data-act="stUndo"]').click();
  check((await page.inputValue('input[data-st="scene.edit.overlay.text"]')) === "", "undo removes the last change");
  await page.locator('[data-act="stRedo"]').click();
  check((await page.inputValue('input[data-st="scene.edit.overlay.text"]')) === "अध्याय दो", "redo puts it back");

  // reorder with the button, keeping the ids
  const ids = j.script.scenes.map((s) => s.id);
  await page.locator('[data-act="stMove"][data-d="-1"]').click();
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  j = await api(`/api/jobs/${job.id}`);
  check(JSON.stringify(j.script.scenes.map((s) => s.id)) === JSON.stringify([ids[1], ids[0], ids[2]]), "move earlier reorders and keeps scene ids");
  check(await page.locator(".st-head h2").innerText() === "Scene 1 of 3", "the moved scene stays selected");

  // reorder by drag and drop: chip 3 onto chip 1
  await page.locator('.st-chip[data-i="2"]').dragTo(page.locator('.st-chip[data-i="0"]'));
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  j = await api(`/api/jobs/${job.id}`);
  check(j.script.scenes[0].id === ids[2], "drag and drop reorders scenes");

  // split scene at the cursor, then merge it back
  await page.locator('.st-chip[data-i="0"]').click();
  const before = j.script.scenes[0].narration;
  await page.locator("#stNarr").evaluate((el) => { el.focus(); el.setSelectionRange(8, 8); });
  await page.locator('[data-act="stSplit"]').click();
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  j = await api(`/api/jobs/${job.id}`);
  check(j.script.scenes.length === 4 && (j.script.scenes[0].narration + " " + j.script.scenes[1].narration) === before, "split at the cursor makes two scenes without cutting a word");
  await page.locator('[data-act="stMerge"]').click();
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  j = await api(`/api/jobs/${job.id}`);
  check(j.script.scenes.length === 3 && j.script.scenes[0].narration === before, "merge with next joins them again, text unchanged");

  // own photo for scene 1
  await page.setInputFiles("input[data-st-media]", path.join(__dirname, "photo.png"));
  await page.waitForSelector(".st-own img", { timeout: 20000 });
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  j = await api(`/api/jobs/${job.id}`);
  check(/^[0-9a-f]{12}\.png$/.test((j.script.scenes[0].edit || {}).bg_file || ""), "own photo uploaded and attached to the scene");

  // video-wide settings
  await page.selectOption('select[data-st="g.captions.style"]', "highlight");
  await page.selectOption('select[data-st="g.audio.voice_polish"]', "studio");
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  j = await api(`/api/jobs/${job.id}`);
  check(j.studio.captions.style === "highlight" && j.studio.audio.voice_polish === "studio", "video-wide settings autosaved");

  // preview the selected scene (real ffmpeg, AI voice needs internet; fall back is checked)
  await page.locator('[data-act="stPreview"]').click();
  // The page saves pending edits before it asks for the preview, so wait for the result rather than
  // checking straight away: done when a preview exists and nothing is running, or an error is shown.
  let ok = false;
  for (let k = 0; k < 120; k++) {
    j = await api(`/api/jobs/${job.id}`);
    if (j.preview && !j.previewing) { ok = true; break; }
    if (!j.previewing && /Preview failed/.test(j.error || "")) break;
    await sleep(1000);
  }
  if (ok) {
    await page.waitForSelector(".st-preview video", { timeout: 10000 });
    check(true, "scene preview rendered and shown");
    await page.waitForFunction(() => document.querySelector(".st-wavewrap .st-wavenote").style.display === "none", null, { timeout: 15000 }).then(
      () => check(true, "the scene's sound is drawn after the preview"), () => check(false, "the scene's sound is drawn after the preview"));
  } else {
    check(/Preview failed/.test(j.error || ""), "preview reports a clear error when it cannot run: " + (j.error || "none"));
  }
  await page.screenshot({ path: path.join(OUT, "studio_2.png"), fullPage: true });

  // narrow screen: the layout still works
  await page.setViewportSize({ width: 420, height: 900 });
  await sleep(300);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 2, "no sideways scrolling on a phone-width screen (" + overflow + "px)");
  await page.screenshot({ path: path.join(OUT, "studio_3_mobile.png"), fullPage: false });

  check(errors.length === 0, "no script errors in the page " + JSON.stringify(errors));
  await browser.close();
  console.log(failures ? `${failures} FAILED` : "ALL PASSED");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
