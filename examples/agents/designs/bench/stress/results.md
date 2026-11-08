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

### Correction: the gate command I called invented was in the documents

Two sections above say the 7B "invented" the gate command
`python -m pytest -q tests/test_cli_coverage.py`. That was my error, not the model's.
`tests/test_cli_coverage.py` is a real file in this repository, and that exact command is
the example gate printed in `docs/SELF_DEVELOPMENT.md:61` and `kosika/cli.py:2757`. The
name is in the material the model was given, so a name check passes it, correctly.

What the model could not support there was the word *currently* -- the claim that this one
test command is *the* gate in force -- which is a claim about a fact, not about a name. The
distinction is the limit of the check built for this: it certifies that a name an answer
acts on was in the documents, and says nothing about whether the sentence around that name
is true. The tree is what decides truth; a served answer is still only a proposal.

### Two problems solved, and what each is worth

**A repeated question costs nothing.** `virtual-model serve` now keeps answers it has
already paid for, keyed by the normalized question plus the profile's identity, the output
budget and the grounding revision. Measured on the live 7B, same question twice:

| call | time | charged | reported |
| first | 150.7 s | 1644 tokens | `X-Kosika-Cache: miss` |
| repeat | 0.022 s | 0 tokens | `X-Kosika-Cache: hit`, `saved_tokens: 1644` |

The journal keeps the hit as a line with zero tokens, and `spent()` skips those lines, so a
repeat spends no allowance -- it is served even to a caller whose day is used up, because it
never reaches the model. A request with a temperature above zero is not kept: the caller
asked for variety, and a kept answer is the opposite of that. A refused answer is never
kept, so a wrong answer cannot become the answer.

**An answer that names what it was not given is refused.** The gateway puts a grounding
rule in front of the model and then checks every path, file, flag and folder the answer
commits to against the documents the profile actually sent. Live, on the question the model
had answered wrongly before:

```
kosika: {"cache": "miss", "grounded": false, "unsupported": ["fail"]}

I cannot answer that: it names `fail`, which do not appear in the documents I was
given, so they may be invented. Nothing in that answer should be acted on.
```

The model was asked and is charged (1595 tokens); the caller is not handed the invention,
and nothing is kept. The same question answered with `tests/test_cli_coverage.py` and
`.kosika/selfdev/ledger.jsonl` passes, because both names are in the retrieved documents --
which is the correct verdict, and the reason for the correction above.

The check has a shape worth stating plainly. It matches names, not meaning: a bare folder
name must appear in the documents *as a path*, so a document that says "failed" does not
license a folder called `fail`, and it cannot tell that a real file was cited for the wrong
reason. Prose claims ("must not be edited by default") are not checked at all. What it
buys is that the class of invention an agent copies into a shell -- a path, a flag, a
module -- does not reach the caller unchecked.

### What a repeat saves, as the operator sees it

Live on the 7B, with the flags the server now prints at startup:

```
answers     : .kosika/selfdev/op-usage-answers.json (0 kept, for 24h)
grounding   : a file, flag, folder or path named in an answer must appear in the documents, or the answer is refused
...
first:  status 200 after 141.958406s   X-Kosika-Cache: miss
repeat: status 200 after   0.012811s   X-Kosika-Cache: hit
saved       : repeat answered from the cache, 1644 tokens not spent (1 so far)
```

A kept answer is also not served for ever: an entry older than `--answer-age-hours`
(default 24, zero for none) is a miss, because quality is not the only risk in a cache -- a
day-old answer about a file that has since changed would speak for a tree it never saw.

### Verification at hand-off

What the served model is held to, run on 2026-11-06 after the cache, grounding and
expiry work, all recorded here so the numbers in `docs/SERVED_VIRTUAL_MODEL.md`
have one source:

| check | result |
| `pytest -q tests/test_virtual_serve.py` | 39 passed |
| `pytest -q tests/test_virtual_model*.py tests/test_training*.py` | 29 passed |
| `pytest -q -k "virtual or selfdev or serve or specialist"` | 197 passed, 31 subtests, 203 s |
| `ruff check` on the four touched files | clean |
| whole suite (`pytest -q`) | stopped at ~5% after seven minutes, no failures seen; not a verdict |

The live check at the same hand-off: first call 141.96 s (`X-Kosika-Cache: miss`), repeat
0.0128 s (`hit`, 1644 tokens not spent), and the folder question refused with
`unsupported: ["fail"]`.

### The spend report, and selfdev refusing an ungrounded proposal

`kosika virtual-model usage` reads the journal back -- calls, tokens, repeats and tokens not
spent, per caller per day. On a three-line journal containing one paid call, one repeat and
one call from another caller:

```
journal     : .kosika/selfdev/report-demo.jsonl
day         caller        calls    tokens  repeats  not spent
2026-11-06  bot               1     1,553        0          0
2026-11-06  editor            1     1,659        1      1,659
total       -                 2     3,212        1      1,659
```

A hit now writes `saved_tokens` into its line, so "work avoided" is evidence rather than
arithmetic over a counter that resets when the process does. A missing journal exits 2 with
one sentence instead of printing an empty table, which would read as a free day.

`VirtualModelProposer` applies the same grounding check before a proposal is parsed. A
proposal naming something the profile never sent becomes a `refused` attempt at
`kosika/selfdev.py:1966` with the invented names in its note, so it never reaches the gate
and the next attempt is told why. The engine restores the tree on that path, so nothing it
might have written is left behind. 44 tests in `tests/test_virtual_serve.py` cover the
gateway, the report and that refusal.

### A citation is checked against the line it names

Grounding a name was half a check: an answer can name a real file and still be wrong about
what is in it. The rule now also asks for `[source:line]`, and every citation an answer makes
is resolved against the profile's own sources:

* a source that is not one of the profile's files is a fault;
* a line past the end of that file is a fault;
* a quotation attached to the citation must appear on that line, character for character
  after case folding.

All three are refused with `kosika.bad_citations` naming the citation, and the caller gets
the refusal instead of the answer. The check is deliberately not retroactive: an answer that
cites nothing is still checked for names and passed, because requiring a citation would
refuse most of what a 7B writes, and a wall is a worse failure than a stated gap.

The limit, unchanged and now narrower: it verifies that a cited line exists and says what the
answer says it says. It does not verify that the line *supports* the sentence around it, and
it still cannot see a prose claim that was never named or cited at all.

50 tests in `tests/test_virtual_serve.py` cover this: a resolving citation passes, a missing
line is refused, a misquote is refused, a correct quote passes, and a citation-free answer is
passed with no fault claimed.

### The live selfdev run: one false refusal, then a kept change

Run against a sandboxed copy (`.kosika/selfdev/scratch`), so no attempt could touch the real
tree. First, with the name check alone:

```
refused   68a2c8e33962  TODO sum the values, and return 0 for an empty list
          note: the answer named kosika/thing.py which the documents do not have, so nothing was applied
          410.4s
kept 0  reverted 0  refused 1  in 1 attempt(s)
```

The path works end to end: a `refused` ledger line, the tree restored, nothing kept. But the
verdict was wrong -- the model named the file the task prompt itself showed, and the prompt
is part of what the model was given. The hole is real for any file the profile's retrieval
does not read, and a new file is the obvious case.

The fix is not a special case for one task: the check now runs over the retrieved documents
*and* the task prompt. The same task afterwards:

```
kept      68a2c8e33962  TODO sum the values, and return 0 for an empty list
          changed: kosika/thing.py
          ok   compile
          ok   import
          ok   lint
          note: checks passed
          196.2s
kept 1  reverted 0  refused 0  in 1 attempt(s)
```

and the sandbox file afterwards is `return sum(values) if values else 0`. So the whole loop
closes in one run: the model proposes, the grounding check refuses what it was not given, the
gate decides, and the ledger says which. 209 tests pass across the reachable suites at that
point (51 in `tests/test_virtual_serve.py`), and `ruff` is clean on the five touched files.

### Refusals are countable, and a task's file is read

Two things the plan above called for, done and checked live rather than argued.

**A refusal now leaves a countable mark.** Asked the folder question against a
fresh journal (146.6 s):

```
kosika: {"bad_citations": [], "cache": "miss", "grounded": false, "unsupported": ["fail"]}
{"at": "2026-11-06T20:15:53", "cached": false, "caller": "editor", "completion_tokens": 54,
 "day": "2026-11-06", "estimated": false, "grounded": false, "model": "kosika-selfdev",
 "prompt_tokens": 1553, "refused": true, "saved_tokens": 0, "tokens": 1607}
```

and `virtual-model usage` over that journal:

```
day         caller        calls    tokens  repeats  not spent  refused
2026-11-06  editor            1     1,607        0          0        1
refused     : answers the documents did not support; they were replaced by a refusal and are still charged
```

The refusal stays in `calls` and `tokens` because it cost 1607 tokens, and the
`refused` column says so. Before this, a profile whose answers were half
refused looked exactly like a healthy one in the only report it has.

**Retrieval is told what the task is about.** Retrieval rank is the question's
words; a task's file need not match them, which is why the sandbox run before
this refused a file the prompt had shown. A request now carries `focus`, the
task's path, and the provider reads that file's first two passages ahead of the
ranked ones. Unit-tested with a document whose words appear nowhere in the
question: it is still read first.

Neither change alters the gate. Both make the pre-gate cheaper to trust: what
was refused is now countable, and the documents a refusal is judged against
include the subject of the task.

213 tests across the reachable suites at this point (54 in
`tests/test_virtual_serve.py`), `ruff` clean on the seven touched files, and no
server left behind (`--port 8827` closed after the run).

### Do twenty answers cite? Measured, not assumed

`examples/agents/designs/bench/stress/citation_probe.py` asked 20 real questions
about this repository through the HTTP door of a real `virtual-model serve`
(7B, warm, temperature 0, 256-token answers, quota lifted). Each answer was
written down with its journal line as it arrived, then every citation was
resolved against the profile with `line_at`. Raw material:
`.kosika/selfdev/citations/{answers,journal}.jsonl` and `summary.json`.

| what | measured |
| --- | --- |
| questions asked / answered | 20 / 20 |
| answers that cite anything | **8 (40%)** |
| citations made | 13 |
| ... resolving to a real file and line | 10 (77%) |
| ... using the literal word `source` | 3 (`[source:61]`, `[source:1981]`, `[source:741]`) |
| ... that support the sentence they are attached to | **0** |
| answers refused by the gateway | 4 (20%) |
| tokens charged | 32,733 |
| wall time | 6,095.6 s; median 122 s, mean 154 s without one 3,170 s stall |

The ten that resolved, read one by one: five are the same line
(`SERVED_VIRTUAL_MODEL.md:81`, about selfdev grounding) attached to four gate
criteria; two point at `LANGUAGE_DETAIL.md:741`, which is a blank line; two at
`SERVED_VIRTUAL_MODEL.md:101`, a measurement-table header, for answers about the
usage report and about cache expiry; one at `:121`, a section heading. Every one
resolves. None supports its sentence. (Line numbers as of the run; that file has
been edited since.)

The four refusals:

| question about | refused for | is the name real? |
| --- | --- | --- |
| `max_context_chars` | `max_context_chars` | yes -- it is in `docs/specialists/selfdev.json`, which retrieval did not include |
| routes | `or`, `user`, `system` | `user`/`system` are chat roles; `or` is a word, not a name |
| budgets | `max_steps` | real in the spec, unretrieved |
| diagnostic codes | `_RULES` | real in `kosika/diagnostics.py`, unretrieved |

Three of the four refused a name that exists; one refused a word that is not a
name. The check fails closed on what it can see, and what it can see is a
retrieved slice, not the sources.

Reading all twenty (my judgement, not a measurement): four stand up when checked
against the tree -- what selfdev keeps and what it records, `kosika run` on a
check failure, conformance comparing refusal strings, cache expiry -- three say
plainly they cannot answer, four were refused, and nine are wrong or partly
wrong. Two of those nine invent an entire "skills" feature and pass the name
check because they name nothing at all. Grounded means "named only what it was
given"; it has never meant "correct".

What the numbers decide:

1. **Requiring a citation would be a wall.** 12 of 20 answers make none.
2. **A resolving citation is not a supporting one.** 10 resolved, 0 supportive.
   The citation rule buys the absence of false citations, and nothing more.
3. **The rule teaches the defect it catches.** It says `[source:line]`, and 3 of
   13 citations copied `source` verbatim. The example should be a real path.
4. **Names should be checked against the sources, not the retrieved slice.**
   `max_context_chars` cost a caller 1,659 tokens to be told its own profile key
   does not exist.

### The same twenty questions, after two changes

Same probe, same twenty questions, same settings; only the gateway changed: the
rule now asks for `[path:line] "the exact words"` and shows a real path instead
of `[source:line]`, a citation that quotes nothing is reported rather than
refused, and a name is an invention only when it is in neither the retrieved
documents nor the profile's sources (with underscored identifiers read as
names). Raw material: `.kosika/selfdev/citations-run1` and `citations-run2`.

| what | before | after |
| --- | --- | --- |
| answers | 20 | 20 |
| answers that cite anything | 8 (40%) | 5 (25%) |
| citations made (distinct) | 13 | 11 |
| citations that do not resolve | 3 -- all the literal word `source` | 1 -- `tests/test_conformance.py:199`, a real file, a line past its end |
| citations followed by a quotation | 0 | 0 |
| answers refused by the gateway | 4 (20%) | 2 (10%) |
| tokens charged | 32,733 | 33,696 |
| median seconds a question | 132.9 | 116.4 |
| wall time | 6,095.6 s | 2,828.6 s |

What moved, and what did not:

1. **The placeholder is gone.** Three citations named the literal word `source`
   before; none do now. The rule no longer teaches a token that cannot resolve,
   and that is the only citation defect the change was aimed at.
2. **False refusals halved, and the remaining ones are different.** `max_context_chars`
   is no longer refused, because the profile's own JSON names it. The two left
   were `or`/`user`/`system` (a word, not a name) and `_RULES`; the latter still
   refused after the change, so the sources fallback is not a blanket pass.
3. **Quoting did not happen.** Asked in so many words for `[path:line] "the exact
   words"`, the model quoted nothing in twenty answers, twice. So the quote
   cannot be *required* without refusing nearly every citing answer -- which is
   why an unquoted citation is reported in `kosika.unquoted_citations` instead.
   The measurement is what decides that, not the intention.
4. **The citation rate fell, 8 to 5.** With twenty questions that difference is
   within noise, and it is not claimed as an improvement; the honest reading is
   that the change did not increase citation.

Not claimed: answer correctness. All twenty were re-asked but only the first
nine were read closely enough to compare, and the changes were aimed at citation
mechanics, not at retrieval. Grading accuracy again needs its own run and its own
reading.

Why the run also got faster: the first run paid a 3,170 s stall on one question;
run two had no such stall, and the median moved by 16 s, which is what a quieter
machine looks like rather than a faster gateway.

### Requiring a citation for a named file: it works, and that is the problem

Run three, same twenty questions, one rule added: a file, path or flag an answer
names needs a citation whose line shows that name. Raw material:
`.kosika/selfdev/citations-run3`.

| what | run 2 (report only) | run 3 (required) |
| --- | --- | --- |
| answers that cite anything | 5 | 4 |
| citations made | 11 | 5 |
| answers refused | 2 (10%) | 10 (50%) |
| ... refused for an unbacked name | 0 | 8 |
| names it refused for | -- | `--gate`, `kosika/`, `.kosika/selfdev/ledger.jsonl` (twice), `tests/test_cli_coverage.py`, `--virtual-model`, `kosika/diagnostics.py`, `kosika/diagnostics.py:_RULES`, `tests/test_conformance.py:199`, `--answer-age-hours` |
| tokens charged | 33,696 | 33,842 |
| median seconds a question | 116.4 | 115.7 |

It catches unbacked names, exactly as intended: eight of twenty answers named a
file or flag and cited nothing, and all eight were refused. The problem is what
those names are. Every one of them exists in this tree -- `--gate`,
`.kosika/selfdev/ledger.jsonl`, `kosika/diagnostics.py`, `tests/test_cli_coverage.py`
-- so the rule refused eight true statements to enforce a citation that would
have made them checkable.

That is not a bug in the check; it is the limit of evidence. The gateway has the
answer and the citation, and nothing else. It cannot tell "true, and the model
did not cite it" from "invented", because without the citation it has no way to
tell. Enforcing the citation therefore prices a truth and an invention the same,
and at 8 in 20 it refuses almost as many right answers as the uncited rule
accepted wrong ones.

So the default does not enforce it. `kosika.unbacked` reports the names an
answer left uncited, and `--require-citations` turns the report into a gate for a
caller who wants exactly that. Run three is then the measured cost of the strict
mode: half the paid answers replaced by refusals, 33,842 tokens for ten usable
ones.

This also closes the citation line of work. Three runs, sixty answers: citations
were rare (8, then 5, then 4), never supported their sentence (0 of 10 when read
by hand), and once required they refused the truth. Citations from this model are
decoration, and no rule about them produces evidence. The evidence has to come
from somewhere else -- retrieval, and the gate the tree already runs.
