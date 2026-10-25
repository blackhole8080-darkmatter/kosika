#!/usr/bin/env node
/**
 * Render the logo from tools/logo.html.
 *
 *   node tools/make-logo.mjs
 *
 * Writes ../media/kosika-logo.png (the lockup, 2x) and
 * ../media/kosika-mark.png (the square mark, for avatars and the OG card).
 * Needs puppeteer-core and Chrome, like the other capture tools.
 */
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "..", "media");
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";

await mkdir(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--font-render-hinting=none"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1300, height: 1100, deviceScaleFactor: 2 });
await page.goto(pathToFileURL(path.join(HERE, "logo.html")).href, { waitUntil: "networkidle2" });
await page.evaluate(() => document.fonts.ready);
await new Promise((r) => setTimeout(r, 400));

const shoot = async (sel, file) => {
  const el = await page.$(sel);
  await el.screenshot({ path: path.join(OUT, file), omitBackground: false });
  console.log("wrote media/" + file);
};

await shoot("#lockup", "kosika-logo.png");
await shoot("#tile", "kosika-mark.png");

await browser.close();
