"""Run the triage program over the labelled tickets with a command-line model.

    python benchmarks/thinking/measure.py NAME PROGRAM [ARGS...]

NAME labels the results file. Each ticket costs two model calls. A ticket whose
run fails (for example an answer outside the choices) is recorded as failed,
not retried.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from kosika.command_model import CommandModelProvider
from kosika.modules import load_program
from kosika.runtime import run

HERE = Path(__file__).parent


def main() -> int:
    name, command = sys.argv[1], sys.argv[2:]
    program = load_program(HERE / "triage.kos")
    provider = CommandModelProvider(command)
    tickets = json.loads((HERE / "tickets.json").read_text("utf-8"))
    rows = []
    for ticket in tickets:
        started = time.monotonic()
        row = {"id": ticket["id"], "want": [ticket["queue"], ticket["urgent"]]}
        try:
            got = run(
                program,
                "main",
                None,
                inputs={"ticket": {"text": ticket["text"]}},
                model_bindings={"assistant": provider},
            )
            row["got"] = [got["queue"], got["urgent"]]
            row["confidence"] = got["queue_confidence"]
        except Exception as exc:  # recorded, never hidden
            row["failed"] = f"{type(exc).__name__}: {exc}"[:300]
        row["seconds"] = round(time.monotonic() - started, 1)
        rows.append(row)
        print(row, flush=True)
    done = [row for row in rows if "got" in row]
    summary = {
        "model": name,
        "tickets": len(rows),
        "ran": len(done),
        "queue_correct": sum(r["got"][0] == r["want"][0] for r in done),
        "urgent_correct": sum(r["got"][1] == r["want"][1] for r in done),
        "rows": rows,
    }
    out = HERE / "results" / f"{name}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "rows"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
