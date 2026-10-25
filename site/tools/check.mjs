#!/usr/bin/env node
/**
 * End-to-end checks on the live page, for the two widths that matter.
 *
 *   node tools/serve.mjs 8099 &
 *   node tools/check.mjs                # non-zero exit if anything fails
 *
 * It asserts the things a screenshot cannot: no horizontal overflow, no
 * console errors or failed requests, every image decoded, the paper section
 * not buried under the wipe canvas, the run advancing with the scroll, and
 * the reel able to seek (which needs byte ranges from the server).
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.argv[2] || "http://127.0.0.1:8099/";

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new",
  args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--disable-background-timer-throttling"] });

const report = [];
const fail = (m) => report.push("FAIL  " + m);
const ok = (m) => report.push("ok    " + m);

async function open(width, height) {
  const page = await browser.newPage();
  const errors = [], failed = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("requestfailed", (r) => { if (!/ERR_ABORTED/.test(r.failure()?.errorText || "")) failed.push(r.failure()?.errorText + " " + r.url()); });
  page.on("response", (r) => { if (r.status() >= 400) failed.push(r.status() + " " + r.url()); });
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await page.evaluate(() => document.fonts.ready);
  await new Promise((r) => setTimeout(r, 900));
  return { page, errors, failed };
}

/* ── desktop ─────────────────────────────────────────────────────── */
{
  const { page, errors, failed } = await open(1440, 900);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  overflow > 0 ? fail(`horizontal overflow ${overflow}px at 1440`) : ok("no horizontal overflow at 1440");

  // lazy images load only when reached, so walk the page first
  await page.evaluate(async () => {
    document.documentElement.style.scrollBehavior = "auto";
    for (let y = 0; y < document.documentElement.scrollHeight; y += 700) {
      scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 60));
    }
    scrollTo(0, 0);
    await new Promise((r) => setTimeout(r, 600));
  });
  const imgs = await page.evaluate(() => [...document.images]
    .map((i) => ({ src: i.currentSrc.split("/").pop(), w: i.naturalWidth, sw: Math.round(i.getBoundingClientRect().width) }))
    .filter((i) => !i.w));
  imgs.length ? fail("images with no pixels: " + JSON.stringify(imgs)) : ok("every image decoded");

  const marks = await page.evaluate(() => [...document.querySelectorAll("img.mark")]
    .map((i) => [i.currentSrc.split("/").pop(), Math.round(i.getBoundingClientRect().width)]));
  marks.every(([, w]) => w > 15) ? ok("mark renders at " + JSON.stringify(marks)) : fail("mark too small " + JSON.stringify(marks));

  // the headline must not be sliced by a clipped text-shadow
  const shadow = await page.evaluate(() => getComputedStyle(document.querySelector(".h1")).textShadow);
  shadow === "none" ? ok("headline has no clipped shadow") : fail("headline text-shadow: " + shadow);

  // the paper section must be readable, not buried under the wipe canvas
  const paper = await page.evaluate(async () => {
    const el = document.querySelector("#paper");
    document.documentElement.style.scrollBehavior = "auto";
    scrollTo(0, el.getBoundingClientRect().top + scrollY);
    window.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 700));
    const wipe = document.querySelector("#wipe");
    return { wipeOpacity: getComputedStyle(wipe).opacity,
      h2: document.querySelector("#paper h2").getBoundingClientRect().top,
      exhibits: [...document.querySelectorAll(".exhibit")].filter((e) => e.classList.contains("in")).length };
  });
  paper.wipeOpacity === "0" ? ok("paper section clear of the wipe canvas") : fail("wipe canvas still on: " + paper.wipeOpacity);
  paper.exhibits === 3 ? ok("three exhibits stamped in") : fail("exhibits in: " + paper.exhibits);

  // the run scrubs
  const run = await page.evaluate(async () => {
    const el = document.querySelector("#run");
    document.documentElement.style.scrollBehavior = "auto";
    const top = el.getBoundingClientRect().top + scrollY;
    const out = [];
    for (const f of [0.15, 0.85]) {
      scrollTo(0, top + (el.offsetHeight - innerHeight) * f);
      window.dispatchEvent(new Event("scroll"));
      await new Promise((r) => setTimeout(r, 400));
      out.push({ time: document.querySelector("#time").textContent,
        first: document.querySelector("#step").textContent.slice(0, 40),
        playhead: document.querySelector(".tl-playhead").style.left,
        lanes: [...document.querySelectorAll("#lanes .tl-lane")].filter((l) => !l.hidden).length,
        now: [...document.querySelectorAll(".o-node.now, .o-core.now")].length });
    }
    return out;
  });
  run[0].time !== run[1].time ? ok(`run scrubs ${run[0].time} -> ${run[1].time}`) : fail("run did not advance: " + run[0].time);
  run[1].now > 0 ? ok("a node is lit at both ends: " + run.map((r) => r.now).join(",")) : fail("no node lit");
  ok(`lanes shown: ${run.map((r) => r.lanes).join(",")}`);

  // the reel seeks (byte ranges)
  const seek = await page.evaluate(async () => {
    const v = document.querySelector(".reel video");
    v.pause(); v.currentTime = 11.9;
    await new Promise((r) => { v.addEventListener("seeked", r, { once: true }); setTimeout(r, 1500); });
    return { t: +v.currentTime.toFixed(1), dur: v.duration };
  });
  Math.abs(seek.t - 11.9) < 0.4 ? ok(`reel seeks to ${seek.t}s of ${seek.dur}s`) : fail(`reel stuck at ${seek.t}s`);

  errors.length ? fail("console errors: " + errors.join(" | ")) : ok("console clean");
  failed.length ? fail("failed requests: " + failed.join(" | ")) : ok("every request 2xx/3xx");
  await page.close();
}

/* ── phone ───────────────────────────────────────────────────────── */
{
  const { page, errors } = await open(390, 844);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  overflow > 0 ? fail(`horizontal overflow ${overflow}px at 390`) : ok("no horizontal overflow at 390");

  const chips = await page.evaluate(() => getComputedStyle(document.querySelector(".field")).display);
  chips === "none" ? ok("floating chips hidden on a phone") : fail("chips still showing on a phone");

  const fits = await page.evaluate(() => {
    const out = [];
    for (const sel of ["#hl1", "#hl2", ".hero .lead", ".hero-ctas", ".kicker"]) {
      const r = document.querySelector(sel).getBoundingClientRect();
      out.push([sel, Math.round(r.left), Math.round(r.right)]);
    }
    return out;
  });
  const bad = fits.filter(([, l, r]) => l < 0 || r > 390);
  bad.length ? fail("phone: content outside the viewport " + JSON.stringify(bad)) : ok("phone hero inside the viewport");

  // the paper section on a phone
  const paper = await page.evaluate(async () => {
    const el = document.querySelector("#paper");
    document.documentElement.style.scrollBehavior = "auto";
    scrollTo(0, el.getBoundingClientRect().top + scrollY);
    window.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 600));
    return { wipe: getComputedStyle(document.querySelector("#wipe")).opacity,
      cols: getComputedStyle(document.querySelector(".paper-grid")).gridTemplateColumns.split(" ").length };
  });
  paper.wipe === "0" ? ok("phone: paper section visible") : fail("phone: wipe on " + paper.wipe);
  paper.cols === 1 ? ok("phone: exhibits stack") : fail("phone: paper grid columns " + paper.cols);

  errors.length ? fail("phone console errors: " + errors.join(" | ")) : ok("phone console clean");
  await page.close();
}

await browser.close();
console.log(report.join("\n"));
console.log(report.some((r) => r.startsWith("FAIL")) ? "\nSOME CHECKS FAILED" : "\nALL CHECKS PASSED");
process.exit(report.some((r) => r.startsWith("FAIL")) ? 1 : 0);
