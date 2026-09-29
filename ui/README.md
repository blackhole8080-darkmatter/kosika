# Kosika UI system

`ui/kosika.css` is the single source of truth for Kosika's interface. The SVG renderer in
`kosika/design.py` mirrors the same palette, so a diagram the engine generates and a surface a
human reads share one visual language.

This directory is deliberately separate from `design/`, which holds deterministic design
*manifests* and generated artifacts.

## Doctrine

**Structure is cyan, intelligence is violet, authority is amber.** A colour is never decoration. If
you cannot name the meaning of a hue in one word, it does not get one.

**Glow means "now".** Three levels exist, and only live state and focus rings use them. A static
diagram is flat. If everything glows, nothing is live.

**Numbers are monospaced and tabular.** Digests, sequence numbers, line counts, hashes, positions.
Alignment is information.

**Diagrams are evidence.** Every figure on a surface shows structure the engine derived. If a model
explains a diagram, the explanation is separate from the diagram.

## Tokens

Surfaces are cool near-blacks with a blue-green cast, so the neon accents read as light on glass
rather than as stickers.

| Token | Value | Use |
| --- | --- | --- |
| `--void` | `#04060A` | Focus-ring cutout, deepest recess |
| `--canvas` | `#070A0F` | Page background |
| `--surface` | `#0B1017` | Cards, resting panels |
| `--surface-2` | `#0F1620` | Raised rows, inputs, chips |
| `--raised` | `#131C27` | Hover |
| `--editor` | `#080C12` | Code surfaces, figure plates |
| `--line` | cyan @ 10% | Hairlines |
| `--line-strong` | cyan @ 20% | Chip and input borders |
| `--line-live` | cyan @ 42% | Live borders only |

## Signal

| Token | Value | Meaning |
| --- | --- | --- |
| `--cyan` | `#22D3EE` | structure — declarations, steps, links, the graph |
| `--violet` | `#A78BFA` | intelligence — models, agents, inference |
| `--amber` | `#FBBF24` | authority — a declared capability to act |
| `--lime` | `#4ADE80` | verified — passed, complete, replayed |
| `--rose` | `#FB7185` | failure — errors, denials |
| `--slate` | `#64748B` | inert — fixtures, offline, disabled |

Node kinds in the generated diagrams follow the same rule:

- cyan — `workflow`, `stage`, `module`, `space`
- violet — `model`, `agent`, `memory`
- amber — `tool`, `approval`
- lime — `function`
- slate — `record`, `network_node`
- distinct — `device` `#38BDF8`, `network` `#F472B6`, `space` `#2DD4BF`

## Type

- Sans: Inter, `Segoe UI Variable`, system-ui — prose, headings, controls.
- Mono: JetBrains Mono, Cascadia Code, `SFMono-Regular` — code, every numeral, every label.
- Eyebrows are mono, uppercase, `0.18em`, and always faint. They orient; they never compete.
- Display sizes tighten tracking (`-0.03em` at 46px down to `-0.008em` at 15px).

## Motion

`--fast: 120ms`, `--slow: 240ms`, both on `cubic-bezier(0.2, 0.7, 0.3, 1)`. Two animations exist in
the whole system: the live event pulse, and the dashed flow on an in-flight edge. Everything
respects `prefers-reduced-motion`.

## Surfaces in this system

- `kosika-preview.html` — language, workflow graph, mindmap, trace, capability matrix, agent loop.
- `kosika_results_preview.html` — a run report; its body is real output and is not restyled, only
  the shell is mapped onto these tokens.
- `kosika/workbench_assets/styles.css` — still on the original light palette. Adopting these tokens
  there is a mechanical swap, because the file is already organised by component.

## Renderer themes

`kosika design show --format svg` takes `--theme lab` (default) or `--theme light`. The theme is
presentation only: `DesignManifest.digest` is computed over the manifest, so retheming never
invalidates a digest, a trace, or a replay. Node ids, kinds, and typed edges remain authoritative —
see `design/DESIGN.md`.

```text
python -m kosika.cli design show design/test-host.json --format svg --view mindmap --theme lab
```
