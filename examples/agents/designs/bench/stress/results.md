# Stress test: designs, canvas, art

Cloud run 2026-11-04, model `nvidia/nemotron-3-ultra-550b-a55b:free` through
OpenRouter, Kosika Brain off so no kept canvas was reused.
`python stress.py` draws each ask; `python stress.py --from-files` measures what
was drawn without asking again.

The variety was named in each ask on purpose: these tests measure how much a
variety can hold, not how well Kosika picks one (`run.py --choices` does that).

## Before (cloud model)

| Ask | Variety | Seconds | Result |
| --- | --- | --- | --- |
| the whole human digestive system, mouth to colon | diagram | 76.3 | drawn: 16 cards, 14 links, 23 labels, 69 words |
| how a database commits a transaction (WAL, fsync) | diagram | 52.6 | drawn: 8 cards, 6 links, 11 labels |
| how a nuclear reactor makes electricity | canvas | 142.0 | drawn, but led with art and pins |
| how the immune system answers a viral infection | canvas | 164.6, 122.6 | **failed twice**: function-call budget exceeded |
| a thunderstorm over a city at night | art | 28.8 | "Midnight Storm Over City" |
| a coral reef in deep water at dawn | art | 33.4 | "Dawn Currents Over Reef" |

## After the three fixes (cloud model)

| Ask | Seconds | Result |
| --- | --- | --- |
| d1, digestive system | 89.6 | drawn; captions valid and read once |
| c1, nuclear reactor | 201.1 | drawn; opens with the diagram, played |
| c2, immune system | 330.6 | **drawn**; opens with the diagram |

What changed:

1. **Captions are valid SVG** (`designs_draw.kos`, note branch of `part_svg`):
   a `<title>` inside `<text>` is not allowed and duplicated the string in the
   DOM. The note is now a `<title>` on the card's `<g>`. Measured:
   `title-in-text: 0`, `group titles: 8`.
2. **A structure ask leads with the diagram**: `dz_stage` takes the canvas's own
   `lead` and gates the picture-drawn styles on it, so a diagram-led canvas gets
   the played diagram first instead of art with pins.
3. **The front door gets a real function-call budget** (`designs.py`), 100,000
   by default via `KOSIKA_DESIGNS_CALLS`; only the Brain path passed it before,
   so a plain `design()` call took the 10,000 default.

## Local model run: qwen2.5:7b (2026-11-06)

Same asks, `qwen2.5:7b` through Ollama, no fallback, `KOSIKA_BRAIN=0`,
`KOSIKA_DESIGNS_CALLS=2000`.

### Finding 1 — plumbing: the front door could not load at all (fixed)

All six asks failed in 4–11 s, before any inference:

> Workflow failed: Memory 'tastes' was not granted; run with --memory FOLDER

`std/designs_generative.kos` declares `memory tastes` and `memory eyes`, and
`designs.kos` imports it, so the front door cannot load without a memory folder.
`design()` passed `--memory` only on the Brain path. Every Python-side design
call was broken — not just the local ones: the same env with the **cloud** model
failed identically in 7.5 s.

Fixed in `designs.py`: the front door always passes `--memory`, at
`KOSIKA_DESIGNS_MEMORY` or `~/.kosika/designs`. Verified: the cloud model drew
d2 again in 33.6 s (`kind: diagram`, 10 labels).

### Finding 2 — a small ask drew correctly; a large one ran out of time

**d2 drew.** 322.5 s, title "Database Transaction Commit Process", 11 labels,
0 empty, 0 repeated, 0 outside, 1 overlap, `problems: []`. The four steps are
correct and in order: Start transaction → Log changes to write-ahead log →
Sync write-ahead log to disk → Record commit in commit record → committed.
See `d2-local7b.png`.

**d1 did not.** 503.8 s, not drawn: `Model did not answer within 400 seconds`.
Sixteen cards is too much to emit at this machine's rate.

**c1, c2, a1, a2 were not run to completion on the local model.** The loop was
stopped once the throughput was known, so their local capability is unmeasured.

### Throughput, measured on an idle machine

| Measure | Value |
| --- | --- |
| 25-token answer | 153.3 s → **0.16 tokens/sec** |
| 32-token answer (measured during another generation) | 143.1 s → 0.22 tokens/sec |
| `size_vram` while loaded | **0** |
| GPU | AMD Radeon (integrated); no `nvidia-smi` |

Ollama cannot offload to this GPU, so the model runs on CPU. The 400 s per-call
ceiling therefore allows roughly **60–90 output tokens**: only asks whose model
answer is tens of tokens finish. The 1,200–1,500 token caps the local path asks
for are unreachable — 1,500 tokens would be about two hours.

### The sort (plumbing versus capability)

| Failure | Plumbing the code can absorb | Capability |
| --- | --- | --- |
| Memory grant missing on the front door | **yes — fixed**, and it was breaking cloud calls too | no |
| d1 `did not answer within 400 seconds` | infrastructure: throughput, not fixable in code here | **unmeasured** |
| d2 layout crowding: connector labels on card edges, wires spanning the canvas | **yes** — layout code can absorb it | no |
| d2 emoji in labels (📝 💾 ✅ ▶️) | **yes** — normalize or strip them | no |
| c1, c2, a1, a2 on the local model | — | **not measured** |

**Capability was measured once, and it passed.** On a small ask the 7B's
knowledge of the commit protocol was correct and correctly ordered; what failed
was time (big asks) and layout (the code's job). Nothing here shows the 7B
reasoning badly, and nothing here shows it handling a canvas.

### Correction to a first reading

A single spot measurement of 0.22 tok/s suggested no wall clock made a local
design viable. That was wrong: d2 finished in 5.4 minutes. The honest statement
is narrower — asks whose structured answer is **tens of tokens** are viable;
asks needing **hundreds** per call are not, on this hardware.

### What this implies for "free and as powerful"

Local is free in money and expensive in time: ~5.4 min for a 4-step diagram
against 34–53 s in the cloud. The binding constraint is output tokens per call,
not price. That is the same conclusion the designs bench reached from the other
direction: the less the model has to emit, the more of the work the checked code
carries — and at 0.16 tok/s a 50-token proposal costs ~4 minutes while a
500-token one costs ~50.

## Still open

- **Canvas is slower than before the fixes**: 142 s → 201 s and 157 s → 331 s,
  since it now renders the diagram page in an iframe as well as the picture.
- **The played diagram dims its unlit parts**, so a still frame reads faint.
- **Canvas label numbers undercount**: inside an iframe labels are escaped, so
  `look()` measures the page around the frame, not the diagram.
- **Layout does not reflow around long labels**: the 7B's d2 drew fine content
  into a crowded frame.
- **Art's silent fallback**: `gen_fallback()` returns "Golden Bloom" with
  `problems: []`, so a caller cannot tell a real piece from the default.

## Evidence

`d2-local7b.png` (the 7B's own drawing), `d1-after.png`, `c1-after2.png`,
`c2-after2.png` (cloud, after the fixes); `openrouter/` (that run's pages and
report, preserved); `local7b/` (the six grant failures in `blocked/`, then d1's
timeout and d2's drawing); `probe/` (cloud d2 after the memory fix);
`probe7b/` (the patient re-run, stopped once throughput was known);
`run-after.log`, `run-after2.log`; `groq-quota/`.
`compare_runs.py` prints the cloud and local runs side by side.

## Self-development loop: the file picker (2026-11-06)

The loop kept nothing in its first eight attempts, and the reason was the
picker: `_nearby_code` counted word stems, so a task about a disagreement
between agents was handed `agent_evaluation.py` and `agent_starters.py`.

Three changes, all deterministic and all in the loop's own path:

- The file is chosen by meaning, using `nomic-embed-text` (768 dimensions,
  cosine), with vectors cached in `.kosika/selfdev/embeddings.sqlite3`; the
  word count stays as the fallback when no embedding model answers.
- The window shown is 140 lines centred on the part of the file where the
  task's words appear, not the top of the file, which is imports.
- `apply_edits` drops a leading line-number column from `find` when what
  remains matches exactly one place, because the model copies the numbers the
  window prints.

Result over fourteen attempts: one kept, thirteen refused. The kept change is
`get_performance_metrics()` on `ExperienceBuffer` in `kosika/learning.py`, 49
lines, with the gate (`tests/test_interference.py`) and `-k learning` (7 tests)
passing. Kept rate 0.07, up from 0.00.

Most refusals are correct rather than failures. The backlog is Phase 3 and 4
feature work, and `deadlock` and `shared memory` appear in no file at all, so
those items want a person; the loop stopping itself after two misses in a row
is the right answer for them.

## Self-development: decomposed tasks (2026-11-06)

`kosika selfdev run --decompose N` asks one question per task -- which existing
name would this extend, or none -- against the 6101 names the
tree actually defines. Only tasks that name real code are attempted; the rest
are printed as dropped, because a roadmap line that names nothing is not a
change anyone can make.

Over 8 tasks, 2 named existing code, 6 resolved to none and
were dropped, and 1 answered in prose rather than JSON (the parse now
takes one object out of the sentence). The run kept 0 and refused 2.

The excerpt now follows the named symbol rather than the task's words:
`HyperparameterTuner` sits at line 332 of `kosika/learning.py`, so the window is
lines 332-471, not lines 1-140 -- which is what the earlier refusal ("the class
is not visible in this excerpt") was asking for.

### Run 10: blocked before it started

The free OpenRouter model reached its daily limit (HTTP 429, 50 of 50 used,
reset at 00:00 UTC), so run 10 asked nothing and the ledger did not move. The
two fixes it was meant to test are verified without a model: the excerpt for the
hyperparameter task is now the top of the file plus lines 332-471, where
`HyperparameterTuner` is, and a generic name such as `engine` is no longer
offered to the chooser at all.

The ledger stands at 18 attempts and 1 kept, a kept rate of 0.06. Since
decomposition landed, four attempts were made and all four refused -- but each
refusal now names a concrete missing fact ("the class is not visible in this
excerpt", "I need to see lines 1-95 to understand the imports") instead of
restating the feature.

## A refusal can ask for lines (2026-11-06)

Run 9's refusals were the useful kind: "the HyperparameterTuner class is not
visible in this excerpt", "I need to see lines 1-95 to understand the imports".
Both were requests for more of the file, recorded as failures. They are now an
answer of their own:

    {"edits": [], "need": {"path": "kosika/learning.py", "lines": [120, 200]},
     "note": "why"}

The engine validates it (`need.lines` must be two positive numbers and at most
400 lines apart), keeps it on the attempt, and shows those lines to the next
attempt under "THE LINES IT ASKED TO SEE", still bounded by the excerpt size.
The request costs one attempt: with `--max-attempts 2` a refusal that asks
therefore gets a second look, and with `--max-attempts 1` it does not.

Verified without a model, since the free quota was exhausted: a stub proposer
that asks for lines 30-40 of a file and then edits it produced status `kept`,
`VALUE = 2` on disk, and the second prompt carrying `lines 30 to 40`. Malformed
needs are rejected (`need.lines must be two line numbers`, `may ask for at most
400 lines`), and `pytest -q -k selfdev` stayed at 28 passed.

## Local virtual model: it answers, and it costs minutes (2026-11-06)

The hosted model's daily quota ran out, so the same specialist profile was put
on `qwen2.5-coder:7b` through Ollama. A virtual model is a layer over a
provider, not a provider itself: the profile supplies the instructions, sources
and examples, and a model still has to answer.

Measured here: 3.63 tok/s of generation (24 tokens in 6.6 s), with prompt
reading dominating -- a 300-token prompt costs about two minutes before the
first token, so a decomposition question takes 100-125 s. That is roughly twenty
times the rate of the general 7B measured earlier (0.16-0.22 tok/s), and it puts
a ten-task decomposition at about twenty minutes, with an attempt on a
6000-character excerpt plus 900 output tokens adding five to eight more.

Quality on the two lines the hosted model had already resolved was identical:
"Build performance metric tracking" named `get_performance_metrics`, and "Build
hyperparameter tuning primitives" named `hyperparametertuner` -- the same
symbols, in the same file, as the 550B chose.

The full local run (`--decompose 2 --cycles 1 --max-attempts 1`) completed and
stopped correctly at "Nothing to attempt". For the two Phase 4 lines it was
given it answered none, which is defensible for "Implement code inspection APIs"
and "Design change proposal and validation workflow". No ledger row was
written, because nothing was attempted.

## A served virtual model, metered per caller

`kosika virtual-model serve PROFILE --model NAME` puts a profile's specialist at
`/v1/chat/completions` on loopback, in the OpenAI shape, so an agent outside this project
can use it with the base URL it already accepts (`--base-url`, an editor's custom endpoint,
another CLI). The grounding is the profile's: its instructions, its sources and its
verified examples. Nothing about the answers changes; what is new is the door.

Every caller is named and keyed (`--key editor=KEY`, repeatable; with no `--key` a key is
generated and printed once, never written down). Each caller gets, per UTC day:
`--calls-per-day` (200), `--tokens-per-day` (200000), and at most `--tokens-per-call`
(8000) per request. The allowance is checked *before* the model is asked, so a refusal
spends nothing:

| situation | answer |
| no key, or a key that is not ours | 401 `invalid_api_key` |
| the day's calls or tokens are used up | 429 `quota_exhausted`, with `X-Kosika-*-Remaining` |
| `max_tokens` above the per-call cap | clipped to the cap |
| the provider failed | 502, naming the failure |
| the body is not the OpenAI shape | 400, saying which part |

Each call is one line of `.kosika/<profile>-usage.jsonl`: caller, day, tokens, and whether
the count was reported or estimated. A day's spend is read back rather than remembered,
and no key and no prompt text is ever written to it.

Honest limits: tokens are the provider's own count when it reports one, and a
four-characters-per-token estimate when it does not (`usage.estimated` says which, so a
caller can tell a measured number from a guessed one); the allowance is per caller name
against this computer's journal, not an account system, so it bounds a collaborator's
habit rather than an adversary; and the model behind the profile still costs whatever it
always did -- the quota limits how much of it a caller may spend, and does not make it
more capable.

Evidence: 14 tests in `tests/test_virtual_serve.py` -- key refused without asking the
model, daily call allowance enforced at the third call with the provider asked exactly
twice, per-call cap clipped from 99999 to 500, reported usage preferred to an estimate,
bad body 400, provider failure 502, allowance returned on a new day, and the same rules
over real HTTP on a bound port. Live smoke: `serve docs/specialists/selfdev.json` answered
`GET /v1/models` with `kosika-selfdev`, refused unkeyed and wrong-key calls with 401, and
announced its allowance and journal path. `pytest -q -k "virtual or selfdev"`: 75 passed.

### The real call, measured

One authenticated completion through the served profile, on the local model, answering
from the profile's own sources:

```
model      : kosika-selfdev        (profile docs/specialists/selfdev.json)
caller     : editor
answer     : compile, import, ruff when installed, and every --gate command;
             a failure undoes the change and appends it to .kosika/selfdev/ledger.jsonl
usage      : prompt 1539, completion 128, total 1667, estimated: false
latency    : 265 s for 128 tokens (local qwen2.5-coder:7b)
journal    : {"at": "2026-11-06T16:59:58.995744+00:00", "caller": "editor",
              "completion_tokens": 128, "day": "2026-11-06", "estimated": false,
              "model": "kosika-selfdev", "prompt_tokens": 1539, "tokens": 1667}
```

Two things this settles. The metering path is real end to end: the count came back
`estimated: false` because the provider reported it, and the journal line says the same
numbers the response carried, so quota is charged against measured tokens rather than a
guess. And the answer it gave is the gate's actual behaviour, not a plausible summary:
compile, import, `ruff check` when present, and the `--gate` commands, with a failure
reverting the change and appending to the ledger.

The cost that a quota cannot fix is the 265 s. A caller's allowance limits how much of the
model it may spend, not how fast the model is; an outside agent pointed at this gateway
gets grounded answers at about two tokens a second, which is fine for a handful of
questions and wrong for an interactive loop.

### What the latency actually is

The 265 s was not the gateway's overhead. Timed on this machine (8 CPU cores, no GPU,
ollama 0.40.13) with the same 896-token prompt and 128 tokens asked for:

| model | read | write | total |
| qwen2.5:7b | 11.8 tok/s | 3.24 tok/s | 159.6 s |
| qwen2.5-coder:7b | 10.1 tok/s | 2.58 tok/s | 183.4 s |
| qwen2.5-coder:1.5b | 46.0 tok/s | 8.10 tok/s | 31.3 s |

The profile sends 1539 prompt tokens (`max_context_chars: 6000`, `max_results: 6`), so the
7B spends about 150 s reading the sources before it writes the first token. The provider
already asks ollama to keep the model loaded for half an hour (`keep_alive`), so the ~65 s
model load is paid once per idle period, not per call.

The same question through the gateway with the 1.5B: **57 s instead of 265 s**, 4.6x faster,
same 1539 prompt tokens and 110 written, counted (`estimated: false`). What that speed cost
was accuracy: the 7B named the gate exactly -- compile, import, `ruff` when installed, the
`--gate` commands, failures undone from captured bytes -- while the 1.5B invented two
checks that the gate does not perform ("documentation must not be edited by default",
"model must not invent the test that would have caught the bug") and omitted the import
check. Faster, and wrong about the thing it was asked.

So the choice on this machine is 4.5 minutes with the gate described correctly, or one
minute with invented rules. Both are served by the same command (`--model` picks), both are
metered identically, and neither is the "free and as powerful as a hosted model" claim --
a 7B on CPU is the floor of this trade, not a way around it.

### Why the second call is fast, and what that means for the quota

The same question asked twice through the gateway on the 7B, with the model kept loaded:

```
call 1: 200 after 233.1 s   prompt 1539, completion 120
call 2: 200 after  49.2 s   prompt 1539, completion 120
```

Ollama reuses the KV cache of the shared prompt prefix, and this profile's prefix is stable
-- the same instructions and the same retrieved sources -- so the 150 s of reading the
sources is paid once, and what remains is the writing: 120 tokens at 2.6 tok/s is 46 s,
which is the whole 49 s. The slow call is the first one after the model has been idle, not
every call, and the provider already asks for a half-hour keep-alive.

That changes what the latency problem is. It is not "the model is too slow to serve"; it is
"the first caller after an idle period pays 233 s and everyone after that pays 49 s at the
7B's accuracy". For an agent making a run of questions the 7B is the right default; for one
question every few hours the 1.5B's one minute is the better trade, and it is a flag, not a
different program.

One thing this also shows: two identical requests at `temperature: 0` did not produce the
same text. The count and the timing repeat exactly, the wording does not, so a served answer
is evidence about the gate's behaviour only when it is checked against the code -- which is
how the 7B's answer was confirmed and the 1.5B's two invented checks were caught.

The half-hour keep-alive is now `KOSIKA_OLLAMA_KEEP_ALIVE` (default `30m`), so a
long-running `virtual-model serve` can ask for hours and keep every caller on the warm
49 s path instead of the cold 233 s one.

### Trimming the context budget: measured, and refused

Same question, same model (qwen2.5-coder:7b), same 120 tokens asked for, and a cold prefix
every time -- changing the sources changes the prefix, so none of these three reused the
cached one:

| `max_context_chars` | results | prompt tokens | time | gate checks named |
| 6000 (as shipped) | 6 | 1539 | 233.1 s | compile, import, ruff, --gate, undo, ledger |
| 3000 | 3 | 794 | 119.6 s | --gate, undo, ledger |
| 1500 | 3 | 382 | 61.6 s | none |

Reading costs about a tenth of a second per prompt token on this CPU (the time left after
subtracting generation at 2.6 tok/s), so halving the sources halves the wait -- and costs
more than half the answer. At 3000 chars the model dropped compile, import and ruff, and
then invented a specific gate: "`python -m pytest -q tests/test_cli_coverage.py`", a command
the request never mentions. That is worse than an omission, because a precise fabrication
reads like evidence. At 1500 chars the answer went generic -- "a series of automated tests"
-- naming nothing the gate actually does.

So the budget stays at 6000 for this profile. The speed trimming would buy is already free
on the warm path (49 s for the second and later calls), and the accuracy it costs is not
recovered by any amount of retrying. Latency here is bought with a loaded model, not with a
smaller prompt.

Caveat on the evidence: one question per budget. The direction is not subtle -- three facts
lost and one invented command -- but a single answer is a single answer, and the earlier
control showed that even `temperature: 0` does not reproduce wording exactly.

### Warming at startup: the first caller stops paying for the cold model

`virtual-model serve --warm` asks the profile one throwaway question as soon as the port
answers, then reports how long that took. With the model explicitly unloaded first
(`ollama stop qwen2.5-coder:7b`), same question, 120 tokens asked for:

| condition | warm step | what the first caller waited | answer |
| model unloaded, `--warm` | `ready in 135s` | 150.8 s | all four checks, undo, ledger |
| model loaded, prefix cold (measured earlier) | none | 233.1 s | all four checks, undo, ledger |

The answer did not change, which is the point: warming tells the model nothing new, it moves
the cold read off the caller. What the caller still pays is reading the passages retrieved
for its own question -- the warm ask retrieves different passages -- plus writing its 120
tokens, so 151 s is not 49 s. It is 82 s less than a cold-prefix call that started from a
loaded model, and the warmed run started from an *unloaded* one, so the saving against a
genuinely cold start is larger than 82 s (the ~65 s load it avoided, by extrapolation, not by
measurement).

Two gaps stated rather than papered over. The second question, a different one, did not run:
the script referenced a request file it never wrote, so whether the warmed instructions help
an unrelated question is still unmeasured. And the warm step is not free -- it spends 135 s
of this computer's time and one unit of the model's own work before anyone asks, which is
worth it only for a server that will be used; a one-off question should not pass `--warm`.

### Does warming help a second, different question? No.

One server, started unloaded and warmed, then two different questions asked in order
(7B, 120 tokens asked for each):

| call | question | time | prompt tokens |
| warm step | -- | 134 s | -- |
| 1st caller | B: where a failed change's report goes | 119.4 s | 1518 |
| 2nd caller | A: what a change must pass | 168.1 s | 1539 |

The second, different question was not faster -- it was slower, within the noise of a busy
CPU. Put against the earlier pair (the same question twice: 233.1 s then 49.2 s), this
settles what the cache actually reuses: an *identical* prompt, not a shared prefix. A
different question retrieves different passages, so it reads its own sources from scratch
every time, about 100-150 s on this machine, and `--warm` cannot pre-read a question nobody
has asked yet.

So `--warm` is worth exactly what it removes: the model load and the instructions block,
which is the 233 s -> 151 s measured for the first caller. Every new question after that
costs its own reading. The cast-iron case for the warm path is the same question asked
again (a retry, or several callers asking the same thing); for genuinely different questions
this machine's floor is about two minutes per answer at 7B quality, and no flag changes that.

One more data point on trust, from the same run: B answered that a failed change's report
goes in "the `session` folder" and that a kept change stops appearing because it "is no
longer ticked in the Session tab". Both are inventions -- the gate writes to
`.kosika/selfdev/ledger.jsonl` -- and it produced them in 35 tokens, faster and more
confidently than the correct answer. Short answers are not safer answers.
