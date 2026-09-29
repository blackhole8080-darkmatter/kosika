"""Isometric cityscape rendering for the Kosika agent city.

Consumes :class:`kosika.city.City` and draws a 2.5D city: one building per
agent resident, districts as blocks along an avenue, roads that follow the
real workflow pipelines, and walls around every prohibition. Residents get
stable human names so any audience can read the city like a place.

Rendering is deterministic: the same city always renders the same picture,
because every coordinate derives from a stable digest of the agent name.
The renderer never invents relationships; roads come from the workflows
the programs actually declare.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .city import City, District, Resident

CITYSCAPE_SCHEMA = "kosika.agent-cityscape/v1"

# Deterministic human-name roster. Names are assigned by stable digest of
# the agent name, so "researcher" is the same person in every render.
_FIRST_NAMES = (
    "Aria", "Bo", "Cleo", "Dara", "Eli", "Faye", "Gus", "Hana",
    "Ivo", "Juno", "Kai", "Lena", "Milo", "Nia", "Otto", "Pia",
    "Quinn", "Rhea", "Silas", "Tess", "Uma", "Vik", "Wren", "Xio",
    "Yara", "Zed", "Ada", "Basil", "Cora", "Dev", "Esme", "Finn",
    "Gaia", "Hugo", "Iris", "Jade", "Kira", "Lyle", "Maya", "Noor",
)
_LAST_NAMES = (
    "Alvarez", "Bishop", "Chen", "Duarte", "Eriksen", "Fontaine",
    "Gallo", "Hassan", "Ibarra", "Jensen", "Kowalski", "Lindqvist",
    "Moreau", "Nakamura", "Okafor", "Petrov", "Quintero", "Rossi",
    "Silva", "Tanaka", "Ustinov", "Vance", "Whitfield", "Xu",
    "Yousafzai", "Zamora", "Ashford", "Brennan", "Castellan", "Dvorak",
)

# Building height: one floor per capability, ground floor always present.
MAX_FLOORS = 6
FLOOR_HEIGHT = 22
BUILDING_WIDTH = 120
BUILDING_DEPTH = 120
BLOCK_PITCH_X = 220
BLOCK_PITCH_Y = 180
AVENUE_WIDTH = 96
MARGIN = 120


def _digest(text: str) -> int:
    return int.from_bytes(
        hashlib.sha256(text.encode("utf-8")).digest()[:8], "big"
    )


def human_name(agent_name: str) -> str:
    """Return the stable human name for an agent."""

    value = _digest(f"resident:{agent_name}")
    first = _FIRST_NAMES[value % len(_FIRST_NAMES)]
    last = _LAST_NAMES[(value >> 8) % len(_LAST_NAMES)]
    return f"{first} {last}"


def building_floors(resident: Resident) -> int:
    """Floors of capability: one per allowed tool, plus the ground floor."""

    return min(len(resident.can) + 1, MAX_FLOORS)


@dataclass(frozen=True)
class Building:
    """One resident's building, placed on the city grid."""

    resident: Resident
    person: str
    floors: int
    x: int
    y: int
    district: str


@dataclass(frozen=True)
class Road:
    """A road between two buildings, drawn from a real workflow step."""

    from_person: str
    to_person: str
    district: str
    order: int


@dataclass(frozen=True)
class CityBlock:
    """A district: a labeled block holding its residents' buildings."""

    district: District
    x: int
    y: int
    width: int
    height: int


def _district_label(district: District) -> str:
    marks = []
    if district.approval_required:
        marks.append("approval")
    if district.max_rounds:
        marks.append(f"{district.max_rounds} rounds")
    suffix = f" ({', '.join(marks)})" if marks else ""
    return f"{district.name}{suffix}"


def plan_cityscape(city: City) -> dict[str, Any]:
    """Place every district, building, and road on the city grid.

    Districts run along a central avenue, widest first so the skyline
    reads naturally. Inside a block, buildings sit in reading order of
    the district's pipeline; agents the workflow never calls keep their
    buildings at the back of the block.
    """

    blocks: list[CityBlock] = []
    buildings: list[Building] = []
    roads: list[Road] = []

    ordered_districts = sorted(
        city.districts, key=lambda d: (-len(d.members), d.name)
    )
    lane_x = MARGIN
    max_lane_depth = 0
    for district in ordered_districts:
        members = [
            city.resident(name)
            for name in district.members
        ]
        members = [person for person in members if person is not None]
        if not members:
            continue
        columns = 2
        rows = (len(members) + columns - 1) // columns
        block_width = columns * BUILDING_WIDTH + (columns - 1) * 40 + 48
        block_height = rows * BLOCK_PITCH_Y + 64
        blocks.append(
            CityBlock(district, lane_x, MARGIN, block_width, block_height)
        )

        # Pipeline order first, then uncalled members by name.
        ordered_names = list(district.pipeline)
        ordered_names += sorted(
            name for name in district.members if name not in ordered_names
        )
        by_name = {person.name: person for person in members}
        for index, name in enumerate(ordered_names):
            person = by_name.get(name)
            if person is None:
                continue
            column = index % columns
            row = index // columns
            buildings.append(
                Building(
                    resident=person,
                    person=human_name(person.name),
                    floors=building_floors(person),
                    x=lane_x + 24 + column * (BUILDING_WIDTH + 40),
                    y=MARGIN + 56 + row * BLOCK_PITCH_Y,
                    district=district.name,
                )
            )

        for order in range(len(district.pipeline) - 1):
            left, right = district.pipeline[order], district.pipeline[order + 1]
            if left in by_name and right in by_name:
                roads.append(
                    Road(
                        from_person=human_name(left),
                        to_person=human_name(right),
                        district=district.name,
                        order=order,
                    )
                )
        lane_x += block_width + AVENUE_WIDTH
        max_lane_depth = max(max_lane_depth, block_height)

    return {
        "schema": CITYSCAPE_SCHEMA,
        "project": city.project,
        "blocks": [vars(block) | {"district": block.district.name} for block in blocks],
        "buildings": [
            {
                "agent": b.resident.name,
                "person": b.person,
                "district": b.district,
                "floors": b.floors,
                "can": list(b.resident.can),
                "cannot": list(b.resident.cannot),
                "model": b.resident.model,
                "x": b.x,
                "y": b.y,
            }
            for b in buildings
        ],
        "roads": [
            {
                "from": r.from_person,
                "to": r.to_person,
                "district": r.district,
                "order": r.order,
            }
            for r in roads
        ],
        "counts": {
            "districts": len(blocks),
            "buildings": len(buildings),
            "roads": len(roads),
        },
    }


def _building_faces(x: int, y: int, width: int, depth: int, height: int) -> tuple[str, str, str]:
    """Return SVG polygon elements for top, right, and left faces of an isometric box."""

    # Isometric projection: 2:1 diamond top.
    top_dx, top_dy = 30, 15
    ax, ay = x, y
    bx, by = x + width, y
    cx, cy = x + width + top_dx, y + top_dy
    dx_, dy_ = x + top_dx, y + top_dy
    
    # Top face (diamond)
    top = (
        f'<polygon points="{ax},{ay} {bx},{by} {cx},{cy} {dx_},{dy_}" '
        f'fill="{{building_top}}" stroke="{{floor_line}}" stroke-width="1"/>'
    )
    
    # Right face (visible when looking from front-left)
    right = (
        f'<polygon points="{bx},{by} {cx},{cy} {cx},{cy - height} {bx},{by - height}" '
        f'fill="{{building_right}}"/>'
    )
    
    # Left face (visible when looking from front-left)
    left = (
        f'<polygon points="{dx_},{dy_} {ax},{ay} {ax},{ay - height} {dx_},{dy_ - height}" '
        f'fill="{{building_left}}"/>'
    )
    
    return top, right, left


def _building_path(x: int, y: int, width: int, depth: int, height: int) -> str:
    """Isometric box: top face, left face, right face."""

    # Isometric projection: 2:1 diamond top.
    top_dx, top_dy = 30, 15
    ax, ay = x, y
    bx, by = x + width, y
    cx, cy = x + width + top_dx, y + top_dy
    dx_, dy_ = x + top_dx, y + top_dy
    return (
        f"M {ax},{ay} L {bx},{by} L {cx},{cy} L {dx_},{dy_} Z "
        f"M {bx},{by} L {bx},{by - height} L {cx},{cy - height} L {cx},{cy} "
        f"M {cx},{cy} L {cx},{cy - height} L {dx_},{dy_ - height} L {dx_},{dy_}"
    )


def render_cityscape_svg(city: City, *, theme: str = "lab") -> str:
    """Render the agent city as an isometric cityscape SVG."""

    plan = plan_cityscape(city)
    blocks = plan["blocks"]
    buildings = plan["buildings"]
    roads = plan["roads"]

    if not blocks:
        width, height = 480, 320
    else:
        width = max(b["x"] + b["width"] for b in blocks) + MARGIN
        height = (
            max(b["y"] + b["height"] for b in blocks)
            + MAX_FLOORS * FLOOR_HEIGHT
            + MARGIN
        )

    dark = theme in {"lab", "night"}
    bg = "#0B1017" if dark else "#F5F3EE"
    block_fill = "#111827" if dark else "#EAE6DC"
    block_stroke = "#1F2937" if dark else "#C9C2B4"
    road_color = "#3B82F6" if dark else "#2563EB"
    wall_color = "#EF4444" if dark else "#DC2626"
    building_top = "#1E293B" if dark else "#D6D0C4"
    building_left = "#0F172A" if dark else "#B8B2A4"
    building_right = "#172033" if dark else "#CAC4B6"
    label_color = "#E5E7EB" if dark else "#374151"
    sub_label = "#94A3B8" if dark else "#6B7280"
    floor_line = "#334155" if dark else "#A8A296"

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
    )
    parts.append(f"<rect width=\"{width}\" height=\"{height}\" fill=\"{bg}\"/>")

    # District blocks (ground plates).
    for block in blocks:
        parts.append(
            f'<rect x="{block["x"]}" y="{block["y"]}" '
            f'width="{block["width"]}" height="{block["height"]}" '
            f'fill="{block_fill}" stroke="{block_stroke}" stroke-width="2" rx="10"/>'
        )
        parts.append(
            f'<text x="{block["x"] + 16}" y="{block["y"] + 28}" '
            f'fill="{label_color}" font-family="Georgia, serif" '
            f'font-size="17" font-weight="bold">'
            f'{_escape(_district_label_from(block))}</text>'
        )

    # Roads: straight avenue segments between consecutive pipeline buildings.
    person_pos = {b["person"]: (b["x"], b["y"]) for b in buildings}
    for road in roads:
        start = person_pos.get(road["from"])
        end = person_pos.get(road["to"])
        if not start or not end:
            continue
        sx, sy = start[0] + BUILDING_WIDTH // 2, start[1] + BUILDING_DEPTH // 2
        ex, ey = end[0] + BUILDING_WIDTH // 2, end[1] + BUILDING_DEPTH // 2
        parts.append(
            f'<line x1="{sx}" y1="{sy}" x2="{ex}" y2="{ey}" '
            f'stroke="{road_color}" stroke-width="6" stroke-linecap="round" '
            f'stroke-dasharray="14 8" opacity="0.85"/>'
        )

    # Buildings, back to front for painter's order.
    for b in sorted(buildings, key=lambda item: -item["y"]):
        height = b["floors"] * FLOOR_HEIGHT
        x, y = b["x"], b["y"]
        top_dx, top_dy = 30, 15
        top, right, left = _building_faces(x, y, BUILDING_WIDTH, BUILDING_DEPTH, height)
        parts.append(top.format(building_top=building_top, floor_line=floor_line))
        parts.append(right.format(building_right=building_right))
        parts.append(left.format(building_left=building_left))
        # Floor lines on the left face.
        for floor in range(1, b["floors"]):
            fy = y + top_dy - floor * FLOOR_HEIGHT
            parts.append(
                f'<line x1="{x + top_dx}" y1="{fy}" '
                f'x2="{x + BUILDING_WIDTH + top_dx}" y2="{fy}" '
                f'stroke="{floor_line}" stroke-width="1" opacity="0.6"/>'
            )
        # Wall: red fence at the building's front-left corner.
        if b["cannot"]:
            wall_x = x - 8
            wall_y = y + BUILDING_DEPTH - 26
            parts.append(
                f'<line x1="{wall_x}" y1="{wall_y}" x2="{wall_x}" '
                f'y2="{wall_y - 34}" stroke="{wall_color}" stroke-width="5"/>'
            )
            parts.append(
                f'<line x1="{wall_x}" y1="{wall_y}" x2="{wall_x + 26}" '
                f'y2="{wall_y - 13}" stroke="{wall_color}" stroke-width="5"/>'
            )
        # Nameplate: human name over the agent's declared name.
        parts.append(
            f'<text x="{x + BUILDING_WIDTH // 2}" y="{y - height - 34}" '
            f'text-anchor="middle" fill="{label_color}" '
            f'font-family="Georgia, serif" font-size="16" font-weight="bold">'
            f'{_escape(b["person"])}</text>'
        )
        parts.append(
            f'<text x="{x + BUILDING_WIDTH // 2}" y="{y - height - 16}" '
            f'text-anchor="middle" fill="{sub_label}" '
            f'font-family="Georgia, serif" font-size="11">'
            f'{_escape(b["agent"])} · {b["floors"]}F</text>'
        )

    parts.append(
        f'<text x="{MARGIN}" y="{height - 40}" fill="{sub_label}" '
        f'font-family="Georgia, serif" font-size="12">'
        f'{_escape(city.name)} — {len(buildings)} residents, '
        f'{len(blocks)} districts, {len(roads)} roads</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _district_label_from(block: dict[str, Any]) -> str:
    district = block["district"]
    if isinstance(district, str):
        return district
    return getattr(district, "name", str(district))


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
