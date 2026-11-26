"""The census behind the Brain page's Platform view: what it finds, and what it never reads."""

from __future__ import annotations

import json
from pathlib import Path

from kosika.platform_census import census


def _agent(folder: Path, name: str, text: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "main.kos").write_text(text, encoding="utf-8")


def test_the_census_finds_agents_and_what_they_use(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    _agent(agents, "assistant", '// main.kos: Kosi, the voice\nimport "std/brain_core.kos"\nimport "std/designs_plot.kos"\n'
           'model thinker {\n}\nmemory talks of Talk\ntool read {\n  source system "web"\n}\n')
    _agent(agents, "steward", '// Steward: looks after services\ntool up {\n  source mcp "files"\n}\n')
    router = tmp_path / "router.json"
    router.write_text(json.dumps({"models": {"quick": {"id": "small-model", "url": "u", "key": "SOME_ENV_NAME"},
                                             "local": {"id": "local-model", "url": "u"}}}), encoding="utf-8")
    parts = {p["name"]: p for p in census(agents, router, tmp_path / "no-work")}
    kosi = parts["Kosi"]
    assert kosi["kind"] == "agent" and kosi["about"] == "Kosi, the voice"
    assert set(kosi["uses"]) == {"Brain", "std/designs_plot", "router", "Kosi talks", "web"}
    assert parts["std/designs_plot"]["kind"] == "design"
    assert parts["files (MCP)"]["kind"] == "mcp"
    assert parts["router"]["uses"] == ["local-model", "small-model"]
    assert set(parts["Brain"]["uses"]) == {"router", "Brain timeline"}
    assert "Kosi" in parts["Studio"]["uses"]
    # Model ids only: a key's name or value never reaches the page.
    assert "SOME_ENV_NAME" not in json.dumps(list(parts.values()))


def test_an_empty_folder_still_has_the_brain_and_studio(tmp_path: Path) -> None:
    parts = {p["name"]: p for p in census(tmp_path / "none", tmp_path / "none.json", tmp_path / "no-work")}
    assert set(parts) == {"Brain", "Brain timeline", "Studio"}
