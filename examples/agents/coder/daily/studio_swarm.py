"""Put a swarm's coders in Kosika Studio, each with its run and the run's story.

    python studio_swarm.py 2026-11-12-4 2026-11-12-5 2026-11-12-6     # then opens Studio

Each run folder of the daily coder (kosika-daily/DATE-N) keeps the coder agent
it ran as (_agents/coder: its program, memory and runs/, the run's trace).
This copies each into kosika-daily/studio-swarm/<coder-name>/, named after
what it was asked, and opens Kosika Studio on that folder: every coder shows
as an agent, and its past run is told in plain words by Studio's story view
(kosika/story.py), from the trace, with nothing run again.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

DAILY = Path.home() / "Aryan_Private" / "kosika-daily"
REPO = Path(__file__).resolve().parents[4]
SHOWN = DAILY / "studio-swarm"


def name_for(run: Path, at: int) -> str:
    asked = (run / "request.txt").read_text(encoding="utf-8") if (run / "request.txt").exists() else run.name
    found = re.search(r"\b([A-Z][A-Z_]+\.md|[a-z_]+\(\)|[a-z_]+\()", asked)
    what = found.group(1).strip("(").replace(".md", "").lower() if found else run.name
    return f"coder-{at}-{re.sub(r'[^a-z0-9_]+', '_', what).strip('_')}"


def main(names: list[str]) -> None:
    SHOWN.mkdir(parents=True, exist_ok=True)
    for at, name in enumerate(names, 1):
        run = DAILY / name
        source = run / "_agents" / "coder"
        if not source.exists():
            print(f"{name}: no coder agent kept there")
            continue
        target = SHOWN / name_for(run, at)
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".gocache"))
        for extra in ("report.md", "request.txt", "change.patch"):
            if (run / extra).exists():
                shutil.copy(run / extra, target / extra)
        print(f"{name} -> {target.name}")
    subprocess.Popen([sys.executable, "-m", "kosika.cli", "studio", str(SHOWN)], cwd=REPO,
                     env={**__import__("os").environ, "PYTHONPATH": str(REPO)})
    print("Kosika Studio is opening on", SHOWN)


if __name__ == "__main__":
    main(sys.argv[1:])
