# Proposal 2: functions and workflows belong to their file

Status: implemented in both engines (reference `kosika/modules.py`, native
`native/modules.go`). Spec: `KOSIKA.md`, "Program and module grammar".
Conformance: `conformance/cases/module_scope.kos`,
`conformance/refused/import_clash.kos`, `conformance/refused/module_record_clash.kos`.

## Why

Every name in a Kosika program was global. Two files could not each have a
helper called `clip`, and a program could not declare a workflow called
`solve` while importing `std/math.kos`, because std/math exports a `solve`
of its own. Kosika AI ran into exactly that: its workflow `solve` was
refused with `Conflicting module declaration 'solve'` and had to become
`ai_solve`. The standard library works around it by hand, starting its
private names with `std_`. Every new std module or agent file makes the
next clash more likely, so this grows worse as Kosika grows.

## What

In each file, a function or workflow name means:

1. the file's own declaration, if it has one; else
2. the one a direct import exports.

That is all. Nothing new is written: no namespaces, aliases or `as`.

```kosika
// main.kos
import "std/math.kos"          // exports fn solve

workflow solve(ask: Ask) -> Report { ... }   // main's own solve
workflow main(ask: Ask) -> Report { r = call solve(ask: ask) }   // this one
```

std/math's own code and tests still call std/math's `solve`.

How the engines do it: when one function or workflow name is declared in
several files, the program's own file keeps the plain name and every other
copy is named `MODULE.NAME` once merged (`std/math.solve`,
`lib/plan_a.clip`); each file's uses are pointed at the copy they mean. A
dot and a slash can never be in a name written in Kosika, so nothing can
clash with it. Traces and errors show these names. A program with no such
clash is not changed at all: same tree, same trace, same identity.

## What stays shared by the whole program, and why

Records, choices, enums, shapes, stores, models, tools, agents, swarms,
memories and boards. A run is given values of these, the host binds them
(a model's provider, a tool's executor, a memory's folder) and a trace
reports them by name, so one name must mean one thing to everyone. Two
files declaring one of them is still refused, now with the reason and the
fix:

    Conflicting module declaration 'Answer' in lib/answer_record and module_record_clash;
    rename one: a record name is shared by the whole program, since records, models,
    tools and the like are what a run is given and reports by name

The Brain's model had to be renamed `brain_thinker` because agents have a
`thinker` of their own. Scoping models too would need hosts to bind
`std/brain_core.thinker` by its full name; that is a later proposal if it
is wanted.

## Errors

| Program | Message |
| --- | --- |
| a file imports one name from two files and has none of its own | `Conflicting module declaration 'area' in lib/clash and lib/clash_too; import_clash imports it from both, so declare its own or import only one` |
| two files declare one record (or model, tool, ...) | `Conflicting module declaration 'Answer' in lib/answer_record and module_record_clash; rename one: a record name is shared by the whole program, ...` |

What changed: a file declaring a function or workflow its import also
exports was refused before (`Conflicting module declaration 'area'`); now
its own comes first.

## Kosika AI, before and after

Before (kosika-agents `agents/ai/main.kos` on the measurable-agents branch):

```kosika
import "std/math.kos"
...
    case _ { call ai_solve(ask) }
...
workflow ai_solve(ask: Ask) -> Report {
```

After: the name it wanted is allowed.

```kosika
import "std/math.kos"
...
    case _ { call solve(ask) }
...
workflow solve(ask: Ask) -> Report {
```

Its 90 tests on `main` pass unchanged on both engines with this change
(nothing in it clashes, so nothing in it is renamed).

## Tests

- `conformance/cases/module_scope.kos`: two libraries each with a private
  `clip`, `plan_of` and `solve`, the program with its own `clip` and its own
  `solve` while importing std/math, whose own tests still pass.
- `conformance/refused/import_clash.kos`: one name imported from two files.
- `conformance/refused/module_record_clash.kos`: two records of one name.
- `tests/test_conformance.py`: both engines write the same trace, naming
  `lib/plan_a.solve`, and each replays the other's.
