# The researcher agent in Kosika and in Python

The same agent written twice, to see honestly what Kosika gives and what it
costs.

- Kosika: `examples/agents/researcher/main.kos`
- Python: `researcher.py` here (standard library only, local Ollama)

Both answer a question from the text files in one folder, show an answer
only with a quote that is really in a line that was found, ask a stronger
model when the fast one is not proven, and say "I will not guess" otherwise.

    python count.py                 # the sizes below
    python -m pytest .              # the Python tests
    python measure.py --model qwen2.5:7b --runs 3

## Size

Lines that do something (no blanks, no comments), from `count.py`.
Characters are the steadier number, because line counts move with the
formatter.

| | Kosika | Python |
|---|---|---|
| The program | 243 lines, 8,787 characters | 253 lines, 8,723 characters |
| of which the agent's own logic | 243 lines | 140 lines, 5,027 characters |
| of which folder safety and search | 0 (the granted `files` tool) | 59 lines |
| of which calling the model and checking its shape | 0 (`use_model`) | 33 lines |
| of which imports and limits | 0 | 21 lines |
| Tests | 82 lines, with the 4 evals | 113 lines |
| Measuring on a real model | 0 (`kosika measure`) | 71 lines (`measure.py`) |

**Kosika is not shorter.** The two programs are the same size. Kosika saves
about 110 lines of plumbing, and then spends about 100 more than Python on
the agent's own logic.

## Why the logic is longer in Kosika

These are things to fix in the language, found by this comparison:

1. **No way to trim chosen characters.** `bare` is 17 lines in Kosika and
   one in Python (`quote.strip(".,;:!?\"' ")`).
2. **No short slice.** `text.slice(0, text.length().min(1500))` against
   `text[:1500]`. It appears five times.
3. **A fallback must be a named function.** `no_hits`, `nothing`,
   `unproven`, `no_text` and `no_summary` each take three lines to return
   one fixed value. `otherwise {matches: []}` would remove all five.
4. **Every step passes records.** `Msg`, `Said`, `Kind`, `Clip`, `Where`,
   `Ask` exist only to wrap one text, and each function unwraps and rewraps.
5. **No `replace`, no `is_empty`, no filter over a list.**
   `out.split(mark).join("")`, `x.length() == 0`, and hand-written loops.
6. **Two functions that differ by one flag** (`question_query`,
   `find_query`) because a workflow step cannot pass a literal argument
   alongside a value.

## What each one guarantees

| | Kosika | Python as written |
|---|---|---|
| Reads only the granted folder | Enforced by the runtime; the program cannot name another folder | Enforced by 20 lines the author wrote and must get right |
| Cannot write, run commands or use the network | Yes: it declares two `observe` tools and nothing else, and that is checked before it runs | No: any line of the file may do anything |
| Model answer has the right fields | Checked by the language | Checked by hand |
| Unproven answer is never shown | Yes (`verify`) | Yes (hand-written loop) |
| The model is told why its answer was refused | Yes, `proven` returns the reason | No, this version just asks the next model |
| A limit on model calls and time | `budget 3 model calls, 240 seconds` | A timeout per call only |
| A record of every step, replayable without the model | Yes, every run | No |
| Wrong types found before running | Yes | Only with a separate type checker |
| Seen step by step in Studio | Yes | No |

Each "No" in the Python column can be built. Each one is more code that
this author did not write, and that the next agent's author would write
again.

## Live run

The same four questions, the same fixed lines to read, three runs each, on
`qwen2.5:7b` (local, CPU only), 10 October 2026.

| | Right | Seconds |
|---|---|---|
| Python (`measure.py`) | 12 of 12 | 101 |
| Kosika (`kosika measure`) | 12 of 12 | 112, 15 model calls |

Equally right. Kosika took about a tenth longer on this run; one run is not
enough to say whether that is the language or the laptop.

## Not claimed

- That Kosika answers better. See the live run above: on these questions
  the two are equally right.
- Four questions, three runs, one model is a small test. It cannot show a
  small difference in either direction.
- One agent is one data point. A different agent could come out differently.
- The Python is plain and standard-library only. A framework would be
  shorter in places and would bring its own guarantees.
- Speed of the language is not compared. Model time dominates both.
