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

  // a stamp lands on the artwork, never across the note it is stamping
  const stamps = await page.evaluate(() => [...document.querySelectorAll(".exhibit")].map((card) => {
    const s = card.querySelector(".stamp").getBoundingClientRect();
    const t = card.querySelector(".sub").getBoundingClientRect();
    const crossed = !(s.right < t.left || s.left > t.right || s.bottom < t.top || s.top > t.bottom);
    return { crossed, art: card.querySelector(".art").getBoundingClientRect().height };
  }));
  stamps.some((s) => s.crossed) ? fail("a stamp covers its exhibit's note") : ok("stamps sit clear of the exhibit notes");

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
      const lanes = document.querySelector("#lanes");
      out.push({ time: document.querySelector("#time").textContent,
        first: document.querySelector("#step").textContent.slice(0, 40),
        playhead: Math.round(document.querySelector(".tl-playhead").getBoundingClientRect().left - lanes.getBoundingClientRect().left),
        transform: getComputedStyle(document.querySelector(".tl-playhead")).transform,
        lanes: [...document.querySelectorAll("#lanes .tl-lane")].filter((l) => !l.hidden).length,
        now: [...document.querySelectorAll(".o-node.now, .o-core.now")].length,
        asking: document.querySelector("#run").classList.contains("asking"),
        arc: parseFloat(getComputedStyle(document.querySelector(".o-trace")).strokeDashoffset),
        head: [Math.round(+document.querySelector("#ohead").getAttribute("cx")),
          Math.round(+document.querySelector("#ohead").getAttribute("cy"))],
        headOn: getComputedStyle(document.querySelector("#ohead")).opacity });
    }
    return out;
  });
  run[0].time !== run[1].time ? ok(`run scrubs ${run[0].time} -> ${run[1].time}`) : fail("run did not advance: " + run[0].time);
  run[1].now > 0 ? ok("a node is lit at both ends: " + run.map((r) => r.now).join(",")) : fail("no node lit");
  run[1].playhead > run[0].playhead ? ok(`playhead moves by transform (${run[0].playhead}px -> ${run[1].playhead}px)`) : fail("playhead did not move: " + JSON.stringify(run.map((r) => r.playhead)));
  ok(`lanes shown: ${run.map((r) => r.lanes).join(",")}, model asked at 85%: ${run[1].asking}`);

  // a new step landing sends one ring out from the node that took it. Note the
  // beats are not evenly spread: the model call is most of the run, so the
  // pulses cluster where the steps are — which is why this walks the opening
  // of the recording rather than the middle.
  const wave = await page.evaluate(async () => {
    const el = document.querySelector("#run");
    document.documentElement.style.scrollBehavior = "auto";
    const top = el.getBoundingClientRect().top + scrollY;
    scrollTo(0, top); window.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 500));
    const before = document.querySelectorAll(".o-wave").length;
    scrollTo(0, top + 90); window.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 260));
    return { before, after: document.querySelectorAll(".o-wave").length };
  });
  wave.after > 0
    ? ok(`a step landing sends a ring out (${wave.before} -> ${wave.after})`)
    : fail("no wave when a step lands: " + JSON.stringify(wave));
  run[1].arc < run[0].arc - 100
    ? ok(`the orbit's arc follows the run (dashoffset ${Math.round(run[0].arc)} -> ${Math.round(run[1].arc)})`)
    : fail("orbit arc did not advance: " + JSON.stringify(run.map((r) => r.arc)));
  run[0].head.join() !== run[1].head.join() && run[1].headOn === "1"
    ? ok(`the dot rides the ring (${run[0].head.join()} -> ${run[1].head.join()})`)
    : fail("dot did not travel: " + JSON.stringify({ h: run.map((r) => r.head), o: run[1].headOn }));

  // the hero's film: silent, playing, and not asking for a user gesture
  const hero = await page.evaluate(async () => {
    const v = document.querySelector("#herofilm");
    if (!v) return { missing: true };
    // the run and paper checks left us down the page, where the hero is paused
    document.documentElement.style.scrollBehavior = "auto";
    scrollTo(0, 0);
    window.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 600));
    // start from the top of the loop: sampling near the end measures a wrap,
    // not a paused video
    if (v.duration) v.currentTime = 1;
    await new Promise((r) => setTimeout(r, 300));
    const before = v.currentTime;
    await new Promise((r) => setTimeout(r, 700));
    return { muted: v.muted, paused: v.paused, moved: +(v.currentTime - before).toFixed(2),
      dur: v.duration, w: v.videoWidth, file: v.currentSrc.split("/").pop() };
  });
  hero.missing ? fail("no hero film") :
    hero.muted && !hero.paused && hero.moved > 0 && hero.w
      ? ok(`hero film plays (${hero.file}, ${hero.w}px wide, muted)`) :
      fail("hero film not playing: " + JSON.stringify(hero));

  // the reel seeks (byte ranges)
  const seek = await page.evaluate(async () => {
    const v = document.querySelector(".reel video");
    v.pause(); v.currentTime = 11.9;
    await new Promise((r) => { v.addEventListener("seeked", r, { once: true }); setTimeout(r, 1500); });
    return { t: +v.currentTime.toFixed(1), dur: v.duration };
  });
  Math.abs(seek.t - 11.9) < 0.4 ? ok(`reel seeks to ${seek.t}s of ${seek.dur}s`) : fail(`reel stuck at ${seek.t}s`);

  // the hero is one screen: headline, one sentence and an action at 1280x800
  const fold = await page.evaluate(async () => {
    document.documentElement.style.scrollBehavior = "auto";
    scrollTo(0, 0);
    await new Promise((r) => setTimeout(r, 300));
    const boxes = [".h1", ".hero .lead", ".hero-ctas .btn.primary"].map((s) => {
      const r = document.querySelector(s).getBoundingClientRect();
      return [s, Math.round(r.top), Math.round(r.bottom), Math.round(getComputedStyle(document.querySelector(s)).opacity * 100)];
    });
    return { boxes, vh: innerHeight, words: document.querySelector(".hero .lead").textContent.trim().split(/\s+/).length };
  });
  const below = fold.boxes.filter(([, , bottom]) => bottom > fold.vh - 8);
  below.length ? fail("hero not above the fold: " + JSON.stringify(below)) : ok("hero headline, sentence and action above the fold (" + fold.vh + "px tall)");
  fold.words <= 24 ? ok(`hero sentence is ${fold.words} words`) : fail(`hero sentence too long: ${fold.words} words`);
  fold.boxes.every(([, , , o]) => o === 100) ? ok("hero copy is visible, not waiting on an animation") : fail("hero copy still faded: " + JSON.stringify(fold.boxes));

  // focus is visible, and arrives without a fade
  const focus = await page.evaluate(async () => {
    const first = document.querySelector(".hero-ctas .btn.primary");
    first.focus();
    await new Promise((r) => setTimeout(r, 40));
    const s = getComputedStyle(first);
    return { outline: s.outlineWidth + " " + s.outlineStyle + " " + s.outlineColor, transition: s.transitionDuration };
  });
  /^2px solid/.test(focus.outline) ? ok("focus ring: " + focus.outline) : fail("no focus ring: " + focus.outline);

  // every control a thumb has to hit is at least 44px tall
  const taps = await page.evaluate(() => [...document.querySelectorAll(".reel-foot .iconbtn, .hero-ctas .btn, .cmd, .faq summary")]
    .map((el) => [el.className.split(" ")[0], Math.round(el.getBoundingClientRect().height)]));
  const small = taps.filter(([, h]) => h < 44);
  small.length ? fail("tap targets under 44px: " + JSON.stringify(small)) : ok(`every tap target is 44px+ (${taps.length} of them)`);

  // the atmosphere and the pointer: two light fields, chips at depth, and
  // buttons that lean the few pixels toward the cursor
  const extras = await page.evaluate(async () => {
    document.documentElement.style.scrollBehavior = "auto";
    scrollTo(0, 0); window.dispatchEvent(new Event("scroll"));
    await new Promise((r) => setTimeout(r, 400));
    const aurora = getComputedStyle(document.querySelector(".aurora")).display;
    const chip = document.querySelector(".field .fx");
    const before = getComputedStyle(chip).transform;
    const btn = document.querySelector(".hero-ctas .btn.primary");
    const r = btn.getBoundingClientRect();
    btn.dispatchEvent(new PointerEvent("pointermove", { clientX: r.right - 2, clientY: r.top + 3, bubbles: true }));
    await new Promise((r2) => setTimeout(r2, 90));
    const magnet = getComputedStyle(btn).transform;
    scrollTo(0, 420); window.dispatchEvent(new Event("scroll"));
    await new Promise((r2) => setTimeout(r2, 320));
    return { aurora, before, after: getComputedStyle(chip).transform, magnet };
  });
  extras.aurora !== "none" ? ok("the aurora fields are running") : fail("aurora hidden: " + extras.aurora);
  extras.before !== extras.after ? ok(`the chips move at depth (${extras.before} -> ${extras.after})`) : fail("chips did not move: " + extras.before);
  extras.magnet !== "none" ? ok("buttons lean toward the pointer") : fail("no magnetic offset: " + extras.magnet);

  // the meter in the reel bar draws the film's own audio
  const meter = await page.evaluate(async () => {
    const v = document.querySelector("#reel");
    document.querySelector(".reel").scrollIntoView({ block: "center" });
    await new Promise((r) => setTimeout(r, 600));
    document.querySelector("#soundbtn").click();
    await new Promise((r) => setTimeout(r, 1500));
    const cv = document.querySelector("#spectrum");
    const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
    const alphas = new Set();
    for (let i = 3; i < d.length; i += 4) if (d[i] > 10) alphas.add(d[i]);
    const out = { muted: v.muted, levels: alphas.size, playing: !v.paused };
    v.muted = true;
    return out;
  });
  meter.levels > 3
    ? ok(`the meter draws the score (${meter.levels} levels, playing: ${meter.playing})`)
    : fail("spectrum flat or missing: " + JSON.stringify(meter));

  // the command blocks are buttons: reachable and copyable by keyboard
  const cmds = await page.evaluate(() => [...document.querySelectorAll(".cmd")].map((b) => b.tagName + "/" + b.getAttribute("type")));
  cmds.every((c) => c === "BUTTON/button") ? ok(`${cmds.length} command blocks are buttons`) : fail("command blocks: " + JSON.stringify(cmds));

  errors.length ? fail("console errors: " + errors.join(" | ")) : ok("console clean");
  failed.length ? fail("failed requests: " + failed.join(" | ")) : ok("every request 2xx/3xx");
  await page.close();
}

/* ── reduced motion ──────────────────────────────────────────────── */
{
  const page = await browser.newPage();
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await new Promise((r) => setTimeout(r, 600));
  const calm = await page.evaluate(() => ({
    film: getComputedStyle(document.querySelector("#herofilm")).display,
    lead: getComputedStyle(document.querySelector(".hero .lead")).opacity,
    letters: getComputedStyle(document.querySelector(".h1 .l")).transform,
    reel: getComputedStyle(document.querySelector(".reel video")).transform,
    arc: getComputedStyle(document.querySelector(".o-trace")).strokeDashoffset,
    head: getComputedStyle(document.querySelector("#ohead")).opacity,
    aurora: getComputedStyle(document.querySelector(".aurora")).display,
    meter: getComputedStyle(document.querySelector("#spectrum")).display
  }));
  calm.film === "none" ? ok("reduced motion: hero film not loaded") : fail("reduced motion: film " + calm.film);
  calm.lead === "1" ? ok("reduced motion: hero copy visible") : fail("reduced motion: lead opacity " + calm.lead);
  calm.letters === "none" ? ok("reduced motion: headline unshifted") : fail("reduced motion: letters " + calm.letters);
  calm.reel === "none" ? ok("reduced motion: film not dollied") : fail("reduced motion: reel " + calm.reel);
  parseFloat(calm.arc) === 0 ? ok("reduced motion: the ring is drawn whole") : fail("reduced motion: arc " + calm.arc);
  calm.head === "0" ? ok("reduced motion: no travelling dot") : fail("reduced motion: dot opacity " + calm.head);
  calm.aurora === "none" ? ok("reduced motion: the aurora is still") : fail("reduced motion: aurora " + calm.aurora);
  calm.meter === "none" ? ok("reduced motion: no meter drawing") : fail("reduced motion: meter " + calm.meter);
  await page.close();
}

/* ── phone ───────────────────────────────────────────────────────── */
{
  const { page, errors } = await open(390, 844);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  overflow > 0 ? fail(`horizontal overflow ${overflow}px at 390`) : ok("no horizontal overflow at 390");

  const chips = await page.evaluate(() => getComputedStyle(document.querySelector(".field")).display);
  chips === "none" ? ok("floating chips hidden on a phone") : fail("chips still showing on a phone");
  const filmOnPhone = await page.evaluate(() => getComputedStyle(document.querySelector(".hero-film")).display);
  filmOnPhone === "none" ? ok("hero film hidden on a phone") : fail("hero film still showing on a phone");

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

  // boxes inside the viewport can still hold text wider than they are, and
  // overflow-x: clip then eats the words instead of letting them wrap
  const clipped = await page.evaluate(() => [".kicker", ".hero .lead", "#hl1", "#hl2", ".hero-meta"]
    .map((sel) => {
      const el = document.querySelector(sel);
      return [sel, el.scrollWidth - el.clientWidth];
    })
    .filter(([, over]) => over > 1));
  clipped.length ? fail("phone: clipped text " + JSON.stringify(clipped)) : ok("phone: no hero text is clipped");

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
