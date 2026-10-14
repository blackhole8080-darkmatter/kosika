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

It is held to the conformance suite in `../conformance`, where it must
print what the reference engine prints, word for word. See
`tests/test_conformance.py`.

## What it does not have yet

- **A checker.** The reference engine refuses a badly typed program before
  running it. This engine runs it, and fails later or not at all. Until it
  has one, do not trust it with a program the reference has not checked.
- Workflows, agents, tools, models, memories, boards, swarms and imports.
  A file that has one is refused, and the refusal names it.
- Tests that script a model's answers.
- Vectors and matrices.
- Traces and replay.

## How it is laid out

    lexer.go     text to tokens
    parser.go    tokens to a program (ast.go says what a program is)
    values.go    what a value is, and how numbers behave
    eval.go      working expressions out, calling functions
    methods.go   the built-in methods
    tests.go     running a file's tests, and the report
    main.go      the command line

Each file follows the reference engine's rule for the same thing, quirks
included, and says so where a quirk is kept.
