"""Measure invented answers in `extract`, with and without Kosika's guards.

    python benchmarks/thinking/evidence_measure.py NAME MODES --ollama MODEL
    python benchmarks/thinking/evidence_measure.py NAME MODES --command PROGRAM [ARGS...]

MODES is a comma-separated list of the workflows in `order.kos`:

    plain     the model alone
    grounded  `with evidence`; a rejected answer fails the run
    guarded   `with evidence retry 2 otherwise` an empty answer

Each ticket in `orders.json` is extracted once per mode and each of its three
fields is compared with a hand-written label:

    right     the labelled value, or correctly left empty
    invented  a value for a fact the ticket does not state
    wrong     a value that differs from the stated one
    missed    left empty although the ticket states it

A grounded run that Kosika rejects counts as `rejected`, not as an answer.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from kosika.command_model import CommandModelProvider
from kosika.model import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OpenAICompatibleProvider,
)
from kosika.modules import load_program
from kosika.runtime import run

HERE = Path(__file__).parent
FIELDS = ("customer", "order_id", "amount")
KINDS = ("right", "invented", "wrong", "missed")


class Counting:
    """Count the calls a provider receives."""

    def __init__(self, inner: ModelProvider):
        self.inner, self.calls = inner, 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return self.inner.complete(request)


def score(got: dict[str, Any], want: dict[str, Any]) -> dict[str, str]:
    marks = {}
    for field in FIELDS:
        have, truth = got.get(field), want[field]
        if isinstance(have, str) and isinstance(truth, str):
            same = have.strip().casefold() == truth.casefold()
        else:
            same = have == truth
        if same:
            marks[field] = "right"
        elif truth is None:
            marks[field] = "invented"
        elif have is None or have == "":
            marks[field] = "missed"
        else:
            marks[field] = "wrong"
    return marks


def main() -> int:
    name, modes, kind = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
    if kind == "--ollama":
        inner: ModelProvider = OpenAICompatibleProvider(
            "http://localhost:11434/v1", sys.argv[4], timeout=600.0
        )
    else:
        inner = CommandModelProvider(sys.argv[4:])
    provider = Counting(inner)
    program = load_program(HERE / "order.kos")
    tickets = json.loads((HERE / "orders.json").read_text("utf-8"))
    totals = {
        mode: {**dict.fromkeys(KINDS, 0), "rejected": 0, "calls": 0, "seconds": 0.0}
        for mode in modes
    }
    rows = []
    for ticket in tickets:
        row: dict[str, Any] = {"id": ticket["id"], "want": ticket["want"]}
        for mode in modes:
            before, started = provider.calls, time.monotonic()
            try:
                got = run(
                    program,
                    mode,
                    None,
                    inputs={"ticket": {"text": ticket["text"]}},
                    model_bindings={"assistant": provider},
                )
                marks = score(got, ticket["want"])
                row[mode] = {"got": got, "marks": marks}
                for mark in marks.values():
                    totals[mode][mark] += 1
            except Exception as exc:  # recorded, never hidden
                row[mode] = {"rejected": f"{type(exc).__name__}: {exc}"[:300]}
                totals[mode]["rejected"] += 1
            totals[mode]["calls"] += provider.calls - before
            totals[mode]["seconds"] = round(
                totals[mode]["seconds"] + time.monotonic() - started, 1
            )
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {"model": name, "tickets": len(rows), "totals": totals, "rows": rows}
    out = HERE / "results" / f"evidence-{name}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(totals, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
