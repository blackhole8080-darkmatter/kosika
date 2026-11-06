# Stress test: designs, canvas, art

Run 2026-11-04, model `nvidia/nemotron-3-ultra-550b-a55b:free` through OpenRouter,
Kosika Brain off so no kept canvas was reused. `python stress.py` draws each ask;
`python stress.py --from-files` measures what was drawn without asking again.

The variety was named in each ask on purpose: these tests measure how much a
variety can hold, not how well Kosika picks one (`run.py --choices` does that).

## Before

| Ask | Variety | Seconds | Result |
| --- | --- | --- | --- |
| the whole human digestive system, mouth to colon | diagram | 76.3 | drawn: 16 cards, 14 links, 23 labels, 69 words |
| how a database commits a transaction (WAL, fsync) | diagram | 52.6 | drawn: 8 cards, 6 links, 11 labels |
| how a nuclear reactor makes electricity | canvas | 142.0 | drawn, but led with art and pins |
| how the immune system answers a viral infection | canvas | 164.6, 122.6 | **failed twice**: function-call budget exceeded |
| a thunderstorm over a city at night | art | 28.8 | "Midnight Storm Over City" |
| a coral reef in deep water at dawn | art | 33.4 | "Dawn Currents Over Reef" |

## After

| Ask | Seconds | Result |
| --- | --- | --- |
| d1, digestive system | 89.6 | drawn; captions valid and read once |
| c1, nuclear reactor | 201.1 | drawn; opens with the diagram, played |
| c2, immune system | 330.6 | **drawn**; opens with the diagram |

Checks: `kosika check` clean on `designs.kos` and `designs_draw.kos`;
117 + 59 program tests pass.

## What changed

1. **Captions are valid SVG** (`designs_draw.kos`, the note branch of
   `part_svg`). The caption used to be
   `<text class="cardhead"><title>note</title>note</text>`: a `<title>`
   nested inside `<text>` is not allowed, so the string existed twice in the
   DOM. The full note is now a `<title>` on the card's `<g>` — still the
   tooltip, no duplicate text. Measured: `title-in-text: 0`, `group titles: 8`.
2. **A structure ask leads with the diagram.** `dz_stage` now takes the
   canvas's own `lead`, and it gates the two picture-drawn styles: a journey
   or living-card stage is only used when the canvas says it leads with the
   picture. A diagram-led canvas gets the two-column stage with the played
   diagram on the left. Before, `why` said "leads with the diagram" while the
   page showed art with pins on it.
3. **The front door gets a real function-call budget** (`designs.py`). Only
   the Brain path asked for more than the 10,000 default; a plain
   `design()` call took the default, which is what stopped the immune-system
   canvas. Now every path passes `--max-function-calls`, default 100,000
   (`KOSIKA_DESIGNS_CALLS`).

## Still open

- **Canvas got slower**: 142 s → 201 s and 157 s → 331 s. It now renders the
  diagram page in an iframe as well as the picture, and the provider's
  latency varies. 5 minutes is a long wait for a canvas.
- **The played diagram dims its unlit parts**, so a still frame of a canvas
  reads faint at the size it is shown. Stepping through lights each part in
  turn; a screenshot catches it mid-play.
- **The canvas label numbers above undercount**: once the diagram lives in an
  iframe its labels are HTML-escaped, so `look()` stops seeing them. The
  `labels=2..4` and `overlaps=0` figures for canvases measure the page around
  the frame, not the diagram inside it.

## Also unchanged (found, not fixed)

- Art still does not depict: "thunderstorm over a city, lightning in wet
  streets" became a `flow` piece; the nouns are dropped, the mood kept.
- Art's rate-limit fallback is silent: `gen_fallback()` returns "Golden Bloom"
  with `problems: []`, so a caller cannot tell a real piece from the default.

## Evidence

`d1-after.png`, `c1-after2.png`, `c2-after2.png` (after the fixes) beside the
originals `d1/d2/c1/a1/a2.png`; pages as `*.html`; per-ask rows in
`report.json`; logs `run-after.log`, `run-after2.log`; the rate-limited first
attempt in `groq-quota/`.
