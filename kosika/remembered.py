"""What `step` units remember: each result with the inputs it was made for.

A `step` is a workflow whose result is remembered (see
``WorkflowDecl.remembered``). Within one run, a second call of the same step
with the same inputs makes no calls and gives the first call's result. With a
memory folder (``run --memory FOLDER``) the results outlive the run: each is
one JSON file in ``FOLDER/steps``, named by a digest of the program, the step
and its inputs, which both engines compute the same way and either may read.

A result is kept for the program it was made by. Change the program, any of
its files, and nothing remembered before is used: a step's result depends on
the functions, models and tools it reaches, not only on its own lines.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .durable import digest


def step_key(program_hash: str, step: str, inputs: dict[str, Any]) -> str:
    """The name a step's result is remembered by."""

    return digest({"inputs": inputs, "program": program_hash, "step": step})


class Remembered:
    """The results of one run's `step` calls, and the folder that keeps them."""

    def __init__(self, folder: str | Path | None = None):
        self.folder = Path(folder) if folder is not None else None
        self._lock = threading.Lock()
        self._known: dict[str, tuple[Any, list[dict[str, Any]]]] = {}

    def _path(self, key: str) -> Path:
        assert self.folder is not None
        return self.folder / "steps" / f"{key}.json"

    def recall(self, key: str) -> tuple[Any, list[dict[str, Any]]] | None:
        """The result kept under ``key`` and the trace of the call that made
        it, or None when nothing is kept there."""

        with self._lock:
            if key in self._known:
                return self._known[key]
        if self.folder is None:
            return None
        try:
            kept = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(kept, dict) or "result" not in kept:
            return None
        events = kept.get("events")
        found = (kept["result"], events if isinstance(events, list) else [])
        with self._lock:
            self._known[key] = found
        return found

    def keep(
        self,
        key: str,
        step: str,
        inputs: dict[str, Any],
        result: Any,
        events: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._known[key] = (result, events)
        if self.folder is None:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        spare = path.with_suffix(".tmp")
        spare.write_text(
            json.dumps(
                {"step": step, "inputs": inputs, "result": result, "events": events},
                ensure_ascii=False,
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        spare.replace(path)
