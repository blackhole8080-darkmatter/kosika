"""Offline demonstration: successful, budget-stopped and cancelled execution."""

import json
import threading
from pathlib import Path

from kosika.controls import ExecutionError
from kosika.execution import ExecutionLimits
from kosika.modules import load_program
from kosika.runtime import run


def main() -> None:
    program = load_program(Path(__file__).with_name("recursion_match.kos"))
    cancelled = threading.Event()
    cancelled.set()
    for label, limits, cancel in (
        ("success", ExecutionLimits(max_function_calls=100), None),
        ("budget", ExecutionLimits(max_function_calls=2), None),
        ("cancelled", ExecutionLimits(), cancelled),
    ):
        events: list[dict] = []
        try:
            result = run(
                program, "main", None, limits=limits, cancel=cancel, _events_out=events
            )
        except ExecutionError as exc:
            print(
                json.dumps(
                    {
                        "scenario": label,
                        "event": events[-1]["event"],
                        "code": exc.code,
                        "used": events[-1]["execution"]["used"],
                    }
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "scenario": label,
                        "result": result,
                        "used": events[-1]["execution"]["used"],
                    }
                )
            )


if __name__ == "__main__":
    main()
