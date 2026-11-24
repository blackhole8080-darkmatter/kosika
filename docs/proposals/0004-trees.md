# Proposal 4: records and choices that hold themselves (trees)

Status: implemented. Spec: `KOSIKA.md`, "Records and schemas".
Tests: `conformance/cases/trees.kos`, `conformance/refused/endless_record.kos`,
`conformance/refused/endless_choice.kos`, the tree test in
`tests/test_conformance.py`, and `tests/test_absent_fields.py`.

## Why

The research on how Kosika should grow (`kosika-research/reports/Programming
paradigms for Kosika.md`) found this the worst gap an AI writer meets. The
work agents do is full of trees: a code syntax tree, a file tree, JSON, a
plan whose steps have steps, a mind map. Before this,

```kosika
record Node { name: Text kids: Node[] }
```

passed `check` on both engines, then the Python engine refused to run it
("Recursive record schema 'Node' is not supported in v0.1") and the Go
engine crashed with a stack overflow. "Check says yes, run says no" is the
case that costs a model the most: it trusts the checker, and the failure
comes too late to repair. And the engines disagreed.

## What

- A record or choice may hold itself. Its JSON schema refers back to itself
  with `{"$ref": "#/$defs/Node"}` where it recurs, and the outermost schema
  of that type carries `"$defs": {"Node": ...}`. Each type carries its own
  definitions (rather than one table at the root) because workflow inputs and
  model answers put schemas inside other schemas; a reference is looked up
  in the nearest enclosing `$defs`.
- Both validators (`kosika/schema.py`, `native/replay.go`) follow `$ref`, so
  a value is checked at every level, and a problem says where:
  `$.folders[0].folders[0]: missing required property 'files'`.
- Filling a left-out optional field with null (what a model often omits)
  follows `$ref` too, at any depth. It also now fills a choice by the
  alternative the value names: before, it used the first alternative's
  fields, which could add a field the value's alternative does not have.
- New check, `check/endless-type`: a type that can never be built is
  refused, with the fix in the message.

  ```
  Record 'Loop' can never be built: its field 'next' always needs another
  Loop. Let the chain stop: write `next: Loop?` or `next: Loop[]`
  ```

## Example

Before: a plan of plans had to be flattened by hand into a list with parent
indexes, and a model asked for an expression had to answer in a made-up
text format that Kosika code then parsed.

After:

```kosika
choice Expr {
  num(value: Number)
  add(left: Expr, right: Expr)
  times(left: Expr, right: Expr)
}

model parser { input: Question output: Expr }

fn eval(e: Expr) -> Number recurse 32 {
  return match e {
    case num(value) { value }
    case add(left, right) { eval(left) + eval(right) }
    case times(left, right) { eval(left) * eval(right) }
  }
}

workflow calculate(question: Question) -> Answer {
  expr = use_model parser(text: question.text)
  out = {value: eval(expr)}
}
```

The model answers in the tree's own shape; Kosika checks every level and
does the exact arithmetic. That is the "deterministic-first" lesson: the
model decides the structure, code does the work.

## Tests

- `conformance/cases/trees.kos`: a folder of folders, an expression tree a
  model answers with, an optional chain. Same output on Python, Go and Go in
  WebAssembly.
- `conformance/refused/endless_*.kos`: both endless forms refused, same
  message on both engines.
- `tests/test_conformance.py`: the traces of a tree run are equal across
  engines (with `$defs` in them), each engine replays the other's, and a
  bad value deep in a tree is refused with the same message.
- `tests/test_absent_fields.py`: filling follows `$ref`; a choice is filled
  by its own alternative.

## Not in this proposal

- Python components (`kosika/component_sdk.py`) still refuse recursive
  dataclasses. Kosika programs are what agents write; components can follow.
- Generic trees (`Tree<T>`) wait for proposal 9 (generics).

## For Kosika agents

A Kosika agent can now ask a model for a plan of plans, a syntax tree or a
file tree, and get it checked at every level before any code uses it.
