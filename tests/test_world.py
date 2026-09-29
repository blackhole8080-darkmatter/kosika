"""Tests for validated AI-world presentation manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kosika.city import build_city
from kosika.world import WORLD_SCHEMA, WorldError, load_world_manifest

ROOT = Path(__file__).parents[1]
WORLD = ROOT / "examples" / "worlds" / "forest_knowledge_reserve.json"


def test_reference_world_loads_and_maps_its_institutions() -> None:
    world = load_world_manifest(WORLD)

    assert world.render_style == "isometric_lowpoly"
    assert world.environment == "forest"
    assert world.labels_by_domain["History & Archives"] == "Archive Lodge"
    assert world.building_ids_by_resident["archivist"] == "archive-lodge"


def test_reference_world_matches_the_city_programs() -> None:
    world = load_world_manifest(WORLD)
    world.validate_city(build_city(ROOT / "examples" / "city"))


def test_world_manifest_rejects_unknown_residents(tmp_path: Path) -> None:
    payload = json.loads(WORLD.read_text(encoding="utf-8"))
    payload["buildings"][0]["residents"] = ["missing_resident"]
    path = tmp_path / "invalid-world.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    world = load_world_manifest(path)
    with pytest.raises(WorldError, match="unknown resident"):
        world.validate_city(build_city(ROOT / "examples" / "city"))


def test_world_manifest_requires_the_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "invalid-world.json"
    path.write_text(
        json.dumps({"schema": "wrong", "name": "World", "environment": "forest"}),
        encoding="utf-8",
    )

    with pytest.raises(WorldError, match=WORLD_SCHEMA):
        load_world_manifest(path)
