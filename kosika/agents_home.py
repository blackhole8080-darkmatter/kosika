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


def studio_settings_home() -> Path | None:
    """The agents_home the installed Studio was set up with, in
    %LOCALAPPDATA%\\Kosika Studio\\settings.json, when it names a folder that
    is there; else None."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    try:
        import json

        said = json.loads((Path(base) / "Kosika Studio" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    home = said.get("agents_home") if isinstance(said, dict) else None
    if isinstance(home, str) and home.strip() and Path(home).expanduser().is_dir():
        return Path(home).expanduser().resolve()
    return None


def agents_home() -> Path:
    """The kosika-agents folder: KOSIKA_AGENTS_HOME, else the one beside
    kosika, else the one the installed Studio's settings name (the installed
    app runs from its own folder, which has no kosika-agents beside it)."""
    told = os.environ.get("KOSIKA_AGENTS_HOME")
    if told:
        return Path(told).expanduser().resolve()
    beside = KOSIKA_HOME.parent / "kosika-agents"
    if beside.is_dir():
        return beside
    return studio_settings_home() or beside


def agents_folder() -> Path:
    """The folder holding one subfolder per agent (kosika-agents/agents)."""
    return agents_home() / "agents"


def agent_program(name: str) -> Path:
    """The main program of one agent, such as agent_program("study")."""
    return agents_folder() / name / "main.kos"
