# Proposal 6: error messages agents can act on

Status: implemented. Spec: `KOSIKA.md`, "Diagnostics", and the function-value
rule in "Records and pure functions".
Tests: `tests/test_diagnostics_parity.py`, `tests/test_diagnostics.py`,
`conformance/refused/captured_local.kos`.

## Why

The research (`kosika-research/reports/Programming paradigms for
Kosika.md`) found the checker's feedback is the main lever for a model that
writes Kosika: a model repairs a program from the error it gets, so the error
has to be the same everywhere, machine-readable, and say what to do. Before
this, the Python engine gave every error a stable code and a JSON report, but
the Go engine (the one that runs on phones and in the browser) printed plain
text only. Some Go messages also differed from Python's, and two common
mistakes gave messages that did not lead to the fix.

## What

- `kosika-native check FILE --format json` prints the same
  `kosika.diagnostics/v1` report as `python -m kosika.cli check FILE --format
  json`: same codes, same messages, and the same range for a located error.
- One code table. `kosika/diagnostics.py` holds the codes, the message rules
  and the new `HELP` lines; `python -m kosika.diagnostics` writes it out and
  the Go engine embeds that file. A test fails if the two drift.
- `help`: the usual fix for the 18 codes a model most often meets, for
  example `parse/workflow-item`: "A workflow holds steps (`name = <one
  call>`) ...".
- New code `engine/not-yet`: valid Kosika that this engine cannot run yet, so
  a model does not "fix" a correct program.
- New code `check/captured-variable`. A function written as a value does not
  capture locals; before, using one said only "unknown variable 'k'". Now:

  ```
  Step 'function scale local times': a function written as a value sees only
  its parameters, not 'k' from around it. Pass it in as a parameter, or write
  a named fn here, which may use 'k'
  ```

- Go's lexer and workflow-item messages now match Python's word for word.

## Measured

Of 173 programs in the repository (conformance, examples, std), 171 give the
same codes and messages on both engines. The two others use `store` and
`swarm`, which the Go engine does not read yet (they are on proposal 5's
known-gaps list).

## Not yet

- Go reports `range: null` for check errors; Python anchors them to the
  declaration or step they name. Next step: port the anchoring.
- Go has no `--format json` for `run`.
