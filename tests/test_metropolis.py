"""Tests for the isometric city renderer.

The projection is easy to get subtly wrong -- a district that forgets its
row offset still renders, it just renders every district on top of the
first -- so the geometry is asserted rather than eyeballed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kosika.city import CityError, build_city
from kosika.metropolis import (
    DOMAINS,
    ROSTER,
    MetroError,
    _domain_for,
    _group_domains,
    _layout_grand_sectors,
    assign_plaques,
    render_city_isometric,
)

BUILDING = re.compile(
    r'<g (data-agent="[^"]*"(?:\s+(?:class|data-[a-z-]+)="[^"]*")*)>(.*?)</g>', re.S)
ATTRIBUTE = re.compile(r'(data-[a-z-]+)="([^"]*)"')
FACE = re.compile(r'<polygon points="([^"]+)"')


def _buildings(svg: str) -> list[dict[str, str]]:
    """Every building group, with its data attributes parsed by name."""
    found = []
    for attributes, body in BUILDING.findall(svg):
        record = dict(ATTRIBUTE.findall(attributes))
        record["body"] = body
        found.append(record)
    return found


def _program(agent: str, tools: int, cannot: list[str], steps: int) -> str:
    tool_lines = "".join(
        f'tool t{index} {{ source mcp "s" operation: "o" access: observe output: Note }}\n'
        for index in range(tools)
    )
    can = ", ".join(f"t{index}" for index in range(tools))
    step_lines = "".join(
        f"  step s{index} {{\n"
        f"    let r{index}: Note = ask {agent}(text: note.text)\n"
        f"    return r{index}\n  }}\n"
        for index in range(steps)
    )
    return (
        "record Note { text: Text }\nmodel m { input: Note output: Note }\n"
        f"{tool_lines}"
        f"agent {agent} {{ model: m can: [{can}] cannot: [{', '.join(cannot)}] "
        f"max_steps: {steps} }}\n"
        f"workflow drive(note: Note) -> Note {{\n{step_lines}}}\n"
    )


def _swarm(name: str, members: list[str], workflow: str) -> str:
    return (
        f"swarm {name} {{ agents: [{', '.join(members)}] workflow: {workflow} "
        "max_rounds: 2 require approval }\n"
    )


def test_roster_has_no_duplicate_names() -> None:
    assert len(set(ROSTER)) == len(ROSTER)


def test_plaques_are_stable_and_unique() -> None:
    first = assign_plaques(["alpha", "beta", "gamma"])
    assert first == assign_plaques(["alpha", "beta", "gamma"])
    assert len(set(first.values())) == 3


def test_plaques_do_not_depend_on_other_agents() -> None:
    """Adding a district must never rename an existing agent."""
    base = assign_plaques(["alpha", "beta"])
    grown = assign_plaques(["alpha", "beta", "gamma", "delta"])
    assert grown["alpha"] == base["alpha"]
    assert grown["beta"] == base["beta"]


def test_plaques_stay_unique_past_the_roster() -> None:
    many = assign_plaques(f"agent{index}" for index in range(len(ROSTER) * 2))
    assert len(set(many.values())) == len(many)


def test_every_agent_becomes_a_person_with_a_subsector(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("alpha", 2, ["arbitrary_shell"], 3) + _swarm("Team", ["alpha"], "drive"),
        encoding="utf-8",
    )
    svg = render_city_isometric(build_city(tmp_path))
    found = _buildings(svg)
    assert len(found) == 1
    resident = found[0]
    assert resident["data-agent"] == "alpha"
    assert resident["data-person"] in ROSTER
    assert "arbitrary_shell" in resident["data-cannot"]
    # The sub-sector it runs, the person standing beside it, and a beacon.
    assert len(FACE.findall(resident["body"])) > 5
    for wall in ("url(#faceleft)", "url(#faceright)"):
        assert wall in resident["body"]
    assert 'class="beacon"' in resident["body"]
    assert "data-department" in resident
    assert "data-domain" in resident


def test_agents_are_people_not_towers(tmp_path: Path) -> None:
    """A person is drawn as a figure with a head, not an extruded block."""
    (tmp_path / "a.kos").write_text(_program("walker", 1, [], 1), encoding="utf-8")
    body = _buildings(render_city_isometric(build_city(tmp_path)))[0]["body"]
    assert "#F4D9BE" in body, "the person should have a skin-toned head"
    assert 'stroke-linecap="round"' in body


def test_sub_sectors_carry_lit_windows(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("lit", 3, [], 4), encoding="utf-8"
    )
    svg = render_city_isometric(build_city(tmp_path))
    body = _buildings(svg)[0]["body"]
    panes = re.findall(r'<polygon points="[^"]+" fill="#FF[0-9A-F]{4}"', body)
    assert panes, "a sub-sector should have lit windows"
    assert 'filter="url(#neon)"' in body


def test_a_kerb_is_drawn_only_where_there_are_prohibitions(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("walled", 1, ["arbitrary_shell"], 1), encoding="utf-8"
    )
    (tmp_path / "b.kos").write_text(
        _program("open_agent", 1, [], 1), encoding="utf-8"
    )
    svg = render_city_isometric(build_city(tmp_path))
    for resident in _buildings(svg):
        cannot = resident["data-cannot"].strip()
        assert ("url(#kerb)" in resident["body"]) == bool(cannot), resident["data-agent"]


def test_domain_tools_are_disjoint() -> None:
    """A tool must not claim two domains, or placement would be a guess."""
    seen: set[str] = set()
    for _, _, tools in DOMAINS:
        assert not (seen & tools), sorted(seen & tools)
        seen |= set(tools)
    assert len(seen) > 0


def test_a_person_lands_in_the_domain_their_tools_imply() -> None:
    """Placement reads the granted capability, not the org chart."""
    city = build_city(Path("examples"))
    groups = _group_domains(city)
    placed = {name: domain for domain, _, people in groups for name in people}
    assert len(placed) == len(city.residents)
    for person in city.residents:
        department = next(
            d.name for d in city.districts if person.name in d.members)
        assert placed[person.name] == _domain_for(person, department)


def test_every_person_is_placed_exactly_once(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("alpha", 1, ["arbitrary_shell"], 1)
        + _swarm("Team", ["alpha"], "drive"), encoding="utf-8"
    )
    city = build_city(tmp_path)
    groups = _group_domains(city)
    placed = [name for _, _, people in groups for name in people]
    assert sorted(placed) == sorted(person.name for person in city.residents)
    assert len(placed) == len(set(placed))


def test_a_person_with_no_tools_falls_back_to_their_department() -> None:
    """The editor grants no tools, so the department is the honest answer."""
    city = build_city(Path("examples"))
    editor = city.resident("editor")
    assert editor is not None
    assert not editor.can
    assert _domain_for(editor, "EditorialBoard") == "Arts & Editorial"


def test_grand_sectors_do_not_overlap(tmp_path: Path) -> None:
    """Megastructures that collided would bury a domain whole."""
    for index in range(6):
        (tmp_path / f"d{index}.kos").write_text(
            _program(f"agent{index}", 1, ["arbitrary_shell"], 1)
            + _swarm(f"Team{index}", [f"agent{index}"], "drive"),
            encoding="utf-8",
        )
    city = build_city(tmp_path)
    groups = _group_domains(city)
    grands, _, _ = _layout_grand_sectors(groups)
    for west in grands:
        for east in grands:
            if west.name == east.name:
                continue
            apart_x = max(west.gx, east.gx) - min(
                west.gx + west.width, east.gx + east.width)
            apart_y = max(west.gy, east.gy) - min(
                west.gy + west.depth, east.gy + east.depth)
            assert apart_x > 0 or apart_y > 0, (west.name, east.name)


def test_every_domain_gets_a_landmark(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("alpha", 1, ["arbitrary_shell"], 1)
        + _swarm("Team", ["alpha"], "drive"), encoding="utf-8"
    )
    svg = render_city_isometric(build_city(tmp_path))
    assert 'data-kind="' in svg
    assert 'data-grand="' in svg


def test_subsector_height_encodes_capability(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("tall_one", 4, ["arbitrary_shell"], 8), encoding="utf-8"
    )
    (tmp_path / "b.kos").write_text(
        _program("short_one", 1, ["arbitrary_shell"], 2), encoding="utf-8"
    )
    storeys = {
        resident["data-agent"]: float(resident["data-storeys"])
        for resident in _buildings(render_city_isometric(build_city(tmp_path)))
    }
    assert storeys["tall_one"] > storeys["short_one"]


def test_avatars_can_walk_to_the_avenue(tmp_path: Path) -> None:
    """Each figure needs a walk target the live page can translate it to."""
    (tmp_path / "a.kos").write_text(
        _program("walker", 1, [], 1) + _swarm("Team", ["walker"], "drive"),
        encoding="utf-8",
    )
    resident = _buildings(render_city_isometric(build_city(tmp_path)))[0]
    assert float(resident["data-walk-dx"]) != 0 or float(resident["data-walk-dy"]) != 0


def test_people_stand_inside_their_domain(tmp_path: Path) -> None:
    """A person must belong to the megastructure that houses their field."""
    (tmp_path / "a.kos").write_text(
        _program("alpha", 1, ["arbitrary_shell"], 1)
        + _swarm("Team", ["alpha"], "drive"), encoding="utf-8"
    )
    resident = _buildings(render_city_isometric(build_city(tmp_path)))[0]
    assert resident["data-domain"], "a person must belong to a knowledge domain"
    assert resident["data-department"] == "Team"


def test_rendering_is_deterministic_and_well_formed(tmp_path: Path) -> None:
    (tmp_path / "a.kos").write_text(
        _program("alpha", 2, ["arbitrary_shell"], 2)
        + _swarm("Team", ["alpha"], "drive"),
        encoding="utf-8",
    )
    city = build_city(tmp_path)
    first = render_city_isometric(city)
    assert first == render_city_isometric(build_city(tmp_path))
    assert ET.fromstring(first).tag.endswith("svg")


def test_a_city_with_no_agents_cannot_be_drawn(tmp_path: Path) -> None:
    (tmp_path / "plain.kos").write_text(
        "fn double(n: Number) -> Number {\n  return n * 2\n}\n", encoding="utf-8"
    )
    with pytest.raises((MetroError, CityError)):
        render_city_isometric(build_city(tmp_path))
