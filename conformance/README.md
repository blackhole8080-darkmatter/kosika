# Kosika conformance suite

What Kosika means, written as programs with the results they must give.
An engine is an implementation of Kosika if it passes this suite. The
suite, not any one engine, is the judge.

Today there are two engines: the reference engine in `kosika/` (Python)
and the native engine in `native/` (Go). The native engine exists so that
Kosika can run on a machine with no Python.

## What is here

    cases/NAME.kos             a program whose tests must all pass
    cases/NAME.expected.txt    what `test` prints for it
    failing/NAME.kos           a program whose tests fail on purpose
    failing/NAME.expected.txt  what `test` prints for it, failures and all

Every program here is pure: records, functions and tests. It asks no
model, uses no tool and reaches no file, so it gives the same result on
any machine.

## What an engine must do

For each `.kos` file, `ENGINE test FILE` must print exactly the text in
the file beside it, and end with exit code 0 when every test passed and 1
when one failed. Line ends may be `\n` or `\r\n`.

An engine must also split every `.kos` file in this repository into the
same tokens as the reference: `ENGINE tokens FILE` prints one JSON object
a line, `{"kind", "value", "line", "column"}`.

`tests/test_conformance.py` holds both engines to this.

## How a case is added

Write `cases/NAME.kos`. Then let the reference engine say what it prints:

    python -m kosika.conformance write

Read the new `.expected.txt` before committing it. It becomes the law, so
it must be what Kosika should do, not merely what one engine happens to do.

## What the suite does not cover yet

- Programs that must be refused before they run. The reference engine
  checks types first; the native engine has no checker yet, and nothing
  here would catch that.
- Workflows, agents, tools, models, memories and imports.
- Vectors and matrices.
- 5 passing programs and 1 failing one are a beginning. A method or rule
  that no case uses is not held to anything.
