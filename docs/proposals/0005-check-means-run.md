# Proposal 5: what one engine checks, the other runs the same

Status: implemented. Spec: `KOSIKA.md`, "Execution" (whole-run limits, and
the agreement rule). Gate: `python -m kosika.conformance agree ENGINE`, run
in CI on Linux. Exceptions: `conformance/known_gaps.txt`.

## Why

The research found that a new language's main weakness is errors a model
can't trust: the checker is the model's teacher. Proposal 4 fixed one case
where `check` said yes and a run said no. This proposal looks for every
other case, across the whole repository, and keeps it from coming back.

A sweep of all 184 Kosika programs outside the suite through both engines
found:

- **One real disagreement.** `learn near` (lessons for differently worded
  questions) checks on Python. The Go engine failed it with a syntax error,
  "Unexpected workflow item 'near'", which tells a model its code is wrong
  when it is not.
- **8 programs the Go engine can't read**, all `swarm`. It already says so
  by name.
- **11 early examples (from 2026-10) that check on neither engine.** They
  use syntax Kosika never had: `!`, `&&`, `||`, `@`, `skill`, `python` tools.
  An agent that learns from the examples folder learns wrong syntax.
- **A limit only one engine let you raise.** The Go engine stopped every
  run at 64 model calls, with no option to raise it. The book-reading agent,
  which needs one model call per page, could not run on Go at all. On
  Python, raising `--max-model-calls` alone was not enough either, because
  a second limit (`--max-requests`, also 64) stopped the run with a
  different message.

## What

- **Run limits.** The Go engine's `run` takes `--max-model-calls` and
  `--max-tool-calls`, from 1 to 1,000,000, with the reference's words when a
  value is out of range. A workflow's `budget` and an environment's only
  lower them. On the reference, `--max-requests` follows
  `--max-model-calls` unless given. A 70-call run now stops at 64 on both
  engines with the same message, and with `--max-model-calls 100` it
  finishes on both with the same trace.
- **`learn near` on Go** is refused by name: "kosika-native cannot run
  `learn near` yet; it learns exact questions: write `learn`, or run this on
  the reference engine".
- **The gate.** `python -m kosika.conformance agree ./kosika-native` checks
  every `.kos` in the repository on both engines. What one checks, the
  other must check. Tests must come out the same. Where an engine lacks a
  feature, it must refuse it by name, never with a syntax error or a crash.
  The 20 exceptions are listed in `conformance/known_gaps.txt`, each with
  its reason. The list only shrinks: a listed program that the engines now
  agree on fails the gate until its line is removed. The gate takes about 90
  seconds.

## Example

Before, on the Go engine:

```
$ kosika-native run book.kos --max-model-calls 100
kosika-native run does not take --max-model-calls
```

After, on both engines:

```
$ kosika-native run book.kos --input book.json --max-model-calls 100
{'count': 70}
```

## Tests

- `tests/test_conformance.py`:
  - a long run stops at the same place on both engines, finishes on both
    when the limit is raised, and leaves equal traces;
  - an out-of-range limit is refused with the same words by both engines;
  - the known-gaps list names only real programs.
- CI: the new step "Both engines agree on every program in the repository".

## What the gate does not do yet

- It doesn't compare the tests of the standard library. `tests/test_std_parity.py` already holds both copies of the library to each other.
- It doesn't compare two programs whose tests take over 60 seconds on the reference: `benchmarks/kosi/board.kos` and `examples/apps/chat/chat_service.kos`. The gate names them as SLOW on every run. In both, Python takes about 155 seconds and Go about 15, a sign that the reference needs speed work.
- It doesn't compare error messages word for word when both engines refuse. Python often adds a hint that Go lacks. That is proposal 6 (diagnostics).
- It doesn't fuzz. Generated programs are the next step for this gate.

## For Kosika agents

The 11 examples that check nowhere are good first tasks for the coder swarm. Each one gets rewritten in real Kosika, so that the examples folder teaches only syntax that works.
