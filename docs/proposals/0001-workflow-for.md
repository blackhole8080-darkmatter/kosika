# Proposal 1: a workflow step made once for each item of a list

Status: implemented in both engines (reference `kosika/`, native `native/`).
Spec: `KOSIKA.md`, "Workflows". Conformance: `conformance/cases/workflow_for.kos`,
`conformance/failing/workflow_for_limit.kos`, `conformance/refused/workflow_for_*.kos`.

## Why

Kosika AI (`agents/ai/main.kos` in kosika-agents) plans up to four steps and
then makes each one. A workflow could not loop, so its `main` carried four
hand-copied step slots, `w1`/`d1` through `w4`/`d4`, each the same `match`
over the step's tool. That is 36 lines where 10 say the same thing, a fifth
step needs another copy, and a fix to one slot has to be made four times.

Loops already exist in functions, but a function cannot ask a model, call
a workflow or use a tool. The rule kept so far was "loops belong in pure
functions", and it is kept: the new form does not let a workflow loop
freely. It lets one step be made once per item, with a bound written in
the program, so every call a run can make is still known before it starts.

## What

```kosika
done = for s in plan.steps limit 4 {
  match s.tool {
    case "calc" { call do_calc(work_next(ask.text, plan, done)) }
    case _ { skip(work_next(ask.text, plan, done)) }
  }
}
```

- The braces hold exactly what may follow `=` in a one-line step: a call
  with its `verify`, `vote`, `retry`, `escalate` and `otherwise`, or a
  `match` of calls. Nothing new is learned to write the body.
- `limit N` (1 through 1000) is required. It reuses the word the `for` of a
  function already uses for its bound, with the same meaning: a longer list
  fails the run before the first pass. Nothing is dropped silently; a program
  that wants the first N writes `.slice(0, N)`.
- Inside the braces the step's own name holds the results of the passes
  before, so a pass can build on the ones before it (Kosika AI's steps
  read what earlier steps found). After the step it holds them all.
- Each pass is an ordinary step named `done[0]`, `done[1]`, ...: its own
  events, budget, resume, replay and `otherwise`. Both engines write the
  same trace and each replays the other's.

Why not `for step in plan.steps, at most 4`: `at most` would be a second
way to say the bound `limit` already says, and "at most" reads as if extra
items were dropped quietly. One word, one meaning.

## Errors, each saying how to fix it

| Program | Message |
| --- | --- |
| no `limit` | `Step 'done' needs a bound: write `done = for s in ... limit N { ... }`, N from 1 through 1000, the most passes it may make` |
| not a list | `Step 'done' loops over 'Text'; a `for` step goes through a list, so give it one, as `for s in plan.steps limit 4`` |
| item name taken | `Step 'done' names its item 'plan', which is already a name here; choose another name for the item` |
| loop is the last step | `Workflow 'main' ends with the `for` step 'done', which gives a list; add a step after it that makes the 'Done' the workflow returns` |
| inside `parallel` | `Step 'done' cannot run in parallel: a `for` step makes its passes one after another` |
| too many items (run) | `Step 'done' was given 3 items, more than its limit 2; raise the limit, or give it the first 2 with `.slice(0, 2)`` |

## Kosika AI, before

```kosika
  w1    = work_at(ask.text, plan, 0, [])
  d1    = match tool_at(plan, 0) {
    case "calc" { call do_calc(w1) }
    case "code" { call do_code(w1) }
    case "think" { call do_think(w1) }
    case "look" { call do_look(w1) }
    case "draw" { do_draw(w1) }
    case _ { skip(w1) }
  }
  w2    = work_at(ask.text, plan, 1, [d1])
  d2    = match tool_at(plan, 1) {
    case "calc" { call do_calc(w2) }
    case "code" { call do_code(w2) }
    case "think" { call do_think(w2) }
    case "look" { call do_look(w2) }
    case "draw" { do_draw(w2) }
    case _ { skip(w2) }
  }
  w3    = work_at(ask.text, plan, 2, [d1, d2])
  d3    = match tool_at(plan, 2) {
    case "calc" { call do_calc(w3) }
    case "code" { call do_code(w3) }
    case "think" { call do_think(w3) }
    case "look" { call do_look(w3) }
    case "draw" { do_draw(w3) }
    case _ { skip(w3) }
  }
  w4    = work_at(ask.text, plan, 3, [d1, d2, d3])
  d4    = match tool_at(plan, 3) {
    case "calc" { call do_calc(w4) }
    case "code" { call do_code(w4) }
    case "think" { call do_think(w4) }
    case "look" { call do_look(w4) }
    case "draw" { do_draw(w4) }
    case _ { skip(w4) }
  }
  missed = first_failed([d1, d2, d3, d4])
  d5    = match missed.ran {
    case true { call redo({ask: ask.text, goal: plan.goal, failed: missed, before: earlier([d1, d2, d3, d4])}) }
    case _ { skip(work_at(ask.text, plan, 4, [])) }
  }
  done  = [d1, d2, d3, d4, d5]
```

## Kosika AI, after

```kosika
  done  = for s in plan.steps limit 4 {
    match s.tool {
      case "calc" { call do_calc(work_next(ask.text, plan, done)) }
      case "code" { call do_code(work_next(ask.text, plan, done)) }
      case "think" { call do_think(work_next(ask.text, plan, done)) }
      case "look" { call do_look(work_next(ask.text, plan, done)) }
      case "draw" { do_draw(work_next(ask.text, plan, done)) }
      case _ { skip(work_next(ask.text, plan, done)) }
    }
  }
  missed = first_failed(done)
  again = match missed.ran {
    case true { call redo({ask: ask.text, goal: plan.goal, failed: missed, before: earlier(done)}) }
    case _ { skip(work_at(ask.text, plan, 4, [])) }
  }
  all   = done.append(again)
```

with one helper, `fn work_next(ask, plan, done) -> Work { return work_at(ask,
plan, done.length(), done) }`. All 90 of `main.kos`'s tests pass with the
after version on both engines. One difference: `report.steps` no longer
carries a blank entry for each unused slot, only the steps the plan had and
the redo slot.

## Tests

- `conformance/cases/workflow_for.kos`: a match of calls (workflow call,
  model, value), passes seeing earlier passes, an empty list, `otherwise`
  standing in for one pass.
- `conformance/failing/workflow_for_limit.kos`: more items than the limit.
- `conformance/refused/workflow_for_{bound,list,item,last,parallel}.kos`.
- `tests/test_workflow_for.py`: what each pass is given, the trace's
  `done[i]` steps and replay, the plain `for` of a value left as it was.
- `tests/test_conformance.py`: the native and reference engines write the
  same trace for a `for` step and each replays the other's.
