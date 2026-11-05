<p align="center">
  <img src="media/kosika-logo.png" alt="Kosika Studio" width="480">
</p>

# The Kosika website

**Live at <https://blackhole8080-darkmatter.github.io/kosika/>** — served by
GitHub Pages from this folder, on every push that touches it.

One self-contained page that says what Kosika and Kosika Studio are: a
language for agents whose programs are checked before they run, granted power
by power, and recorded run by run — and a Studio where you can read and replay
what happened without asking a model anything.

![The hero](shots/site-hero.jpg)

- `index.html` — the whole page. No build step, no dependencies, no network
  calls. The typeface (Figtree, SIL Open Font License — see
  `fonts/Figtree-OFL.txt`) is served from the same folder.
- `assets/site.css`, `assets/site.js` — the two worlds (ink screen, cream
  paper) and the motion.
- `assets/mark.svg` — the mark: a gold ring, a dot resting on it, and K from
  Figtree's own outline, so it needs no font to render. It is also the
  favicon.
- `media/` — the film, its poster, and the logo.

## What is on the page

| Section | What it does |
| --- | --- |
| The hero | The headline rises letter by letter while an orbit draws itself and a dot travels it. Behind it, the film's own run moves — silent, soft, four seconds mirrored. The floating chips are the language's words. |
| The film | *One Run, On Paper*, 18 seconds, with sound. |
| What it is | Four claims: checked, granted, recorded, replayable. |
| The run | A real recorded run, replayed by your scroll: the diagram lights up, the timeline scrubs, the clock ticks. |
| Studio | The window it was recorded in, with a recorded run inside it. |
| The paper file | The film's second half as a dossier — three stamped exhibits and Kosi, the clerk. |
| Start | Three commands, and the honest FAQ. |

![The film](shots/site-film.jpg)

![The run, scrubbed](shots/site-run.jpg)

![The paper file](shots/site-paper.jpg)

## The loop in the hero

The hero plays the film itself, not a second animation: a two-second slice of the
running timeline (5.15s–7.15s), cropped past the recording's clock, soft-focused
and masked, at 22% over the ink. It is mirrored into a four-second loop, so the
clips fill the lanes and then empty them — no cut, no jump back.

```text
# 1. the slice, from the composition
node film/render.mjs --from 5.15 --to 7.15 --fps 30 --crf 24 --out /tmp/hero-run.mp4

# 2. crop the clock out, mirror it into a loop
ffmpeg -i /tmp/hero-run.mp4 -filter_complex \
  "[0:v]crop=1380:520:530:295,scale=1280:482,split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1[o]" \
  -map "[o]" -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p -movflags +faststart -an \
  media/kosika-hero-loop.mp4
```

140 KB, no audio track at all. It pauses when the hero leaves the viewport, is
hidden under `prefers-reduced-motion`, and is not served to phones at all.

## The film in the middle

**One Run, On Paper** is the page's centrepiece and it is code. One HTML file
draws every frame from a single number: at time `t`, the same canvas is
produced on your screen as in the encoder. There is no footage and no video
model.

- `film/index.html` — the composition. `render(t)` is the whole film; open it
  with `?t=11.9` to see one frame, or `?play` to watch it loop.
- `film/render.mjs` — the renderer: a headless browser seeks the page frame
  by frame, crops the canvas, averages 180°-shutter subframes in place, and
  pipes them to ffmpeg.
- `film/score.py` — the score, synthesised from the same timeline: one room,
  a low bed, three paper thuds, one bell.

The beats:

| Time | On screen | What carries over |
| --- | --- | --- |
| 0.00 | a resting dot in the dark | the dot |
| 1.00 | the dot draws its own orbit | the ring is an iris |
| 4.60 | the run: lanes, playhead, clips | the playhead is the dot |
| 7.20 | halftone dot wipe | the playhead's dot seeds the wipe |
| 7.35 | a case file: three exhibits, stamped | the paper |
| 13.40 | pull back: one file of many | the desk is paper too |
| 15.30 | the wordmark, and the period | the period is the dot |

![Six frames from the finished cut](shots/film-contact-sheet.jpg)

Rendering it again:

```text
node tools/serve.mjs 8099
node film/render.mjs --out ../media/kosika-film.mp4 --fps 30 --workers 4
node film/render.mjs --stills 2.4,11.9,17.2 --out ../media/stills/
```

540 frames take about a minute. The score is synthesised by `film/score.py`
from the same timeline and muxed with ffmpeg (`-c:v copy`, AAC), so picture
and sound stay in step by construction.

## Design and motion

The motion follows a small written system rather than improvisation per
section. It was drawn from the design skills kept beside this repo in
`Aryan_Private/programs` — Hallmark's floor, garden-skills'
`web-design-engineer` and `motion-showreel` craft notes, `baoyu-design`, the
style library in `awesome-design-skills`, `ui-ux-pro-max`'s animation rules
(spring/physics curves, exits 60–70% of entrances, one or two moving elements
per view, 44px tap targets), the cinematic reference in
`awesome-claude-design/design-md/cinematic`, and the motion-graphics prompts in
`awesome-opus5-5-videos` (draw-on marks, kinetic type, a smear behind anything
that travels):

- **One movement at a time.** Siblings arrive 60ms apart, entrances take
  0.55s, exits 0.22s, and nothing but `transform` and `opacity` is animated.
- **Three motion characters, in tokens:** `--settle` (arrives and stays),
  `--snap` (hard start, long glide), `--spring` (overshoots, for things that
  land — the stamps).
- **The scroll is a camera.** The recording's stage pushes in 2% across its
  320vh; the film pushes in and back over its own 18 seconds, on its clock, so
  pausing holds the frame still.
- **The orbit is the timeline.** A gold arc runs round the run's ring, growing
  with the scroll, its head the same dot the film travels on. Both ends measure
  *arc length*, not angle: on an ellipse of rx 250 / ry 108 a fifth of the angle
  is not a fifth of the path, and the dot would run ahead of its arc.
- **Kinetic type, once.** Section headlines split into words at load, each
  rising out of its own mask 55ms after the last. The parent does not fade as
  well — one movement. Reduced motion keeps the plain line.
- **The dot has a comet.** Six short arcs of falling alpha behind it, in the
  dot's own colour rather than the ring's gold, so it reads as travel.
- **Atmosphere behind the hero.** Two fields of light drifting on 34- and
  44-second alternates, transform-only, gone entirely under reduced motion.
- **The chips sit at different depths.** They already drift on their own
  timers; scrolling now lifts them apart, each by its own depth.
- **A step landing sends one ring out** from the node that took it — spent on
  the moment, not looped. The beats are front-loaded (the model call is most of
  the run), so the rings cluster where the steps are.
- **The meter in the reel bar is the film's own score,** drawn by an analyser
  on the audio element rather than on a timer. It ticks flat until you ask for
  sound, and it is hidden on a phone.
- **Buttons lean a few pixels toward the pointer,** on the same spring, on a
  pointer with a real hover. The hover lift rides on top rather than replacing
  it, and the card glyphs ring and tilt on hover and focus.
- **Events drift past the stage.** Three trace lines float in the run's side
  margins, gone below 1180px and under reduced motion — the hero's floating
  motif, but saying something the diagram cannot.
- **The Studio window arrives as a product shot:** tipped back 9°, then squared
  up on the spring. It settles in place; nothing keeps moving afterwards.
- **The playhead moves by `transform`,** measured against a cached track
  width, so scrubbing never asks the browser for layout.
- **Focus is a state, not a fade:** rings appear instantly, the command blocks
  are `<button>`s, every control a thumb has to hit is 44px or taller, the body
  sits at a 16px base, and the film's camera move is skipped under
  `prefers-reduced-motion`.
- **The hero is one screen:** headline, one 21-word sentence and an action
  above the fold at 1280×800.

One deliberate deviation: the tracked-caps `overline` above each section is
this site's own editorial system, kept on purpose against the skill's ban on
small-caps labels. The paper's stamps land on the exhibit artwork, never across
the note they stamp.

## Serving it

```text
node tools/serve.mjs 8099          # http://127.0.0.1:8099/
```

`tools/serve.mjs` is dependency-free and supports byte ranges, which matters:
the reel is a real `<video>`, and seeking it needs `206 Partial Content`.
`python -m http.server` does not send that, so the film plays but cannot be
scrubbed. Any static host (GitHub Pages included) is fine.

To publish on GitHub Pages, serve this folder as the site root — the workflow
in `.github/workflows/pages.yml` already uploads `site/` on every push that
touches it.

## Rebuilding the pictures

```text
node tools/capture-shots.mjs       # shots/site-*.jpg, 1440×900 and 390×844
node tools/make-logo.mjs           # media/kosika-logo.png, media/kosika-mark.png
node tools/logo.html               # the logo lockup, as a page
```

Both need `puppeteer-core` and a Chrome install (`CHROME_PATH` overrides the
default). The screenshots are taken from the live page and park the reel on a
frame worth photographing, so they always match the code.

## Notes

- The page needs no server: the recorded run is baked in as static data
  (`const STORY`), and replay asks no model — which is exactly the point it
  makes. To bake in a different run, replace `STORY` with the JSON a running
  Studio returns from `/api/story?id=<agent>&trace=<trace>`.
- Motion is written for people who want to see it: the hero's first paint is
  synchronous, the headline and counters have timer fallbacks, and everything
  has a `prefers-reduced-motion` resting state (and a `<noscript>` one).
- Every colour on the page and in Studio comes from one token set, so the site
  re-grades itself with the same Dark Matter identity the app wears.
- `node tools/check.mjs` walks the page at 1440 and 390, with a
  reduced-motion pass, and asserts what the last review verified by hand: no
  overflow, no console errors, every image decoded, the paper section clear of
  the wipe canvas, the stamps clear of the exhibit notes, the run scrubbing
  (playhead advancing by transform, the orbit's arc following it, the dot
  riding the ring), the hero film playing and the reel seeking, the hero inside
  one screen, focus rings present, 44px tap targets, and the floating chips and
  the film gone on a phone.
