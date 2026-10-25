#!/usr/bin/env node
/**
 * Capture the site's own screenshots for the README.
 *
 *   node tools/capture-shots.mjs                # writes ../shots/site-*.jpg
 *   node tools/capture-shots.mjs --only hero    # just one, while working on it
 *   node tools/capture-shots.mjs --base http://127.0.0.1:8099/
 *
 * Needs puppeteer-core and Chrome, like the film renderer. Serve `site/`
 * first (python -m http.server 8099 --directory site).
 */
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "..", "shots");
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const BASE = opt("--base", "http://127.0.0.1:8099/");
const CHROME = opt("--chrome", process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe");

await mkdir(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  protocolTimeout: 300000,
  args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--disable-lcd-text",
         "--font-render-hinting=none", "--disable-background-timer-throttling"]
});

async function shot({ file, width = 1440, height = 900, scroll = 0, wait = 1400, scale = 1, seek = null }) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: scale });
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  // let the hero's letters land, then go where we were asked
  await new Promise(r => setTimeout(r, 1800));
  await page.evaluate((y) => {
    document.documentElement.style.scrollBehavior = "auto";
    document.documentElement.scrollTop = y;
    window.dispatchEvent(new Event("scroll"));
  }, scroll);
  await new Promise(r => setTimeout(r, wait));
  if (seek !== null) {
    // park the reel on a frame worth photographing instead of the opening hold;
    // the player resumes on scroll, so pause and seek as late as possible
    await page.evaluate(async (t) => {
      const v = document.querySelector(".reel video");
      if (!v) return;
      v.pause();
      if (v.readyState < 1) await new Promise(r => v.addEventListener("loadedmetadata", r, { once: true }));
      v.currentTime = t;
      await new Promise(r => { v.addEventListener("seeked", r, { once: true }); setTimeout(r, 800); });
    }, seek);
    await new Promise(r => setTimeout(r, 350));
  }
  await page.screenshot({ path: path.join(OUT, file), type: "jpeg", quality: 88 });
  console.log("wrote shots/" + file);
  await page.close();
}

const at = async (page, sel, offset = 0) => page.evaluate((s, o) => {
  const el = document.querySelector(s);
  return Math.round(el.getBoundingClientRect().top + scrollY + o);
}, sel, offset);

// a helper page just to read section offsets once
const probe = await browser.newPage();
await probe.setViewport({ width: 1440, height: 900 });
await probe.goto(BASE, { waitUntil: "networkidle2" });
await probe.evaluate(() => document.fonts.ready);
const y = {
  film: await at(probe, "#film", -14),
  what: await at(probe, "#what", 40),
  run: await at(probe, "#run", 500),
  studio: await at(probe, "#studio", 60),
  paper: await at(probe, "#paper", 40),
  numbers: await at(probe, "#numbers", 60),
  start: await at(probe, "#start", 60)
};
await probe.close();
console.log(y);

// --only hero,film,... re-shoots just those; handy while working on one
const only = opt("--only", null)?.split(",");
const want = (name) => !only || only.includes(name);

if (want("hero")) await shot({ file: "site-hero.jpg", scroll: 0, wait: 1200 });
if (want("film")) await shot({ file: "site-film.jpg", scroll: y.film, wait: 1200, seek: 11.9 });
if (want("run")) await shot({ file: "site-run.jpg", scroll: y.run, wait: 1800 });
if (want("studio")) await shot({ file: "site-studio.jpg", scroll: y.studio, wait: 1600 });
if (want("paper")) await shot({ file: "site-paper.jpg", scroll: y.paper, wait: 2400 });
if (want("start")) await shot({ file: "site-start.jpg", scroll: y.start, wait: 1500 });
if (want("mobile")) await shot({ file: "site-mobile.jpg", width: 390, height: 844, scroll: 0, wait: 1400 });

await browser.close();
console.log("done");
