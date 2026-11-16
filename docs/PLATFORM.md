# Kosika as a software-building platform

**Status: design, not description.** Nothing in this document is implemented
unless a line says it is. It sits between the language contract
([KOSIKA.md](../KOSIKA.md)), the honest present
([KOSIKA_OVERVIEW.md](../KOSIKA_OVERVIEW.md)) and the agent roadmap
([KOSIKA_ROADMAP.md](../KOSIKA_ROADMAP.md)), and it borrows that roadmap's one
rule: every stage ends with a measured number, not a checklist.

The goal is narrow on purpose. Kosika is not becoming a general coding agent
that edits Python and TypeScript repositories. It is becoming the place where
**AI software is built, and where the artifact can be checked, replayed and
signed**. Foreign code is reached through `kosika.component/v1` declarations
with a pinned SHA-256, never by editing it directly.

## The four axes

A platform is four properties, not a feature list. Each is a different kind of
work, which is why they cannot be done in one push.

| Axis | What a platform needs | Where Kosika is today |
| --- | --- | --- |
| **Unit of work** | a project: many programs, targets, dependencies, a build graph | one file. `load_program(entry, root)`, `program.module_hashes`, `kosika/project_analysis.py` and `project_map.py` analyse a tree but nothing builds one |
| **The loop** | propose → test → check → measure → keep or undo, at repository scale | `kosika/selfdev.py`: one file, one exact-match edit, one model, no test generation |
| **The artifact** | content-addressed, signed, reproducible, with a manifest of what it needs | `kosika.pack/v1` carries `program_hash`, `module_hashes` and `needs`, and is **signed with Ed25519** as of stage 0; reproducibility and a lockfile are stages 1 and 3 |
| **Authority** | policy per project and environment, not a flag per run | `--allow-folder`, `--allow-shell`, `--approve`, `kosika.env`. Per invocation, not per project |

The asset that already exists and is rare: `kosika.development/v1`
(`kosika/development.py`), a versioned machine interface with `inspect`,
`check`, `run`, `trace` and `map`. That is the contract an external builder
agent would call, and stage 4 publishes it.

## Stage 0 — make the foundation honest

A platform cannot be built on a red gate.

- **Green every gate CI runs.** `mypy kosika` and `mypy kosika tests` are
  green (44 errors in 9 files, then 143 across the test corpus, now none).
  `ruff check` is green too: the tracked tree held 11 findings, each fixed
  where it came from rather than suppressed — a missing `typing` import that
  would have raised at runtime in an example, a dead camera assignment, an
  ambiguous single-letter name, two imports moved to the top, and two lambda
  assignments. The other 10,292 findings local `ruff check .` reported came
  from `external-integrations`, five submodules a CI checkout never
  initialises; they are excluded in `pyproject.toml` so a local run agrees
  with CI. `ruff format` then reformatted 120 tracked files (250 were already
  formatted), and the pass caught one real problem: rewrapped calls stranded
  `# type: ignore[arg-type]` comments where mypy no longer reads them. The
  fix was the signature's, not the comment's — `bind_notify_tool` never reads
  the tool argument, so it now declares `ToolDecl | None` and eleven
  suppressions in `tests/test_notify.py` are gone.

  One gate is not yet counted: `coverage report --fail-under=80`. The corpus
  is 185 files and needs a little over 400 s to instrument twelve of them on
  this laptop, so it is a job for a machine that can run it uninterrupted,
  not for an interactive session.
- **Sign the pack. Done.** Ed25519 over `program_hash` + `module_hashes` in
  `kosika/signature.py` (RFC 8032 in pure Python: no new dependency),
  `kosika pack sign`, and `kosika pack check --require-signature`.
  `tests/test_pack_signing.py` pins the RFC's three vectors, a changed program
  hash, a changed module whose hash was made to match, and an unsigned pack.
  The seed lives in `.kosika/pack-key.json` and never in a pack; a pack
  carries only the public key its signature verified under.
- **One program hash. Done.** Both engines name a program by its sources
  (`kosika/identity.py`, `native/identity.go`): the line `kosika-program/v1`,
  then one line per module in sorted path order holding the module's path and
  `sha256` of its bytes — the digests `Program.module_hashes` already records.
  A trace's `run_start.program_hash`, a pack's `program_hash`, a swarm's plan
  and the app's view all print it, so `kosika-native hash FILE.kos` and the
  reference agree: measured on the 121 loadable programs under `examples/`
  and `conformance/cases/`, the other 7 being refused by the native engine
  for features it does not read yet (swarms). `tests/test_identity.py` pins
  the format and holds the two engines to it. What the tree hash was for
  stays put: approvals remain on `plan_identity`
  (`kosika/virtual_model.py`), which `tests/test_hash_stability.py` keeps
  from moving.
- **Count conformance coverage. Measured.** `kosika/coverage.py` reads every
  `raise CheckError` site in `kosika/checker.py`, runs each program under
  `conformance/refused/`, and credits a rule when a refusal ends on its line
  (from the traceback, not by matching message text). Today **53 of 218**
  rules have a refused program: 163 cases, 143 of which end at a checker
  rule, 20 in the parser or module loader, and none stale.
  `tests/test_conformance_coverage.py` fails if that count falls. The list of
  the other 165 rules, by line and message, is what to work through next.

**Exit:** all three CI jobs green; `pack verify` refuses a tampered pack;
both engines print one program hash for one program.

## Stage 1 — the project becomes the unit

- **`kosika.toml`. Done.** `kosika/project.py` reads the project's name,
  entry, targets, modules, environments and policy. A target names a workflow
  the entry program declares plus the shape it is built into (`pack` is the
  one shape this device knows), so "build" means more than "check": a target
  naming a workflow that is not there is refused, not quietly widened to the
  whole program. TOML is read with `tomllib` where the interpreter has it and,
  on 3.10, with a small reader for the subset a project file uses.
- **A build graph and `kosika build`. Done.** `kosika/build.py` checks the
  whole graph through `report_path` before writing anything, refuses a
  declared module the program does not load, and emits `build/program.kpack`
  — the pack, which already carries every module and the program's identity —
  with a signed `build/manifest.json` naming the targets and what the program
  needs. A build that fails writes nothing at all.
- **Deterministic and incremental. Done.** The `build_hash` covers content
  only: schema, project, entry, targets, program identity, module hashes,
  needs, and the digest of every file written. The device's signing key is
  deliberately left out of it, so two machines with different keys agree on
  the hash and carry different signatures — a signature says who built it,
  never what it is. A second build with nothing changed writes nothing and
  reports `changed = False`. `tests/test_build.py` holds all of it: three
  targets, a rebuild, a copy of the project in another folder with its own
  key, the refusals, and a tampered manifest that stops verifying.
- **`kosika init`. Done.** `kosika init [FOLDER]` writes `kosika.toml` for a
  folder, and a small program too when the folder has none, taking the project
  name from the folder's name and the target's workflow from what the entry
  program actually declares. A folder holding several programs and no
  `main.kos` is asked to name one rather than guessed at, and an existing
  `kosika.toml` is read, not replaced, unless `--force`. What it writes builds:
  `init` followed by `build` is the promise, and the test holds it.
- **`kosika add`.** Still open: it resolves a module by content hash (stage 3),
  not from a registry, so it waits for that resolver to exist.

**Exit:** a three-target project builds twice with identical hashes, and a
build on a second machine of a signed pack reproduces the first machine's
artifact hash.

## Stage 2 — the loop at repository scale

This is the stage that decides whether the platform is real. Today's loop
proposes a single exact-match edit and keeps it if the tree still compiles and
lints. That is a demonstration of a principle, not a development loop.

1. **A task is a file with a test.** `.kosika/tasks/<id>.md` holds the
   sentence, the acceptance test and the budget. The loop's first act is to
   write a test that fails for the right reason. `kosika test` already scripts
   every model answer, so the acceptance test is deterministic and free.
2. **Edits are typed, not string surgery.** Exact character matching is why a
   third of proposals fail. Expose `kosika edit` over the AST: replace a
   declaration body, add a field, insert a step. The parser and printer exist.
3. **A change set is one plan.** `PLAN_TOOL = "$plan"` in `durable.py` already
   approves an agent's whole list of changes. Use it: one plan, one approval,
   one reviewable diff, one atomic undo.
4. **The ledger feeds the proposal.** The runtime already retries with
   `rejected` pairs. Pass the last failures *for this task kind* into the
   proposer, so the loop that records becomes a loop that learns.
5. **Test answers are keyed, not positional.** `answers assistant as
   "triage": [...]`, with a check-time refusal when the count does not match
   the model calls on that path. Positional answers drift silently the moment
   a step is inserted.

**Exit:** the benchmark below, run and published.

## The benchmark (build this before the loop)

Thirty held-out tasks is the smallest number that separates a real loop from a
lucky one. Without it, every stage-2 change is judged by taste.

- **Composition:** 10 fix (a test fails, make it pass), 6 extend (add a
  workflow step or a record field), 5 refactor (move a declaration, keep
  behaviour), 5 test-gap (write the missing test, make it pass), 4
  behaviour-change (change a result and update every caller).
- **Held out:** no task appears in `examples/`, `benchmarks/` or the prompt
  material used to tune the proposer. Tasks are stored with the commit they
  start from.
- **Each task records:** starting commit, one-sentence ask, acceptance test in
  `.kos`, a budget (`max_model_calls`, wall clock), and the existing suite that
  must still pass.
- **Scoring, per task, boolean:** every acceptance test passes, the existing
  suite passes unchanged, and the run's trace replays to the same result. A
  kept change that cannot be replayed is a failure, not a partial success.
- **Also recorded per task:** wall clock, model calls, tokens, edits proposed,
  edits refused by the check, and whether the failure text was reused.
- **Pass bar for stage 2:** 18 of 30 (60%), zero regressions on the existing
  suite, median wall clock at most 10 minutes on this machine.
- **Anti-gaming:** acceptance tests are written by someone other than the
  proposer where possible; the suite is run before the change to prove the
  task starts failing; and the same task run twice cold must not be scored as
  a learning result.

## Stage 3 — artifact, provenance, distribution

- **`kosika.lock`:** content-addressed module fetch over plain HTTPS with a
  pinned SHA-256. No registry, no accounts, no namespace to squat. It matches
  the instinct already in `components.py`, which re-hashes an artifact on
  every call.
- **A provenance record in every pack:** who signed, which model and version
  proposed each change, the task id, the trace hash, the gate results. This is
  what a reviewer actually needs and no coding agent ships it.
- **`kosika verify <pack>`:** replay every recorded change's test, check every
  signature, refuse on any mismatch. One command, offline, no model.
- **`kosika.policy`:** per environment, which models are qualified (the
  `qualified` ledger already records this), which folders, which tool
  sources, which budgets. Authority moves from flags to project state.

**Exit:** `kosika verify` on a pack built on another machine, offline, with one
mismatched test, refuses and names the change.

## Stage 4 — the platform surface

- **`development/v1` becomes the public API**, versioned and conformance
  tested, with `build`, `verify` and `develop` operations added to the five
  that exist. External builder agents talk to that, not to the CLI.
- **Studio becomes the review surface:** task → plan → diff → gate results →
  trace → signature. A place to approve a change set with evidence attached,
  not a chat window.
- **CI as a feature:** `kosika verify` in a workflow, so a pull request is
  "this change set is signed, tested and replayable".

**Exit:** an external agent completes one benchmark task through
`development/v1` alone, and the resulting pack verifies on a second machine.

## The metric this platform is actually for

Everyone measures whether a model wrote code. The claim only this stack can
make is in `KOSIKA_ROADMAP.md`: the **model-independence ratio**, the share of
a re-run's answers that need no model. On a build platform it becomes:

> **Cost per task, run 1 against run 10.** A platform whose tenth execution of
> a task family costs a fraction of the first, with identical verified output,
> is a different category of product from an agent that pays full price every
> time.

The harness exists in outline (`kosika learn-eval`, `benchmarks/learning/`,
cold and warm cases). Point it at build tasks rather than questions, and
publish the ratio even when it is embarrassing — that is what makes the other
numbers believable.

## Guardrails

- No language feature lands unless a benchmark task needs it.
- No stage relaxes approvals, grants or replay to make a number look better.
- No provenance claim ships before `kosika verify` exists.
- The Go engine passes stage 1's build conformance before stage 3 ships.
- Every stage lands with a design note and a conformance case, so the work
  survives one person's attention span.

## Open questions

1. Ed25519 through a dependency, or a small pure-Python implementation? The
   project is dependency-light (tree-sitter only), so this is a real decision,
   not a detail.
2. Is a target a workflow, an artifact, or both?
3. Does `kosika.policy` live in the project or in an environment file? Two
   sources for one name is a refusal elsewhere in this language; it should be
   a refusal here too.
4. Where does the benchmark's acceptance test live when the task is a
   refactor, and the honest answer is "the existing suite"?
