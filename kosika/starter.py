"""Starter projects for `kosika new`.

A starter is the smallest program that asks a model a question and returns a
typed answer, with an offline fixture so it runs before any model is set up.
It grants nothing: running it live still needs an explicit `--model`.
"""

from __future__ import annotations

from pathlib import Path

STARTER_SOURCE = """\
// A typed question goes in, a typed answer comes out.
record Question {
  text: Text
}

record Answer {
  text: Text
}

// A model is declared by what it takes and returns. Which real model
// answers is chosen when you run the program, never in this file.
model assistant {
  input: Question
  output: Answer
}

// Each line of a workflow is one step: a name for the result, and one call.
workflow main(question: Question) -> Answer {
  answer = use_model assistant(question.text)
}

// A test supplies the model's answer, so it runs without a model.
test "the answer comes back typed" {
  answers assistant: [{text: "Kosika is a typed language for AI workflows you can check and replay."}]
  result = main({text: "What is Kosika in one sentence?"})
  expect result.text == "Kosika is a typed language for AI workflows you can check and replay."
}
"""

STARTER_INPUT = """\
{
  "question": {"text": "What is Kosika in one sentence?"}
}
"""

STARTER_RESPONSES = """\
{
  "assistant": [
    {"text": "Kosika is a typed language for AI workflows you can check and replay."}
  ]
}
"""

STARTER_README = """\
# {name}

A starter Kosika project. Run these from this folder.

Check the program:

    kosika check main.kos

Run its test without a model:

    kosika test main.kos

Run it offline with the recorded answer in `responses.json`:

    kosika run main.kos --input input.json --model-fixture responses.json

Run it with a real local model (for example one served by Ollama):

    kosika run main.kos --input input.json --model YOUR_MODEL

Replay the last run without calling any model:

    kosika replay run_trace.jsonl

See what the program contains:

    kosika mindmap main.kos --format text

Edit the question in `input.json`, or add fields to `Answer` in `main.kos`
and to `responses.json`, then run `kosika check main.kos` again.
"""


from .agent_starters import AGENT_FIXTURES, AGENT_STARTERS  # noqa: E402


class StarterError(ValueError):
    """A starter project cannot be created at the requested location."""


AGENT_README = """\
# {name}

{what}

Run its tests first. They use saved answers and need no model:

```
kosika test main.kos
```

{offline}Run it on a model:

```
{command}
```

See what a run did, step by step:

```
{command} --trace run.jsonl
kosika story run.jsonl
```

Then open `main.kos` and change it. `kosika check main.kos` tells you at once
if something is wrong, and the tests tell you if you broke what worked.
"""

#: The kinds `kosika new --kind` accepts.
KINDS = ("hello", *AGENT_STARTERS)


def starter_files(name: str, kind: str = "hello") -> dict[str, str]:
    if kind != "hello":
        if kind not in AGENT_STARTERS:
            raise StarterError(
                f"Unknown kind {kind!r}; choose one of: {', '.join(KINDS)}"
            )
        what, command, source, inputs = AGENT_STARTERS[kind]
        files = {
            "main.kos": source,
            "input.json": inputs,
            "README.md": AGENT_README.format(
                name=name, what=what, command=command, offline=_offline(kind)
            ),
        }
        fixture = AGENT_FIXTURES.get(kind)
        if fixture is not None:
            files["responses.json"] = fixture
        return files
    return {
        "main.kos": STARTER_SOURCE,
        "input.json": STARTER_INPUT,
        "responses.json": STARTER_RESPONSES,
        "README.md": STARTER_README.format(name=name),
    }


def _offline(kind: str) -> str:
    if kind not in AGENT_FIXTURES:
        return ""
    return (
        "Run it offline with the answers recorded in `responses.json`:\n\n"
        "```\n"
        "kosika run main.kos --input input.json --model-fixture responses.json "
        "--trace run.jsonl\n"
        "kosika replay run.jsonl\n"
        "```\n\n"
    )


def create_starter(directory: Path, kind: str = "hello") -> list[Path]:
    """Write a starter project into a new or empty directory."""

    files = starter_files(directory.resolve().name, kind)
    if directory.exists():
        if not directory.is_dir():
            raise StarterError(f"{directory} exists and is not a directory")
        if any(directory.iterdir()):
            raise StarterError(
                f"{directory} is not empty; choose a new folder so nothing is "
                "overwritten"
            )
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, content in files.items():
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
