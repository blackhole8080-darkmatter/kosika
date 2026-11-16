# Kosika Mind layer — design (A1)

Date: 2026-11-02
Status: approved in conversation, pending spec review
Supersedes: nothing; this is the first Mind-layer design.

## Goal

Give Kosika a cognitive loop that lives in the **runtime**, not in any one agent, so every
model — including future virtual models — inherits it. The loop must **think, reflect,
resonate and improve**; its memory must **gather, use and relate information across fields**;
and it must **spread** what it learns across a declared network. Every existing Kosika
component must be utilized when asked or needed — nothing is dead weight.

## Locked decisions

1. **Platform layer.** The loop lives in `runtime.py`, not inside a single agent program.
2. **Approach A1.** A declarative component registry in `std/mind.kos` that the perceive-stage
   dispatcher consults on every request, so utilization is a property of the engine.
3. **Bounded-autonomous self-improvement.** Checker gate + fitness gate + hashed rollback +
   sandbox trial; the improver can never grant itself capabilities.
4. **Federation in slice one**, reusing the `network.py` manifest; no consensus protocol.
5. **Canvas = reuse.** The Mind layer draws through the existing Designs canvas
   (`std/designs.kos` `dz_canvas`) and is viewed in the existing Studio Designs/study surface.
   No new renderer and no new editor are built.

## Architecture — the loop and its real hooks

`perceive → retrieve → think → reflect → act → consolidate → spread`

| Stage | Responsibility | Hook (verified) | State today |
|---|---|---|---|
| perceive | route the request to the right agent/component | `routing.py` `route_question` / `by_words` | exists (agents only); registry extends to components |
| retrieve (resonate) | pull related memory + KG neighbours into the prompt | `runtime.py` `_ask_model`; `knowledge_graph.semantic_search` | KG exists but **in-memory only** → persist via `durable.py` |
| think | the model call with thinking verbs | `runtime.py` `_ask_model` | exists |
| reflect | validate the answer (schema, evidence, confidence); retry/vote/escalate | after `_ask_model` returns | **new** |
| act | tool calls under grants | `runtime.py` `_call_declared_tool` | exists |
| consolidate (improve) | distil lessons; bounded self-improvement tick | `learning.py` + `services.py` scheduler | `learning.py` is a facade, fresh per call |
| spread (sync) | push memory deltas + learned rules to declared peers | `network.py` manifest + swarm wrapper | `swarm.py` has **no transport** |

Slice one closes the three bolded gaps: KG persistence, the reflect stage, and a minimal
federation transport.

## Canvas — reuse Designs, add only a persistence shim

Kosika already ships the renderer and the viewer:

- Renderer: `std/designs.kos` — nine kinds including `"canvas"`; `workflow dz_canvas` draws
  structure + moving picture (+ reaction) on one canvas and returns `svg`, `page`, `embed`.
- Viewer: Studio `/api/design` (the front door Chat and agents share) and `/api/study`
  ("words, the person's notes, and a Designs canvas"); the Designs sidebar area; and the
  designs agent at `examples/agents/designs`.

Therefore the Mind layer does **not** build a canvas. It:

1. **Writes** each stage's artifact through the existing front door (`designs.design()` /
   `dz_canvas`): perceive → route map, retrieve → KG-neighbourhood mind-map, think → plan,
   reflect → verdicts, consolidate → lesson notes, spread → peer map.
2. **Persists** each drawn artifact beside `memory/` (a page is ephemeral today; `study`
   keeps notes but not the page). A thin shim saves `kind/title/svg/page` and lists them in
   the Designs tab, so the person can scroll back everything the AI made.

Because artifacts derive from the trace and the KG, the canvas stays checked and replayable.
No free-form pixel editor: unbounded and un-checkable.

## Utilization registry and dashboard

- `std/mind.kos` declares every component with *when to use it*: designs, component SDK,
  stdlib (math/science/search/paths/stats), skills, memory + knowledge graph, tools
  (files/shell/web), swarm + services.
- The perceive dispatcher consults the registry on every request, so a design ask routes to
  Designs, a code task to the component SDK, a recall question to memory/KG.
- A new `/api/mind` endpoint in `app.py` reports per-component utilization, so "is everything
  being used?" is answerable at a glance.

## Data flow — one request end-to-end

request → registry picks agent + components → retrieve injects memory + KG context → model
answers → reflect validates (rejects `confidence=7`, invented labels, wrong types — the exact
bugs compare-python caught) → act under grants → trace written → consolidate distils a lesson
→ spread offers it to peers → canvas artifacts drawn and persisted.

Every step lands in the JSONL trace, so the whole loop **replays offline identically**.

## Self-improvement safety (bounded-autonomous)

- **No self-granting** — the improver can never add capabilities; grants stay human-approved.
- **Checker gate** — any self-modified code must pass `kosika check`.
- **Fitness gate** — keep a learned rule only if the stage it came from then passes its tests
  (the OS-builder precedent).
- **Hashed + rollback** — every modification content-hashed; instant rollback to last-good.
- **Sandbox trial** — trials run in `--in-session` before promotion.

## Federation (slice one)

Reuse the `network.py` manifest (nodes, modules, memory_graphs; policy default `observe`,
`remote=False`). The spread stage propagates memory deltas + learned rules to declared peers
over a minimal file/stdio handoff, wrapped in a swarm run (one approval, budgeted, replayable).
No consensus protocol — deliberately out of scope.

## Testing

- Unit: KG persistence round-trip; retrieve-injection; reflect-retry; registry dispatch.
- Integration: live-model end-to-end with reflect; two-node federation handoff; Mind→Designs
  canvas produces a persisted artifact.
- Replay: every Mind run replays offline identically.
- Utilization: a test asserting each registered component is dispatchable.

## Slices

1. KG persistence + retrieve + reflect + minimal federation propagation — the core loop, live
   on one model.
2. Registry + dispatcher + `/api/mind` dashboard + Mind→Designs-canvas wiring + the canvas
   persistence shim.
3. Consolidate / improve (bounded self-improvement) + services tick.
4. Federation hardening + swarm transport.

## Out of scope

- A free-form drawing editor or new canvas renderer (reuse Designs instead).
- Consensus / quorum across peers.
- Self-granting of capabilities.
- Virtual models (to be specified separately by the author).
