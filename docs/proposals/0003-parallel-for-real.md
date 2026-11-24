# Proposal 3: `parallel { }` runs in parallel on every engine

Status: implemented. Spec: `KOSIKA.md`, "Workflows" (`parallel { STEPS }`).
Test: `native/parallel_test.go`; the reference's is `tests/test_parallel.py`.

## Why

The ask was a `together { }` block that runs independent steps at the same
time. Kosika already has it: `parallel { }`, with the rules that make it
safe (a step in the block may not use another's result, change anything,
call a workflow, ask an agent or touch memory), and a trace that is
exactly the one written order gives. Adding `together` would be a second
word for the same thing, against "keep the grammar small and regular".

What was missing was the engine that matters most. The Python reference
began a block's model and read-only tool calls at the same time; the Go
engine, the primary one and the one on phones, made them one after
another. One program gave the same answer on both, but not in the same
time. An agent that asks three models in a block waited three times as
long on the Go engine.

## What

The Go engine now begins the first model call of each step of a block at
the same time, when the block begins (`beginTogether` in
`native/workflows.go`). When each step's turn comes it asks exactly as
before, and the request it makes is answered by the call already on its
way. Everything that keeps a run honest is untouched and still in written
order: the events, the model-call budget, `verify`, `retry` (a second
request is asked then), `otherwise`, the deadline. So the trace is the same
as before and replays the same.

A call is not begun early when:

- its model answers in order, as a test's scripted answers do, so a test
  means the same every time;
- its step learns (it may need no model) or votes;
- its arguments cannot be worked out yet (the step then reports why, in
  its turn);
- the run resumes a session.

Not yet on the Go engine: beginning read-only tool calls early, which the
reference does. Models are where the waiting is; tools can follow.

## Example

```kosika
workflow main(a: Job) -> Both {
  parallel {
    x = use_model fast(a.text)
    y = use_model slow(a.text + "!")
  }
  out = both(x, y)
}
```

Two 0.6 s model calls: before, about 1.2 s on the Go engine; now about
0.6 s, as on the reference.

## Tests

`native/parallel_test.go`: a model server that takes 0.6 s per call sees
both calls at once, the run takes under 1.1 s, and the trace has `x` before
`y` with the joined answer. It fails with `beginTogether` switched off, and
passes under `go test -race`. `tests/test_parallel.py` holds the reference
to the same, and `conformance/cases/parallel.kos` both engines to the
answers.

## For Kosika agents

Kosika AI's steps mostly depend on each other (each reads what earlier
steps found), so the step loop of proposal 1 stays in order. Where an
agent asks several models the same question, or gathers several sources
before thinking, it can write them in one `parallel { }` and now get the
time back on every engine.
