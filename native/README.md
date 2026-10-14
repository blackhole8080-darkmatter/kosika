# Kosika's native engine

Kosika run with no Python. It is written in Go and builds to one program
file.

    go build -o kosika-native.exe .
    kosika-native test FILE.kos      run the tests the file carries
    kosika-native tokens FILE.kos    the file's tokens, one JSON object a line
    kosika-native run FILE.kos --input IN.json --model qwen2.5:7b
                                     run a workflow, asking a real model
    kosika-native replay TRACE.jsonl check a run's trace, running nothing

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
records, functions (nested ones, closures, recursion within a stated
depth), `if`, `match`, `for`, `while`, `repeat`, `try`, `fail`, text with
values written in (`f"..."`), and the built-in methods on text, numbers,
lists, maps and optional values. Whole numbers are exact at any size.

Models, with the records they take and give, and general ones that take
and give text. Workflows, whose steps call
a function, run another workflow, use a tool through an agent, or ask a
model (asked again on a refused answer with `retry`), ask an agent, or
think (`classify`, `extract`, `summarize`, `decide`), with a fallback
(`otherwise`) or a choice of call (`match`); tools and agents as they are
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

It is held to the conformance suite in `../conformance`, where it must
print what the reference engine prints, word for word. See
`tests/test_conformance.py`.

## What it does not have yet

- Steps with `verify`, `vote`, `escalate` or `with evidence`, and
  `parallel` blocks.
- An agent using a tool that changes the world. Replaying an agent run's
  own trace, an answer with evidence, or a verified answer.
- Reaching a real tool other than through an MCP server: MCP servers, `system` tools, files, pages, the
  clock and chance. Approvals.
- Memories, boards, swarms and imports.
- A file that has any of these is refused, and the refusal names it.
- Vectors and matrices.
- Traces and replay.

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
