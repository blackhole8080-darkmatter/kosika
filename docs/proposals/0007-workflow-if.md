# Proposal 7: `if` steps, and finishing a workflow early

Status: implemented in both engines. Spec: `KOSIKA.md`, "Choosing between
calls". Tests: `conformance/cases/workflow_if.kos`,
`conformance/refused/workflow_if_else.kos`,
`conformance/refused/workflow_finish_{type,all,for}.kos`.

## Why

The research (`kosika-research/reports/Programming paradigms for
Kosika.md`) asked for branching in workflows without giving up "one call per
step", which is what makes each step traced, budgeted, resumed and replayed
exactly. Workflows could already choose a call with `match`, so agents wrote
every yes/no choice as a `match` on a Bool:

```kosika
  found = match hits.matches.is_empty() {
    case false { call look(msg, hits) }
    case _ { {answer: "", quote: ""} }
  }
```

There are 97 such steps in 28 files across kosika-agents, the standard
library and the examples. And a workflow had no way to stop early. When the
answer was already known (cached, refused, nothing to do), every later step
still had to run, each one passing an empty value along, as Chat does:

```kosika
  drawn = match kind.words { case true { cs_no_design() } case _ { call design(...) } }
  words = match cs_drawn(drawn) { case true { cs_no_words() } case _ { call answer(...) } }
  out   = cs_out(drawn, words)
```

## What

```kosika
found = if hits.matches.is_empty() { {answer: "", quote: ""} } else { call look(msg, hits) }

more  = if hit.found { finish from_cache(hit) } else { use_model thinker(ask) }
out   = shape(more)
```

- An `if` step is a `match` step on its condition (`case true`, `case _`).
  `else if` chains match the place of the first condition that holds. It
  takes nothing new to learn and needs no new runtime path. Its trace is a
  match step's, so old traces and replay are unchanged.
- An `else` is required, because a step always makes one call. Without one,
  the program is refused with the fix in the message:
  `Step 'more' needs an `else` at 23:10: an `if` step makes one call
  whichever way its condition goes, so write `else { ... }` ...`
- `finish` before a branch's call ends the workflow with that call's result.
  The steps after it are not made. It is still one call in one step, and the
  trace simply ends there.
- `finish(x)` is still a call of a function named `finish`. kosika-agents
  has one (`agents/assistant/main.kos`), and it keeps working.

| Refused program | Message |
| --- | --- |
| no `else` | `Step 'more' needs an `else` at L:C: ...` (`parse/if-step-else`) |
| finish of the wrong type | `Step 'more' finishes workflow 'main' with 'Hit', but it returns 'Reply'; ...` (`check/finish`) |
| every branch finishes | `Step 'more' finishes workflow 'main' whichever branch is taken; make it the last step and drop `finish`, or give it a branch that goes on` |
| `finish` inside `for` | `Step 'done' cannot finish workflow 'main' from inside a `for` step; ...` |

Both new codes have a `help` line (proposal 6), and the Go engine reports
them the same way.

## Also fixed on the way

- The Go engine ended `test` with exit code 1 for a program that does not
  parse. The reference ends with 2, the same as any refused program, and now
  Go does too. "This engine cannot run this yet" still ends with 1.
- The conformance runner leaves a program's own path out of what it
  compares. A parse error names its file, and where the suite lives differs
  from machine to machine. These are the suite's first refused programs that
  fail to parse.

## Not yet

- Rewriting the 97 `match` on Bool steps as `if` steps, and the Chat
  workflow with `finish`. That is a good coder-swarm task: it is mechanical,
  and the conformance suite and both engines check each change.
