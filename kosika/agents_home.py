"""Where the Kosika agents live.

The agents (coder, hands, lookout, gatherer, desk, the assistant Kosi uses,
the study companion, the coder swarm and the virtual models) are kept in
their own repo, kosika-agents, next to this one:

    projects/
      kosika/          the language, its engines and Studio
      kosika-agents/   agents/  swarm/  models/  benchmarks/

KOSIKA_AGENTS_HOME points somewhere else when yours sit elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The kosika repo this package belongs to.
KOSIKA_HOME = Path(__file__).resolve().parent.parent

#: How to get the agents when they are missing, said wherever they are needed.
FETCH = (
    "The Kosika agents live in their own repo now. Put it next to kosika with "
    "`git clone https://github.com/blackhole8080-darkmatter/kosika-agents`, "
    "or point KOSIKA_AGENTS_HOME at your copy."
)


def agents_home() -> Path:
    """The kosika-agents folder: KOSIKA_AGENTS_HOME, or the one beside kosika."""
    told = os.environ.get("KOSIKA_AGENTS_HOME")
    return Path(told).expanduser().resolve() if told else KOSIKA_HOME.parent / "kosika-agents"


def agents_folder() -> Path:
    """The folder holding one subfolder per agent (kosika-agents/agents)."""
    return agents_home() / "agents"


def agent_program(name: str) -> Path:
    """The main program of one agent, such as agent_program("study")."""
    return agents_folder() / name / "main.kos"
