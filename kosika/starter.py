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

workflow main(question: Question) -> Answer {
  step respond {
    let answer: Answer = use_model assistant(text: question.text)
    return answer
  }
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

Run it offline with the recorded answer in `responses.json`:

    kosika run main.kos --input input.json --model-fixture responses.json

Run it with a real local model (for example one served by Ollama):

    kosika run main.kos --input input.json --model YOUR_MODEL

Replay the last run without calling any model:

    kosika replay run_trace.jsonl

See what the program contains:

    kosika audience-map main.kos --audience outsider --format text

Edit the question in `input.json`, or add fields to `Answer` in `main.kos`
and to `responses.json`, then run `kosika check main.kos` again.
"""


class StarterError(ValueError):
    """A starter project cannot be created at the requested location."""


def starter_files(name: str) -> dict[str, str]:
    return {
        "main.kos": STARTER_SOURCE,
        "input.json": STARTER_INPUT,
        "responses.json": STARTER_RESPONSES,
        "README.md": STARTER_README.format(name=name),
    }


def create_starter(directory: Path) -> list[Path]:
    """Write a starter project into a new or empty directory."""

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
    for filename, content in starter_files(directory.resolve().name).items():
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
