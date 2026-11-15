"""Send a run's model traffic to Respan, once a key says where.

Kosika asks its model over a plain HTTP request, so nothing here is traced
until RESPAN_API_KEY is in the environment. With the key set, `start()` is
called once at startup, before any model is asked anything, and the spans
Respan collects go to https://api.respan.ai when the program ends. Without the
key the program runs exactly as it did before, and the `tracing` extra need not
even be installed.

The key is a server secret: it belongs in the environment (.env, or the
Infisical project `make dev` runs with), never in source or a browser bundle.
"""

from __future__ import annotations

import atexit
import contextlib
import os
from typing import Any

#: The variable Respan reads. Set it to trace; leave it empty to trace nothing.
ENV_KEY = "RESPAN_API_KEY"

_tracing: Any = None


def start() -> bool:
    """Turn tracing on when RESPAN_API_KEY is set; say whether it is on.

    Called once at startup, so a second call (a `start` that re-dispatches to
    `studio`, for example) returns the answer the first one gave. Tracing that
    cannot be started never stops the program: a missing package or a rejected
    key leaves the run exactly as it would have been.
    """
    global _tracing
    if _tracing is not None:
        return True
    if not os.environ.get(ENV_KEY):
        return False
    try:
        from respan import Respan

        _tracing = Respan(app_name="kosika")
    except Exception:  # noqa: BLE001 - tracing must not break a run
        return False
    atexit.register(_stop)
    return True


def _stop() -> None:
    """Hand over whatever is still held when the program ends."""
    if _tracing is None:
        return
    with contextlib.suppress(Exception):
        _tracing.shutdown()
