# Kosika's native engine

Kosika run with no Python. It is written in Go and builds to one program
file.

    go build -o kosika-native.exe .
    kosika-native test FILE.kos      run the tests the file carries
    kosika-native tokens FILE.kos    the file's tokens, one JSON object a line

## What it has

The core of the language, which reaches nothing outside the program:
records, functions (nested ones, closures, recursion within a stated
depth), `if`, `match`, `for`, `while`, `repeat`, `try`, `fail`, text with
values written in (`f"..."`), and the built-in methods on text, numbers,
lists, maps and optional values. Whole numbers are exact at any size.

Workflows, whose steps call a function, run another workflow, or use a
tool through an agent, with a fallback (`otherwise`) or a choice of call
(`match`); tools and agents as they are declared; and tests
that run a workflow and script what its tools answer. A tool is reached
only through those scripted answers: this engine reaches no real tool yet,
and one that changes the world is refused, as the reference refuses it in
a test, for want of a recorded approval.

Before it runs a program it checks it, as the reference does: a program
whose types do not agree is refused, with the reference's own message, on
the output and with exit code 2. A name the file uses but never declares is
refused as the file is read, like a fault in the text.

It is held to the conformance suite in `../conformance`, where it must
print what the reference engine prints, word for word. See
`tests/test_conformance.py`.

## What it does not have yet

- Models, and so asking an agent or a model, and thinking steps.
- Steps with `verify`, `vote`, `retry` or `escalate`, which belong to
  models, and `parallel` blocks.
- Reaching a real tool: MCP servers, `system` tools, files, pages, the
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
