#!/usr/bin/env node
/**
 * The renderer for "One Run, On Paper".
 *
 *   node render.mjs --stills 0,2.4,6.2,9.4,14.6,17.2 --out stills/
 *   node render.mjs --out ../media/kosika-film.mp4 --fps 30 --workers 4
 *
 * Frames are screenshots of window.render(t) — the composition is a pure
 * function of time — written as JPEG and encoded with ffmpeg. Needs
 * puppeteer-core and a local Chrome. Serve the site first, from site/:
 *
 *   node tools/serve.mjs 8099
 *
 * Flags: --url (default http://127.0.0.1:8099/film/index.html),
 *        --chrome, --w/--h, --crf, --keep-frames, --from/--to (seconds).
 */
import { spawn } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

// puppeteer-core is a dev-only tool: install it anywhere and point NODE_PATH
// (or PUPPETEER_DIR) at it; it is never committed with the site.
const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const flag = (k) => args.includes(k);

const URL_ = opt("--url", "http://127.0.0.1:8099/film/index.html");
const CHROME = opt("--chrome", process.env.CHROME_PATH ||
  "C:/Program Files/Google/Chrome/Application/chrome.exe");
const FPS = Number(opt("--fps", 30));
const WORKERS = Number(opt("--workers", 3));
const OUT = path.resolve(opt("--out", "film.mp4"));
const CRF = opt("--crf", "20");
const QUALITY = Number(opt("--quality", 94));
const STILLS = opt("--stills", null);

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  protocolTimeout: 600000,
  args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--disable-lcd-text",
         "--font-render-hinting=none", "--allow-file-access-from-files",
         "--disable-gpu", "--disable-dev-shm-usage", "--disable-background-timer-throttling"]
});

async function openPage() {
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto(URL_, { waitUntil: "load", timeout: 60000 });
  await page.waitForFunction("window.READY === true", { timeout: 60000 });
  return page;
}

/* The composition is a canvas, so the frame is read straight out of it:
   toDataURL is deterministic and does not wait on the compositor. */
const still = async (page, t, file) => {
  const dataUrl = await page.evaluate((tt, q) => {
    window.render(tt);
    return document.getElementById("c").toDataURL("image/jpeg", q);
  }, t, QUALITY / 100);
  await writeFile(file, Buffer.from(dataUrl.slice(dataUrl.indexOf(",") + 1), "base64"));
};

if (STILLS) {
  const dir = path.resolve(opt("--out", "stills"));
  await mkdir(dir, { recursive: true });
  const page = await openPage();
  const times = STILLS.split(",").map(Number);
  for (const t of times) {
    const file = path.join(dir, `t${t.toFixed(2).replace(".", "_")}.jpg`);
    await still(page, t, file);
    console.log("still", file);
  }
  await browser.close();
  process.exit(0);
}

/* ── full render ──────────────────────────────────────────────────── */
const page0 = await openPage();
const DUR = await page0.evaluate(() => window.DUR);
const from = Number(opt("--from", 0));
const to = Math.min(Number(opt("--to", DUR)), DUR);
const total = Math.round((to - from) * FPS);
const framesDir = path.join(path.dirname(OUT), ".frames-" + path.basename(OUT, ".mp4"));
await rm(framesDir, { recursive: true, force: true });
await mkdir(framesDir, { recursive: true });
await mkdir(path.dirname(OUT), { recursive: true });

console.log(`rendering ${total} frames (${from}s→${to}s @ ${FPS}fps) with ${WORKERS} workers`);
const started = Date.now();
let done = 0;

const pages = [page0];
for (let i = 1; i < WORKERS; i++) pages.push(await openPage());

await Promise.all(pages.map(async (page, w) => {
  for (let i = w; i < total; i += WORKERS) {
    const t = from + i / FPS;
    const file = path.join(framesDir, String(i).padStart(5, "0") + ".jpg");
    await still(page, t, file);
    done++;
    if (done % 40 === 0 || done === total) {
      const el = (Date.now() - started) / 1000;
      console.log(`  ${done}/${total} frames · ${el.toFixed(0)}s · ~${((total - done) * el / done).toFixed(0)}s left`);
    }
  }
}));
await browser.close();

const seconds = ((Date.now() - started) / 1000).toFixed(1);
console.log(`frames done in ${seconds}s; encoding`);

await new Promise((res, rej) => {
  const ff = spawn("ffmpeg", [
    "-y", "-framerate", String(FPS), "-i", path.join(framesDir, "%05d.jpg"),
    "-c:v", "libx264", "-preset", "slow", "-crf", CRF, "-pix_fmt", "yuv420p",
    "-movflags", "+faststart", "-an", OUT
  ], { stdio: ["ignore", "inherit", "inherit"] });
  ff.on("exit", (code) => (code === 0 ? res() : rej(new Error("ffmpeg exit " + code))));
});

if (!flag("--keep-frames")) await rm(framesDir, { recursive: true, force: true });
const { size } = await import("node:fs/promises").then(m => m.stat(OUT));
console.log(`wrote ${OUT} (${(size / 1048576).toFixed(1)} MiB)`);
