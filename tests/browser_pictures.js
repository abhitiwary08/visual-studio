// Browser test of AI pictures in the Studio. Uses the free placeholder pictures, so it costs nothing.
//
//   1. Start a separate dashboard with its own data folder and placeholder pictures:
//        set YT_DATA_DIR=%TEMP%\yt_ui_test & set IMAGES_FORCE_PROVIDER=placeholder
//        python run.py --no-browser --port 8799
//   2. npm install playwright-core, then:   node tests/browser_pictures.js <folder for screenshots>
// The AI writer is not called: the plan is written through the API, as the writer would.
const { chromium } = require(process.env.PLAYWRIGHT || "playwright-core");
const path = require("path");

const BASE = "http://127.0.0.1:8799";
const OUT = process.argv[2] || require("os").tmpdir();
const H = { "X-Dashboard": "1", "Content-Type": "application/json" };
let failures = 0;
const check = (ok, msg) => { console.log((ok ? "PASS " : "FAIL ") + msg); if (!ok) failures++; };
const api = async (p, opts = {}) => (await fetch(BASE + p, { ...opts, headers: { ...H, ...(opts.headers || {}) } })).json();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const look = { c1: "A 10-year-old boy, dark wavy hair in a small top knot, saffron dhoti, rudraksha bead on a thread.",
                 c2: "A calm dark-skinned god, golden crown, deep green robes, kind eyes." };
  const script = { title: "चित्र परीक्षण", scenes: [
    { narration: "नचिकेता नदी के किनारे बैठा था।", visual: { type: "image", prompt: "Nachiketa sits by a river at dawn, wide shot.", chars: ["c1"] } },
    { narration: "यम उसके सामने प्रकट हुए।", visual: { type: "image", prompt: "Yama appears before Nachiketa, medium shot.", chars: ["c2", "c1"] } },
    { narration: "उत्तिष्ठत जाग्रत", visual: { type: "slide", text: "उत्तिष्ठत जाग्रत" } },
  ] };
  const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ topic: "pictures browser test", kind: "upanishad_story", format: "long", minutes: 1, script_json: JSON.stringify(script) }) });
  await api(`/api/jobs/${job.id}/storyboard`, { method: "PUT", body: JSON.stringify({ storyboard: { style: "miniature", setting: "Vedic India by a river.",
    characters: [{ id: "c1", name: "नचिकेता", name_en: "Nachiketa", look: look.c1 }, { id: "c2", name: "यम", name_en: "Yama", look: look.c2 }] } }) });

  const browser = await chromium.launch({ executablePath: process.env.BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE + "/", { waitUntil: "networkidle" });

  // Setup shows the picture providers
  await page.locator("#setupBtn").click();
  await page.waitForSelector('[aria-label="AI pictures"]');
  check(await page.locator('input[name="imgProv"]').count() === 3, "Setup lists the picture providers");
  // the New video form offers AI pictures
  await page.locator("#newBtn").click();
  check(await page.locator('input[data-f="ai_images"]').count() === 1, "New video form offers AI pictures");

  // Studio: the pictures panel with both characters, and the draw button with a count
  await page.locator(`.job[data-id="${job.id}"]`).click();
  await page.locator('.tab[data-tab="studio"]').click();
  await page.waitForSelector('[data-panel="pictures"]');
  check(await page.locator(".st-charcard").count() === 2, "the pictures panel shows the two characters");
  const drawBtn = page.locator('[data-act="stDraw"][data-all="0"]');
  check(/Draw missing \(2\)/.test(await drawBtn.innerText()) && /free/.test(await drawBtn.innerText()), "draw button counts missing pictures and says it is free");

  // the scene editor has the AI picture type, its description and character boxes
  check(await page.locator('input[name="stvt"][value="image"]').isChecked(), "scene 1 is an AI picture");
  check(await page.locator('input[data-st-char="c1"]').isChecked() && !(await page.locator('input[data-st-char="c2"]').isChecked()), "character boxes match the scene");
  await page.locator('input[data-st-char="c2"]').check();
  await page.waitForFunction(() => document.querySelector("#stStatus").textContent === "All changes saved", null, { timeout: 15000 });
  let j = await api(`/api/jobs/${job.id}`);
  check(JSON.stringify(j.script.scenes[0].visual.chars) === JSON.stringify(["c1", "c2"]), "ticking a character saves it in character order");
  await page.locator('input[data-st-char="c2"]').uncheck();

  // draw everything: sheets first, then scenes; progress shows live; thumbnails appear
  await drawBtn.click();
  let sawProgress = false;
  for (let k = 0; k < 120; k++) {
    if (await page.locator("#stImgProgress").count()) sawProgress = true;
    j = await api(`/api/jobs/${job.id}`);
    if (!j.imaging && j.scene_images && Object.keys(j.scene_images).length === 2) break;
    await sleep(300);
  }
  check(Object.keys(j.scene_images || {}).length === 2 && j.storyboard.characters.every((c) => c.ref), "both character sheets and both scene pictures were drawn");
  check(sawProgress, "progress was shown while drawing");
  await page.waitForSelector(".st-chip .st-thumb", { timeout: 10000 });
  check(await page.locator(".st-chip .st-thumb").count() === 2, "scene strip shows the pictures");
  check(await page.locator(".st-charcard img").count() === 2, "character cards show their sheets");
  await page.waitForSelector(".st-bigpic");
  check(true, "the scene shows its picture");

  // redraw scene 1: a second version appears and the first can be chosen again
  const first = j.scene_images[j.script.scenes[0].id].file;
  await page.fill("#stPrompt", "Nachiketa walks along the river, close-up, golden light.");
  await page.locator('[data-act="stDrawScene"]').click();
  for (let k = 0; k < 60; k++) {
    j = await api(`/api/jobs/${job.id}`);
    if (!j.imaging && j.scene_images[j.script.scenes[0].id].file !== first) break;
    await sleep(300);
  }
  check(j.script.scenes[0].visual.prompt.startsWith("Nachiketa walks"), "the edited description was saved before drawing");
  await page.waitForSelector(".st-versions button", { timeout: 10000 });
  check(await page.locator(".st-versions button").count() === 2, "earlier versions are listed");
  await page.locator(`.st-versions button[data-file="${first}"]`).click();
  await sleep(800);
  j = await api(`/api/jobs/${job.id}`);
  check(j.scene_images[j.script.scenes[0].id].file === first, "choosing an earlier version uses it again");

  // editing a character's look saves the character bible
  await page.locator('textarea[data-sb="characters.0.look"]').fill(look.c1 + " Carries a small water pot.");
  await sleep(1500);
  j = await api(`/api/jobs/${job.id}`);
  check(/water pot/.test(j.storyboard.characters[0].look) && j.storyboard.characters[0].ref, "character edits save and keep the sheet");

  await page.screenshot({ path: path.join(OUT, "pictures.png"), fullPage: true });
  check(errors.length === 0, "no script errors " + JSON.stringify(errors));
  await browser.close();
  console.log(failures ? `${failures} FAILED` : "ALL PASSED");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
