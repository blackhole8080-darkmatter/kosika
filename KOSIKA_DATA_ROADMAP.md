# Kosika data roadmap: databases a program is granted, and state it can replay

Written 27 October 2026. This is the database pillar beside
[KOSIKA_ROADMAP.md](KOSIKA_ROADMAP.md): what Kosika already keeps, what the
language still cannot reach, and the order to build it in. Every phase ends
with a number.

The rule held throughout:

> A database is host authority, never language authority. The checker knows
> the shape of an answer, the host knows the file, and the trace holds both.

## What already exists, and what it is not

Kosika keeps durable state today, in three places:

- `memory NAME of Record` (see [KOSIKA.md](KOSIKA.md)), one JSON file per step,
  keyed by text, with `recall`, `recall ... like` and `remember`.
- `know` lessons kept by `learn` steps, in a host memory folder.
- The host's own journals and approval ledgers — SQLite files Kosika writes for
  itself, which no program can read.

All three are *values Kosika already typed*. None of them is a **queryable
store**: a program cannot ask a question whose shape it does not already name,
cannot join two sets of facts, cannot read a table someone else wrote, and
cannot keep a million rows. That is the gap this pillar fills.

Also absent today, and worth naming so it is not mistaken for done: no
migrations, and no database any program can name for itself. Phases 0 to 3
below have shipped; a phase with a shipped slice says what is still owing.

## Phase 0 — `system "db"`: one file the runner names (slice 1, this change)

The smallest honest store, in the shape every other system tool already has.

- A seventh kind of system tool, `source system "db"`, with two operations:
  `query` (read-only, may be `access: observe`) and `run` (writes, so
  `access: execute`, so the workflow must say `require approval`).
- Granted by the runner with `--database FILE`. The file exists only if they
  name it; declaring a `db` tool grants nothing.
- A `query` opens SQLite with `mode=ro`, so it cannot write even by accident.
  A `run` opens the same file read-write, after an approval for that exact
  statement and its bound values.
- Results are a grid of text: `{columns: Text[], rows: Text[][], more: Bool}`.
  Every cell is text, because a SQLite column is whatever the row put there;
  `more` says the row limit cut the answer short. Only `values` are bound, never
  spliced, so a model's text cannot become SQL.
- **Done when:** a program queries a granted database, writes to it after an
  approval, is refused the write without one, and both replays return the
  recorded answers with no database present. (This change; see
  `tests/test_database.py`.)

## Phase 1 — Rows Kosika can name (slice 2, this change)

A grid of text is total but clumsy. A third operation makes rows typed.

- A third db operation, `operation: "rows"`, takes the same statement and
  answers `{rows: R[], more: Bool}`, where `R` is the program's own record.
  Each column is mapped by name onto the field of that name, and a row that
  does not fit is refused, naming the row and the column — the same strictness
  a model's answer already gets. `rows` only looks, so it MAY be `observe`.
- Only `Text`, `Number` and `Bool` fields can be a row, and each cell must
  actually be one: a text where a `Number` field is declared is refused with
  the cast it wants, and an absent value (`NULL`) is refused because a field is
  never absent — the statement says what to put there (`coalesce`). So the
  difference a grid cannot keep, between an empty text and an absent value, is
  a difference a program must now decide.
- `std/db.kos` grows what the grid needs meanwhile: `column(columns, name)` for
  a position, `cell(row, columns, name)` for one cell by name, `as_number(text)`
  for a cell that must hold a number, and `grid_rows(columns, rows)` for a map
  of column names to cells per row. `Number.to_text` and `Text.to_number`
  already existed; `as_number` is not a second spelling of the latter but its
  refusing half, since a program reading a cell usually has to decide what a
  cell that is not a number means.
- **Shipped when:** a program declares a row record, is given the same rows a
  grid gives, is refused a row that does not fit with the column named, and
  both answers replay from the trace with no database present. Measured in
  `tests/test_database.py`, and the shorthands read a grid the same way in both
  engines (`conformance/cases/std.kos`).
- **Still owing:** the benchmark that writes one task both ways and times them.
  The claim that the typed version is shorter is not yet measured, so it is not
  made here.

## Phase 2 — Schema as a checked declaration (slice 3, this change)

A program that reads or writes tables should say what tables it means, and a
run should fail before the first step when the store disagrees.

- `store NAME at "FILE" { table notes { id: Number title: Text done: Bool } }`
  in the language, checked against the database before the first step: a
  missing table, a missing column or a wrong affinity is a check or preflight
  error naming the file and the column.
- `kosika db init FILE --from main.kos` writes the tables a program declares,
  and `kosika db inspect FILE` says what is there. Both print the plan first;
  neither alters an existing column silently.
- The declaration's own words are checked with no file, so `kosika check`
  passes a program whose tables are not there yet. Whether the file agrees is
  the preflight, and a run that declares a store and names no database is
  refused rather than run with its promise unchecked. A store's file must be
  the one the runner named, so the declaration cannot quietly point at another
  database than the granted one.
- **Shipped when:** a program declares the tables it means, is refused before
  any step against a file whose column drifted (the file, table and column
  named), is refused when the granted file is a different one, and
  `kosika db init` writes the tables it declares while refusing a table that
  is already there and disagrees. Measured in `tests/test_store.py`.
- **Still owing:** migrations. `db init` creates a table that is not there and
  never changes one that is, so a rename, a retype or a dropped column stays a
  person's own statement, and a store still says nothing about keys, defaults,
  constraints or indexes — which is why `db init` writes none.

## Phase 3 — Durable state joins replay (slice 4, this change)

The trace is the reason to put a database behind this contract at all.

- A `query` records its statement, its bound values and its answer, so
  `kosika replay` reproduces a database-backed run with no database present.
  A `run` records the statement, the values and the rows changed.
- A run also records the tables the granted file had when it began, so a
  replay can put a recorded statement back to SQLite with no database at all
  (`kosika.database.sent_problem`). SQLite is asked to read the statement
  (`explain`), and an authorizer says whether it changes anything: a recorded
  statement that binds other values than were sent, or that changes the
  database under an operation that only looks, is refused; so is one that is
  more than one statement, and one SQLite cannot read at all. Nothing is ever
  run, and no database is needed.
- A write also records the approval it was given — who said yes, the plan
  hash and the action hash the approval ledger claimed — and a replay
  recomputes that hash over the step path, the tool and the arguments as
  recorded. A `run` whose statement or values were edited after the yes is
  refused even when the edited statement stays sendable, which is the case the
  statement judge alone could not catch.
- **Shipped when:** a recorded run built on a dropped database replays byte for
  byte, a doctored statement in the trace is refused by the validator, and a
  doctored statement or value under a recorded approval is refused too.
  Measured in `tests/test_database.py` (`RecordedStatementReplayTests`,
  `SentStatementTests`, `GrantedTablesTests`, `RecordedApprovalTests`).
- **Still owing:** the approval is tamper-evident, not a signature: a trace
  whose `approval` was taken out is judged on the call alone, as a trace made
  before approvals were recorded is, because a version bump would strand every
  trace at once and the native engine records no approval yet. A statement
  naming a table or column the run did not record is left alone rather than
  judged, since the run may have made one. A db call an agent made from its own
  transcript is not judged at all, because the transcript does not record which
  of its tools reach a database.

## Phase 4 — Databases in `kosika.env`, and read-only by default

- An `environment` gains `allow database "./notes.db"` (and `allow database
  "... " write` for the rarer case), so a project writes its grants once
  beside its programs, as it already does for folders and sites.
- **Done when:** an environment that allows a database read-only refuses a
  `run` tool at preflight, and the same program runs unchanged when the
  environment says `write`.

## Phase 5 — Other engines behind the same contract

Only after the contract has held for a year of local work.

- The same two operations against Postgres or a hosted SQLite reached through
  a declared service, with the connection named by the host and never by the
  program, and the same grants, limits and trace records.
- Vector rows stay out of the language surface: nearest-neighbour search is
  already `recall ... like` and `std/search.kos`, and should not grow a second
  spelling.
- **Done when:** one program runs unchanged against a local file and a hosted
  engine, with one recorded trace per engine.

## Deliberately not promised

- No ORM, no query builder, and no SQL generated by a model that the checker
  then trusts: the statement is the program's own text.
- No database the runner did not name. A hosted engine in Phase 5 stays
  out until local files are measured.
- No path where a database write happens without an approval, a grant, and a
  trace line.

## What shipped

| Piece | Where |
| --- | --- |
| The bound tool, grid and rows | `kosika/database.py` |
| Declaration rules and shapes | `kosika/system_tools.py` |
| The grant | `kosika/cli.py`, `kosika run ... --database FILE` |
| The grid shorthands | `kosika/std/db.kos`, and its copy in `native/std/` |
| The contract | `KOSIKA.md`, "System tools" and "The standard library" |
| The declaration, its words | `kosika/ast.py` (`StoreDecl`), `kosika/parser.py`, `kosika/checker.py` |
| The comparison, and `db init` / `db inspect` | `kosika/database.py` (`check_stores`, `plan_init`, `describe`), `kosika/cli.py` (`_db_command`) |
| The recorded tables, and the statement judge | `kosika/database.py` (`granted_tables`, `sent_problem`), `kosika/cli.py`, `kosika/runtime.py` |
| The recorded approval, and its hash | `kosika/durable.py` (`Claimed`), `kosika/runtime.py` |
| The replay check | `kosika/trace_replay.py` (`_approval_problem`, `sent_problem`) |
| The tests | `tests/test_database.py`, `tests/test_store.py`, `conformance/cases/std.kos`, `conformance/failing/std.kos` |
