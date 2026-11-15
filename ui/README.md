# Kosika UI system

`ui/kosika.css` is the single source of truth for Kosika's interface. Two pages in the project
root read it: `kosika-preview.html` and `kosika-design-sheet.html`.

The SVG renderer in `kosika/design.py` and the `design/` manifests this file once shared a palette
with were retired along with the city pictures. The tokens stay because those two pages still use
them.

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
- `kosika-design-sheet.html` — the tokens themselves, drawn.
- `ui/legacy/kosika-preview.light.html` — the earlier light-palette preview, kept for comparison.