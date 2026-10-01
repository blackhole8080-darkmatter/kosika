"""An isometric city rendered from a :class:`kosika.city.City`.

The city has three tiers, and each one answers a different question:

* a **grand sector** is a megastructure -- a library, an observatory, a
  gallery, a museum, a mall -- that gives the skyline its landmarks;
* a **department** is a real swarm program, drawn as a themed hall inside
  the megastructure that owns it;
* a **resident** is a *person*, standing beside the sub-sector they run.

Nothing here is invented. Grand sectors are groupings of the real district
programs. Departments are those programs, named and shaped by their own
workflow. Sub-sectors and people are the agents: the sign names the person,
the plate and the data attributes keep the declaration authoritative.

The projection is the classic 2:1 dimetric one. Every coordinate is integral
or half-integral so the output is byte-stable for a given city.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .city import City, Resident

TILE_WIDTH = 64
TILE_HEIGHT = 32
HEIGHT_UNIT = 9
BASE_HEIGHT = 26
FLOORS_PER_STEP = 4
MARGIN = 40
TERRAIN_TILE_SIZE = 4.0
PERSON_HEIGHT = 15.0

# Buildings are addressed by a person on the sign, but the declaration they
# came from stays authoritative: the real agent name is always on the plate
# and in the data attributes. People go by one name, the way a city directory
# lists them. The roster is deterministic and keyed by the agent name, so a
# given agent is always the same person.
ROSTER: tuple[str, ...] = (
    "Jim",
    "Adam",
    "Alan",
    "Grace",
    "Barbara",
    "Donald",
    "Katherine",
    "Radia",
    "Leslie",
    "Hedy",
    "Jean",
    "Tim",
    "Vint",
    "Frances",
    "Tony",
    "Karen",
    "Dennis",
    "Margaret",
    "John",
    "Edsger",
    "Sophie",
    "Bjarne",
    "Shafi",
    "Andrew",
    "Michael",
    "Olga",
    "Elizabeth",
    "Mary",
    "Betty",
    "Sara",
    "Charles",
    "Peter",
    "Anny",
    "Niklaus",
    "Corrado",
    "Robin",
    "Susan",
    "Erna",
    "Carol",
    "Anita",
    "James",
    "Lynn",
    "Guido",
    "Adele",
    "Roberto",
    "David",
    "Sandra",
    "Nikola",
    "Priya",
    "Wei",
    "Amara",
    "Diego",
    "Ingrid",
)


class MetroError(ValueError):
    """Raised when an isometric city cannot be drawn."""


@dataclass(frozen=True)
class Plaque:
    """The human name shown on a building, and where it came from."""

    person: str
    agent: str


def assign_plaques(agents: Iterable[str]) -> dict[str, str]:
    """Give every agent a stable human name.

    The mapping is a hash of the agent name, so it survives the city growing
    or shrinking: adding a district never renames an existing agent.
    Collisions walk forward through the roster rather than depending on
    insertion order.
    """
    taken: set[str] = set()
    assigned: dict[str, str] = {}
    for agent in sorted(set(agents)):
        seed = int(hashlib.sha1(agent.encode("utf-8")).hexdigest(), 16)
        for step in range(len(ROSTER)):
            candidate = ROSTER[(seed + step) % len(ROSTER)]
            if candidate not in taken:
                taken.add(candidate)
                assigned[agent] = candidate
                break
        else:
            # Every name is spoken for. Stay deterministic rather than
            # silently dropping the suffix that keeps them distinct.
            suffix = 2
            while True:
                candidate = f"{ROSTER[seed % len(ROSTER)]} {suffix}"
                if candidate not in taken:
                    taken.add(candidate)
                    assigned[agent] = candidate
                    break
                suffix += 1
    return assigned


def _project(gx: float, gy: float, z: float) -> tuple[float, float]:
    """The classic 2:1 dimetric projection onto the screen."""
    return (
        (gx - gy) * TILE_WIDTH / 2,
        (gx + gy) * TILE_HEIGHT / 2 - z,
    )


def _fmt(value: float) -> str:
    return f"{value:g}"


def _polygon(points: list[tuple[float, float]], **attrs: Any) -> str:
    body = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points)
    rendered = "".join(
        f' {key.replace("_", "-")}="{value}"' for key, value in attrs.items()
    )
    return f'<polygon points="{body}"{rendered} />'


def _box(
    place: Any,
    gx: float,
    gy: float,
    w: float,
    d: float,
    z0: float,
    z1: float,
    top: str,
    left: str,
    right: str,
    stroke: str,
) -> str:
    """An extruded box: two walls, a top, and a cast shadow."""
    x0, y0 = place(gx, gy, z0)
    x1, y1 = place(gx + w, gy, z0)
    x2, y2 = place(gx + w, gy + d, z0)
    x3, y3 = place(gx, gy + d, z0)
    tx0, ty0 = place(gx, gy, z1)
    tx1, ty1 = place(gx + w, gy, z1)
    tx2, ty2 = place(gx + w, gy + d, z1)
    tx3, ty3 = place(gx, gy + d, z1)
    return (
        _polygon(
            [(x + 8, y + 6) for x, y in ((x0, y0), (x1, y1), (x2, y2), (x3, y3))],
            fill="#000000",
            opacity="0.45",
        )
        + _polygon(
            [(x3, y3), (x2, y2), (tx2, ty2), (tx3, ty3)],
            fill=left,
            stroke=stroke,
            stroke_width=0.7,
        )
        + _polygon(
            [(x1, y1), (x2, y2), (tx2, ty2), (tx1, ty1)],
            fill=right,
            stroke=stroke,
            stroke_width=0.7,
        )
        + _polygon(
            [(tx0, ty0), (tx1, ty1), (tx2, ty2), (tx3, ty3)],
            fill=top,
            stroke=stroke,
            stroke_width=1,
        )
    )


# --------------------------------------------------------------------------
# The knowledge-domain taxonomy.
#
# A grand sector is a *field of expertise*, not an org chart. Each domain
# claims a set of tools, and a person is placed by the tools they were
# actually granted -- so the Science observatory holds everyone who touches
# measurements and simulation, whichever department hired them. The tool sets
# are disjoint: no tool belongs to two domains, so the partition is real
# rather than a guess.
# --------------------------------------------------------------------------

DOMAINS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "Software Engineering",
        "foundry",
        frozenset(
            {
                "read_diff",
                "run_tests",
                "apply_patch",
                "query_dependencies",
                "publish_docs",
            }
        ),
    ),
    (
        "Knowledge & Scholarship",
        "library",
        frozenset(
            {
                "fetch_pdf",
                "search_papers",
                "check_source",
                "resolve_doi",
                "read_docs",
                "search_docs",
            }
        ),
    ),
    (
        "Science",
        "observatory",
        frozenset(
            {
                "propose_hypothesis",
                "read_measurements",
                "run_simulation",
                "plot_results",
            }
        ),
    ),
    (
        "History & Archives",
        "museum",
        frozenset(
            {
                "read_primary",
                "search_archive",
                "build_timeline",
                "cross_reference",
            }
        ),
    ),
    (
        "Education",
        "university",
        frozenset(
            {
                "compose_lesson",
                "fetch_lesson",
                "grade_submission",
                "collect_feedback",
            }
        ),
    ),
    (
        "AI & Machine Learning",
        "lab",
        frozenset(
            {
                "run_eval",
                "propose_prompt",
                "sample_dataset",
                "scan_for_harm",
            }
        ),
    ),
    ("Arts & Editorial", "gallery", frozenset()),
    ("Civic Operations", "civic", frozenset({"check_disk", "inspect_structured"})),
)

# A person with no granted tools has no capability signature, so the
# department they were declared in is the honest fallback.
DEPARTMENT_DOMAIN: dict[str, str] = {
    "EditorialBoard": "Arts & Editorial",
}

COMMONS_NAME = "Commons"
COMMONS_KIND = "square"

DOMAIN_COLOURS: dict[str, str] = {
    "Software Engineering": "#A3E635",
    "Knowledge & Scholarship": "#A78BFA",
    "Science": "#22D3EE",
    "History & Archives": "#FBBF24",
    "Education": "#FB923C",
    "AI & Machine Learning": "#F472B6",
    "Arts & Editorial": "#F9A8D4",
    "Civic Operations": "#38BDF8",
    COMMONS_NAME: "#94A3B8",
}

_PALETTE = (
    "#22D3EE",
    "#F472B6",
    "#A3E635",
    "#A78BFA",
    "#FBBF24",
    "#34D399",
    "#FB7185",
    "#38BDF8",
    "#FB923C",
    "#2DD4BF",
)


@dataclass(frozen=True)
class EnvironmentStyle:
    """Deterministic visual treatment for a live AI world."""

    name: str
    sky_top: str
    sky_mid: str
    sky_bottom: str
    haze: str
    ground: str
    ground_stroke: str
    road: str
    road_stroke: str
    terrain: str
    terrain_accent: str


ENVIRONMENTS: dict[str, EnvironmentStyle] = {
    "metropolitan": EnvironmentStyle(
        "metropolitan",
        "#0B1224",
        "#070B16",
        "#03050A",
        "#2A3A6B",
        "#080C16",
        "#1F2C46",
        "#151A24",
        "#5A6675",
        "#0B1224",
        "#38BDF8",
    ),
    "urban": EnvironmentStyle(
        "urban",
        "#17233C",
        "#0D1424",
        "#070A12",
        "#354D7A",
        "#101725",
        "#2D3C57",
        "#1B2230",
        "#65738A",
        "#101725",
        "#A78BFA",
    ),
    "suburban": EnvironmentStyle(
        "suburban",
        "#102A38",
        "#102129",
        "#081316",
        "#2F665B",
        "#10261F",
        "#315344",
        "#182B29",
        "#55766C",
        "#163229",
        "#A3E635",
    ),
    "rural": EnvironmentStyle(
        "rural",
        "#203B4A",
        "#172A2A",
        "#0C1715",
        "#4D8066",
        "#172B20",
        "#46654B",
        "#1D3028",
        "#6A866D",
        "#1B3325",
        "#FBBF24",
    ),
    "desert": EnvironmentStyle(
        "desert",
        "#432B3A",
        "#281B27",
        "#130E16",
        "#9A5D58",
        "#34201C",
        "#795044",
        "#2B211F",
        "#967266",
        "#432A20",
        "#F59E0B",
    ),
    "forest": EnvironmentStyle(
        "forest",
        "#0B2630",
        "#071A1D",
        "#030B0D",
        "#1C5A58",
        "#0B211D",
        "#275348",
        "#122A29",
        "#4B7467",
        "#103229",
        "#34D399",
    ),
    "water": EnvironmentStyle(
        "water",
        "#092D46",
        "#071C31",
        "#030A16",
        "#1D5F82",
        "#08283D",
        "#286080",
        "#0E2B40",
        "#4D87A5",
        "#0B3550",
        "#22D3EE",
    ),
}

ENVIRONMENT_NAMES = tuple(ENVIRONMENTS)


def environment_style(name: str) -> EnvironmentStyle:
    """Return a named environment or fail before producing a misleading view."""

    try:
        return ENVIRONMENTS[name]
    except KeyError as exc:
        choices = ", ".join(ENVIRONMENT_NAMES)
        raise MetroError(
            f"Unknown environment {name!r}; choose from {choices}"
        ) from exc


SUBSECTOR_W = 1.45
SUBSECTOR_D = 1.25
SUBSECTOR_GAP = 0.16
SUBSECTOR_COLUMNS = 4
PLATFORM_MARGIN = 1.3
BAND_GAP = 0.9


@dataclass(frozen=True)
class Grand:
    """A knowledge domain, drawn as a megastructure around its people."""

    name: str
    kind: str
    colour: str
    people: tuple[str, ...]
    gx: float
    gy: float
    width: float
    depth: float

    @property
    def depth_key(self) -> float:
        return self.gx + self.gy


def _domain_for(person: Resident, department: str) -> str:
    """Place a person by the tools they were actually granted.

    Every tool belongs to at most one domain, so the count of a person's
    tools per domain is a real capability reading rather than a guess. Ties
    break on the taxonomy order so the answer never depends on dict order.
    """
    tally: dict[str, int] = {}
    for tool in person.can:
        for name, _, tools in DOMAINS:
            if tool in tools:
                tally[name] = tally.get(name, 0) + 1
    if tally:
        best = max(tally.values())
        for name, _, _ in DOMAINS:
            if tally.get(name) == best:
                return name
    return DEPARTMENT_DOMAIN.get(department, COMMONS_NAME)


def _group_domains(city: City) -> list[tuple[str, str, tuple[str, ...]]]:
    """Bucket every resident into the knowledge domain they belong to."""
    domain_of: dict[str, str] = {}
    department_of: dict[str, str] = {}
    for district in city.districts:
        for member in district.members:
            person = city.resident(member)
            if person is None:
                continue
            department_of.setdefault(person.name, district.name)
            domain_of.setdefault(person.name, _domain_for(person, district.name))

    buckets: dict[str, list[str]] = {}
    for resident_name in sorted(domain_of):
        buckets.setdefault(domain_of[resident_name], []).append(resident_name)
    if not buckets:
        raise MetroError("No agents to draw; the city is empty")

    ordered = [name for name, _, _ in DOMAINS if name in buckets]
    ordered += [name for name in sorted(buckets) if name not in ordered]
    kinds = {name: kind for name, kind, _ in DOMAINS}
    kinds[COMMONS_NAME] = COMMONS_KIND
    return [(name, kinds.get(name, "mall"), tuple(buckets[name])) for name in ordered]


def _department_map(city: City) -> dict[str, str]:
    """The department each resident was declared in, for data attributes."""
    mapping: dict[str, str] = {}
    for district in city.districts:
        for member in district.members:
            if city.resident(member) is not None:
                mapping.setdefault(member, district.name)
    return mapping


def _band_depth(district_members: int) -> float:
    rows = max(1, math.ceil(district_members / SUBSECTOR_COLUMNS))
    return 1.5 + rows * (SUBSECTOR_D + SUBSECTOR_GAP)


def _layout_grand_sectors(
    groups: list[tuple[str, str, tuple[str, ...]]],
) -> tuple[list[Grand], float, float]:
    """Ring the megastructures around a civic centre.

    Grand sectors sit on a regular polygon, which keeps the plan stable no
    matter how many people a domain ends up holding.
    """
    measured: list[tuple[str, str, str, float, float]] = []
    for index, (name, kind, people) in enumerate(groups):
        if not people:
            continue
        width = SUBSECTOR_COLUMNS * (SUBSECTOR_W + SUBSECTOR_GAP) + PLATFORM_MARGIN * 2
        depth = _band_depth(len(people)) + PLATFORM_MARGIN * 2
        colour = DOMAIN_COLOURS.get(name, _PALETTE[index % len(_PALETTE)])
        measured.append((name, kind, colour, width, depth))

    if not measured:
        raise MetroError("No agents to draw; the city is empty")

    span = max(
        max(w for _, _, _, w, _ in measured), max(d for _, _, _, _, d in measured)
    )
    # Adjacent megastructures sit 2*r*sin(pi/n) apart on the ring, so this
    # keeps neighbouring platforms from colliding while staying compact.
    radius = max(span * 1.35, 10.0)
    centre = radius

    grands: list[Grand] = []
    count = len(measured)
    for index, (name, kind, colour, width, depth) in enumerate(measured):
        angle = math.radians(90 - index * (360.0 / count))
        gx = centre + radius * math.cos(angle) - width / 2
        gy = centre + radius * math.sin(angle) - depth / 2
        grands.append(
            Grand(
                name=name,
                kind=kind,
                colour=colour,
                people=groups[index][2],
                gx=gx,
                gy=gy,
                width=width,
                depth=depth,
            )
        )
    extent = 2 * (radius + span / 2) + 2
    return grands, extent, extent


def _corridors(grands: list[Grand]) -> tuple[list[float], list[float]]:
    """Road centrelines running between neighbouring megastructures."""
    lanes: dict[int, list[Grand]] = {}
    for grand in grands:
        lanes.setdefault(round(grand.gy), []).append(grand)
    verticals: set[float] = set()
    for row in lanes.values():
        row.sort(key=lambda g: g.gx)
        for west, east in zip(row, row[1:]):
            verticals.add((west.gx + west.width + east.gx) / 2)

    stacks: dict[int, list[Grand]] = {}
    for grand in grands:
        stacks.setdefault(round(grand.gx), []).append(grand)
    horizontals: set[float] = set()
    for column in stacks.values():
        column.sort(key=lambda g: g.gy)
        for north, south in zip(column, column[1:]):
            horizontals.add((north.gy + north.depth + south.gy) / 2)
    return sorted(verticals), sorted(horizontals)


def _nearest(candidates: list[float], value: float, fallback: float) -> float:
    if not candidates:
        return fallback
    return min(candidates, key=lambda candidate: abs(candidate - value))


def _roads(
    place: Any,
    grands: list[Grand],
    extent: float,
    road_stroke: str = "#5A6675",
) -> list[str]:
    """A ring road, the corridors between megastructures, and the spurs."""
    verticals, horizontals = _corridors(grands)
    half = 0.7
    surfaces: list[str] = []

    def strip(points: list[tuple[float, float]], kind: str) -> str:
        return _polygon(
            points,
            fill="url(#asphalt)",
            stroke=road_stroke,
            stroke_width=1,
            **{"data-road": kind},
        )

    for centre in verticals:
        surfaces.append(
            strip(
                [
                    place(centre - half, 0, 0),
                    place(centre + half, 0, 0),
                    place(centre + half, extent, 0),
                    place(centre - half, extent, 0),
                ],
                "corridor",
            )
        )
    for centre in horizontals:
        surfaces.append(
            strip(
                [
                    place(0, centre - half, 0),
                    place(extent, centre - half, 0),
                    place(extent, centre + half, 0),
                    place(0, centre + half, 0),
                ],
                "corridor",
            )
        )

    for grand in grands:
        mid_x = grand.gx + grand.width / 2
        mid_y = grand.gy + grand.depth / 2
        target_x = _nearest(verticals, mid_x, extent / 2)
        target_y = _nearest(horizontals, mid_y, extent / 2)
        if abs(target_x - mid_x) > half:
            surfaces.append(
                strip(
                    [
                        place(min(mid_x, target_x), mid_y - 0.4, 0),
                        place(max(mid_x, target_x), mid_y - 0.4, 0),
                        place(max(mid_x, target_x), mid_y + 0.4, 0),
                        place(min(mid_x, target_x), mid_y + 0.4, 0),
                    ],
                    "spur",
                )
            )
        if abs(target_y - mid_y) > half:
            surfaces.append(
                strip(
                    [
                        place(mid_x - 0.4, min(mid_y, target_y), 0),
                        place(mid_x - 0.4, max(mid_y, target_y), 0),
                        place(mid_x + 0.4, max(mid_y, target_y), 0),
                        place(mid_x + 0.4, min(mid_y, target_y), 0),
                    ],
                    "spur",
                )
            )

    inset = 1.6
    ring = (inset, inset, extent - inset, extent - inset)
    corners = [
        (ring[0], ring[1]),
        (ring[2], ring[1]),
        (ring[2], ring[3]),
        (ring[0], ring[3]),
    ]
    for (ax, ay), (bx, by) in zip(corners, [*corners[1:], corners[0]]):
        surfaces.append(
            strip(
                [
                    place(ax - half, ay - half, 0),
                    place(bx - half, by - half, 0),
                    place(bx + half, by + half, 0),
                    place(ax + half, ay + half, 0),
                ],
                "ring",
            )
        )
    return surfaces


# --------------------------------------------------------------------------
# Landmarks. Each megastructure gets a silhouette that reads at a glance.
# --------------------------------------------------------------------------


def _landmark(place: Any, grand: Grand, base: float) -> str:
    """Draw the megastructure that gives a grand sector its name."""
    kind = grand.kind
    colour = grand.colour
    gx, gy = grand.gx + 0.8, grand.gy + 0.8
    w, d = grand.width - 1.6, grand.depth - 1.6
    if kind == "library":
        return _landmark_library(place, gx, gy, w, d, base, colour)
    if kind == "observatory":
        return _landmark_observatory(place, gx, gy, w, d, base, colour)
    if kind == "gallery":
        return _landmark_gallery(place, gx, gy, w, d, base, colour)
    if kind == "museum":
        return _landmark_museum(place, gx, gy, w, d, base, colour)
    if kind == "square":
        return _landmark_square(place, gx, gy, w, d, base, colour)
    if kind == "university":
        return _landmark_university(place, gx, gy, w, d, base, colour)
    if kind == "lab":
        return _landmark_lab(place, gx, gy, w, d, base, colour)
    if kind == "civic":
        return _landmark_civic(place, gx, gy, w, d, base, colour)
    if kind == "foundry":
        return _landmark_foundry(place, gx, gy, w, d, base, colour)
    return _landmark_mall(place, gx, gy, w, d, base, colour)


def _landmark_foundry(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A working foundry: a sawtooth workshop roof over a glowing furnace line."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w,
            d,
            base,
            base + 20,
            "#0A1512",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    ]
    for index in range(4):
        t0 = gx + w * index / 4
        t1 = gx + w * (index + 1) / 4
        a0, a1 = place(t0, gy, base + 20)
        b0, b1 = place(t1, gy, base + 20)
        peak_a = place(t0, gy + d / 2, base + 32)
        peak_b = place(t1, gy + d / 2, base + 32)
        parts.append(
            f'<polygon points="{_fmt(a0)},{_fmt(a1)} {_fmt(b0)},{_fmt(b1)} '
            f'{_fmt(peak_b[0])},{_fmt(peak_b[1])} {_fmt(peak_a[0])},{_fmt(peak_a[1])}" '
            f'fill="{colour}" fill-opacity="0.20" stroke="{colour}" stroke-width="1.1"/>'
        )
        glow_x, glow_y = place(t0, gy + d, base + 12)
        parts.append(
            f'<circle cx="{_fmt(glow_x)}" cy="{_fmt(glow_y)}" r="2.2" fill="#FFD9A0" '
            f'filter="url(#neon)" opacity="{0.5 + (index % 2) * 0.4:.1f}"/>'
        )
    stack = place(gx + w * 0.82, gy + d * 0.3, base)
    top = place(gx + w * 0.82, gy + d * 0.3, base + 52)
    parts.append(
        f'<line x1="{_fmt(stack[0])}" y1="{_fmt(stack[1])}" x2="{_fmt(top[0])}" '
        f'y2="{_fmt(top[1])}" stroke="{colour}" stroke-width="4.5" '
        f'stroke-linecap="round" filter="url(#neon)"/>'
    )
    return "".join(parts)


def _landmark_university(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """Stepped lecture terraces rising to a bell tower."""
    parts = []
    for step in range(3):
        inset = w * 0.08 * step
        parts.append(
            _box(
                place,
                gx + inset,
                gy + d * 0.05 * step,
                w - inset * 2,
                d - d * 0.1 * step,
                base + step * 13,
                base + step * 13 + 15,
                "#18120A",
                "url(#faceleft)",
                "url(#faceright)",
                colour,
            )
        )
    for index in range(5):
        t = (index + 0.5) / 5
        bx, by = place(gx + w * t, gy + d, base + 6)
        parts.append(
            f'<rect x="{_fmt(bx - 2.6)}" y="{_fmt(by - 2.6)}" width="5.2" height="5.2" '
            f'fill="#FFE8C0" filter="url(#neon)" opacity="0.8"/>'
        )
    foot = place(gx + w * 0.5, gy + d * 0.4, base)
    head = place(gx + w * 0.5, gy + d * 0.4, base + 58)
    parts.append(
        f'<line x1="{_fmt(foot[0])}" y1="{_fmt(foot[1])}" x2="{_fmt(head[0])}" '
        f'y2="{_fmt(head[1])}" stroke="{colour}" stroke-width="3.2" filter="url(#neon)"/>'
    )
    for angle in (-26, 0, 26):
        rad = math.radians(angle)
        arm = place(
            gx + w * 0.5 + math.cos(rad) * 1.2,
            gy + d * 0.4 + math.sin(rad) * 0.6,
            base + 50,
        )
        parts.append(
            f'<line x1="{_fmt(head[0])}" y1="{_fmt(head[1])}" x2="{_fmt(arm[0])}" '
            f'y2="{_fmt(arm[1])}" stroke="{colour}" stroke-width="1.6" opacity="0.9"/>'
        )
    parts.append(
        f'<circle class="beacon" cx="{_fmt(head[0])}" cy="{_fmt(head[1])}" r="2.4" '
        f'fill="{colour}" filter="url(#neon)"/>'
    )
    return "".join(parts)


def _landmark_lab(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A glass research tower with cooling stacks and a dish."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w * 0.6,
            d,
            base,
            base + 46,
            "#1A0C18",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        ),
        _box(
            place,
            gx + w * 0.68,
            gy + d * 0.25,
            w * 0.28,
            d * 0.5,
            base,
            base + 30,
            "#1A0C18",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        ),
    ]
    for level in range(5):
        t = (level + 0.5) / 5
        row = place(gx, gy + d * t, base + 8 + level * 8)
        row2 = place(gx + w * 0.6, gy + d * t, base + 8 + level * 8)
        parts.append(
            f'<line x1="{_fmt(row[0])}" y1="{_fmt(row[1])}" x2="{_fmt(row2[0])}" '
            f'y2="{_fmt(row2[1])}" stroke="{colour}" stroke-width="1.5" opacity="0.7"/>'
        )
    for index in range(2):
        foot = place(gx + w * (0.72 + index * 0.16), gy + d * 0.3, base + 30)
        top = place(gx + w * (0.72 + index * 0.16), gy + d * 0.3, base + 46)
        parts.append(
            f'<line x1="{_fmt(foot[0])}" y1="{_fmt(foot[1])}" x2="{_fmt(top[0])}" '
            f'y2="{_fmt(top[1])}" stroke="#F472B6" stroke-width="2.6" opacity="0.8" '
            f'filter="url(#neon)"/>'
        )
    dish = place(gx + w * 0.3, gy + d * 0.5, base + 62)
    parts.append(
        f'<ellipse cx="{_fmt(dish[0])}" cy="{_fmt(dish[1])}" rx="7" ry="3.4" '
        f'fill="none" stroke="{colour}" stroke-width="1.8" filter="url(#neon)"/>'
    )
    parts.append(
        f'<line x1="{_fmt(dish[0])}" y1="{_fmt(dish[1])}" x2="{_fmt(dish[0] + 4)}" '
        f'y2="{_fmt(dish[1] - 7)}" stroke="{colour}" stroke-width="1.4"/>'
    )
    return "".join(parts)


def _landmark_civic(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A town hall: a columned portico under a shallow dome."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w * 0.7,
            d,
            base,
            base + 18,
            "#0C1620",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    ]
    for index in range(6):
        t = (index + 0.5) / 6
        foot = place(gx + w * 0.7 * t, gy + d, base)
        head = place(gx + w * 0.7 * t, gy + d, base + 16)
        parts.append(
            f'<line x1="{_fmt(foot[0])}" y1="{_fmt(foot[1])}" x2="{_fmt(head[0])}" '
            f'y2="{_fmt(head[1])}" stroke="{colour}" stroke-width="2" opacity="0.9"/>'
        )
    cx, cy = place(gx + w * 0.35, gy + d * 0.5, base + 18)
    parts.append(
        f'<path d="M {_fmt(cx - 18)},{_fmt(cy)} A 18,12 0 0 1 {_fmt(cx + 18)},{_fmt(cy)} Z" '
        f'fill="{colour}" fill-opacity="0.26" stroke="{colour}" stroke-width="1.4"/>'
    )
    top = place(gx + w * 0.35, gy + d * 0.5, base + 44)
    parts.append(
        f'<line x1="{_fmt(cx)}" y1="{_fmt(cy)}" x2="{_fmt(top[0])}" y2="{_fmt(top[1])}" '
        f'stroke="{colour}" stroke-width="2" filter="url(#neon)"/>'
    )
    parts.append(
        f'<circle class="beacon" cx="{_fmt(top[0])}" cy="{_fmt(top[1])}" r="2.6" '
        f'fill="{colour}" filter="url(#neon)"/>'
    )
    return "".join(parts)


def _landmark_library(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A tiered ziggurat of stacked reading terraces, with corner spires."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w,
            d,
            base,
            base + 26,
            "#0B1220",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    ]
    for step in range(3):
        inset = w * 0.1 * step
        parts.append(
            _box(
                place,
                gx + inset,
                gy + inset * (d / w if w else 0),
                w - inset * 2,
                d - inset * 2,
                base + 26 + step * 18,
                base + 40 + step * 18,
                "#101a2e",
                "url(#faceleft)",
                "url(#faceright)",
                colour,
            )
        )
    cx, cy = place(gx + w / 2, gy + d / 2, base + 76)
    parts.append(
        f'<circle class="beacon" cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="3" fill="{colour}" '
        f'filter="url(#neon)"/>'
    )
    for sx, sy in ((0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)):
        px, py = place(gx + w * sx, gy + d * sy, base)
        qx, qy = place(gx + w * sx, gy + d * sy, base + 34)
        parts.append(
            f'<line x1="{_fmt(px)}" y1="{_fmt(py)}" x2="{_fmt(qx)}" y2="{_fmt(qy)}" '
            f'stroke="{colour}" stroke-width="1.4" opacity="0.85" filter="url(#neon)"/>'
        )
    return "".join(parts)


def _landmark_observatory(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A drum under a dome, with a telescope arm angled at the sky."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w,
            d,
            base,
            base + 20,
            "#0B1220",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    ]
    sides = 8
    cx, cy0 = gx + w / 2, gy + d / 2
    lower: list[tuple[float, float]] = []
    upper: list[tuple[float, float]] = []
    for index in range(sides):
        angle = math.radians(index * 360 / sides)
        px = cx + math.cos(angle) * (w / 2) * 0.55
        py = cy0 + math.sin(angle) * (d / 2) * 0.55
        sx, sy = place(px, py, base + 20)
        ux, uy = place(px, py, base + 40)
        lower.append((sx, sy))
        upper.append((ux, uy))
    parts.append(
        _polygon(
            lower + upper[::-1], fill="url(#faceright)", stroke=colour, stroke_width=0.8
        )
    )
    apex_x, apex_y = place(cx, cy0, base + 62)
    dome = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in upper)
    parts.append(
        f'<polygon points="{dome} {_fmt(apex_x)},{_fmt(apex_y)}" fill="{colour}" '
        f'opacity="0.30" stroke="{colour}" stroke-width="1.2"/>'
    )
    parts.append(
        f'<polygon points="{dome} {_fmt(apex_x)},{_fmt(apex_y)}" fill="none" '
        f'stroke="{colour}" stroke-width="2.4" opacity="0.7" filter="url(#neon)"/>'
    )
    tip_x, tip_y = place(cx + w * 0.5, cy0, base + 78)
    hub_x, hub_y = place(cx, cy0, base + 62)
    parts.append(
        f'<line x1="{_fmt(hub_x)}" y1="{_fmt(hub_y)}" x2="{_fmt(tip_x)}" y2="{_fmt(tip_y)}" '
        f'stroke="{colour}" stroke-width="3" stroke-linecap="round" filter="url(#neon)"/>'
    )
    parts.append(
        f'<circle class="beacon" cx="{_fmt(hub_x)}" cy="{_fmt(hub_y)}" r="2.6" '
        f'fill="{colour}" filter="url(#neon)"/>'
    )
    return "".join(parts)


def _landmark_gallery(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A wide, low hall with a barrel-vault roof and a columned portico."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w,
            d,
            base,
            base + 16,
            "#12091c",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    ]
    ax0, ay0 = place(gx, gy, base + 16)
    ax1, ay1 = place(gx + w, gy, base + 16)
    ax2, ay2 = place(gx + w, gy + d, base + 16)
    ax3, ay3 = place(gx, gy + d, base + 16)
    peak = place(gx + w / 2, gy + d / 2, base + 40)
    parts.append(
        f'<path d="M {_fmt(ax1)},{_fmt(ay1)} L {_fmt(ax2)},{_fmt(ay2)} '
        f'Q {_fmt(peak[0])},{_fmt(peak[1] - 6)} {_fmt(ax0)},{_fmt(ay0)} Z" '
        f'fill="{colour}" fill-opacity="0.22" stroke="{colour}" stroke-width="1.3"/>'
    )
    for index in range(6):
        t = (index + 0.5) / 6
        px = gx + w * t
        bottom = place(px, gy + d, base)
        head = place(px, gy + d, base + 14)
        parts.append(
            f'<line x1="{_fmt(bottom[0])}" y1="{_fmt(bottom[1])}" x2="{_fmt(head[0])}" '
            f'y2="{_fmt(head[1])}" stroke="{colour}" stroke-width="2.1" opacity="0.9"/>'
        )
    for index in range(5):
        t = (index + 0.5) / 5
        bx, by = place(gx + w * t, gy, base + 20)
        parts.append(
            f'<circle cx="{_fmt(bx)}" cy="{_fmt(by)}" r="1.8" fill="#FFF3C4" '
            f'filter="url(#neon)"/>'
        )
    return "".join(parts)


def _landmark_museum(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A classical block: pediment, colonnade, and a clock tower."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w * 0.72,
            d,
            base,
            base + 22,
            "#141024",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    ]
    for index in range(8):
        t = (index + 0.5) / 8
        px = gx + w * 0.72 * t
        bottom = place(px, gy + d, base)
        head = place(px, gy + d, base + 20)
        parts.append(
            f'<line x1="{_fmt(bottom[0])}" y1="{_fmt(bottom[1])}" x2="{_fmt(head[0])}" '
            f'y2="{_fmt(head[1])}" stroke="{colour}" stroke-width="1.9" opacity="0.92"/>'
        )
    l0x, l0y = place(gx, gy, base + 22)
    l1x, l1y = place(gx + w * 0.72, gy, base + 22)
    apex_x, apex_y = place(gx + w * 0.36, gy, base + 38)
    parts.append(
        f'<polygon points="{_fmt(l0x)},{_fmt(l0y)} {_fmt(l1x)},{_fmt(l1y)} '
        f'{_fmt(apex_x)},{_fmt(apex_y)}" fill="{colour}" fill-opacity="0.26" '
        f'stroke="{colour}" stroke-width="1.3"/>'
    )
    parts.append(
        _box(
            place,
            gx + w * 0.78,
            gy + d * 0.2,
            w * 0.2,
            d * 0.5,
            base,
            base + 44,
            "#101728",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        )
    )
    fx, fy = place(gx + w * 0.88, gy + d * 0.45, base + 36)
    parts.append(
        f'<circle cx="{_fmt(fx)}" cy="{_fmt(fy)}" r="4.2" fill="#0B1220" '
        f'stroke="{colour}" stroke-width="1.6"/>'
    )
    parts.append(
        f'<line x1="{_fmt(fx)}" y1="{_fmt(fy)}" x2="{_fmt(fx)}" y2="{_fmt(fy - 2.6)}" '
        f'stroke="{colour}" stroke-width="1.2"/>'
        f'<line x1="{_fmt(fx)}" y1="{_fmt(fy)}" x2="{_fmt(fx + 2)}" y2="{_fmt(fy)}" '
        f'stroke="{colour}" stroke-width="1.2"/>'
    )
    return "".join(parts)


def _landmark_mall(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """A multi-level arcade with a lit sign band and a broad entrance."""
    parts = [
        _box(
            place,
            gx,
            gy,
            w,
            d * 0.55,
            base,
            base + 24,
            "#0C1A16",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        ),
        _box(
            place,
            gx,
            gy + d * 0.62,
            w,
            d * 0.38,
            base,
            base + 14,
            "#0C1A16",
            "url(#faceleft)",
            "url(#faceright)",
            colour,
        ),
    ]
    a0, _b0 = place(gx, gy + d * 0.55, base + 20)
    a1, _b1 = place(gx + w, gy + d * 0.55, base + 20)
    parts.append(
        f'<line x1="{_fmt(a0)}" y1="{_fmt(_b0)}" x2="{_fmt(a1)}" y2="{_fmt(_b1)}" '
        f'stroke="{colour}" stroke-width="3.4" opacity="0.9" filter="url(#neon)"/>'
    )
    for index in range(7):
        t = (index + 0.5) / 7
        bx, by = place(gx + w * t, gy + d * 0.55, base + 12)
        parts.append(
            f'<rect x="{_fmt(bx - 2.4)}" y="{_fmt(by - 3.2)}" width="4.8" height="6.4" '
            f'fill="#D9FFE8" opacity="{0.35 + (index % 3) * 0.2:.2f}"/>'
        )
    canopy_x0, canopy_y0 = place(gx + w * 0.3, gy + d * 0.95, base)
    canopy_x1, canopy_y1 = place(gx + w * 0.7, gy + d * 0.95, base)
    parts.append(
        f'<line x1="{_fmt(canopy_x0)}" y1="{_fmt(canopy_y0)}" x2="{_fmt(canopy_x1)}" '
        f'y2="{_fmt(canopy_y1)}" stroke="{colour}" stroke-width="2" opacity="0.7"/>'
    )
    parts.append(
        f'<circle class="beacon" cx="{_fmt((canopy_x0 + canopy_x1) / 2)}" '
        f'cy="{_fmt((canopy_y0 + canopy_y1) / 2)}" r="2.4" fill="{colour}" '
        f'filter="url(#neon)"/>'
    )
    return "".join(parts)


def _landmark_square(
    place: Any, gx: float, gy: float, w: float, d: float, base: float, colour: str
) -> str:
    """An open civic square: a fountain, radial paths, and flag poles."""
    parts: list[str] = []
    centre = place(gx + w / 2, gy + d / 2, base)
    for radius, opacity in ((3.4, 0.10), (2.5, 0.16), (1.5, 0.26)):
        parts.append(
            f'<ellipse cx="{_fmt(centre[0])}" cy="{_fmt(centre[1])}" rx="{_fmt(radius * 32)}" '
            f'ry="{_fmt(radius * 16)}" fill="{colour}" fill-opacity="{opacity}" '
            f'stroke="{colour}" stroke-width="1" stroke-opacity="0.5"/>'
        )
    parts.append(
        f'<ellipse cx="{_fmt(centre[0])}" cy="{_fmt(centre[1] - 6)}" rx="16" ry="8" '
        f'fill="#CFFAFE" opacity="0.5" filter="url(#neon)"/>'
    )
    top = place(gx + w / 2, gy + d / 2, base + 22)
    parts.append(
        f'<line x1="{_fmt(centre[0])}" y1="{_fmt(centre[1])}" x2="{_fmt(top[0])}" '
        f'y2="{_fmt(top[1])}" stroke="{colour}" stroke-width="2.6" filter="url(#neon)"/>'
    )
    for index in range(4):
        angle = math.radians(45 + index * 90)
        px = gx + w / 2 + math.cos(angle) * w * 0.42
        py = gy + d / 2 + math.sin(angle) * d * 0.42
        foot = place(px, py, base)
        head = place(px, py, base + 30)
        parts.append(
            f'<line x1="{_fmt(foot[0])}" y1="{_fmt(foot[1])}" x2="{_fmt(head[0])}" '
            f'y2="{_fmt(head[1])}" stroke="{colour}" stroke-width="1.6" opacity="0.8"/>'
        )
    parts.append(
        f'<circle class="beacon" cx="{_fmt(top[0])}" cy="{_fmt(top[1])}" r="2.6" '
        f'fill="{colour}" filter="url(#neon)"/>'
    )
    return "".join(parts)


# --------------------------------------------------------------------------
# Residents: a sub-sector they run, and a person standing beside it.
# --------------------------------------------------------------------------


def _person_height(person: Resident) -> float:
    """How tall the sub-sector they run stands, encoding granted capability."""
    return (
        BASE_HEIGHT
        + len(person.can) * HEIGHT_UNIT
        + min(person.max_steps, 12) * FLOORS_PER_STEP
    )


def _subsector(
    place: Any, gx: float, gy: float, person: Resident, plaque: str, colour: str
) -> str:
    """A small pavilion: the sub-sector a person works in."""
    height = _person_height(person) * 0.22
    x0, y0 = place(gx, gy, 0)
    x1, y1 = place(gx + SUBSECTOR_W, gy, 0)
    x2, y2 = place(gx + SUBSECTOR_W, gy + SUBSECTOR_D, 0)
    x3, y3 = place(gx, gy + SUBSECTOR_D, 0)
    tx0, ty0 = place(gx, gy, height)
    tx1, ty1 = place(gx + SUBSECTOR_W, gy, height)
    tx2, ty2 = place(gx + SUBSECTOR_W, gy + SUBSECTOR_D, height)
    tx3, ty3 = place(gx, gy + SUBSECTOR_D, height)
    seed = int(hashlib.sha1(person.name.encode("utf-8")).hexdigest()[:8], 16)
    state = seed & 0x7FFFFFFF
    panes: list[str] = []
    for column in range(3):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        u0 = 0.14 + column * 0.27
        u1 = u0 + 0.18
        opacity = 0.45 + ((state >> 16) % 45) / 100
        # The first pane is always a warm interior light, so a sub-sector
        # reads as occupied even when the district hue is a cold neon.
        tint = "#FFE2B0" if column == 0 else ("#FFF6DC", colour)[(state >> 8) % 2]
        panes.append(
            _polygon(
                [
                    (x3 + (x2 - x3) * u0, y3 + (y2 - y3) * u0),
                    (x3 + (x2 - x3) * u1, y3 + (y2 - y3) * u1),
                    (
                        tx3 + (tx2 - tx3) * u1,
                        ty3 + (ty2 - ty3) * u1 - (ty3 - y3) * 0.38,
                    ),
                    (
                        tx3 + (tx2 - tx3) * u0,
                        ty3 + (ty2 - ty3) * u0 - (ty3 - y3) * 0.38,
                    ),
                ],
                fill=tint,
                opacity=f"{opacity:.2f}",
            )
        )
    return (
        _polygon(
            [(x + 6, y + 4) for x, y in ((x0, y0), (x1, y1), (x2, y2), (x3, y3))],
            fill="#000000",
            opacity="0.4",
        )
        + _polygon(
            [(x0, y0), (x1, y1), (x2, y2), (x3, y3)],
            fill="url(#kerb)" if person.cannot else "#0D1421",
            stroke="#FF5C7A" if person.cannot else colour,
            stroke_width=1.1,
            **({"data-prohibited": "1"} if person.cannot else {}),
        )
        + _polygon(
            [(x3, y3), (x2, y2), (tx2, ty2), (tx3, ty3)],
            fill="url(#faceleft)",
            stroke="#223049",
            stroke_width=0.6,
        )
        + "".join(panes)
        + _polygon(
            [(x1, y1), (x2, y2), (tx2, ty2), (tx1, ty1)],
            fill="url(#faceright)",
            stroke="#223049",
            stroke_width=0.6,
        )
        + _polygon(
            [(tx0, ty0), (tx1, ty1), (tx2, ty2), (tx3, ty3)],
            fill="#0C1220",
            stroke=colour,
            stroke_width=1,
        )
    )


def _person(
    place: Any,
    gx: float,
    gy: float,
    person: Resident,
    plaque: str,
    colour: str,
    grand: Grand,
) -> tuple[str, float, float]:
    """The agent as a virtual human: a figure that can stand and walk.

    The body is drawn as limbs rather than a block, and the walk target sits
    on the avenue at the foot of the megastructure, so the live page can
    translate the figure out of its sub-sector when the person is working.
    """
    foot = place(gx, gy, 0)
    hip = place(gx, gy, PERSON_HEIGHT * 0.42)
    chest = place(gx, gy, PERSON_HEIGHT * 0.74)
    head = place(gx, gy, PERSON_HEIGHT * 0.9)
    head_r = 2.5

    # A deterministic facing keeps a given person turned the same way twice.
    seed = int(hashlib.sha1(person.name.encode("utf-8")).hexdigest()[:8], 16)
    facing = 1.0 if (seed >> 5) % 2 else -1.0
    swing = 1.5 * facing
    lean = 0.9 * facing

    # The avenue runs along the front of the megastructure; the delta is what
    # the live page translates the figure by when work starts.
    walk = place(gx, grand.gy + grand.depth - 0.35, 0)
    dx = walk[0] - foot[0]
    dy = walk[1] - foot[1]

    limb = 2.1
    return (
        f'<g class="figure">'
        f'<ellipse cx="{_fmt(foot[0])}" cy="{_fmt(foot[1] + 1.5)}" rx="5.2" ry="2.6" '
        f'fill="#000000" opacity="0.5"/>'
        # legs
        f'<line class="leg" x1="{_fmt(foot[0] - swing)}" y1="{_fmt(foot[1])}" '
        f'x2="{_fmt(hip[0] - swing * 0.4)}" y2="{_fmt(hip[1])}" stroke="#1B2740" '
        f'stroke-width="{_fmt(limb)}" stroke-linecap="round"/>'
        f'<line class="leg" x1="{_fmt(foot[0] + swing)}" y1="{_fmt(foot[1])}" '
        f'x2="{_fmt(hip[0] + swing * 0.4)}" y2="{_fmt(hip[1])}" stroke="#26365A" '
        f'stroke-width="{_fmt(limb)}" stroke-linecap="round"/>'
        # torso
        f'<line x1="{_fmt(hip[0])}" y1="{_fmt(hip[1])}" x2="{_fmt(chest[0] + lean * 0.4)}" '
        f'y2="{_fmt(chest[1])}" stroke="{colour}" stroke-width="3.6" '
        f'stroke-linecap="round" filter="url(#neon)" opacity="0.95"/>'
        # arms
        f'<line class="arm" x1="{_fmt(chest[0] - 2.2 * facing)}" y1="{_fmt(chest[1] + 0.4)}" '
        f'x2="{_fmt(chest[0] - 3.4 * facing)}" y2="{_fmt(chest[1] + 4.6)}" '
        f'stroke="#2B3E63" stroke-width="{_fmt(limb - 0.4)}" stroke-linecap="round"/>'
        f'<line class="arm" x1="{_fmt(chest[0] + 2.2 * facing)}" y1="{_fmt(chest[1] + 0.4)}" '
        f'x2="{_fmt(chest[0] + 3.4 * facing)}" y2="{_fmt(chest[1] + 4.6)}" '
        f'stroke="{colour}" stroke-width="{_fmt(limb - 0.4)}" stroke-linecap="round" '
        f'opacity="0.9"/>'
        # head
        f'<circle cx="{_fmt(head[0] + lean * 0.5)}" cy="{_fmt(head[1])}" r="{_fmt(head_r)}" '
        f'fill="#F4D9BE" stroke="{colour}" stroke-width="0.8"/>'
        f'<circle class="beacon" cx="{_fmt(head[0] + lean * 0.5)}" cy="{_fmt(head[1])}" '
        f'r="{_fmt(head_r + 1.5)}" fill="{colour}" filter="url(#neon)" opacity="0.5"/>'
        f'<text class="plate" x="{_fmt(head[0] + lean * 0.5)}" y="{_fmt(head[1] - head_r - 3.5)}" '
        f'fill="{colour}" font-size="8.5" font-weight="700" text-anchor="middle">'
        f"{_escape(_clip(plaque, 12))}</text>"
        f"</g>",
        dx,
        dy,
    )


def _band(
    place: Any,
    grand: Grand,
    people: list[Resident],
    top: float,
    city: City,
    plaques: dict[str, str],
    departments: dict[str, str],
    palette_offset: int,
    building_ids: Mapping[str, str] | None = None,
) -> str:
    """Lay a domain's people out as sub-sectors, with a person beside each.

    People are ordered by the department they came from and then by their
    name, so a domain that draws members from several teams still reads as
    an ordered floor rather than a heap.
    """
    order = {name: index for index, name in enumerate(sorted(departments))}
    people = sorted(people, key=lambda p: (departments.get(p.name, ""), p.name))
    del order

    hall_w = SUBSECTOR_COLUMNS * (SUBSECTOR_W + SUBSECTOR_GAP)
    hall_d = _band_depth(len(people))
    x0, y0 = place(grand.gx + PLATFORM_MARGIN / 2, top, 0)
    x1, y1 = place(grand.gx + PLATFORM_MARGIN / 2 + hall_w, top, 0)
    x2, y2 = place(grand.gx + PLATFORM_MARGIN / 2 + hall_w, top + hall_d, 0)
    x3, y3 = place(grand.gx + PLATFORM_MARGIN / 2, top + hall_d, 0)
    parts = [
        _polygon(
            [(x0, y0), (x1, y1), (x2, y2), (x3, y3)],
            fill="#080D17",
            stroke=grand.colour,
            stroke_width=1.1,
            **{"data-domain-floor": _escape(grand.name)},
        )
    ]

    standing: list[tuple[float, str]] = []
    for index, person in enumerate(people):
        column = index % SUBSECTOR_COLUMNS
        row = index // SUBSECTOR_COLUMNS
        gx = grand.gx + PLATFORM_MARGIN / 2 + column * (SUBSECTOR_W + SUBSECTOR_GAP)
        gy = top + 0.55 + row * (SUBSECTOR_D + SUBSECTOR_GAP)
        plaque = plaques.get(person.name, person.name)
        shade = _PALETTE[(palette_offset + index) % len(_PALETTE)]
        department = departments.get(person.name, "")
        figure, dx, dy = _person(
            place,
            gx + SUBSECTOR_W + SUBSECTOR_GAP * 0.5,
            gy + SUBSECTOR_D * 0.5,
            person,
            plaque,
            shade,
            grand,
        )
        group = (
            f'<g data-agent="{_escape(person.name)}" data-person="{_escape(plaque)}" '
            f'data-department="{_escape(department)}" '
            f'data-domain="{_escape(grand.name)}" '
            + (
                f'data-building="{_escape(building_ids[person.name])}" '
                if building_ids is not None and person.name in building_ids
                else ""
            )
            + f'data-walk-dx="{_fmt(dx)}" data-walk-dy="{_fmt(dy)}" '
            f'data-cannot="{_escape(", ".join(person.cannot))}" '
            f'data-can="{_escape(", ".join(person.can))}" '
            f'data-storeys="{_fmt(_person_height(person))}" class="avatar">'
            + _subsector(place, gx, gy, person, plaque, shade)
            + figure
            + "</g>"
        )
        standing.append((gx + gy, group))
    parts.append("".join(markup for _, markup in sorted(standing, key=lambda i: i[0])))
    del city
    return "".join(parts)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _terrain_tiles(place: Any, extent: float, style: EnvironmentStyle) -> str:
    """Draw deterministic isometric terrain tiles beneath the city."""
    limit = math.ceil(extent / TERRAIN_TILE_SIZE)
    parts = [
        f'<g data-terrain-tiles="1" fill="none" stroke="{style.ground_stroke}" '
        'stroke-width="0.45">'
    ]
    for gy in range(limit):
        for gx in range(limit):
            x = gx * TERRAIN_TILE_SIZE
            y = gy * TERRAIN_TILE_SIZE
            points = [
                place(x, y, 0),
                place(x + TERRAIN_TILE_SIZE, y, 0),
                place(x + TERRAIN_TILE_SIZE, y + TERRAIN_TILE_SIZE, 0),
                place(x, y + TERRAIN_TILE_SIZE, 0),
            ]
            fill = style.terrain if (gx + gy) % 2 else style.ground
            opacity = "0.28" if (gx + gy) % 2 else "0.16"
            parts.append(
                _polygon(
                    points,
                    fill=fill,
                    opacity=opacity,
                    **{"data-tile": f"{gx}:{gy}"},
                )
            )
    parts.append("</g>")
    return "".join(parts)


def _environment_scene(style: EnvironmentStyle, width: float, height: float) -> str:
    """Draw restrained terrain cues behind the authoritative city geometry."""

    parts = [
        f'<g data-environment="{_escape(style.name)}" opacity="0.48" '
        f'fill="none" stroke="{style.terrain_accent}">'
    ]
    if style.name == "water":
        for index in range(7):
            y = height * (0.67 + index * 0.045)
            parts.append(
                f'<path d="M 0 {_fmt(y)} Q {_fmt(width * 0.16)} {_fmt(y - 8)} '
                f"{_fmt(width * 0.32)} {_fmt(y)} T {_fmt(width * 0.64)} {_fmt(y)} "
                f'T {_fmt(width)} {_fmt(y)}" stroke-width="1.4" opacity="0.72"/>'
            )
    elif style.name == "desert":
        for index in range(5):
            y = height * (0.72 + index * 0.055)
            parts.append(
                f'<path d="M 0 {_fmt(y)} Q {_fmt(width * 0.22)} {_fmt(y - 24)} '
                f'{_fmt(width * 0.47)} {_fmt(y)} T {_fmt(width)} {_fmt(y)}" '
                f'stroke-width="2.2" opacity="0.65"/>'
            )
    elif style.name in {"forest", "suburban", "rural"}:
        for index in range(12):
            x = 24 + index * max(width / 13, 1)
            base = height * (0.78 + (index % 3) * 0.018)
            size = 12 + (index % 4) * 4
            parts.append(
                f'<path d="M {_fmt(x)} {_fmt(base)} l {_fmt(-size)} 0 '
                f"{_fmt(size)} {_fmt(-size * 1.7)} {_fmt(size)} {_fmt(size * 1.7)} "
                f'z" fill="{style.terrain}" stroke-width="1.2"/>'
            )
    else:
        for index in range(9):
            y = height * (0.69 + index * 0.034)
            parts.append(
                f'<path d="M 0 {_fmt(y)} H {_fmt(width)}" '
                f'stroke-width="0.8" opacity="0.55"/>'
            )
    parts.append("</g>")
    return "".join(parts)


def _blockscape_tree(place: Any, gx: float, gy: float, colour: str) -> str:
    """Draw a layered, low-poly tree with readable volume."""
    trunk = _box(
        place,
        gx - 0.35,
        gy - 0.35,
        0.7,
        0.7,
        0,
        8,
        "#6B4423",
        "#4A2E1A",
        "#8B5A2B",
        "#2C1D18",
    )
    lower = [
        place(gx - 1.35, gy - 1.35, 6.5),
        place(gx + 1.35, gy - 1.35, 6.5),
        place(gx + 1.35, gy + 1.35, 6.5),
        place(gx - 1.35, gy + 1.35, 6.5),
    ]
    lower_peak = place(gx, gy, 16)
    upper = [
        place(gx - 1.0, gy - 1.0, 13),
        place(gx + 1.0, gy - 1.0, 13),
        place(gx + 1.0, gy + 1.0, 13),
        place(gx - 1.0, gy + 1.0, 13),
    ]
    upper_peak = place(gx, gy, 26)
    faces = [
        _polygon([lower[0], lower[1], lower_peak], fill=colour, stroke="#163B2E"),
        _polygon([lower[1], lower[2], lower_peak], fill="#2F7D4A", stroke="#163B2E"),
        _polygon([lower[2], lower[3], lower_peak], fill="#286A3D", stroke="#163B2E"),
        _polygon([lower[3], lower[0], lower_peak], fill="#459A58", stroke="#163B2E"),
        _polygon([upper[0], upper[1], upper_peak], fill="#57B967", stroke="#1C5A45"),
        _polygon([upper[1], upper[2], upper_peak], fill="#3E9B58", stroke="#1C5A45"),
        _polygon([upper[2], upper[3], upper_peak], fill="#2F7D4A", stroke="#1C5A45"),
        _polygon([upper[3], upper[0], upper_peak], fill="#4EAE63", stroke="#1C5A45"),
    ]
    return trunk + "".join(faces)


def _blockscape_building(
    place: Any,
    gx: float,
    gy: float,
    width: float,
    depth: float,
    height: float,
    colour: str,
    kind: str,
) -> str:
    """Draw a readable, block-built institution."""
    top = "#263A54"
    left = "#152438"
    right = "#1C3048"
    parts = [_box(place, gx, gy, width, depth, 0, height, top, left, right, colour)]
    for row in range(2):
        for column in range(3):
            wx = gx + 0.9 + column * (width - 1.8) / 3
            wy = gy + depth + 0.02
            z = 1.0 + row * max(1.2, (height - 2.0) / 2)
            points = [
                place(wx, wy, z),
                place(wx + 0.62, wy, z),
                place(wx + 0.62, wy, z + 0.62),
                place(wx, wy, z + 0.62),
            ]
            parts.append(_polygon(points, fill="#FFE6A7", opacity="0.82"))
    roof_z = height + 0.8
    roof = [
        place(gx - 0.15, gy - 0.15, roof_z),
        place(gx + width + 0.15, gy - 0.15, roof_z),
        place(gx + width + 0.15, gy + depth + 0.15, roof_z),
        place(gx - 0.15, gy + depth + 0.15, roof_z),
    ]
    parts.append(_polygon(roof, fill="#314A69", stroke=colour, stroke_width=1.3))
    peak = place(gx + width / 2, gy + depth / 2, height + 6.5)
    parts.extend(
        (
            _polygon(
                [roof[0], roof[1], peak],
                fill="#3E5D82",
                stroke=colour,
                stroke_width=0.8,
            ),
            _polygon(
                [roof[1], roof[2], peak],
                fill="#294565",
                stroke=colour,
                stroke_width=0.8,
            ),
            _polygon(
                [roof[2], roof[3], peak],
                fill="#203A59",
                stroke=colour,
                stroke_width=0.8,
            ),
            _polygon(
                [roof[3], roof[0], peak],
                fill="#345477",
                stroke=colour,
                stroke_width=0.8,
            ),
        )
    )
    parts.append(
        f'<text x="{_fmt(place(gx + width / 2, gy + depth + 0.2, height)[0])}" '
        f'y="{_fmt(place(gx + width / 2, gy + depth + 0.2, height)[1] + 12)}" '
        f'fill="{colour}" font-size="10" font-weight="700" text-anchor="middle">'
        f"{_escape(_clip(kind, 16))}</text>"
    )
    return "".join(parts)


def _lowpoly_building(
    place: Any,
    gx: float,
    gy: float,
    width: float,
    depth: float,
    height: float,
    colour: str,
    kind: str,
) -> str:
    """Draw an original low-poly institution with a landmark silhouette."""
    parts = [_blockscape_building(place, gx, gy, width, depth, height, colour, kind)]
    facade = [
        place(gx + 0.25, gy + depth + 0.08, 0.2),
        place(gx + width - 0.25, gy + depth + 0.08, 0.2),
        place(gx + width - 0.25, gy + depth + 0.08, height - 0.8),
        place(gx + 0.25, gy + depth + 0.08, height - 0.8),
    ]
    parts.append(_polygon(facade, fill="#A96445", stroke="#6E472F", stroke_width=0.8))
    for column in range(3):
        wx = gx + 0.9 + column * 1.55
        window = [
            place(wx, gy + depth + 0.12, 6.4),
            place(wx + 0.85, gy + depth + 0.12, 6.4),
            place(wx + 0.85, gy + depth + 0.12, 8.2),
            place(wx, gy + depth + 0.12, 8.2),
        ]
        parts.append(
            _polygon(window, fill="#B9F0E6", stroke="#F6D28B", stroke_width=0.55)
        )
    tower_x = gx + 0.35
    tower_y = gy + 0.35
    tower_height = height + 10.0
    parts.append(
        _box(
            place,
            tower_x,
            tower_y,
            1.55,
            1.55,
            height - 0.2,
            tower_height,
            "#D7B56D",
            "#9A693C",
            "#B98248",
            "#6E472F",
        )
    )
    tower_roof = [
        place(tower_x - 0.1, tower_y - 0.1, tower_height + 0.2),
        place(tower_x + 1.65, tower_y - 0.1, tower_height + 0.2),
        place(tower_x + 1.65, tower_y + 1.65, tower_height + 0.2),
        place(tower_x - 0.1, tower_y + 1.65, tower_height + 0.2),
    ]
    tower_peak = place(tower_x + 0.78, tower_y + 0.78, tower_height + 3.8)
    parts.extend(
        (
            _polygon(
                [tower_roof[0], tower_roof[1], tower_peak],
                fill="#E7C873",
                stroke="#8F5D31",
            ),
            _polygon(
                [tower_roof[1], tower_roof[2], tower_peak],
                fill="#B77B45",
                stroke="#8F5D31",
            ),
            _polygon(
                [tower_roof[2], tower_roof[3], tower_peak],
                fill="#8F5D31",
                stroke="#6E472F",
            ),
            _polygon(
                [tower_roof[3], tower_roof[0], tower_peak],
                fill="#C8924E",
                stroke="#6E472F",
            ),
        )
    )
    door = [
        place(gx + width / 2 - 0.55, gy + depth + 0.06, 0.1),
        place(gx + width / 2 + 0.55, gy + depth + 0.06, 0.1),
        place(gx + width / 2 + 0.55, gy + depth + 0.06, 4.0),
        place(gx + width / 2 - 0.55, gy + depth + 0.06, 4.0),
    ]
    parts.append(_polygon(door, fill="#3A2832", stroke="#F6D28B", stroke_width=0.8))
    flag_top = place(tower_x + 0.78, tower_y + 0.78, tower_height + 8.2)
    parts.append(
        f'<line x1="{_fmt(tower_peak[0])}" y1="{_fmt(tower_peak[1])}" '
        f'x2="{_fmt(flag_top[0])}" y2="{_fmt(flag_top[1])}" stroke="#F6D28B" '
        'stroke-width="1.3"/>'
        + _polygon(
            [
                flag_top,
                (flag_top[0] + 7, flag_top[1] + 2),
                (flag_top[0], flag_top[1] + 6),
            ],
            fill=colour,
            stroke="#F6D28B",
            stroke_width=0.6,
        )
    )
    return "".join(parts)


def _lowpoly_fountain(place: Any, gx: float, gy: float) -> str:
    """Draw a small plaza landmark to break up the technical grid."""
    base = _box(
        place,
        gx,
        gy,
        2.2,
        2.2,
        0,
        1.0,
        "#B7CBD2",
        "#718A94",
        "#8EAAB2",
        "#49636D",
    )
    basin = [
        place(gx - 0.15, gy - 0.15, 1.1),
        place(gx + 2.35, gy - 0.15, 1.1),
        place(gx + 2.35, gy + 2.35, 1.1),
        place(gx - 0.15, gy + 2.35, 1.1),
    ]
    jet = place(gx + 1.1, gy + 1.1, 3.4)
    return (
        '<g data-lowpoly-prop="fountain">'
        + base
        + _polygon(basin, fill="#35B8C2", stroke="#D4F5F4", stroke_width=0.7)
        + _polygon(
            [
                place(gx + 0.75, gy + 0.75, 1.2),
                place(gx + 1.45, gy + 0.75, 1.2),
                place(gx + 1.45, gy + 1.45, 1.2),
                place(gx + 0.75, gy + 1.45, 1.2),
                jet,
            ],
            fill="#A9F2EE",
            stroke="#D4F5F4",
            stroke_width=0.6,
        )
        + "</g>"
    )


def _lowpoly_rock(place: Any, gx: float, gy: float, colour: str) -> str:
    """Draw an angular decorative rock."""
    base = _box(
        place, gx, gy, 1.8, 1.3, 0, 1.2, colour, "#53616D", "#71808D", "#3C4650"
    )
    top = [
        place(gx + 0.1, gy + 0.1, 1.25),
        place(gx + 1.0, gy - 0.25, 2.8),
        place(gx + 1.8, gy + 0.25, 1.25),
        place(gx + 1.2, gy + 1.2, 2.1),
        place(gx + 0.25, gy + 1.1, 1.25),
    ]
    return (
        '<g data-lowpoly-prop="rock">'
        + base
        + _polygon(top, fill="#8B9AA3", stroke="#4A5963", stroke_width=0.8)
        + "</g>"
    )


def _blockscape_agent(
    place: Any,
    gx: float,
    gy: float,
    person: Resident,
    plaque: str,
    colour: str,
    grand: Grand,
    department: str,
    building_id: str | None,
) -> str:
    """Draw one authoritative resident as a small block-side avatar."""
    figure, dx, dy = _person(place, gx, gy, person, plaque, colour, grand)
    building = (
        f'data-building="{_escape(building_id)}" ' if building_id is not None else ""
    )
    return (
        f'<g data-agent="{_escape(person.name)}" data-person="{_escape(plaque)}" '
        f'data-department="{_escape(department)}" data-domain="{_escape(grand.name)}" '
        f'{building}data-walk-dx="{_fmt(dx)}" data-walk-dy="{_fmt(dy)}" '
        f'data-cannot="{_escape(", ".join(person.cannot))}" '
        f'data-can="{_escape(", ".join(person.can))}" '
        f'data-storeys="{_fmt(_person_height(person))}" class="avatar">'
        + figure
        + "</g>"
    )


def _render_blockscape(
    city: City,
    style: EnvironmentStyle,
    title: str,
    labels: Mapping[str, str],
    kinds: Mapping[str, str],
    building_ids: Mapping[str, str],
    world_camera: str,
    render_style: str,
) -> str:
    """Render a deterministic isometric AI world with layered game geometry."""
    groups = _group_domains(city)
    map_width, map_depth = 36.0, 28.0
    corners = [
        _project(0, 0, 0),
        _project(map_width, 0, 0),
        _project(map_width, map_depth, 0),
        _project(0, map_depth, 0),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x + MARGIN * 2
    height = max_y - min_y + MARGIN * 2 + 120
    offset_x = MARGIN - min_x
    offset_y = MARGIN - min_y + 90

    def place(gx: float, gy: float, z: float) -> tuple[float, float]:
        x, y = _project(gx, gy, z)
        return x + offset_x, y + offset_y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(width)}" '
        f'height="{_fmt(height)}" viewBox="0 0 {_fmt(width)} {_fmt(height)}" '
        'role="img" aria-labelledby="city-title city-desc" '
        f'data-environment="{_escape(style.name)}" '
        f'data-world="{_escape(title)}" data-camera="{_escape(world_camera)}" '
        f'data-render-style="{_escape(render_style)}" '
        f'font-family="Menlo,Consolas,monospace">',
        f'<title id="city-title">{_escape(title)} {_escape(render_style)} world</title>',
        f'<desc id="city-desc">A stylized isometric low-poly {style.name} world with '
        f"{len(city.residents)} authoritative agents across {len(groups)} institutions. "
        "Buildings, paths, terrain, props, and resident identities are deterministic.</desc>",
        "<defs>"
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{style.sky_top}"/>'
        f'<stop offset="0.5" stop-color="{style.sky_mid}"/>'
        f'<stop offset="1" stop-color="{style.sky_bottom}"/>'
        "</linearGradient>"
        '<linearGradient id="faceleft" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#0E1524"/><stop offset="1" stop-color="#182338"/>'
        "</linearGradient>"
        '<linearGradient id="faceright" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#182338"/><stop offset="1" stop-color="#0E1524"/>'
        "</linearGradient>"
        '<filter id="neon" x="-70%" y="-70%" width="240%" height="240%">'
        '<feGaussianBlur stdDeviation="2.4" result="blur"/>'
        '<feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>'
        "</filter>"
        "</defs>",
        f'<rect width="{_fmt(width)}" height="{_fmt(height)}" fill="url(#sky)"/>',
        _environment_scene(style, width, height),
        _terrain_tiles(place, map_width, style),
    ]
    for gx in range(28, 35):
        for gy in range(22, 27):
            points = [
                place(gx, gy, 0.03),
                place(gx + 1, gy, 0.03),
                place(gx + 1, gy + 1, 0.03),
                place(gx, gy + 1, 0.03),
            ]
            parts.append(
                _polygon(
                    points,
                    fill="#167A91",
                    stroke="#2AA7B5",
                    stroke_width=0.45,
                    opacity="0.88",
                    **{"data-water-tile": f"{gx}:{gy}"},
                )
            )

    path_tiles = []
    for gx in range(2, 35):
        for gy in (13, 14):
            path_tiles.append((gx, gy))
    for gy in range(2, 27):
        for gx in (17, 18):
            path_tiles.append((gx, gy))
    for gx, gy in path_tiles:
        points = [
            place(gx, gy, 0.04),
            place(gx + 1, gy, 0.04),
            place(gx + 1, gy + 1, 0.04),
            place(gx, gy + 1, 0.04),
        ]
        parts.append(
            _polygon(
                points,
                fill=style.road,
                stroke=style.road_stroke,
                stroke_width=0.55,
                opacity="0.92",
                **{"data-path-tile": f"{gx}:{gy}"},
            )
        )
    if render_style == "isometric_lowpoly":
        parts.extend(
            (
                _lowpoly_fountain(place, 16.9, 12.2),
                _lowpoly_rock(place, 11.5, 8.5, "#7D8B98"),
                _lowpoly_rock(place, 24.5, 17.5, "#8897A0"),
                _lowpoly_rock(place, 4.5, 19.5, "#6E7C86"),
            )
        )

    tree_sites: tuple[tuple[int, int], ...] = (
        (2, 4),
        (4, 23),
        (31, 4),
        (33, 22),
        (26, 25),
        (9, 25),
    )
    if render_style == "isometric_lowpoly":
        tree_sites = tree_sites + ((12, 3), (23, 3), (2, 17), (33, 15), (13, 24))
    tree_colours = ("#3E9B58", "#4EAE63", "#2F7D4A", "#5DBB6D")
    for tree_index, (gx, gy) in enumerate(tree_sites):
        colour = tree_colours[tree_index % len(tree_colours)]
        parts.append(_blockscape_tree(place, gx, gy, colour))

    positions = ((5, 5), (18, 4), (27, 9), (7, 17), (20, 18), (29, 19), (3, 9))

    plaques = assign_plaques(person.name for person in city.residents)
    departments = _department_map(city)
    buildings: list[tuple[float, str]] = []
    for index, (domain, default_kind, people_names) in enumerate(groups):
        gx, gy = positions[index % len(positions)]
        colour = DOMAIN_COLOURS.get(domain, _PALETTE[index % len(_PALETTE)])
        kind = kinds.get(domain, default_kind)
        height = 18.0 + min(len(people_names) * 1.2, 6.0)
        building_id = next(
            (
                building_ids.get(person_name)
                for person_name in people_names
                if person_name in building_ids
            ),
            None,
        )
        building_renderer = (
            _lowpoly_building
            if render_style == "isometric_lowpoly"
            else _blockscape_building
        )
        building_markup = building_renderer(
            place, gx, gy, 6.0, 4.2, height, colour, kind
        )
        building_group = (
            f'<g data-building-id="{_escape(building_id or "")}" '
            f'data-domain="{_escape(domain)}" data-kind="{_escape(kind)}" '
            'class="block-building">'
            + building_markup
            + f'<text x="{_fmt(place(gx + 3, gy + 4.8, 0)[0])}" '
            f'y="{_fmt(place(gx + 3, gy + 4.8, 0)[1] + 14)}" fill="{colour}" '
            'font-size="13" font-weight="700" text-anchor="middle">'
            f"{_escape(_clip(labels.get(domain, domain), 24))}</text></g>"
        )
        buildings.append((gx + gy, building_group))
        grand = Grand(domain, kind, colour, people_names, gx, gy, 6.0, 4.2)
        people = [person for person in city.residents if person.name in people_names]
        for person_index, person in enumerate(people):
            agent_gx = gx + 0.8 + (person_index % 3) * 2.2
            agent_gy = gy + 5.3 + (person_index // 3) * 1.6
            buildings.append(
                (
                    gx + gy + 0.2 + person_index * 0.01,
                    _blockscape_agent(
                        place,
                        agent_gx,
                        agent_gy,
                        person,
                        plaques[person.name],
                        _PALETTE[(index + person_index) % len(_PALETTE)],
                        grand,
                        departments.get(person.name, ""),
                        building_ids.get(person.name),
                    ),
                )
            )

    parts.extend(markup for _, markup in sorted(buildings, key=lambda item: item[0]))
    parts.extend(
        [
            f'<text x="{MARGIN}" y="{MARGIN - 4}" fill="#E6EDF5" font-size="21" '
            f'font-weight="700">{_escape(title)} {_escape(render_style)}</text>',
            f'<text x="{MARGIN}" y="{MARGIN + 16}" fill="#8FA4C4" font-size="12">'
            f"{len(city.residents)} agents · {len(groups)} institutions · "
            "isometric low-poly map with landmarks, props, and layered roofs</text>",
            f'<text x="{MARGIN}" y="{_fmt(height - 34)}" fill="#8FA4C4" font-size="11">'
            "paths connect institutions · blocks show capabilities · live events drive agents</text>",
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def render_city_isometric(
    city: City,
    *,
    environment: str = "metropolitan",
    world_name: str | None = None,
    world_labels: Mapping[str, str] | None = None,
    world_kinds: Mapping[str, str] | None = None,
    world_buildings: Mapping[str, str] | None = None,
    world_camera: str = "isometric",
    render_style: str = "city",
) -> str:
    """Draw the agent world in isometric projection."""

    style = environment_style(environment)
    title = world_name or city.name
    labels = world_labels or {}
    kinds = world_kinds or {}
    building_ids = world_buildings or {}
    groups = _group_domains(city)
    if render_style in {"blockscape", "voxel", "isometric_lowpoly"}:
        return _render_blockscape(
            city, style, title, labels, kinds, building_ids, world_camera, render_style
        )
    if render_style != "city":
        raise MetroError(f"Unknown render style {render_style!r}")
    grands, extent, _ = _layout_grand_sectors(groups)
    if kinds:
        grands = [
            replace(grand, kind=kinds.get(grand.name, grand.kind)) for grand in grands
        ]
    plaques = assign_plaques(person.name for person in city.residents)
    departments = _department_map(city)

    corners = [
        _project(0, 0, 0),
        _project(extent, 0, 0),
        _project(extent, extent, 0),
        _project(0, extent, 0),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = (max_x - min_x) + MARGIN * 2
    height = (max_y - min_y) + MARGIN * 2 + 150
    offset_x = MARGIN - min_x
    offset_y = MARGIN - min_y + 120

    def place(gx: float, gy: float, z: float) -> tuple[float, float]:
        x, y = _project(gx, gy, z)
        return x + offset_x, y + offset_y

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(width)}" '
        f'height="{_fmt(height)}" viewBox="0 0 {_fmt(width)} {_fmt(height)}" '
        f'role="img" aria-labelledby="city-title city-desc" '
        f'data-environment="{_escape(style.name)}" '
        f'data-world="{_escape(title)}" '
        f'data-camera="{_escape(world_camera)}" '
        f'font-family="Menlo,Consolas,monospace">',
        f'<title id="city-title">{_escape(title)} '
        f"{_escape(style.name)} AI world</title>",
        f'<desc id="city-desc">An isometric {style.name} view of '
        f"{len(city.residents)} people across {len(city.districts)} departments, "
        f"grouped into {len(grands)} grand sectors. Each grand sector is a "
        f"landmark megastructure; inside it every department runs a themed hall "
        f"and each person stands beside the sub-sector they run. A red hatched "
        f"kerb marks a declared prohibition.</desc>",
        "<defs>"
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{style.sky_top}"/>'
        f'<stop offset="0.5" stop-color="{style.sky_mid}"/>'
        f'<stop offset="1" stop-color="{style.sky_bottom}"/>'
        "</linearGradient>"
        '<radialGradient id="haze" cx="0.5" cy="0.42" r="0.72">'
        f'<stop offset="0" stop-color="{style.haze}" stop-opacity="0.55"/>'
        f'<stop offset="1" stop-color="{style.haze}" stop-opacity="0"/>'
        "</radialGradient>"
        '<radialGradient id="lamp" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#FFD9A0" stop-opacity="0.5"/>'
        '<stop offset="0.55" stop-color="#FFB347" stop-opacity="0.16"/>'
        '<stop offset="1" stop-color="#FFB347" stop-opacity="0"/>'
        "</radialGradient>"
        '<linearGradient id="faceleft" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#0E1524"/>'
        '<stop offset="1" stop-color="#182338"/>'
        "</linearGradient>"
        '<linearGradient id="faceright" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="#182338"/>'
        '<stop offset="1" stop-color="#0E1524"/>'
        "</linearGradient>"
        '<filter id="neon" x="-70%" y="-70%" width="240%" height="240%">'
        '<feGaussianBlur stdDeviation="2.4" result="blur"/>'
        '<feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>'
        "</filter>"
        '<filter id="softglow" x="-90%" y="-90%" width="280%" height="280%">'
        '<feGaussianBlur stdDeviation="5" result="blur"/>'
        '<feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>'
        "</filter>"
        '<pattern id="kerb" width="7" height="7" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)">'
        '<rect width="7" height="7" fill="#2A1418"/>'
        '<line x1="0" y1="0" x2="0" y2="7" stroke="#E0644F" stroke-width="2.5"/>'
        "</pattern>"
        '<pattern id="asphalt" width="6" height="6" patternUnits="userSpaceOnUse">'
        f'<rect width="6" height="6" fill="{style.road}"/>'
        "</pattern>"
        "</defs>",
        f'<rect width="{_fmt(width)}" height="{_fmt(height)}" fill="url(#sky)"/>',
        f'<ellipse cx="{_fmt(width / 2)}" cy="{_fmt(height * 0.42)}" '
        f'rx="{_fmt(width * 0.46)}" ry="{_fmt(height * 0.4)}" fill="url(#haze)"/>',
        _environment_scene(style, width, height),
    ]
    parts.append(_terrain_tiles(place, extent, style))

    ground: list[tuple[float, str]] = []
    for markup in _roads(place, grands, extent, style.road_stroke):
        ground.append((-1.0, markup))

    for grand in grands:
        x0, y0 = place(grand.gx, grand.gy, 0)
        x1, y1 = place(grand.gx + grand.width, grand.gy, 0)
        x2, y2 = place(grand.gx + grand.width, grand.gy + grand.depth, 0)
        x3, y3 = place(grand.gx, grand.gy + grand.depth, 0)
        slab = [(x0, y0), (x1, y1), (x2, y2), (x3, y3)]
        ground.append(
            (
                grand.depth_key,
                _polygon(
                    slab,
                    fill=style.ground,
                    stroke=grand.colour,
                    stroke_width=2,
                    **{"data-grand": _escape(grand.name), "data-kind": grand.kind},
                ),
            )
        )
        ground.append(
            (
                grand.depth_key,
                _polygon(
                    slab,
                    fill="none",
                    stroke=grand.colour,
                    stroke_width=4.5,
                    opacity=0.24,
                    filter="url(#softglow)",
                ),
            )
        )

    for _, markup in sorted(ground, key=lambda item: item[0]):
        parts.append(markup)

    standing: list[tuple[float, str]] = []
    for grand in grands:
        people = [person for person in city.residents if person.name in grand.people]
        floor = _band(
            place,
            grand,
            people,
            grand.gy + PLATFORM_MARGIN / 2,
            city,
            plaques,
            departments,
            len(standing),
            building_ids,
        )
        # The megastructure rises behind the people it houses.
        landmark = _landmark(place, grand, 6.0)
        standing.append((grand.depth_key - 0.5, landmark))
        standing.append((grand.depth_key, floor))
        label_x, label_y = place(grand.gx + grand.width / 2, grand.gy + grand.depth, 0)
        standing.append(
            (
                grand.depth_key + 0.5,
                f'<text x="{_fmt(label_x)}" y="{_fmt(label_y + 22)}" fill="{grand.colour}" '
                f'font-size="13" font-weight="700" text-anchor="middle" '
                f'filter="url(#neon)">{_escape(_clip(labels.get(grand.name, grand.name), 24))}</text>',
            )
        )

    for _, markup in sorted(standing, key=lambda item: item[0]):
        parts.append(markup)

    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN - 4}" fill="#E6EDF5" font-size="21" '
        f'font-weight="700">{_escape(title)} agent world</text>'
    )
    parts.append(
        f'<text x="{MARGIN}" y="{MARGIN + 16}" fill="#8FA4C4" font-size="12">'
        f"{len(city.residents)} people &#183; {len(city.districts)} departments "
        f"&#183; {len(grands)} grand sectors &#183; isometric 2:1</text>"
    )
    parts.append(
        f'<text x="{MARGIN}" y="{height - 44}" fill="#8FA4C4" font-size="11">'
        "each person runs one sub-sector &#183; sub-sector height = granted tools "
        "+ step budget &#183; red kerb = cannot</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
