/* Kosika Studio — the site's motion.
   Scroll is the timeline. Everything that moves is a function of either
   time or scroll, so a frame can always be recomputed from scratch. */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (x, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
  const lerp = (a, b, t) => a + (b - a) * t;
  const seg = (t, a, b) => clamp((t - a) / (b - a || 1e-6));
  const sineInOut = t => -(Math.cos(Math.PI * t) - 1) / 2;
  const expoOut = t => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));
  function spring(t, { z = .55, w = 13 } = {}) {
    if (t <= 0) return 0; if (t >= 1) return 1;
    const e = Math.exp(-z * w * t), wd = w * Math.sqrt(Math.max(.02, 1 - z * z));
    return 1 - e * Math.cos(wd * t);
  }
  const rng = (seed => () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296))(7);
  const CALM = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DPR = Math.min(2, window.devicePixelRatio || 1);

  /* ── the hero: letters rise, the orbit draws itself ───────────── */
  (function splitHeads() {
    let n = 0;
    for (const el of [$("#hl1"), $("#hl2")]) {
      if (!el) continue;
      const words = el.textContent.split(" ");
      el.textContent = "";
      for (const word of words) {
        const w = document.createElement("span");
        w.className = "w";
        for (const ch of word) {
          const l = document.createElement("span");
          l.className = "l"; l.textContent = ch;
          l.style.setProperty("--d", (0.18 + n * 0.022).toFixed(2) + "s");
          w.appendChild(l); n++;
        }
        el.appendChild(w);
        el.appendChild(document.createTextNode(" "));
      }
    }
    // rAF may be deferred in a hidden tab; a timer keeps the headline from
    // staying hidden if the page is first painted long after load
    const go = () => document.body.classList.add("live");
    requestAnimationFrame(go);
    setTimeout(go, 120);
  })();

  const orbit = (() => {
    const cv = $("#orbitcv");
    if (!cv) return { pause() {} };
    const ctx = cv.getContext("2d");
    let w = 1, h = 1, raf = 0, running = false, t0 = performance.now();
    let px = 0, py = 0, tx = 0, ty = 0;

    function size() {
      const r = cv.getBoundingClientRect();
      w = Math.max(1, r.width); h = Math.max(1, r.height);
      cv.width = Math.round(w * DPR); cv.height = Math.round(h * DPR);
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    }
    size();
    addEventListener("resize", size);

    function ellipse(cx, cy, rx, ry, t) {
      const rot = Math.sin(t * .18) * .04;
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot);
      ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }

    function draw(now) {
      const t = (now - t0) / 1000;
      px = lerp(px, tx, .06); py = lerp(py, ty, .06);
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2 + px * 16, cy = h * .46 + py * 12;
      const rx = Math.min(w * .44, 620), ry = Math.min(h * .34, 300);

      // two hairlines that are always there
      ctx.strokeStyle = "rgba(245,180,92,.16)"; ctx.lineWidth = 1.2;
      ellipse(cx, cy, rx, ry, t);
      ctx.setLineDash([2, 9]); ctx.strokeStyle = "rgba(139,124,246,.28)"; ctx.lineWidth = 1;
      ellipse(cx, cy, rx * 1.09, ry * 1.12, t + 1);
      ctx.setLineDash([]);

      // the ring draws itself once, then breathes
      const p = CALM ? 1 : expoOut(clamp(t / 2.4));
      ctx.save();
      ctx.strokeStyle = "rgba(245,180,92,.5)"; ctx.lineWidth = 1.8;
      ctx.shadowColor = "rgba(245,180,92,.55)"; ctx.shadowBlur = 18;
      ctx.translate(cx, cy); ctx.rotate(Math.sin(t * .18) * .04);
      ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p);
      ctx.stroke(); ctx.restore();

      // the dot travels the orbit; it slows to rest at each quarter
      const a = -Math.PI / 2 + Math.PI * 2 * (CALM ? .12 : (t * .045) % 1);
      // the ring leans with the pointer, so the dot has to lean with it: one
      // rotation, applied to the point itself, or the dot sits off its own arc
      const rot = Math.sin(t * .18) * .04;
      const ex = Math.cos(a) * rx, ey = Math.sin(a) * ry;
      const dx = cx + ex * Math.cos(rot) - ey * Math.sin(rot);
      const dy = cy + ex * Math.sin(rot) + ey * Math.cos(rot);

      // the comet: the dot's own path, thinning behind it. Six short arcs of
      // falling alpha — a smear, not a second animation.
      if (!CALM) {
        const span = .30, seg = 6;
        ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot); ctx.lineCap = "round";
        for (let i = 0; i < seg; i++) {
          const a1 = a - span * (i / seg), a0 = a - span * ((i + 1) / seg);
          // the dot's own light, not the ring's gold: a smear of the head reads
          // as travel, where a second gold arc would read as ring thickness
          ctx.strokeStyle = `rgba(255,244,226,${(.4 * (1 - i / seg) + .05).toFixed(3)})`;
          ctx.lineWidth = 3.2 * (1 - i / seg) + .6;
          ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, a0, a1); ctx.stroke();
        }
        ctx.restore();
      }

      const g = ctx.createRadialGradient(dx, dy, 0, dx, dy, 60);
      g.addColorStop(0, "rgba(245,180,92,.55)"); g.addColorStop(1, "rgba(245,180,92,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(dx, dy, 60, 0, 7); ctx.fill();
      ctx.fillStyle = "#fff4e2"; ctx.beginPath(); ctx.arc(dx, dy, 4.2, 0, 7); ctx.fill();

      // a few satellites, only while the ring is being drawn
      if (p < 1) {
        ctx.fillStyle = "rgba(233,236,248,.5)";
        for (let i = 0; i < 6; i++) {
          const sa = -Math.PI / 2 + (i / 6) * Math.PI * 2;
          if (i / 6 > p) continue;
          ctx.beginPath(); ctx.arc(cx + Math.cos(sa) * rx, cy + Math.sin(sa) * ry, 1.8, 0, 7); ctx.fill();
        }
      }
      if (running) raf = requestAnimationFrame(draw);
    }
    return {
      // paint one frame synchronously: a throttled tab or a covered window may
      // never run rAF, and a blank hero is worse than a still one
      start() { if (running) return; running = true; t0 = performance.now() - 2600; draw(performance.now()); },
      /* one frame, no loop: what a reader who asked for less motion gets */
      still() { running = false; cancelAnimationFrame(raf); t0 = performance.now() - 2600; draw(performance.now()); },
      pause() { running = false; cancelAnimationFrame(raf); },
      look(x, y) { tx = x; ty = y; }
    };
  })();

  /* the film behind the headline: silent, and only while the hero is on
     screen — a hero that keeps decoding video under the case file is a waste */
  const heroFilm = (() => {
    const v = $("#herofilm");
    if (!v) return { play() {}, pause() {} };
    v.muted = true;
    if (CALM) { v.autoplay = false; v.pause(); }
    return {
      play() { if (!CALM && v.paused) v.play().catch(() => {}); },
      pause() { if (!v.paused) v.pause(); }
    };
  })();

  addEventListener("pointermove", (e) => {
    if (CALM) return;
    orbit.look((e.clientX / innerWidth - .5) * 2, (e.clientY / innerHeight - .5) * 2);
    const x = (e.clientX / innerWidth - .5), y = (e.clientY / innerHeight - .5);
    const f = $("#field");
    if (f) f.style.transform = `translate3d(${x * -26}px, ${y * -18}px, 0)`;
    // the film drifts the other way, a little: depth without movement
    const fl = $("#herofilm");
    if (fl) { fl.style.setProperty("--fx", `${x * 9}px`); fl.style.setProperty("--fy", `${y * 6}px`); }
  }, { passive: true });

  new IntersectionObserver(([e]) => {
    if (e.isIntersecting) { CALM ? orbit.still() : orbit.start(); heroFilm.play(); }
    else { orbit.pause(); heroFilm.pause(); }
  }, { threshold: 0 }).observe($("#top"));

  /* the floating words */
  (function floats() {
    const field = $("#field");
    if (!field || CALM) return;
    const bits = [
      ["checked", 11, 20, 116, 1.4], ["granted", 78, 17, 106, .8], ["recorded", 6, 62, 112, 1.1],
      ["replay", 85, 58, 126, 1.7],      ["budgets", 17, 86, 96, .6], ["traces", 20, 8, 100, 1.25],
      ["local", 90, 40, 84, .9], ["private", 3, 40, 100, 1.55]
    ];
    for (const [word, left, top, size, depth] of bits) {
      const fx = document.createElement("div");
      fx.className = "fx";
      fx.style.cssText = `left:${left}%;top:${top}%;width:${size}px;height:${Math.round(size * .42)}px;--drift:${(7 + depth * 3).toFixed(1)}s;--delay:-${(depth * 1.7).toFixed(1)}s`;
      fx.dataset.d = depth; // how far the chip travels as the page moves
      const i = document.createElement("i"); i.textContent = word; fx.appendChild(i);
      field.appendChild(fx);
    }
    // the decorative rings live in the margin: put them where the type is not
    for (const [left, top, size, drift] of [[7, 12, 150, "12.5s"], [86, 26, 104, "10s"]]) {
      const fx = document.createElement("div");
      fx.className = "fx ring";
      fx.style.cssText = `left:${left}%;top:${top}%;width:${size}px;height:${size}px;--drift:${drift}`;
      fx.appendChild(document.createElement("i"));
      field.appendChild(fx);
    }
  })();

  /* ── chrome ───────────────────────────────────────────────────── */
  const bar = $("#bar"), pbar = $("#progressbar");
  const fxBits = $$(".field .fx");
  const onScrollChrome = () => {
    const y = scrollY;
    bar.classList.toggle("scrolled", y > 40);
    const max = document.documentElement.scrollHeight - innerHeight;
    pbar.style.transform = `scaleX(${max > 0 ? clamp(y / max) : 0})`;
    // the chips are at different depths, so the page moving lifts them apart:
    // the ones meant to be further away travel less
    if (!CALM && y < innerHeight * 1.2) {
      const k = clamp(y / innerHeight, 0, 1.2);
      for (const fx of fxBits) {
        const d = parseFloat(fx.dataset.d || "1");
        fx.style.transform = `translate3d(0, ${(-k * d * 44).toFixed(1)}px, 0)`;
      }
    }
  };
  addEventListener("scroll", onScrollChrome, { passive: true });
  onScrollChrome();

  /* ── the buttons lean toward the pointer ──────────────────────── */
  (function magnet() {
    if (CALM || !matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    for (const el of $$(".hero-ctas .btn, .bar .cta")) {
      const move = (e) => {
        const r = el.getBoundingClientRect();
        const x = clamp((e.clientX - (r.left + r.width / 2)) / r.width, -1, 1);
        const y = clamp((e.clientY - (r.top + r.height / 2)) / r.height, -1, 1);
        el.style.setProperty("--mx", (x * 5).toFixed(1) + "px");
        el.style.setProperty("--my", (y * 4).toFixed(1) + "px");
      };
      const rest = () => { el.style.setProperty("--mx", "0px"); el.style.setProperty("--my", "0px"); };
      el.addEventListener("pointermove", move, { passive: true });
      el.addEventListener("pointerleave", rest, { passive: true });
      el.addEventListener("blur", rest);
    }
  })();

  /* ── reveal, counters, stamps ─────────────────────────────────── */
  /* Section headlines are the largest type on the page, so they get the one
     kinetic moment: each word rises out of its own mask, 55ms after the last.
     Words, not letters — a headline has to stay readable while it moves. A
     reader who asked for less motion keeps the plain line, and the <noscript>
     rule already covers the rest. */
  if (!CALM) {
    for (const h of $$("h2.rise")) {
      const inner = [];
      const walk = (node) => {
        for (const child of [...node.childNodes]) {
          if (child.nodeType === 3) {
            const frag = document.createDocumentFragment();
            for (const part of child.textContent.split(/(\s+)/)) {
              if (!part) continue;
              if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); continue; }
              const w = document.createElement("span"); w.className = "hw";
              const i = document.createElement("span"); i.className = "hi"; i.textContent = part;
              w.appendChild(i); frag.appendChild(w); inner.push(i);
            }
            child.replaceWith(frag);
          } else if (child.nodeType === 1) walk(child);
        }
      };
      walk(h);
      inner.forEach((i, n) => i.style.setProperty("--d", (n * .055).toFixed(3) + "s"));
      h.classList.add("kinetic");
    }
  }

  const revealIO = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("in");
      revealIO.unobserve(e.target);
      if (e.target.classList.contains("stat")) e.target.classList.add("counted");
    }
  }, { threshold: .16, rootMargin: "0px 0px -8% 0px" });
  $$(".rise").forEach(el => revealIO.observe(el));

  function countUp(el) {
    const to = parseFloat(el.dataset.to || "0");
    const dec = parseInt(el.dataset.dec || "0", 10);
    const pre = el.dataset.prefix || "";
    if (CALM) { el.textContent = pre + to.toFixed(dec); return; }
    const finish = () => { el.textContent = pre + to.toFixed(dec); };
    el.closest(".stat")?.classList.add("counted");
    // a background tab may never tick rAF; never leave a counter at zero
    setTimeout(finish, 1600);
    const start = performance.now(), dur = 1100;
    const step = (now) => {
      const k = clamp((now - start) / dur);
      const v = to * expoOut(k);
      el.textContent = pre + v.toFixed(dec);
      if (k < 1) requestAnimationFrame(step);
      else el.textContent = pre + to.toFixed(dec);
    };
    requestAnimationFrame(step);
  }
  const statIO = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) { countUp(e.target); statIO.unobserve(e.target); }
  }, { threshold: .6 });
  $$("#stats .v").forEach(el => statIO.observe(el));

  let kosiStamp = 0;
  const stampIO = new IntersectionObserver((es) => {
    for (const e of es) {
      if (!e.isIntersecting) continue;
      const card = e.target, i = [...card.parentElement.children].indexOf(card);
      card.classList.add("in");
      setTimeout(() => {
        if (CALM) { card.classList.add("stamped"); return; }
        card.classList.add("stamped");
        if (i === 2) kosiStamp = performance.now();
      }, 420 + i * 140);
      stampIO.unobserve(card);
    }
  }, { threshold: .45 });
  $$(".exhibit").forEach(el => stampIO.observe(el));

  /* card sheen */
  $$(".card").forEach(card => card.addEventListener("pointermove", (e) => {
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    card.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  }, { passive: true }));

  /* copy */
  $$(".cmd").forEach(cmd => {
    cmd.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(cmd.dataset.copy); } catch { return; }
      cmd.classList.add("copied");
      const tip = $(".tip", cmd); const was = tip.textContent;
      tip.textContent = "copied";
      setTimeout(() => { cmd.classList.remove("copied"); tip.textContent = was; }, 1600);
    });
  });

  /* ── the run: one real recording, scrubbed ────────────────────── */
  const RUN = {
    seconds: 88.6, tokens: 485, steps: 22,
    leaves: [
      ["kind", "code", 0.1, "computed kind_of in code", "ask"],
      ["known", "memory", 0.1, "looked up 'me' in memory books", "the book"],
      ["past", "memory", 0.1, "looked up 'me' in memory talks", "the conversation"],
      ["ask", "code", 0.1, "computed outside_question in code", "true"],
      ["got", "code", 0.1, "computed no_help in code", "nothing yet"],
      ["known", "memory", 0.1, "looked up 'me' in memory books", "the book"],
      ["moment", "code", 0.1, "looked at the clock", "18:05"],
      ["past", "memory", 0.1, "looked up 'me' in memory talks", "the conversation"],
      ["first", "code", 0.1, "computed ask_prompt in code", "the prompt"],
      ["prompt", "code", 0.1, "computed with_talk in code", "the prompt"],
      ["reply", "model", 88.0, "asked model voice", "485 tokens"],
      ["turn", "code", 0.1, "computed spoken in code", "the answer"],
      ["book", "code", 0.1, "computed book_of in code", "the book"],
      ["saved", "memory", 0.1, "remembered the book under 'me'", "1 entry"],
      ["past", "memory", 0.1, "looked up 'me' in memory talks", "the conversation"],
      ["talk", "code", 0.1, "computed heard in code", "the conversation"],
      ["kept", "memory", 0.1, "remembered the conversation under 'me'", "1 entry"],
      ["said", "code", 0.1, "computed say in code", "the answer"]
    ]
  };
  const laneOf = k => (k === "model" || k === "think" || k === "agent") ? 0
    : (k === "tool" || k === "memory" || k === "board" || k === "files" || k === "web") ? 1
      : k === "code" ? 3 : 2;
  const nodeOf = k => (k === "model" || k === "think" || k === "agent") ? "n-model"
    : (k === "tool" || k === "web") ? "n-tools"
      : (k === "memory" || k === "board") ? "n-memory"
        : k === "files" ? "n-files" : "n-core";

  const beats = [];
  {
    let at = 0;
    for (const [name, kind, seconds, did, answer] of RUN.leaves) {
      const d = Math.max(0.1, seconds);
      beats.push({ name, kind, did, answer, at, end: at + d, leaf: true });
      at += d;
    }
    const total = at || 1;
    for (const b of beats) { b.at = (b.at / total) * RUN.seconds; b.end = (b.end / total) * RUN.seconds; }
  }

  function buildTimeline(rulerEl, lanesEl, withPlayhead) {
    if (!rulerEl || !lanesEl) return null;
    const names = ["Model", "Tools", "Steps", "Code"];
    const laneEls = names.map(name => {
      const d = document.createElement("div"); d.className = "tl-lane"; d.dataset.name = name;
      lanesEl.appendChild(d); return d;
    });
    let playhead = null;
    if (withPlayhead) { playhead = document.createElement("div"); playhead.className = "tl-playhead"; lanesEl.appendChild(playhead); }
    const major = 15;
    for (let t = 0; t <= RUN.seconds + .001; t += major) {
      const s = document.createElement("span");
      s.style.left = ((t / RUN.seconds) * 100).toFixed(2) + "%";
      s.textContent = t === 0 ? "0" : `${t}s`;
      rulerEl.appendChild(s);
    }
    for (const b of beats) {
      const clip = document.createElement("div");
      clip.className = "tl-clip k" + laneOf(b.kind);
      clip.style.left = ((b.at / RUN.seconds) * 100).toFixed(2) + "%";
      clip.style.width = Math.max(.4, ((b.end - b.at) / RUN.seconds) * 100).toFixed(2) + "%";
      clip.title = b.name;
      if (b.end - b.at > 3) {
        const label = document.createElement("span"); label.textContent = b.name; clip.appendChild(label);
      }
      laneEls[laneOf(b.kind)].appendChild(clip);
      b.clip = clip;
    }
    // a lane this run never touched says nothing; an empty row reads as a bug
    for (const el of laneEls) if (!el.querySelector(".tl-clip")) el.hidden = true;
    return playhead;
  }
  const playhead = buildTimeline($("#ruler"), $("#lanes"), true);
  buildTimeline($("#mruler"), $("#mlanes"), false);

  const runEl = $("#run");
  const timeEl = $("#time"), stepEl = $("#step"), progressEl = $("#progress");
  const orbitSvg = $(".orbit svg");
  // the playhead moves by transform, not by left: a scroll-driven move should
  // never ask the browser for layout. Its track's width is measured on resize.
  const track = playhead && playhead.parentElement;
  let trackW = (track && track.clientWidth) || 1, lastP = 0;
  if (track && "ResizeObserver" in window) {
    new ResizeObserver(() => { trackW = track.clientWidth || 1; setRun(lastP); }).observe(track);
  } else addEventListener("resize", () => { trackW = (track && track.clientWidth) || 1; setRun(lastP); });
  const nodes = ["n-you", "n-model", "n-tools", "n-memory", "n-files", "n-core"].reduce((acc, id) => {
    acc[id] = document.getElementById(id); return acc;
  }, {});
  // the orbit's arc and head: the same geometry the svg is drawn in, so no
  // measurement and no layout — two attributes per frame of scroll
  const trace = document.querySelector(".o-trace"), head = document.getElementById("ohead");
  // measured off the path itself, so the arc is a true fraction of the ring
  const ARC = (trace && trace.getTotalLength && trace.getTotalLength()) || 1169.2;
  if (trace) { trace.style.strokeDasharray = ARC + " " + ARC; trace.style.strokeDashoffset = ARC; }
  /* The dash is measured in arc length, so the head has to be too: on an
     ellipse of rx 250 / ry 108 a fifth of the *angle* is not a fifth of the
     *path*, and the dot would run ahead of its own arc. This table maps arc
     length back to the angle that produces it. */
  const ANGLE_AT = (() => {
    const N = 720, RX = 250, RY = 108, at = new Float64Array(N + 1);
    let run = 0, px = RX, py = 0;
    at[0] = 0;
    for (let i = 1; i <= N; i++) {
      const a = (i / N) * Math.PI * 2, x = Math.cos(a) * RX, y = Math.sin(a) * RY;
      run += Math.hypot(x - px, y - py); px = x; py = y;
      at[i] = run;
    }
    return (f) => { // f: 0..1 of the perimeter -> angle, radians from the right
      const want = clamp(f) * run;
      let lo = 0, hi = N;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (at[mid] < want) lo = mid + 1; else hi = mid; }
      const i = Math.max(1, lo);
      const span = at[i] - at[i - 1] || 1;
      return ((i - 1 + (want - at[i - 1]) / span) / N) * Math.PI * 2;
    };
  })();

  /* one ring out from the node that just took a beat: the only reward the
     diagram gives, and it is spent on the moment a step lands */
  const SVGNS = "http://www.w3.org/2000/svg";
  function waveAt(id) {
    const g = document.getElementById(id);
    const c = g && g.querySelector("circle");
    if (!c) return;
    const ring = document.createElementNS(SVGNS, "circle");
    ring.setAttribute("cx", c.getAttribute("cx"));
    ring.setAttribute("cy", c.getAttribute("cy"));
    ring.setAttribute("r", c.getAttribute("r"));
    ring.setAttribute("class", "o-wave");
    g.appendChild(ring);
    setTimeout(() => ring.remove(), 900);
  }
  let lastBeat = null;

  function setRun(p) {
    if (!playhead) return;
    const forward = p > lastP + .0005;
    lastP = p;
    const t = p * RUN.seconds;
    const mm = Math.floor(t / 60), ss = Math.floor(t % 60);
    timeEl.innerHTML = `${mm}:${String(ss).padStart(2, "0")}<b>.${Math.floor((t % 1) * 10)}</b>`;
    playhead.style.transform = `translate3d(${(p * trackW).toFixed(2)}px, 0, 0)`;
    progressEl.style.transform = `scaleX(${p.toFixed(4)})`;
    // a slow push through the whole recording: the camera is the scroll
    if (orbitSvg && !CALM) orbitSvg.style.transform = `scale(${(1 + p * 0.022).toFixed(4)})`;
    // the gold arc is the run's progress, and its head rides the ring; under
    // reduced motion the css holds the ring full and the head hidden
    if (trace && head && !CALM) {
      trace.style.strokeDashoffset = (ARC * (1 - p)).toFixed(1);
      const a = -Math.PI / 2 + ANGLE_AT(p);
      head.setAttribute("cx", (340 + Math.cos(a) * 250).toFixed(2));
      head.setAttribute("cy", (150 + Math.sin(a) * 108).toFixed(2));
      head.style.opacity = p > .002 ? 1 : 0;
    }
    let current = null;
    for (const b of beats) {
      const seen = p >= b.at / RUN.seconds;
      if (b.clip) b.clip.classList.toggle("seen", seen);
      if (seen) current = b;
    }
    for (const id in nodes) if (nodes[id]) nodes[id].classList.remove("now", "seen");
    const model = document.getElementById("n-model");
    if (model) model.classList.remove("ask");
    if (runEl) runEl.classList.remove("asking");
    if (current) {
      stepEl.innerHTML = `<b>${current.name}</b> — ${current.did} <span style="color:var(--faint)">→ ${current.answer}</span>`;
      const n = document.getElementById(nodeOf(current.kind));
      if (n) n.classList.add("now");
      if (forward && !CALM && current !== lastBeat) waveAt(nodeOf(current.kind));
      lastBeat = current;
      // the model call is the one costly step: the stage leans in for it
      if (current.kind === "model" && !CALM) {
        if (model) model.classList.add("ask");
        if (runEl) runEl.classList.add("asking");
      }
    }
    const lit = new Set();
    for (const b of beats) if (p >= b.at / RUN.seconds) lit.add(nodeOf(b.kind));
    lit.forEach(id => { const n = document.getElementById(id); if (n) n.classList.add("seen"); });
    if (p < .06 && nodes["n-you"]) nodes["n-you"].classList.add("now");
  }
  setRun(0);

  let runQueued = false;
  function onRunScroll() {
    if (!runEl || runQueued) return;
    runQueued = true;
    requestAnimationFrame(() => {
      runQueued = false;
      const r = runEl.getBoundingClientRect();
      const span = Math.max(1, r.height - innerHeight);
      const p = clamp(-r.top / span);
      setRun(p);
    });
  }
  addEventListener("scroll", onRunScroll, { passive: true });
  onRunScroll();

  /* ── the film reel ────────────────────────────────────────────── */
  (function reel() {
    const v = $("#reel"); if (!v) return;
    const tc = $("#tc"), playbtn = $("#playbtn"), soundbtn = $("#soundbtn");
    const fmt = s => `0:${String(Math.floor(s % 60)).padStart(2, "0")}.${Math.floor((s % 1) * 10)}`;
    const paint = () => {
      tc.innerHTML = `<b>${fmt(v.currentTime || 0)}</b> / ${fmt(v.duration || 18)}`;
      const label = $("span", playbtn);
      if (label) label.textContent = v.paused ? "Play" : "Pause";
      // A slow push in and back, on the film's own clock: the camera move a
      // recording of a screen would not otherwise have. It is a there-and-back
      // curve, not a one-way climb, so the loop point has nothing to jump.
      if (!CALM && v.duration) {
        const tri = 1 - Math.abs(1 - 2 * (v.currentTime / v.duration));
        v.style.transform = `scale(${(1 + sineInOut(clamp(tri)) * 0.035).toFixed(4)})`;
      }
    };
    v.addEventListener("timeupdate", paint);
    v.addEventListener("loadedmetadata", paint);
    v.addEventListener("play", paint); v.addEventListener("pause", paint);

    let userPaused = false;
    playbtn.addEventListener("click", () => {
      if (v.paused) { userPaused = false; v.play().catch(() => {}); }
      else { userPaused = true; v.pause(); }
    });
    /* the meter in the bar: an analyser on the film's own audio, drawn as bars.
       It is silent until someone asks for sound, and it says what the score is
       doing rather than moving on a timer. */
    const spec = $("#spectrum");
    const sctx = spec && !CALM ? spec.getContext("2d") : null;
    const BARS = 40;
    let ac = null, analyser = null, bins = null, specRaf = 0;
    function drawSpectrum(live) {
      if (!sctx) return;
      const w = spec.width, h = spec.height;
      sctx.clearRect(0, 0, w, h);
      if (!live) {
        // at rest: a row of ticks, so the meter reads as a meter that is
        // waiting rather than a gap in the bar
        sctx.fillStyle = "rgba(148,160,220,.26)";
        const gap0 = 2, bw0 = (w - gap0 * (BARS - 1)) / BARS;
        for (let i = 0; i < BARS; i++) sctx.fillRect(i * (bw0 + gap0), h - 2, bw0, 2);
        return;
      }
      analyser.getByteFrequencyData(bins);
      const gap = 2, bw = (w - gap * (BARS - 1)) / BARS;
      for (let i = 0; i < BARS; i++) {
        const v2 = bins[Math.floor(i * bins.length / BARS)] / 255;
        const bh = Math.max(1, v2 * h);
        sctx.fillStyle = `rgba(245,180,92,${(.32 + v2 * .62).toFixed(2)})`;
        sctx.fillRect(i * (bw + gap), h - bh, bw, bh);
      }
    }
    function specLoop() {
      const live = !!(analyser && !v.muted && !v.paused);
      drawSpectrum(live);
      if (live) specRaf = requestAnimationFrame(specLoop);
      else { specRaf = 0; drawSpectrum(false); }
    }
    function startAudio() {
      if (!spec || CALM) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!ac) {
        try {
          ac = new AC();
          const src = ac.createMediaElementSource(v);
          analyser = ac.createAnalyser();
          analyser.fftSize = 256; analyser.smoothingTimeConstant = .8;
          bins = new Uint8Array(analyser.frequencyBinCount);
          src.connect(analyser); analyser.connect(ac.destination);
        } catch { ac = null; analyser = null; return; }
      }
      ac.resume();
      if (!specRaf) specLoop();
    }
    v.addEventListener("pause", () => { cancelAnimationFrame(specRaf); specRaf = 0; drawSpectrum(false); });
    v.addEventListener("play", () => { if (!v.muted && analyser && !specRaf) specLoop(); });
    drawSpectrum(false);

    soundbtn.addEventListener("click", () => {
      v.muted = !v.muted;
      if (!v.muted) { v.volume = .9; startAudio(); v.play().catch(() => {}); }
      else { cancelAnimationFrame(specRaf); specRaf = 0; drawSpectrum(false); }
      $("span", soundbtn).textContent = v.muted ? "Sound on" : "Sound off";
    });
    v.muted = true;

    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting && e.intersectionRatio > .35) { if (!userPaused && !CALM) v.play().catch(() => {}); }
      else v.pause();
    }, { threshold: [0, .35, .6] });
    io.observe(v);
    paint();
  })();

  /* ── Kosi, the clerk: a boiling ink line ──────────────────────── */
  (function kosi() {
    const cv = $("#kosi"); if (!cv) return;
    const ctx = cv.getContext("2d");
    const S = 296;
    let raf = 0, running = false;
    const INK = "#101426", PAPER = "#f6eeda", GOLD = "#e8a44a", RED = "#e2543c";

    function draw(now) {
      const t = now / 1000;
      ctx.clearRect(0, 0, S, S);
      const seed = Math.floor(t * 10);
      const r = (() => { let a = (seed * 9301 + 49297) >>> 0; return () => ((a = (a * 1664525 + 1013904223) >>> 0) / 4294967296); })();
      const j = () => (r() - .5) * 5.5;
      const pressing = kosiStamp && now - kosiStamp < 480;
      const pose = pressing ? "press" : "idle";

      ctx.save();
      ctx.translate(S / 2, S / 2 + 12);
      ctx.scale(1.55, 1.55);
      ctx.lineJoin = "round"; ctx.lineCap = "round";

      // the shadow it stands on
      ctx.fillStyle = "rgba(16,20,38,.16)"; ctx.beginPath(); ctx.ellipse(0, 78, 46, 8, 0, 0, 7); ctx.fill();

      // legs
      ctx.strokeStyle = INK; ctx.lineWidth = 7;
      ctx.beginPath(); ctx.moveTo(-22 + j(), 58); ctx.lineTo(-26 + j(), 74);
      ctx.moveTo(20 + j(), 58); ctx.lineTo(26 + j(), 74); ctx.stroke();

      // body
      const bob = pressing ? -3 : Math.sin(t * 2.1) * 2.2;
      ctx.beginPath();
      ctx.moveTo(-46 + j(), 16 + bob);
      ctx.bezierCurveTo(-52 + j(), -46 + bob, 52 + j(), -46 + bob, 46 + j(), 16 + bob);
      ctx.bezierCurveTo(44 + j(), 62 + bob, -44 + j(), 62 + bob, -46 + j(), 16 + bob);
      ctx.closePath();
      ctx.fillStyle = PAPER; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 7; ctx.stroke();

      ctx.beginPath(); ctx.ellipse(0, 30 + bob, 28, 20, .1, 0, 7);
      ctx.fillStyle = "rgba(16,20,38,.06)"; ctx.fill();

      // eyes, with a blink on a script
      const blink = (Math.floor(t * 10) % 74 > 69) ? .14 : 1;
      for (const ex of [-17, 15]) {
        ctx.save(); ctx.scale(1, blink);
        ctx.beginPath(); ctx.ellipse(ex, -6, 7.5, 9.5, 0, 0, 7); ctx.fillStyle = INK; ctx.fill();
        ctx.beginPath(); ctx.arc(ex + 2.6, -9, 2.2, 0, 7); ctx.fillStyle = "#fff"; ctx.fill();
        ctx.restore();
      }
      ctx.beginPath(); ctx.arc(0, 12, 7, .15 * Math.PI, .85 * Math.PI);
      ctx.strokeStyle = INK; ctx.lineWidth = 4.5; ctx.stroke();

      // arms and the stamp
      const swing = pressing ? -26 : Math.sin(t * 2.1 + 1) * 5;
      ctx.strokeStyle = INK; ctx.lineWidth = 7;
      ctx.beginPath(); ctx.moveTo(-43 + j(), 20); ctx.lineTo(-62 + j(), 34 + swing);
      ctx.moveTo(43 + j(), 20); ctx.lineTo(60 + j(), 28 - swing * .5); ctx.stroke();

      ctx.save(); ctx.translate(64, 26 - swing * .5); ctx.rotate(pressing ? -.22 : .16);
      ctx.fillStyle = GOLD; ctx.beginPath(); ctx.roundRect(-9, -22, 18, 22, 4); ctx.fill();
      ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.stroke();
      ctx.fillStyle = RED; ctx.beginPath(); ctx.roundRect(-13, -30, 26, 10, 3); ctx.fill(); ctx.stroke();
      ctx.restore();

      ctx.restore();

      if (pressing) {
        const k = (now - kosiStamp) / 480;
        ctx.save(); ctx.globalAlpha = (1 - k) * .5; ctx.strokeStyle = INK; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.ellipse(S / 2 + 70, S / 2 + 30, 24 + 40 * k, (24 + 40 * k) * .3, 0, 0, 7); ctx.stroke();
        ctx.restore();
      }
      if (running) raf = requestAnimationFrame(draw);
    }
    const io = new IntersectionObserver(([e]) => {
      if (!e) return;
      if (e.isIntersecting && !CALM) { if (!running) { running = true; draw(performance.now()); } }
      else if (!e.isIntersecting) { running = false; cancelAnimationFrame(raf); }
    }, { threshold: .1 });
    io.observe(cv);
    // one static frame straight away, so the clerk is never an empty box
    draw(performance.now());
  })();

  /* ── the dot wipe: one world becomes the other ────────────────── */
  (function wipe() {
    const cv = $("#wipe"); const paper = $("#paper");
    if (!cv || !paper || CALM) return;
    const ctx = cv.getContext("2d");
    let w = 0, h = 0, active = false;

    function size() {
      w = innerWidth; h = innerHeight;
      cv.width = Math.round(w * Math.min(1.5, DPR)); cv.height = Math.round(h * Math.min(1.5, DPR));
      cv.style.width = w + "px"; cv.style.height = h + "px";
      ctx.setTransform(cv.width / w, 0, 0, cv.height / h, 0, 0);
    }
    size(); addEventListener("resize", size);

    const STEP = 74;
    function paint(k) {
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2, cy = h * .5;
      const diag = Math.hypot(w, h);
      ctx.fillStyle = "#efe6d2";
      ctx.beginPath();
      for (let y = -STEP; y < h + STEP; y += STEP) {
        for (let x = -STEP; x < w + STEP; x += STEP) {
          const d = Math.hypot(x - cx, y - cy) / diag;
          const local = clamp((k - d * .34) / .66);
          const rr = STEP * 1.4 * expoOut(local);
          if (rr <= .6) continue;
          ctx.moveTo(x + rr, y); ctx.arc(x, y, rr, 0, 7);
        }
      }
      ctx.fill();
    }

    // The dots reach full cover exactly as the paper section's own edge
    // reaches the top of the viewport. Past that point the section *is* the
    // paper, so the canvas goes away — holding it on would put a cream sheet
    // over the section's heading for half a screen of scrolling.
    function check() {
      const r = paper.getBoundingClientRect();
      const vh = innerHeight;
      const k = clamp(1 - r.top / (vh * .95));
      const on = r.top > .5 && r.top < vh * .95;
      if (on && k > 0.001) {
        if (!active) { cv.style.opacity = "1"; active = true; }
        paint(k);
      } else if (active) {
        cv.style.opacity = "0"; ctx.clearRect(0, 0, w, h); active = false;
      }
    }
    addEventListener("scroll", check, { passive: true });
    addEventListener("resize", check);
    check();
  })();

  /* pause the page's own rAF work when it is hidden */
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { orbit.pause(); heroFilm.pause(); }
    else if ($("#top").getBoundingClientRect().bottom > 0) { orbit.start(); heroFilm.play(); }
  });
})();
