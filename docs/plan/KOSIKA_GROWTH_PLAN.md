# Kosika growth plan

Written on 2026-11-12 by three Kosika coder agents working at once, each a real
coder run through Kosika's model router, then joined and checked against what
Kosika already has. Their own parts are kept beside this file:
`PLAN_ENGINES.md` (coder-1), `PLAN_MODELS.md` (coder-2), `PLAN_AGENTS.md` (coder-3).
Their runs, with the story of each, are in Kosika Studio (`kosika-daily/studio-swarm`).

**How good were the parts?** coder-2's models plan is grounded in Kosika and
mostly right. coder-1 and coder-3 only read KOSIKA.md, so they proposed
things Kosika already has (a coder agent, hands, a swarm, logs) and some that
do not fit it (a Python "hello world" benchmark, a separate Go type-checker,
while both engines share one checker contract). Below, every step is either
taken from a part as it is, corrected, or marked as already done.

The goal throughout: **the Kosika coder agents develop Kosika, on Kosika's own
models, with the cloud only as a teacher.**

---

## 1. Kosika's own models (from coder-2, corrected)

| # | Step | State | Done when |
|---|------|-------|-----------|
| 1 | Declare `kosika-small` and `kosika-coder` as models programs can name | **open** | a workflow names them in `use_model` and `kosika check` accepts it |
| 2 | Layers in Kosika (`std/layers.kos`) | **done** today | a 5-layer net learns to 99% on unseen code |
| 3 | Training in Kosika (`std/learn.kos`, batch maths in both engines) | **done** today | language specialist: 27% → 99% |
| 4 | Run downloaded GGUF models inside the Go engine, no Ollama | **open** | kosika-coder answers with Ollama stopped |
| 5 | Router chooses local models by measured results | **done** (`virtual_up.py` probes, ledger) | local share rises week on week (now 2%) |
| 6 | Bind the coder agents to local models first | **partly** (router order) | a swarm run with cloud off passes ≥ 1 of 3 |
| 7 | Retrain from accepted answers (kept answers, coder fixes) | **partly** (`smarter.py`, `make_fixes.py`, weekly run) | each weekly model beats the last on the same 15 |
| 8 | Measure offline (`score.py`, `diagnose.py`) | **done** | every new model gets the same 15-change score |
| 9 | Local confidence gate: ask the cloud only when local tries disagree | **open** | fewer cloud calls with the same pass rate |
| 10 | Prove it: the coder swarm passes Kosika changes with the network off | **open** | 3 of 3 on the standard changes, offline |

## 2. The language and its engines (coder-1's part replaced)

coder-1's steps did not fit Kosika. These are the ones the coders actually
ran into this week:

| # | Step | Why | Done when |
|---|------|-----|-----------|
| 1 | `ask … until done, at most N` | agents stop when finished instead of running fixed rounds | the browser agent uses one `ask` line |
| 2 | Permission policy in the runtime | looking and reading run free; sending, buying, deleting ask | browsing needs one `y` per risky step only |
| 3 | A `train` statement over `std/layers.kos` | training said in one line, checkpoints in Kosika logs | `train net on data by loss, budget …` runs in both engines |
| 4 | `kosika log show` in the CLI | the log viewer under its real name | `kosika log show hands.kl` prints the table |
| 5 | Faster batch maths in Go beyond multiply (minus, scaled, transpose) | the 5-layer net still takes 6½ min | under 2 min, same model |
| 6 | Kosika std for GGUF reading and quantized maths | step 1.4 above | a 1.5B model answers in Go |

## 3. Agents and Studio (coder-3's part, corrected)

| # | Step | State |
|---|------|-------|
| 1 | Coder agent | **exists** (`examples/agents/coder`) |
| 2 | Coder swarm | **exists** (`daily.py --swarm`), now with a live board |
| 3 | Hands on the screen, own pointers | **done** this week (`hands.py`, `pointer.py`, designed in `cursor.kos`) |
| 4 | Browser hands with checks and backup plans | **done** this week (`browser.py`, `browse.kos`) |
| 5 | Permissions | **open**: see 2.2 |
| 6 | Learned routines from Kosika logs | **open**: replay a passing run's steps with no model |
| 7 | Kosika logs | **done** (`system "log"`, both engines) |
| 8 | Coder scaffolding: the program writes registrations and shells, the model only the body | **open**, next |
| 9 | Coder edit guard, own Go build folder per coder | **done** today |
| 10 | Studio page for the swarm: board, pointers and stories together | **open** |

---

## Order of work

1. Coder scaffolding (3.8), then rerun the three standard changes on kosika-coder alone.
2. Score the next Kaggle kosika-coder; swap it in if it beats 14/15 apply, 2 exact.
3. GGUF models inside the Go engine (1.4, 2.6): Kosika with no Ollama.
4. `ask … until done` and the permission policy (2.1, 2.2).
5. Learned routines (3.6) and the confidence gate (1.9).
6. The offline proof (1.10).
