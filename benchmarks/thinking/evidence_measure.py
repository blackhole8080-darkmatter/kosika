"""Compare `extract` with and without evidence on tickets with missing facts.

    python benchmarks/thinking/evidence_measure.py NAME PROGRAM [ARGS...]

Each ticket is extracted twice, once plainly and once `with evidence`, and
each field is compared with a hand-written label. A field is "invented" when
the model gave a value the ticket does not state. A grounded run that Kosika
rejects is counted as rejected, not as an answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from kosika.command_model import CommandModelProvider
from kosika.modules import load_program
from kosika.runtime import run

HERE = Path(__file__).parent
FIELDS = ("customer", "order_id", "amount")


def score(got: dict, want: dict) -> dict:
    counts = {"right": 0, "invented": 0, "wrong": 0, "missed": 0}
    for field in FIELDS:
        have, truth = got.get(field), want[field]
        if isinstance(have, str) and isinstance(truth, str):
            same = have.strip().casefold() == truth.casefold()
        else:
            same = have == truth
        if same:
            counts["right"] += 1
        elif truth is None:
            counts["invented"] += 1
        elif have is None:
            counts["missed"] += 1
        else:
            counts["wrong"] += 1
    return counts


def main() -> int:
    name, command = sys.argv[1], sys.argv[2:]
    program = load_program(HERE / "order.kos")
    provider = CommandModelProvider(command)
    tickets = json.loads((HERE / "orders.json").read_text("utf-8"))
    rows = []
    totals = {
        mode: {"right": 0, "invented": 0, "wrong": 0, "missed": 0, "rejected": 0}
        for mode in ("plain", "grounded")
    }
    for ticket in tickets:
        row: dict = {"id": ticket["id"], "want": ticket["want"]}
        for mode in ("plain", "grounded"):
            try:
                got = run(
                    program,
                    mode,
                    None,
                    inputs={"ticket": {"text": ticket["text"]}},
                    model_bindings={"assistant": provider},
                )
                row[mode] = got
                for key, value in score(got, ticket["want"]).items():
                    totals[mode][key] += value
            except Exception as exc:  # recorded, never hidden
                row[mode] = {"rejected": f"{type(exc).__name__}: {exc}"[:300]}
                totals[mode]["rejected"] += 1
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {"model": name, "tickets": len(rows), "totals": totals, "rows": rows}
    out = HERE / "results" / f"evidence-{name}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
