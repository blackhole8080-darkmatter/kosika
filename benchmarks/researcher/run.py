"""Ask the researcher agent forty questions about a folder of notes.

    python run.py --model qwen2.5:7b
    python run.py --model qwen2.5:1.5b --strong qwen2.5:7b --embed-model nomic-embed-text --runs 3

Unlike the evals inside the agent, nothing here is scripted: the real
search reads the real folder, the real model answers, and with
`--embed-model` the real search by meaning and the second check are used.

Each answer is put in one of four classes:

    right         the expected fact, with the right file and line
    wrong shown   an answer was shown and it is not right. This is the
                  harmful kind: the agent said something false with a quote.
    missed        the notes hold the answer and the agent declined to give one
    failed        the run stopped with an error

For a question the notes do not answer, declining is `right` and showing
any answer is `wrong shown`.

Results are saved in `results/`, one file per run of this script, so that
later runs can be compared with earlier ones.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from kosika.cli import _workflow_model_bindings  # noqa: E402
from kosika.embeddings import Meaning, ollama_embedder  # noqa: E402
from kosika.modules import load_program  # noqa: E402
from kosika.runtime import run  # noqa: E402
from kosika.system_tools import bind_system_tools  # noqa: E402

AGENT = REPO / "examples" / "agents" / "researcher" / "main.kos"
KINDS = ("words", "meaning", "several", "none")


def judge(question: dict[str, Any], text: str) -> str:
    declined = text.startswith(
        ("I will not guess", "I found nothing", "Ask me something")
    )
    if question["kind"] == "none":
        return "right" if declined else "wrong shown"
    if declined:
        return "missed"
    source = f"From: {question['file']}, line {question['line']}"
    answered = text.split("\nQuote:")[0]
    if question["answer"].lower() in answered.lower() and source in text:
        return "right"
    return "wrong shown"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--strong", help="The model asked when the first is not proven."
    )
    parser.add_argument(
        "--embed-model", help="Turns on search by meaning and the second check."
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--kind", choices=KINDS, help="Ask only this kind of question.")
    parser.add_argument("--limit", type=int, help="Ask only the first N questions.")
    parser.add_argument(
        "--label",
        default="",
        help="A word for this version of the agent, kept with the results.",
    )
    args = parser.parse_args()

    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    if args.kind:
        questions = [q for q in questions if q["kind"] == args.kind]
    questions = questions[: args.limit] if args.limit else questions
    program = load_program(AGENT)
    spec = args.model + (f",strong={args.strong}" if args.strong else "")
    models = _workflow_model_bindings(
        argparse.Namespace(
            model=spec,
            model_binding=None,
            model_fixture=None,
            models=None,
            model_url=None,
            model_protocol="ollama",
            model_timeout=300.0,
        ),
        program,
    )
    with tempfile.TemporaryDirectory(prefix="kosika-bench-") as scratch:
        meaning = (
            Meaning(
                ollama_embedder(args.embed_model),
                args.embed_model,
                Path(scratch) / "vectors.sqlite3",
            )
            if args.embed_model
            else None
        )
        tools = bind_system_tools(program, folder=HERE / "notes", meaning=meaning)
        rows: list[dict[str, Any]] = []
        started = time.monotonic()
        for number, question in enumerate(questions, 1):
            outcomes: list[str] = []
            texts: list[str] = []
            for attempt in range(args.runs):
                try:
                    said = run(
                        program,
                        "main",
                        Path(scratch) / f"q{number}-{attempt}.jsonl",
                        inputs={"msg": {"text": question["question"]}},
                        model_bindings=models,
                        tool_bindings=tools,
                    )["text"]
                    outcomes.append(judge(question, said))
                except Exception as exc:  # noqa: BLE001 - every failure is a result
                    said = f"{type(exc).__name__}: {exc}"
                    outcomes.append("failed")
                texts.append(said)
            rows.append({**question, "outcomes": outcomes, "said": texts})
            print(
                f"{number:2}/{len(questions)} {question['kind']:8} {' '.join(o[:5] for o in outcomes):18} {question['question']}",
                flush=True,
            )
        seconds = round(time.monotonic() - started, 1)

    classes = ("right", "wrong shown", "missed", "failed")
    table = {
        kind: {
            name: sum(
                row["outcomes"].count(name) for row in rows if row["kind"] == kind
            )
            for name in classes
        }
        for kind in KINDS
    }
    total = {name: sum(table[kind][name] for kind in KINDS) for name in classes}
    asked = len(rows) * args.runs
    print(
        f"\n{spec}"
        + (f" with {args.embed_model}" if args.embed_model else "")
        + f": {asked} answers in {seconds} s"
    )
    print(f"{'':10}{'right':>7}{'wrong shown':>13}{'missed':>8}{'failed':>8}")
    for kind in KINDS:
        if any(table[kind].values()):
            print(
                f"{kind:10}"
                + "".join(
                    f"{table[kind][name]:>{width}}"
                    for name, width in zip(classes, (7, 13, 8, 8))
                )
            )
    print(
        f"{'all':10}"
        + "".join(
            f"{total[name]:>{width}}" for name, width in zip(classes, (7, 13, 8, 8))
        )
    )

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = spec.replace(":", "-").replace(",", "+").replace("=", "-")
    out = (
        HERE
        / "results"
        / f"{stamp}_{name}{'_meaning' if args.embed_model else ''}{'_' + args.label if args.label else ''}.json"
    )
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "when": stamp,
                "label": args.label,
                "model": spec,
                "embed_model": args.embed_model or "",
                "runs": args.runs,
                "seconds": seconds,
                "total": total,
                "by_kind": table,
                "questions": rows,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"saved {out.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
