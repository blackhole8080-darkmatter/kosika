"""Validated presentation manifests for live Kosika AI worlds.

World manifests describe placement and presentation only. Kosika source remains
authoritative for agents, capabilities, workflows, and execution events.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .metropolis import environment_style
from .model_steps import strict_json

if TYPE_CHECKING:
    from .city import City

WORLD_SCHEMA = "kosika.ai-world/v1"
MAX_WORLD_BYTES = 128_000
MAX_BUILDINGS = 32
WORLD_RENDER_STYLES = ("city", "blockscape", "voxel", "isometric_lowpoly")


class WorldError(ValueError):
    """Raised when a world manifest is invalid or mismatches its city."""


@dataclass(frozen=True)
class WorldBuilding:
    """A themed institution mapped to a renderer knowledge domain."""

    id: str
    domain: str
    label: str
    kind: str
    residents: tuple[str, ...]


@dataclass(frozen=True)
class WorldManifest:
    """Presentation metadata for one event-backed AI world."""

    name: str
    environment: str
    camera: str
    buildings: tuple[WorldBuilding, ...]
    description: str | None = None
    render_style: str = "city"

    @property
    def labels_by_domain(self) -> dict[str, str]:
        return {building.domain: building.label for building in self.buildings}

    @property
    def kinds_by_domain(self) -> dict[str, str]:
        return {building.domain: building.kind for building in self.buildings}

    @property
    def building_ids_by_resident(self) -> dict[str, str]:
        return {
            resident: building.id
            for building in self.buildings
            for resident in building.residents
        }

    def validate_city(self, city: City) -> None:
        known = {resident.name for resident in city.residents}
        missing = sorted(set(self.building_ids_by_resident) - known)
        if missing:
            raise WorldError(
                "World manifest names unknown resident(s): " + ", ".join(missing)
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": WORLD_SCHEMA,
            "name": self.name,
            "environment": self.environment,
            "camera": self.camera,
            "render_style": self.render_style,
            "buildings": [
                {
                    "id": building.id,
                    "domain": building.domain,
                    "label": building.label,
                    "kind": building.kind,
                    "residents": list(building.residents),
                }
                for building in self.buildings
            ],
        }
        if self.description is not None:
            result["description"] = self.description
        return result


def _text(value: Any, field: str, *, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldError(f"World manifest field {field!r} must be nonempty text")
    value = value.strip()
    if len(value) > limit:
        raise WorldError(f"World manifest field {field!r} exceeds {limit} characters")
    return value


def load_world_manifest(path: Path) -> WorldManifest:
    """Load and validate a bounded world manifest from JSON."""

    try:
        if path.stat().st_size > MAX_WORLD_BYTES:
            raise WorldError(f"World manifest exceeds {MAX_WORLD_BYTES} bytes")
        payload = strict_json(path.read_text(encoding="utf-8"))
    except WorldError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise WorldError(f"Invalid world manifest {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise WorldError("World manifest root must be an object")
    if payload.get("schema") != WORLD_SCHEMA:
        raise WorldError(f"World manifest schema must be {WORLD_SCHEMA!r}")
    name = _text(payload.get("name"), "name", limit=120)
    environment = _text(payload.get("environment"), "environment", limit=40)
    try:
        environment_style(environment)
    except ValueError as exc:
        raise WorldError(str(exc)) from exc
    camera = _text(payload.get("camera", "isometric"), "camera", limit=40)
    if camera != "isometric":
        raise WorldError(
            "World manifest currently supports only the 'isometric' camera"
        )
    render_style = _text(payload.get("render_style", "city"), "render_style", limit=40)
    if render_style not in WORLD_RENDER_STYLES:
        choices = ", ".join(WORLD_RENDER_STYLES)
        raise WorldError(f"World manifest render_style must be one of {choices}")
    description = payload.get("description")
    if description is not None:
        description = _text(description, "description", limit=320)

    raw_buildings = payload.get("buildings")
    if (
        not isinstance(raw_buildings, list)
        or not raw_buildings
        or len(raw_buildings) > MAX_BUILDINGS
    ):
        raise WorldError(
            f"World manifest buildings must contain 1 to {MAX_BUILDINGS} objects"
        )

    buildings: list[WorldBuilding] = []
    ids: set[str] = set()
    domains: set[str] = set()
    residents: set[str] = set()
    for index, raw in enumerate(raw_buildings):
        if not isinstance(raw, dict):
            raise WorldError(f"World building {index} must be an object")
        building_id = _text(raw.get("id"), f"buildings[{index}].id", limit=80)
        domain = _text(raw.get("domain"), f"buildings[{index}].domain", limit=120)
        label = _text(raw.get("label"), f"buildings[{index}].label", limit=120)
        kind = _text(raw.get("kind"), f"buildings[{index}].kind", limit=40)
        raw_residents = raw.get("residents", [])
        if not isinstance(raw_residents, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_residents
        ):
            raise WorldError(
                f"World building {building_id!r} residents must be text names"
            )
        building_residents = tuple(item.strip() for item in raw_residents)
        if len(set(building_residents)) != len(building_residents):
            raise WorldError(f"World building {building_id!r} repeats a resident")
        if building_id in ids:
            raise WorldError(f"Duplicate world building id {building_id!r}")
        if domain in domains:
            raise WorldError(f"Duplicate world building domain {domain!r}")
        overlap = sorted(residents.intersection(building_residents))
        if overlap:
            raise WorldError(
                "Resident(s) assigned to multiple world buildings: "
                + ", ".join(overlap)
            )
        ids.add(building_id)
        domains.add(domain)
        residents.update(building_residents)
        buildings.append(
            WorldBuilding(building_id, domain, label, kind, building_residents)
        )

    return WorldManifest(
        name,
        environment,
        camera,
        tuple(buildings),
        description,
        render_style,
    )


__all__ = [
    "WORLD_RENDER_STYLES",
    "WorldBuilding",
    "WorldError",
    "WorldManifest",
    "load_world_manifest",
]
