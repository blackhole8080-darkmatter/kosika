"""The Kosika agents these tests use as real programs.

They live in the kosika-agents repo beside kosika (or KOSIKA_AGENTS_HOME).
Without it, the tests that need them are skipped and say how to get them;
CI sets KOSIKA_AGENTS_REQUIRED=1 so they fail there instead of skipping.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from kosika.agents_home import FETCH, agents_folder, agents_home

AGENTS = agents_folder()
SWARM = agents_home() / "swarm"


def need_agents() -> Path:
    """The agents folder, or the test module is skipped (failed in CI)."""
    if not AGENTS.is_dir():
        if os.environ.get("KOSIKA_AGENTS_REQUIRED"):
            raise RuntimeError(f"No agents at {AGENTS}. {FETCH}")
        raise unittest.SkipTest(FETCH)
    return AGENTS
