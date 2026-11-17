# Kosika Intelligence — quantum-inspired layer

Date: 2026-11-03
Status: approved in conversation; slice 1 started (interference core built and tested)
Extends: `2026-11-02-kosika-mind-layer-design.md` (A1 loop: perceive → retrieve → think →
reflect → act → consolidate → spread). This document does not replace that loop; it gives
each stage a sharper meaning and adds the pieces the loop was missing.

## What "quantum-inspired" means here — and what it does not

Real quantum speedups (Shor, Grover) come from qubit physics, not from the shape of the
mathematics. Classical software cannot borrow them. What it can borrow is the algorithmic
discipline:

> carry many possibilities at once, let independent evidence amplify the right ones and
> cancel the wrong ones, then measure exactly once.

So this layer claims **quality at equal cost**, never a smaller complexity class. Every
comparison is "same model, same budget, better answers", measured by the eval harness
below. If a number does not move, the mechanism is dropped and the metaphor goes with it.
The vocabulary is an organising idea, not a marketing claim, and the code says so in its
own docstring.

## Mapping onto the existing loop

| Quantum idea | Kosika mechanism | Where it lands | State today |
|---|---|---|---|
| Superposition | think stage carries a population of candidate answers/paths instead of one greedy answer | `runtime._ask_model` + candidate generator | new |
| Interference | each *distinct* source that supports a candidate adds amplitude, each that contradicts subtracts; corroboration amplifies, contradiction cancels | `kosika/interference.py` | **built, 10 tests** |
| Measurement | act commits to exactly one candidate, or abstains when the best is too weak or two are indistinguishable | `interference.measure` → act stage | **built (core)** |
| Amplitude | the existing `confidence` on `MemoryBubble`/judgement becomes a learned prior; consolidate raises and lowers it from outcomes | `cognitive.py` + `mind.py` consolidate | new |
| Tunnelling | self-improvement may accept a worse-fitness move to escape a local optimum, bounded by the existing fitness gate | slice 3 of the mind layer | new, bounded |
| Entanglement | correlated KG updates: when one node's confidence moves, related nodes move coherently | `knowledge_graph.infer` + `graph_store` | partial |

The two "arms" from the conversation are not a separate organ: they are two **interference
sources**. The symbolic/checking arm and the statistical arm both speak; agreement between
them is what raises a candidate, and disagreement is what cancels it.

## Components

| Component | Role | Exists? |
|---|---|---|
| Interference core (`kosika/interference.py`) | candidates + evidence → one measurement or abstention, with a traceable record | **new, done** |
| Candidate generator | think stage proposes N candidates (different source mixes, different plans) | new |
| Evidence sources | KG, memory, statistical arm (`std/stats.kos`, embeddings), live web (`system_tools` `fetch` / `fetch_from`), the model itself | exist, need wiring |
| Measure + abstain | the act stage's single commitment | **core done**, act wiring new |
| Eval harness | three suites, scored, baseline vs quantum mode | new |
| Ledger | one row per measurement (what was chosen, on what evidence, confidence, cost) | pattern exists (designs bench) |

## The three suites you named

Every suite runs in two modes — **baseline** (single-path, current behaviour) and
**quantum-inspired** (population + interference) — so the difference is a number, not a
description. Same model, same budget, same questions.

| Suite | What it proves | Pass signals |
|---|---|---|
| Famous people | KG retrieval and interference over stable facts | correct answers, provenance attached, contradictions surfaced rather than averaged |
| News | live web evidence, freshness, source weighting | current answers with citations, and honest abstention when the web is unavailable |
| Kosika designs | the creativity loop end to end | valid designs drawn, reflect verdicts recorded, visible in Studio |

Abstention is a *pass*, not a failure: "I cannot tell from what I have" is the correct
answer when the evidence is thin, and the harness scores it as such.

## Error handling

- **No fresh sources** (web down): answer from KG + model, labelled as lacking fresh
  sources, confidence capped — never silently presented as current.
- **All candidates weak**: abstain with the reason, never pick the least bad.
- **Ties**: not measured. A coin toss dressed as judgement is worse than silence.
- **Contradiction**: recorded in the decision, never averaged away.
- **Self-improvement**: the interference machinery cannot touch the checker gate, fitness
  gate, sandbox or rollback. Bounded exploration happens strictly inside them.

## Slices

### Slice 1 — the interference core (started)
- [x] `kosika/interference.py`: `Evidence`, `Candidate`, `score`, `measure`, `record`.
- [x] One voice per source; two sources outrank one source twice; one source capped at
      `THIN_CAP`; contradiction cancels and is kept; abstain below `MEASURE_FLOOR`;
      ties unmeasured; order-independent.
- [x] `tests/test_interference.py` — 10 tests, no model, no network.
- [ ] Candidate generator for the think stage (needs a model; slice 2).

### Slice 2 — wiring and the first measured comparison
- Feed the think stage N candidates instead of one answer; score them with `measure`.
- Evidence from KG + memory + the model; web added for the news suite only.
- Ledger rows from `record(decision)`.
- Build the three suites in baseline and quantum modes; publish the first scorecard.

### Slice 3 — learning the amplitudes
- Consolidate moves `confidence` priors from measured outcomes: sources that proved
  reliable gain weight, sources that misled lose it. Bounded, reviewed, tested.

### Slice 4 — Approach 2, measured against Slice 2
- A standalone candidate-search service behind the same think/reflect interface, compared
  on the same three suites. Kept only if it beats the in-process version on quality,
  latency or auditability.

## Out of scope

- Real quantum hardware, qubit simulation, or any complexity-class claim.
- A new renderer or editor (the Mind layer reuses Designs, per the A1 spec).
- Self-granting of capabilities.
- Coding mastery and self-development: each is its own spec and plan, to follow this layer
  rather than be bundled with it.
