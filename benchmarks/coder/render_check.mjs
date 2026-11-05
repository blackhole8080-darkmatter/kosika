#!/usr/bin/env node
/**
 * The judge for a design task: a browser, not a grep.
 *
 *   node render_check.mjs            # run in the task's folder
 *
 * Every other check in this suite reads the source and says whether the words
 * are shaped right. That is not what a page is. A page is what a browser
 * draws, and the two mistakes that cost the most in real design work are the
 * ones source cannot show: a layout that scrolls sideways on a phone, a
 * control too small to hit with a thumb, an animation that was written and
 * never starts, text the colour of its own background. All four look correct
 * in the file and are wrong on the screen.
 *
 * So this serves the folder, opens it at two widths, and measures. Nothing
 * here reads the coder's CSS or JavaScript: a page may be one file or twelve,
 * may use a framework or nothing. Only `index.html` has to exist.
 *
 * The checks are the brief, made mechanical — each one is something the task
 * asked for in words:
 *
 *   renders        a body with real text, no console error, no failed request
 *   hero           an h1 of 40px or more, wholly inside the first screen
 *   hierarchy      the h1 is at least 1.8x the body size
 *   menu           six prices, and opening hours with a time in them
 *   motion         three elements change between not being on screen and
 *                  being scrolled into view — measured, not searched for
 *   phone          no sideways scrolling at 390, and a layout that differs
 *                  from the desktop one
 *   thumb           every visible link and button is 44px tall or more
 *   readable       text against its background clears WCAG AA, for all but
 *                  a fifth of the text measured
 *   pixels         the screenshot is not blank and not one flat colour
 *
 * Two pictures are left in `_check/` so a person can look at what was judged,
 * and one flat colour of a broken stylesheet fails the last check rather than
 * being described as "modern".
 *
 * It needs `puppeteer-core` (a dev dependency, not shipped with Kosika) and a
 * Chrome or Edge already on the machine:
 *
 *   npm install --no-save --no-package-lock puppeteer-core
 *
 * and CHROME_PATH names a browser if it is somewhere unusual. When either is
 * missing the check fails and says so: a judge that cannot see must not pass
 * the work by default.
 */
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const require = createRequire(import.meta.url);

const FOLDER = process.cwd();
const ROOT = path.resolve(FOLDER);
const PICTURES = path.join(FOLDER, "_check");

const failures = [];
const done = [];
const fail = (what) => failures.push(what);
const ok = (what) => done.push(what);

/* ── the folder, over http ─────────────────────────────────────────────
   A file:// page cannot load an ES module and treats fonts differently, so
   the page is served the way it would be in the world. */

const TYPES = {
  ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".webp": "image/webp", ".avif": "image/avif",
  ".gif": "image/gif", ".ico": "image/x-icon", ".woff2": "font/woff2",
  ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
  ".mp4": "video/mp4", ".webm": "video/webm",
};

async function serve() {
  const server = createServer(async (request, response) => {
    try {
      const asked = decodeURIComponent(new URL(request.url, "http://127.0.0.1").pathname);
      const file = path.resolve(ROOT, "." + (asked.endsWith("/") ? asked + "index.html" : asked));
      if (!file.startsWith(ROOT)) { response.writeHead(403).end("outside the folder"); return; }
      const body = await readFile(file);
      response.writeHead(200, {
        "content-type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch {
      response.writeHead(404).end("not found");
    }
  });
  // A keep-alive socket outlives the request that opened it, and a server
  // waiting on one never lets the check finish: the sockets are held so they
  // can be closed with the server.
  const sockets = new Set();
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  return {
    url: `http://127.0.0.1:${server.address().port}/`,
    close: () => {
      for (const socket of sockets) socket.destroy();
      server.close();
    },
  };
}

/* ── a png, read back ──────────────────────────────────────────────────
   Chrome writes 8-bit non-interlaced png. Decoding it here rather than
   asking a package keeps the judge's own dependency to one, and the five
   filters are the whole of it. */

function pixels(png) {
  let at = 8, width = 0, height = 0, depth = 0, color = 0, interlace = 0;
  const parts = [];
  while (at + 8 <= png.length) {
    const length = png.readUInt32BE(at);
    const kind = png.toString("ascii", at + 4, at + 8);
    const data = png.subarray(at + 8, at + 8 + length);
    if (kind === "IHDR") {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      depth = data[8]; color = data[9]; interlace = data[12];
    } else if (kind === "IDAT") parts.push(data);
    else if (kind === "IEND") break;
    at += 12 + length;
  }
  if (depth !== 8 || interlace !== 0 || (color !== 6 && color !== 2)) {
    throw new Error(`unreadable png (depth ${depth}, colour ${color}, interlace ${interlace})`);
  }
  const channels = color === 6 ? 4 : 3;
  const stride = width * channels;
  const raw = zlib.inflateSync(Buffer.concat(parts));
  const out = Buffer.alloc(height * stride);
  let previous = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const row = out.subarray(y * stride, (y + 1) * stride);
    for (let x = 0; x < stride; x++) {
      const left = x >= channels ? row[x - channels] : 0;
      const above = previous[x];
      const corner = x >= channels ? previous[x - channels] : 0;
      let value = line[x];
      if (filter === 1) value += left;
      else if (filter === 2) value += above;
      else if (filter === 3) value += (left + above) >> 1;
      else if (filter === 4) {
        const a = Math.abs(above - corner), b = Math.abs(left - corner), c = Math.abs(left + above - 2 * corner);
        value += a <= b && a <= c ? left : b <= c ? above : corner;
      }
      row[x] = value & 0xff;
    }
    previous = row;
  }
  return { width, height, channels, data: out };
}

/** How many colours, how much variation, how much of one colour — over rows y0..y1. */
function lookedAt(picture, y0, y1) {
  const seen = new Set();
  const counts = new Map();
  let n = 0, sum = 0, squares = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = 0; x < picture.width; x++) {
      const i = (y * picture.width + x) * picture.channels;
      const r = picture.data[i], g = picture.data[i + 1], b = picture.data[i + 2];
      const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
      seen.add(key);
      counts.set(key, (counts.get(key) || 0) + 1);
      const light = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      sum += light; squares += light * light; n++;
    }
  }
  const mean = sum / n;
  return {
    distinct: seen.size,
    spread: Math.sqrt(Math.max(0, squares / n - mean * mean)),
    top: Math.max(...counts.values()) / n,
  };
}

/* ── the page, measured ────────────────────────────────────────────────
   Everything below runs inside the browser. Returning plain numbers keeps
   the assertions here readable and the reasons for a failure printable. */

const HERO = () => {
  const h1 = document.querySelector("h1");
  if (!h1) return null;
  const box = h1.getBoundingClientRect();
  return {
    size: parseFloat(getComputedStyle(h1).fontSize),
    bottom: box.bottom,
    screen: innerHeight,
    body: parseFloat(getComputedStyle(document.body).fontSize),
    text: (document.body.innerText || "").trim().length,
  };
};

const PRICES = () => {
  const text = document.body.innerText || "";
  const prices = new Set((text.match(/[$£€]\s?\d[\d,.]*/g) || []).map((found) => found.replace(/\s/g, "")));
  const hours = /(mon|tue|wed|thu|fri|sat|sun)/i.test(text);
  const time = /\b\d{1,2}(:\d{2})?\s?(am|pm)\b/i.test(text);
  return { prices: prices.size, hours, time };
};

/** Elements on screen now, or they are not what a thumb has to hit. */
const THUMB = () => [...document.querySelectorAll("a, button, [role=button], input[type=submit]")]
  .filter((element) => element.getClientRects().length)
  .map((element) => {
    const box = element.getBoundingClientRect();
    return { text: (element.innerText || element.value || element.tagName).trim().slice(0, 24), height: Math.round(box.height) };
  })
  .filter((item) => item.height > 0);

/**
 * Text against the colour behind it, in WCAG terms.
 *
 * The colour behind a paragraph is often its parent's, or its parent's
 * parent's, so it is looked up rather than guessed, and a piece of text over
 * a gradient or a photograph is left out — that cannot be measured from a
 * computed style, and a judge should not invent a number for it.
 */
const CONTRAST = () => {
  const parse = (value) => {
    const numbers = (value.match(/[\d.]+/g) || []).map(Number);
    if (numbers.length < 3) return null;
    return { r: numbers[0], g: numbers[1], b: numbers[2], a: numbers.length > 3 ? numbers[3] : 1 };
  };
  const light = ({ r, g, b }) => {
    const channel = (value) => {
      const v = value / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  };
  const ratio = (a, b) => {
    const [high, low] = [light(a), light(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  };
  // Nothing opaque all the way up means the page is painted on the canvas,
  // and the canvas is white: an unstyled page is black on white, not black on
  // black.
  const canvas = { r: 255, g: 255, b: 255, a: 1 };
  const behind = (element) => {
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage && style.backgroundImage !== "none") return null;
      const colour = parse(style.backgroundColor);
      if (!colour) return null;
      if (colour.a >= 0.9) return colour;
    }
    return canvas;
  };

  const measured = [];
  const seen = new Set();
  for (const element of document.querySelectorAll("body *")) {
    if (seen.size >= 60) break;
    const own = [...element.childNodes].filter((node) => node.nodeType === 3).map((node) => node.textContent.trim()).join("");
    if (!own || !element.getClientRects().length) continue;
    seen.add(element);
    const style = getComputedStyle(element);
    const ink = parse(style.color);
    const paper = behind(element);
    if (!ink || !paper) continue;
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    measured.push({ ratio: Math.round(ratio(ink, paper) * 100) / 100, needs: large ? 3 : 4.5, size });
  }
  return measured;
};

/** What moves when the page is scrolled: a signature of every element. */
const MOVEMENT = () => [...document.querySelectorAll("body *")].map((element) => {
  const style = getComputedStyle(element);
  return [style.transform, style.opacity, style.clipPath, style.filter].join("|");
});

function browserPath() {
  const tries = [
    process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  ].filter(Boolean);
  return tries.find((candidate) => existsSync(candidate));
}

/* ── the run ─────────────────────────────────────────────────────────── */

let browser = null;
let served = null;

try {
  if (!existsSync(path.join(FOLDER, "index.html"))) {
    fail("there is no index.html in the folder, so there is no page to look at");
  } else {
    let puppeteer = null;
    try {
      puppeteer = require("puppeteer-core");
    } catch {
      fail("the browser check cannot run: puppeteer-core is not installed "
        + "(npm install --no-save --no-package-lock puppeteer-core)");
    }
    const executablePath = browserPath();
    if (puppeteer && !executablePath) {
      fail("the browser check cannot run: no Chrome or Edge found; name one with CHROME_PATH");
    }

    if (puppeteer && executablePath) {
      browser = await puppeteer.launch({
        executablePath, headless: "new",
        args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--disable-background-timer-throttling"],
      });
      served = await serve();
      await mkdir(PICTURES, { recursive: true });

      // A page is not wrong for having no favicon, and asking for one is not a
      // broken request: the browser asks on its own, for every page.
      const BrowserAsked = (url) => /\/favicon\.ico$/.test(url);

      const open = async (width, height) => {
        const page = await browser.newPage();
        const errors = [];
        const broken = [];
        page.on("console", (message) => {
          // The complaint about a missing favicon names no address of its own.
          const where = message.location()?.url || "";
          if (message.type() === "error" && !BrowserAsked(where) && !/favicon/.test(message.text())) errors.push(message.text() + (where ? " at " + where : ""));
        });
        page.on("pageerror", (error) => errors.push("pageerror: " + error.message));
        page.on("requestfailed", (request) => {
          const why = request.failure()?.errorText || "";
          if (!/ERR_ABORTED/.test(why) && !BrowserAsked(request.url())) broken.push(why + " " + request.url());
        });
        page.on("response", (response) => {
          if (response.status() >= 400 && !BrowserAsked(response.url())) broken.push(response.status() + " " + response.url());
        });
        await page.setViewport({ width, height, deviceScaleFactor: 1 });
        await page.goto(served.url, { waitUntil: "networkidle2", timeout: 20000 });
        await page.evaluate(() => document.fonts.ready);
        await new Promise((ready) => setTimeout(ready, 1200));
        return { page, errors, broken };
      };

      /* desktop */
      const wide = await open(1440, 900);
      const desktop = wide.page;

      const hero = await desktop.evaluate(HERO);
      if (!hero) {
        fail("there is no h1, so the page has no hero");
      } else {
        hero.size >= 40 ? ok(`hero is ${Math.round(hero.size)}px`)
          : fail(`the h1 is ${Math.round(hero.size)}px: a hero wants 40 or more`);
        hero.bottom <= hero.screen
          ? ok("the hero is inside the first screen")
          : fail(`the h1 ends ${Math.round(hero.bottom - hero.screen)}px below the fold`);
        hero.text >= 200 ? ok(`the page carries ${hero.text} characters of text`)
          : fail(`the page has only ${hero.text} characters of text`);
        hero.size >= hero.body * 1.8
          ? ok(`type hierarchy: ${Math.round(hero.size / hero.body * 10) / 10}x the body`)
          : fail(`the h1 is ${Math.round(hero.size / hero.body * 10) / 10}x the body: 1.8x is the floor`);
      }

      const menu = await desktop.evaluate(PRICES);
      menu.prices >= 6 ? ok(`${menu.prices} prices on the menu`)
        : fail(`the menu shows ${menu.prices} prices: six items were asked for`);
      menu.hours && menu.time ? ok("opening hours are there")
        : fail("opening hours are missing or carry no time");

      wide.errors.length ? fail("the page logs errors: " + wide.errors.slice(0, 2).join(" / "))
        : ok("no console errors");
      wide.broken.length ? fail("requests failed: " + wide.broken.slice(0, 2).join(" / "))
        : ok("every request answered");

      const across = await desktop.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      across > 1 ? fail(`the page scrolls sideways by ${across}px at 1440`)
        : ok("no sideways scrolling at 1440");

      /* motion, which source cannot show: what is not on screen yet, and
         whether scrolling to it changes anything */
      const before = await desktop.evaluate(MOVEMENT);
      await desktop.evaluate(async () => {
        document.documentElement.style.scrollBehavior = "auto";
        const step = Math.round(innerHeight * 0.75);
        for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
          scrollTo(0, y);
          await new Promise((ready) => setTimeout(ready, 120));
        }
      });
      await new Promise((ready) => setTimeout(ready, 900));
      const after = await desktop.evaluate(MOVEMENT);
      const moved = before.reduce((count, signature, index) => count + (signature !== after[index] ? 1 : 0), 0);
      moved >= 3 ? ok(`${moved} elements move as the page is scrolled`)
        : fail(`only ${moved} element${moved === 1 ? "" : "s"} changed while scrolling: the animation asked for is not running`);

      const readable = await desktop.evaluate(CONTRAST);
      if (readable.length < 3) {
        fail(`only ${readable.length} pieces of text could be measured against their background`);
      } else {
        const dim = readable.filter((item) => item.ratio < item.needs);
        dim.length / readable.length <= 0.2
          ? ok(`text is readable: worst ${Math.min(...readable.map((item) => item.ratio))}:1`)
          : fail(`${dim.length} of ${readable.length} pieces of text are too dim, worst ${Math.min(...dim.map((item) => item.ratio))}:1`);
      }

      const wideShot = await desktop.screenshot({ encoding: "binary" });
      await writeFile(path.join(PICTURES, "wide.png"), wideShot);

      /* phone */
      const narrow = await open(390, 844);
      const phone = narrow.page;

      const sideways = await phone.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      sideways > 1 ? fail(`the page scrolls sideways by ${sideways}px at 390`)
        : ok("no sideways scrolling at 390");

      const thumbs = await phone.evaluate(THUMB);
      const small = thumbs.filter((item) => item.height < 44);
      small.length === 0
        ? ok(`every one of ${thumbs.length} controls is 44px or taller`)
        : fail(`${small.length} controls are under 44px on a phone: ` + small.slice(0, 3).map((item) => `"${item.text}" ${item.height}px`).join(", "));

      const settled = await phone.evaluate(() => ({
        height: document.documentElement.scrollHeight,
        h1: document.querySelector("h1")?.getBoundingClientRect().width || 0,
      }));
      const wideSettled = await desktop.evaluate(() => ({
        height: document.documentElement.scrollHeight,
        h1: document.querySelector("h1")?.getBoundingClientRect().width || 0,
      }));
      const differs = Math.abs(settled.height - wideSettled.height) / Math.max(1, wideSettled.height) > 0.1
        || Math.abs(settled.h1 - wideSettled.h1) / Math.max(1, wideSettled.h1) > 0.1;
      differs ? ok(`the layout changes on a phone (${wideSettled.height}px tall on a desk, ${settled.height}px on a phone)`)
        : fail("the layout is the same at 390 as at 1440: nothing was reflowed for a phone");

      await phone.screenshot({ path: path.join(PICTURES, "phone.png") });

      /* the picture itself */
      // Puppeteer hands back a Uint8Array, which knows nothing of png framing.
      const picture = pixels(Buffer.from(wideShot));
      const all = lookedAt(picture, 0, picture.height);
      const heroPart = lookedAt(picture, 0, Math.round(picture.height * 0.36));
      all.distinct >= 6 && all.spread >= 10
        ? ok(`the page draws ${all.distinct} colours, variation ${Math.round(all.spread)}`)
        : fail(`the page is nearly blank: ${all.distinct} colours, variation ${Math.round(all.spread)}`);
      heroPart.spread >= 6
        ? ok(`the hero has something in it (variation ${Math.round(heroPart.spread)})`)
        : fail(`the hero is one flat colour (variation ${Math.round(heroPart.spread)}): nothing was drawn in it`);
      all.top <= 0.92
        ? ok("no single colour covers the screen")
        : fail(`${Math.round(all.top * 100)}% of the screen is one colour`);
    }
  }
} catch (error) {
  fail("the page could not be judged: " + (error && error.message ? error.message : String(error)));
} finally {
  if (browser) await browser.close().catch(() => {});
  if (served) served.close();
}

for (const line of done) console.log("ok    " + line);
for (const line of failures) console.log("FAIL  " + line);
if (failures.length) {
  console.log(`${failures.length} of ${failures.length + done.length} checks failed: ${failures[0]}`);
  process.exit(1);
}
console.log(`all ${done.length} rendered checks passed`);
// Nothing is left to wait for: a browser or a socket that lingers must not
// hold the check open, because the benchmark around it is waiting.
process.exit(0);
