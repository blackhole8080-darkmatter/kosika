# Kosika developing Kosika

This is the loop that lets Kosika keep moving when a hosted coding assistant is
unavailable, rate-limited or too expensive: **a change is proposed by whatever
model or agent is at hand, and it is kept only if the tree proves it.**

No quota is involved on the default path. The default proposer is a model
served on your own computer (`http://127.0.0.1:11434`), so the loop costs
nothing but wall-clock time and can run all night.

The design follows the language's own principle — *unlimited expression,
bounded authority* — applied to the language's own source:

| The model | The tree |
| --- | --- |
| Proposes one edit | Decides whether the edit is allowed at all |
| May be a 7B model on a CPU | Compiles, imports, lints, runs your tests |
| Is wrong often | Undoes the edit byte for byte |
| Is never asked about state | Keeps the ledger of what worked |

Nothing a model says becomes true. Nothing it proposes survives a failing
check.

## Three commands

```text
kosika selfdev survey                 # what the tree says is unfinished
kosika selfdev status                 # what earlier runs tried and kept
kosika selfdev run --model NAME       # propose, check, keep or undo
```

### survey

No model is asked anything. It reads:

* `TODO`, `FIXME`, `XXX`, `HACK` markers in `kosika/` and `tests/`;
* `raise NotImplementedError` stubs, titled with the function above them;
* unchecked `- [ ]` items in `AI_SUPERIORITY_ROADMAP.md`,
  `AI_SUPERIORITY_PROGRESS.md`, `KOSIKA_ROADMAP.md` and `KOSIKA_PLAN.md`.

Each finding becomes a task with an id derived from its kind, file and title —
not its line number, so moving code does not orphan its history. Read more
folders with `--folder examples`.

```text
$ kosika selfdev survey --limit 5
12 task(s) named by /home/you/kosika under kosika, tests
  3f9a1c07b2de  unimplemented kosika/swarm.py:88   unimplemented: elect_leader
  b71d4e0a9c33  marker        kosika/mind.py:41   TODO: keep the last retrieval
  ...
```

### run

```text
# a model on this computer; no quota, no bill
kosika selfdev run --model qwen2.5-coder:7b --cycles 5

# plus the checks that matter to you
kosika selfdev run --model qwen2.5-coder:7b \
  --gate "python -m pytest -q tests/test_cli_coverage.py"

# an agent already installed, while you still have it
kosika selfdev run --command "claude -p" --cycles 3

# what would be asked, without asking anything
kosika selfdev run --dry-run
```

Each attempt, in order:

1. **The request.** The task, the file as it is now (with line numbers that are
   not part of it), the paths that may change, the exact JSON shape of an
   answer, and the first line of every failure at this task so far.
2. **The rules.** A proposal may name only a path inside the project and inside
   `--allow` (default `kosika`, `tests`). A `find`/`replace` edit must match
   exactly one place in the file, character for character. Anything else is
   refused before a byte is written.
3. **The check.** The *changed files* must compile, import (if they are in
   `kosika/`), pass `ruff check` when it is installed, and pass every `--gate`
   command. This is a floor, not the whole suite — name the test command that
   protects the code you care about.
4. **The outcome.** Kept, or undone from the bytes captured before the edit.
   Either way it is appended to `.kosika/selfdev/ledger.jsonl` with the failure
   text, which becomes the note for the next attempt.

A task gets two attempts by default (`--max-attempts`), is given up on after
three attempts were not kept (`--max-task-failures`), and a run stops after
three unkept attempts in a row (`--max-consecutive-failures`) — that is a
broken setup, not a hard task. `--commit` commits each kept change with the
task id in the message; without it, changes stay in the working tree for you to
read.

### status

```text
$ kosika selfdev status
root        : /home/you/kosika
ledger      : /home/you/kosika/.kosika/selfdev/ledger.jsonl
attempts    : 14
  marker         kept 6/11  kept-rate 0.55
  unimplemented  kept 1/3   kept-rate 0.33
last        : kept TODO: keep the last retrieval
```

`kept-rate` per kind is the whole learning loop: a kind of task that keeps
failing falls in the ranking, a kind that keeps passing rises, and a task that
has been given up on is not attempted again.

## Running it while you sleep

`--until-hours` replaces `--cycles` and stops on a time budget instead of a
count; `--sleep` spaces attempts out. Both are ordinary flags, so a scheduler
can start it:

```powershell
# Windows, once a night at 02:00
schtasks /create /tn "kosika selfdev" /sc daily /st 02:00 /tr ^
  "python -m kosika.cli selfdev run --model qwen2.5-coder:7b --until-hours 3 --sleep 5 --commit"
```

```bash
# POSIX, once a night
0 2 * * * cd ~/kosika && python -c 'from kosika.cli import main; raise SystemExit(main())' \
  selfdev run --model qwen2.5-coder:7b --until-hours 3 --sleep 5 --commit
```

A run stops on its own when the backlog is empty, so a night with nothing to do
costs nothing.

## What this cannot do, said plainly

* **A local model is not a hosted frontier model.** Expect many proposals to be
  refused or undone. The gate is what makes that survivable, not a substitute
  for judgement: a change can compile, import and lint cleanly and still be
  wrong. Read `kosika selfdev status` and the diff before you trust a night's
  work; `--commit` off is the default for that reason.
* **The gate is only as good as the checks you give it.** Without `--gate`, a
  change is checked for syntax, imports and lint — nothing about behaviour.
  The loop cannot invent the test that would have caught a bug.
* **Documentation is not edited by default.** No check distinguishes a finished
  paragraph from a deleted checkbox, so `--allow docs` is available and
  unverified.
* **It does not choose what Kosika should become.** It removes what the tree
  already admits is unfinished. Direction is still yours; this keeps the queue
  moving while you are away.
