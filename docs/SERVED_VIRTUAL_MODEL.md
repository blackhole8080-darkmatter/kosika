# Serving a Kosika model to any agent

`kosika virtual-model serve` puts one virtual-model profile behind the OpenAI
shape at `http://127.0.0.1:<port>/v1`, so an agent outside Kosika — an editor
plugin, another assistant, a script — can use the same grounded specialist
Kosika uses, through the one door every client already speaks.

What it adds to the profile is not intelligence. It is a **door with a meter, a
memory and a conscience**: who may ask, how much they may spend, which questions
were already answered, and whether the answer names things the profile never
gave it.

```text
kosika virtual-model serve docs/specialists/selfdev.json \
  --model qwen2.5-coder:7b --protocol ollama --port 8813 \
  --calls-per-day 200 --tokens-per-day 200000 --tokens-per-call 8000 \
  --key editor=THE-KEY --warm
```

```bash
curl -s http://127.0.0.1:8813/v1/chat/completions \
  -H "Authorization: Bearer THE-KEY" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"what must a selfdev change pass?"}]}'
```

Any OpenAI client works the same way: base URL `http://127.0.0.1:8813/v1`, model
`kosika-selfdev` (listed at `/v1/models`), key in `Authorization`.

## The three things it decides, in order

| Decision | When | What it costs the caller |
| --- | --- | --- |
| Is this key one of ours? | before anything else | nothing; `401 invalid_api_key` |
| Was this exact question already answered? | before the allowance and before the model | nothing, and no allowance; `X-Kosika-Cache: hit` |
| May this caller ask now? | before the model | nothing; `429 quota_exhausted` with the counters |
| Does the answer name or cite what it was given? | after the model answers | the call is charged, the answer is replaced by a refusal; a file or flag it names also needs a citation whose line shows that name |

The journal (`.kosika/<profile>-usage.jsonl`, or `--state`) is the record: one
line per call, no keys and no prompt text. A cached repeat is a line with zero
tokens that `spent()` skips, so it never eats an allowance.

## Reading the spend

```bash
kosika virtual-model usage docs/specialists/selfdev.json
kosika virtual-model usage docs/specialists/selfdev.json --format json --days 7
```

```text
journal     : .kosika/selfdev-usage.jsonl
day         caller        calls    tokens  repeats  not spent  refused
2026-11-06  bot               1     1,553        0          0        0
2026-11-06  editor            1     1,659        1      1,659        0
total       -                 2     3,212        1      1,659        0
```

A repeat is counted apart from a call and adds no tokens, so "allowance spent"
and "work avoided" are two different numbers rather than one flattering one. A
hit writes down what it saved, so the second column is evidence, not a counter
remembered across restarts. A journal that does not exist is a refusal with
exit 2, not an empty table that looks like a free day.

A refusal is not free, so it stays inside `calls` and `tokens`; the `refused`
column says how many of those calls the documents did not support. Every line
records the verdict (`grounded`, `refused`), so a profile that refuses half its
questions reads as expensive *and* refusing rather than as expensive and
healthy. That column is also how the citation question gets answered: it counts
what happened instead of what was expected.

## Using it from selfdev

```bash
kosika selfdev run --model qwen2.5-coder:7b \
  --virtual-model docs/specialists/selfdev.json --cycles 1
```

`VirtualModelProposer` asks with the same grounding rule the served gateway puts
in front of a model, and checks the answer the same way. A proposal that names a
file, flag or folder the profile's documents do not contain never reaches the
gate: it is recorded as a `refused` attempt carrying the invented names, so the
next attempt at that task is told what went wrong instead of being handed the
invention. The gate still decides whether a change lives; this only decides what
is allowed to be tried.

A name the task prompt itself showed counts as grounded, because what the model
was given is the retrieved documents *and* the prompt. A live run against a
sandboxed copy is what showed the difference: the first attempt was refused for
naming the very file it had been asked about -- a real false positive, since the
profile's retrieval reads this repository and the sandbox copy is not part of it
-- and after the fix the same task was kept, with compile, import and lint
passing, in 196 s.

The same run also showed retrieval's blind spot. Retrieval ranks the profile's
documents against the question's words, and a task's own file need not match
them. So a request now carries the task's path as `focus`, and the provider
reads that file first, up to two passages, before the ranked ones. The task is
the one thing that always knows what the answer is about.

## What is measured to work

All on this 8-core CPU with no GPU, 7B `qwen2.5-coder`, `128` tokens asked for
unless stated. Evidence and the runs behind it: `examples/agents/designs/bench/stress/results.md`.

| What | Measured |
| --- | --- |
| A real grounded answer | 150.7–233.1 s, 1644 tokens, `estimated: false` |
| The same question again | **0.012–0.022 s, 0 tokens charged**, `saved_tokens: 1644` |
| A different question after a warm start | 168.1 s — no reuse; the cache reuses an identical prompt, not a shared prefix |
| `--warm` before the first caller | `ready in 134s`; first caller 150.8 s instead of 233.1 s |
| An invented folder (`fail`) | `kosika.grounded: false`, `unsupported: ["fail"]`, refusal returned, nothing kept |
| A real path (`tests/test_cli_coverage.py`) | passes, because the name is in the retrieved documents |
| A citation to a real line | `[kosika/selfdev.py:3]` passes when that line exists |
| A citation to a missing line | `kosika/selfdev.py:99` is refused: `bad_citations: ["kosika/selfdev.py:99"]` |
| A quotation not on its cited line | refused; the quoted text is checked against that line |
| An answer with no citation at all | passes, and the gap is stated rather than implied |
| A refusal, written down | line has `"grounded": false, "refused": true`, 1607 tokens still charged; `usage` shows `refused 1` |
| A task's own file, which its words do not match | read first via `focus`: two passages ahead of the ranked ones |
| Quota | refused before the model is asked; the provider is never called for a refused caller |
| Restart | a kept answer survives, and expires after `--answer-age-hours` (default 24) |
| Citation rate, 20 real questions | 8/20 cited; 13 citations, 10 resolved, 3 used the literal word `source` |
| Do resolved citations support their sentence? | 0 of 10 (line checked by hand, not by the gateway) |
| The same 20 questions after the rule and name fixes | 5/20 cited, 11 citations, 1 unresolved, 0 quoted, 2 refused (was 4), median 116 s (was 133 s) |
| Same 20 again, a citation required for a named file | 4/20 cited, 5 citations, 10 refused -- 8 for an unbacked name, and every name it refused is real |
| Refusals | 4/20: `max_context_chars` and `_RULES` (real, unretrieved), `max_steps`, and `or`/`user`/`system` |
| Cost of twenty questions | 32,733 tokens, 6,095.6 s; median 122 s, mean 154 s without one 3,170 s stall |

## What this cannot do, said plainly

* **It is not fast.** A question it has not seen costs about two minutes at 7B
  quality on this machine. Only a repeat is instant. Warming removes the model
  load and the instructions, nothing more; there is no way to pre-read a
  question nobody has asked.
* **It is not as able as a hosted model.** The answers are sometimes wrong. The
  served text is a proposal, and the tree is still what decides.
* **The grounding check matches names and citations, not meaning.** It
  certifies that a path, flag or folder an answer acts on was in the documents
  the profile sent, and that a `[source:line]` it cites resolves to a real line
  that contains any quotation attached to it. It cannot tell that a real file
  was cited for the wrong reason, and it does not check prose at all
  ("documentation must not be edited by default"). A wrong sentence about a
  real name still passes. The `test_cli_coverage.py` correction in `results.md`
  is that limit, recorded against my own earlier error.
* **Citing is asked for, and asking buys less than it looks.** Over 20 real
  questions asked through this gateway: 8 answers cited anything (40%), they
  made 13 citations, 10 resolved to a real line (77%), and **none of the 10
  supported the sentence it was attached to** -- five cited one line about
  grounding beside four gate criteria, two pointed at a blank line, two at a
  measurement-table header for answers about the report and about expiry. Three
  copied the literal word `source` from the rule's own example, which reads
  `[source:line]`. So an uncited answer is normal, and a resolving citation is
  not evidence; what is enforced is only that a citation present is a real
  file and line, never that it says what the sentence claims. Re-asked after the
  rule was fixed, the same model made 11 citations and quoted none of them --
  twice, so a quotation cannot be required without refusing nearly every citing
  answer. What did change: the three citations naming the literal word `source`
  fell to zero, and refusals fell from four to two.

  One requirement was added after that comparison, and it is the narrow one the
  measurements support: a file, path or flag an answer *names* must have a
  citation whose line shows it (`kosika.unbacked`). Citations are not required
  per sentence -- the model would not write them -- but a name is a thing a
  caller can act on, and an answer that names a file while citing nothing has
  given the caller nothing to check. Measured, that requirement refused 8 of 20
  answers and every name it refused was real: an uncited truth and an invention
  look identical to a checker that has only the citation. So it is off by
  default, `--require-citations` turns it on, and `kosika.unbacked` reports the
  names either way.
* **A bare folder name must appear as a path.** A document that says "failed"
  does not license a folder called `fail`; a document that says the word
  "session" does not license a `session` folder.
* **A repeat is only as true as when it was made.** Kept answers are not
  re-checked, which is why they expire; `--no-cache` exists for a caller who
  would rather pay than be told something a day old.
* **A refused answer still costs.** The model was asked, so the caller is
  charged for it. There is no free refusal.
* **Loopback only, by design.** The server binds to `127.0.0.1`; serving beyond
  it prints a warning and is the owner's decision, not a default.
* **The key is shown once.** Without `--key`, one is made and printed at
  startup and never written anywhere.

## Picking the work back up

Done and behind us: the spend report (`virtual-model usage`), selfdev refusing
an ungrounded proposal before the gate sees it, checking every citation an
answer makes against the line it names, writing each verdict into the journal
so refusals are countable, retrieval reading the file a task names, and the
citation rate itself measured over twenty questions. What is left, in the order
that pays:

Done after that measurement, all tested, none of them a new gate:

* **The rule asks for evidence, not a location.** It now asks for
  `[path:line] "the exact words"` and shows a real path; the old example said
  `[source:line]`, and three citations copied the word `source` verbatim.
* **An unquoted citation is reported, not refused.** `kosika.unquoted_citations`
  lists citations that resolve to a line and quote none of it. Refusing them
  would refuse the answers being honest about where they looked; a quotation
  that is *not* on its line still refuses the answer.
* **A name is an invention only when it exists nowhere.** The check now falls
  back to the profile's sources, so `max_context_chars` is no longer refused by
  a profile whose own JSON names it, and an underscored identifier is read as a
  name rather than as a folder claim.

What is left, in the order that pays:

1. **Move to retrieval, where the evidence actually is.** Three runs closed the
   citation question: 8, 5 and 4 answers cited; none of the ten resolved
   citations supported its sentence; and requiring one for a named file refused
   eight names that all exist. What is left is that nine of twenty answers were
   wrong, two of them inventing a feature outright. More or better passages is
   the lever, scored on correctness and time together.
2. **Cut the cost of a new question, by measurement** — reads cost roughly
   0.1 s per prompt token here, so fewer or better-ranked passages is the only
   real lever. Measure accuracy and time together before believing it; trimming
   the context to 3000 characters already destroyed the answers once.
3. **Prime what is actually repeated** — warming a handful of a caller's real
   questions, if and only if the journal shows they repeat. `virtual-model
   usage` is what would say so.

Before resuming, the checks this work is held to:

```bash
python -m pytest -q -k "virtual or selfdev or serve or specialist"   # 227 passed, 31 subtests
python -m ruff check kosika/virtual_serve.py kosika/virtual_model.py \
  kosika/selfdev.py kosika/cli.py kosika/model.py \
  tests/test_virtual_serve.py tests/test_virtual_model.py             # clean
```

A live smoke of all three decisions, about four minutes:

```bash
# ask twice: the first is a miss, the second a hit with no tokens charged
# ask the folder question: grounded false, unsupported ["fail"]
```

The full suite is slower than this work justifies on its own (it was still at
~5% after seven minutes with no failures); the targeted suites above are what
this code can reach.
