# Kosika roadmap: toward self-learning agents and better-than-Python AI software

Written 16 October 2026. This is the bridge between what the vision wants
([KOSIKA_VISION.md](KOSIKA_VISION.md)), what is honest about the present
([KOSIKA_OVERVIEW.md](KOSIKA_OVERVIEW.md)), and what the backlog tracks
([KOSIKA_PLAN.md](KOSIKA_PLAN.md)). Every phase ends with a measured number,
in the same spirit as the researcher-versus-Python comparison and `spec-eval`
before it.

## The charter

- The model is a **tutor, not a dependency**. Every model step has the chance
  to amortize: ask once, keep the accepted answer as provenance-checked
  knowledge, and answer alone next time.
- An agent carries its own **skills, knowledge, memory, senses and profile**,
  all declared in the language, all granted per-run, all replayable.
- **Models are optional**, used when resident knowledge cannot answer, under
  an escalation contract the checker enforces.
- **Swarms** divide tasks into narrow bounded roles, so novelty concentrates
  in one escalatable step; agents lend verified competence to each other
  through typed, journaled message channels.
- Kosika's job is **AI software development** — skills, capabilities,
  parsing, coding, design, research — and it must beat Python for that work
  by measurement, not by taste.

One naming rule, held forever: agents that answer alone are
**model-amortized**, never "model-free". The model compiled the knowledge;
that is the honest and stronger claim.

## Phase 0 — Prime form (the success of everything that follows)

Tidy the body before training it.

- **Retire the dead weight.** [RETIRE_LIST.md](RETIRE_LIST.md) already names
  the groups and their import archives. Recommendations 1, 4 and 5
  (city pictures, the first desktop app, the Python authoring loop) are the
  clear ones; that is about 10.7k lines plus most of the 13-minute test
  suite's cost. After each group goes, delete the reasoning for the ones
  retired so it does not regrow.
- **Decide the fate of `intelligence.py`.** Either its useful parts become
  language-backed or it is retired. It stays quarantined otherwise.
- **Done when:** the full suite runs in a small fraction of today's 13
  minutes, and no support-status experiment remains reachable only by
  commands Studio never calls.

## Phase 1 — The independence number comes first

You cannot improve what you don't count.

- **A census on every learning run.** From the trace, count: model steps
  answered in total and how many were answered from kept lessons, by how
  (`same`, `close`, `examples`). The `model_return` events already record
  the `learned` origin, so this is aggregation, not new semantics.
- **A task set to weight it.** Reuse the `spec-eval` scaffolding or the
  researcher task set: a fixed set of questions run cold-start (every step
  to the model, everything kept) and then re-run with no model.
- **Done when:** `kosika run` reports its lesson census, and a task set
  records a **model-independence ratio** — the share of model answers a
  re-run gives without asking a model — as a number in a results file.
- **Implemented:** `kosika learn-eval SOURCE --tasks TASKS --output NEW_DIR`
  runs bounded fixture-backed cold and provider-free warm cases, judges both
  against exact expected results, validates each trace by replay, and writes
  `report.json`, raw traces and readable lesson files. The fixed starter set
  is `benchmarks/learning/tasks.json`; its 3/3 cold and warm results establish
  harness behavior only, not live-model quality or broader generalization.
  Existing output directories are refused rather than overwritten.


## Phase 2 — Lessons that actually match

The resident answer only helps if the resident question is recognized.

- Replace the word-overlap likeness with **semantic similarity**, from
  [embeddings.py](kosika/embeddings.py), computed locally so a run without
  the model is still a run without any remote service.
- Keep the same thresholds, same fallbacks, same trace behavior; measure the
  independence ratio before and after on the same task set.
- **Done when:** the ratio rises on the Phase 1 task set, or the data shows
  word-overlap was already enough (also publishable honesty).

## Phase 3 — Knowledge with a lifecycle

Learned memory that cannot be corrected adaptively compounds its errors.

- Give lessons and memory statuses: `unverified → supported → trusted →
  contradicted → archived` (from the vision's memory bubbles). A model
  answer enters `unverified` — that alone never auto-promotes.
- Promotion needs **checkable evidence**: a quote that exists in a source
  step, a test that passed, a fixture hash. Contradiction detection marks a
  lesson `contradicted`; a `forget` step retires what is proven wrong.
- Replay must reproduce every promotion.
- **Done when:** a poisoned lesson (kept once, wrong) is demonstrably
  corrected by evidence in a task run, in the traces.

## Phase 4 — The escalation contract

"Models optional, used when necessary" becomes a checked property of the
program, the way read-only tools and memory folders are checked today.

- Every `learn` step may declare what to do when resident knowledge cannot
  answer: ask the model (cold-start, once), borrow a pooled lesson, or
  refuse to its `otherwise`.
- The checker rejects a program that claims to run without a model and has
  a step with no escalation path. The trace records which steps consumed
  model budget.
- **Done when:** a program declaring `no model` starts and runs to the end
  exactly when its census allows — refused clearly when it does not.

## Phase 5 — Senses and profile

- Sensors join `ambient`: declared, typed, granted per-run like memory
  folders. A sense a program was not granted is refused before start.
- A `profile` shapes budgets, retries, escalation strictness and
  explanation depth only. The checker rejects a profile that touches
  permissions or tools.
- **Done when:** a granted-senses agent behaves differently than a
  refused-senses one in the checker's output, and a profile changes a run's
  budgets visible in the trace.

## Phase 6 — The swarm and the lesson pool

The payoff phase, deliberately last, built on everything above.

- Typed message channels between agents, lowered into tools exactly as
  memory is: declared, journaled, replay-safe.
- A **provenance-gated lesson pool**: one agent's lessons become another
  agent's priors only at `supported` or better, so one hallucination cannot
  become swarm policy (this is the immune system; it goes in with the
  pool, not after).
- Consensus as typed quorum votes over checkable answers.
- **Done when:** a **swarm independence ratio** is measured — the share of
  work units a cold-started swarm completes with no model — and beats the
  single-agent ratio on the same tasks.

## Phase 7 — Better than Python, by measurement

The claim the vision finally runs on, and the one this roadmap exists to
deserve.

- Extend the comparison harness past the researcher N=1: several task
  families from `benchmarks/`, written by more than one author when
  possible, measured for lines, correctness, and now also the independence
  ratio Python cannot offer at any length.
- **Done when:** the same tasks take fewer working lines in Kosika than in
  checked Python, with the guarantees Python lacks, run and replayed by
  Kosika's own tooling.

## What is deliberately not promised

- Hosted models, distributed runtimes, and long-running multi-device
  agents remain out of scope until the phases above land.
- No phase relaxes approvals, grants, or replayability to make a number
  look better.

The next step is Phase 1: the census, so every later claim is in the same
units as the first one.
