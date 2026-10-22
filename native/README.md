# Kosika's native engine

Kosika run with no Python. It is written in Go and builds to one program
file. Its one outside package, `golang.org/x/text` (for matching quotes
as the reference does), is kept in `vendor/`, so it builds with no
network.

    go build -o kosika-native.exe .
    kosika-native test FILE.kos      run the tests the file carries
    kosika-native tokens FILE.kos    the file's tokens, one JSON object a line
    kosika-native run FILE.kos --input IN.json --model qwen2.5:7b
                                     run a workflow, asking a real model
    kosika-native replay TRACE.jsonl check a run's trace, running nothing
    kosika-native capabilities       the conformance surfaces it reads

`run` takes the reference's flags for models: `--model NAME` (or
`fast,strong=big`), `--model-url`, `--model-protocol openai|ollama`,
`--model-binding`, `--model-timeout`, `--max-output-tokens`, and
`--workflow` and `--input`. Without `--model-url` it asks a local Ollama
server. `--mcp-server SERVER --mcp-manifest PINNED.json [--mcp-source
NAME]` lets the program's tools from that source reach an MCP server,
checked against its pinned manifest first; a server written in Python is
started with the Python on the machine, as the reference starts it.
`--model-fixture FILE` answers each model from a file instead, offline. It
prints the workflow's result as `kosika run` does, and writes the run's
trace to `--trace` (`run_trace.jsonl` unless told otherwise): the same
events as the reference's, apart from the time and the program's hash,
which here is a hash of the program's text. Each engine's `replay`
accepts the other's traces.

A tool that changes the world runs only with an approval of exactly that
action, as in the reference: `--journal FILE --binding-id ID` keeps what
the run has done, a run that reaches such a tool stops with exit code 3
and prints the action to approve, and `--approval-record FILE` (or
`--approve`, asking at the terminal) lets it run, once. Begun again, a run
takes what it already did from its journal and does nothing twice. The
journal is this engine's own: two JSON files, not the reference's SQLite,
so an approval given to one engine does not serve the other.

## What it has

The core of the language, which reaches nothing outside the program:
records, choices (tagged unions, constructed unqualified and matched by
alternative with its fields bound), functions (nested ones, closures,
recursion within a stated depth), `if`, `match`, `for`, `while`, `repeat`,
`try`, `fail`, text with values written in (`f"..."`), and the built-in
methods on text, numbers, lists, maps and optional values. Whole numbers
are exact at any size.

Models, with the records they take and the records or choices they give,
general ones that take and give text, and ones that answer `as text`.
Workflows, whose steps call
a function, run another workflow, use a tool through an agent, or ask a
model (its answer checked by a function of the program with `verify`,
asked again on a refused answer with `retry`, asked several times with
`vote N`, the models listed with `using` taking turns, or handed to a
stronger model with `escalate`, or answered from what it learned with
`learn` when run with `--memory`), ask an agent, or
think (`classify`, `extract`, `summarize`, `decide`, the first three
quoting the text they were given when written `with evidence`), with a
fallback
(`otherwise`) or a choice of call (`match`), or are a plain value worked
out by code (`count = before + 1`); `parallel` blocks, whose steps it
runs in the order written, which gives the trace the reference keeps; tools and agents as they are
declared; and tests that run a workflow and script what its models and
tools answer. With `run`, a model is a real one: a model server spoken to
as the reference speaks to it, with the same requests word for word; a
tool is answered by a pinned MCP server; and an agent's model may call the
agent's tools, each answer given back to it, until it answers. A tool that
changes the world is refused, as the reference refuses it without a
recorded approval.

Before it runs a program it checks it, as the reference does: a program
whose types do not agree is refused, with the reference's own message, on
the output and with exit code 2. A name the file uses but never declares is
refused as the file is read, like a fault in the text.

Memories and boards, as the reference has them: each is made two tools
that only look, used through a generated agent. `run --memory FOLDER`
keeps a program's memories there, one JSON file a memory written as the
reference writes it; an environment keeps them in its own folder; a test's
memories are its own. A board lives for one run.

`run --env NAME` runs in an environment of the project's `kosika.env`, as
`kosika run --env` does: the models, MCP servers, budgets and the folder
for traces and journals all come from it, the options it decides are
refused beside it, and a broken file is refused with the reference's
words and place.

It is held to the conformance suite in `../conformance`, where it must
print what the reference engine prints, word for word. `capabilities`
names the surfaces it reads; a suite program that needs one it does not
claim is skipped by name. See `tests/test_conformance.py`.

## What it does not have yet

- An agent's plan that writes a Python file: the reference reads such a
  file with Python's own parser before showing the plan, which this
  engine does not carry, so such a plan is refused by name. A plan of
  other files is read as the reference reads it: plainly wrong files, a
  Kosika file that does not check or whose tests fail, and the agent's
  own `check`; skills chosen by a host are not here. Replaying an agent
  run's own trace.
- `system` tools other than files, shell, services, web and meaning
  (kosika, agents, outside, notify, linux, computer). Its search by
  meaning remembers vectors in a store of its own
  (`~/.kosika/embeddings-native.bin`), not the reference's SQLite one, so
  each engine embeds a line once for itself.
- Swarms.
- A file that has any of these is refused, and the refusal names it.
- Vectors and matrices.

## How it is laid out

    lexer.go     text to tokens
    parser.go    tokens to a program (ast.go says what a program is)
    values.go    what a value is, and how numbers behave
    parse_workflows.go   reading tools, agents and workflows
    parse_models.go      reading models
    thinking.go  thinking steps made model calls, and general models
    live.go      asking a model: scripted answers, or a model server
    mcp.go       reaching a tool on a pinned MCP server
    run.go       the run command
    trace.go     writing a run's trace
    replay.go    checking a trace
    journal.go   the journal, and approvals
    env.go       environments: reading kosika.env
    stores.go    memories and boards, made tools, and where they are kept
    modules.go   a program in several files: import and export
    choices.go   tagged unions: choices, their constructor names, and matching
    ambient.go   the clock and chance: `now` and `random`
    system.go    files in the granted folder, and programs started there
    direct.go    `read file`, `list files`, `write file` in one line
    defaults.go  default values, and making a record by calling it
    enums.go     enums
    shapes.go    shapes: types every record with their functions is
    everyday.go  everyday methods on text, numbers, lists and dates
    agent_changes.go   an agent's own changes, planned and approved
    check.go     the checker: refusing a program before it runs
    check_workflows.go   the checker's rules for tools, agents, workflows
    workflows.go running a workflow, and the tools a test scripts
    sugar.go     a value written where a call belongs, made a call
    types.go     what the checker knows about types and methods
    eval.go      working expressions out, calling functions
    methods.go   the built-in methods
    tests.go     running a file's tests, and the report
    main.go      the command line

Each file follows the reference engine's rule for the same thing, quirks
included, and says so where a quirk is kept.
