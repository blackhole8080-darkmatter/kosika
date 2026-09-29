"""Tests for the multi-program agent city.

The city owns aggregation only; drawing belongs to :mod:`kosika.design`.
These tests therefore check the folded population and the design manifest
the city hands over, and that the shared renderer draws the walls.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kosika.city import (
    CITY_NAME,
    CITY_SCHEMA,
    PLAZA,
    CityError,
    build_city,
    render_city_svg,
)
from kosika.city_live import render_city_live
from kosika.cityscape import render_cityscape_svg
from kosika.metropolis import render_city_isometric

PROGRAM_WITH_SWARM = """
record Note { text: Text }
record Draft { text: Text }

tool read_docs {
  source mcp "local_mock"
  operation: "search"
  access: observe
  output: Note
}

model scholar { input: Note output: Note }

agent researcher {
  model: scholar
  can: [read_docs]
  cannot: [write_files, arbitrary_shell]
  max_steps: 4
}

agent editor {
  cannot: [arbitrary_shell]
}

workflow review(note: Note) -> Note {
  step step_one {
    let seen: Note = ask researcher(text: note.text)
    return seen
  }
  step step_two {
    let made: Note = ask editor(text: seen.text)
    return made
  }
}

swarm Board {
  agents: [researcher, editor]
  workflow: review
  max_rounds: 3
  require approval
}
"""

FIRST_PROGRAM = """
agent monitor {
  can: [check_disk]
  cannot: [arbitrary_shell]
  max_steps: 2
}
"""

SECOND_PROGRAM = """
agent monitor {
  can: [inspect_structured]
  cannot: [write_files]
  max_steps: 6
}
"""


RESEARCH_LIKE = PROGRAM_WITH_SWARM.replace("Board", "ResearchBoard")


def _write(root: Path, name: str, source: str) -> None:
    (root / name).write_text(source, encoding="utf-8")


def test_city_is_built_from_real_agents(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    city = build_city(tmp_path)
    assert city.project == tmp_path.name
    assert city.name == CITY_NAME
    assert sorted(p.name for p in city.residents) == ["editor", "researcher"]
    assert "read_docs" in city.tools
    assert "Note" in city.records


def test_city_name_defaults_to_maya_and_can_be_overridden(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    assert build_city(tmp_path).name == "Maya"
    assert build_city(tmp_path, name="Elsewhere").name == "Elsewhere"
    # The project stays the source directory; the name is only the city's.
    assert build_city(tmp_path, name="Elsewhere").project == tmp_path.name


def test_every_city_render_is_titled_with_the_city_name(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    city = build_city(tmp_path, name="Elsewhere")
    isometric = render_city_isometric(city)
    assert '<title id="city-title">Elsewhere agent city at night</title>' in isometric
    assert "Elsewhere agent city" in isometric
    assert "Elsewhere —" in render_cityscape_svg(city)
    page = render_city_live(city)
    assert "<title>Elsewhere live city</title>" in page
    assert "<h1>Elsewhere &#183; live city</h1>" in page
    assert city.to_text().startswith("Elsewhere, an agent city for ")
    assert city.to_dict()["name"] == "Elsewhere"


def test_swarm_becomes_a_district_and_members_move_into_it(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    city = build_city(tmp_path)
    district = next(d for d in city.districts if d.name == "Board")
    assert district.kind == "swarm"
    assert district.workflow == "review"
    assert district.max_rounds == 3
    assert district.approval_required is True
    assert district.members == ("editor", "researcher")
    assert city.resident("researcher").district == "Board"


def test_prohibitions_are_carried_into_the_manifest(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    city = build_city(tmp_path)
    assert city.resident("researcher").cannot == ("arbitrary_shell", "write_files")
    agent = next(
        node
        for node in city.to_manifest()["nodes"]
        if node["id"] == "agent.researcher"
    )
    assert agent["cannot"] == ["arbitrary_shell", "write_files"]
    assert agent["can"] == ["read_docs"]
    assert agent["max_steps"] == 4


def test_repeated_agents_merge_and_union_their_capabilities(tmp_path: Path) -> None:
    _write(tmp_path, "one.kos", FIRST_PROGRAM)
    _write(tmp_path, "two.kos", SECOND_PROGRAM)
    city = build_city(tmp_path)
    monitor = city.resident("monitor")
    assert monitor is not None
    assert monitor.can == ("check_disk", "inspect_structured")
    assert monitor.cannot == ("arbitrary_shell", "write_files")
    assert monitor.max_steps == 6
    assert monitor.programs == ("one.kos", "two.kos")


def test_agents_without_a_swarm_land_in_the_plaza(tmp_path: Path) -> None:
    _write(tmp_path, "one.kos", FIRST_PROGRAM)
    city = build_city(tmp_path)
    plaza = next(d for d in city.districts if d.name == PLAZA)
    assert plaza.kind == "open"
    assert plaza.members == ("monitor",)
    assert plaza.approval_required is False


def test_manifest_is_a_valid_design_manifest(tmp_path: Path) -> None:
    """The city must satisfy the design schema it hands rendering to."""
    from kosika.design import validate_design

    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    manifest = validate_design(build_city(tmp_path).to_manifest())
    assert {node["kind"] for node in manifest.nodes} == {
        "swarm",
        "approval",
        "agent",
        "tool",
    }


def test_districts_and_approvals_become_nodes(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    manifest = build_city(tmp_path).to_manifest()
    kinds = [node["kind"] for node in manifest["nodes"]]
    assert kinds.count("swarm") == 1
    # The only swarm requires approval, so it gets an approval node.
    assert kinds.count("approval") == 1
    assert kinds.count("agent") == 2
    edges = {(edge["source"], edge["target"], edge["kind"]) for edge in manifest["edges"]}
    assert ("swarm.Board", "agent.researcher", "contains") in edges
    assert ("swarm.Board", "approval.Board", "requires_approval") in edges


def test_plaza_is_slugged_into_a_legal_identifier(tmp_path: Path) -> None:
    _write(tmp_path, "one.kos", FIRST_PROGRAM)
    manifest = build_city(tmp_path).to_manifest()
    assert any(node["id"] == "swarm.Public_Plaza" for node in manifest["nodes"])


def test_the_pipeline_is_the_order_the_workflow_runs_agents(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    district = next(d for d in build_city(tmp_path).districts if d.name == "Board")
    assert district.pipeline == ("researcher", "editor")


def test_a_pipeline_never_leaves_its_district(tmp_path: Path) -> None:
    """Workflow names are program-local, so resolution must be too.

    Two programs may both define a workflow called ``review``; a swarm must
    get the one from the program that declared it, never the other.
    """
    _write(tmp_path, "alpha.kos", PROGRAM_WITH_SWARM)
    _write(tmp_path, "beta.kos", RESEARCH_LIKE.replace("Board", "Board2"))
    city = build_city(tmp_path)
    for district in city.districts:
        assert set(district.pipeline) <= set(district.members), district.name


def test_programs_sharing_a_basename_do_not_overwrite_each_other(
    tmp_path: Path,
) -> None:
    """``a/research.kos`` and ``b/research.kos`` are different programs."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "research.kos").write_text(RESEARCH_LIKE, encoding="utf-8")
    (tmp_path / "b" / "research.kos").write_text(
        RESEARCH_LIKE.replace("Board", "Board2").replace(
            "researcher", "second_researcher"
        ).replace("editor", "second_editor"),
        encoding="utf-8",
    )
    city = build_city(tmp_path)
    assert city.resident("second_researcher") is not None
    board = next(d for d in city.districts if d.name == "ResearchBoard")
    assert board.pipeline == ("researcher", "editor")


def test_roads_follow_the_pipeline_in_the_manifest(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    manifest = build_city(tmp_path).to_manifest()
    roads = [edge for edge in manifest["edges"] if edge["kind"] == "calls"]
    assert [(edge["source"], edge["target"]) for edge in roads] == [
        ("agent.researcher", "agent.editor")
    ]


def test_manifest_places_nodes_without_overlap(tmp_path: Path) -> None:
    """The metropolitan grid must not stack two buildings in one place."""
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    nodes = build_city(tmp_path).to_manifest()["nodes"]
    boxes = [
        (node["position"]["x"], node["position"]["y"],
         node["size"]["width"], node["size"]["height"])
        for node in nodes
    ]
    for index, (x, y, w, h) in enumerate(boxes):
        for other in boxes[index + 1 :]:
            ox, oy, ow, oh = other
            assert not (x < ox + ow and ox < x + w and y < oy + oh and oy < y + h)


def test_agents_without_a_workflow_have_no_road(tmp_path: Path) -> None:
    """A plaza agent is real but has no declared order, so it draws no road."""
    _write(tmp_path, "one.kos", FIRST_PROGRAM)
    city = build_city(tmp_path)
    plaza = next(d for d in city.districts if d.name == PLAZA)
    assert plaza.pipeline == ()
    assert not [
        edge for edge in city.to_manifest()["edges"] if edge["kind"] == "calls"
    ]


def test_walls_are_drawn_for_every_prohibited_agent(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    svg = render_city_svg(build_city(tmp_path))
    assert svg.count("data-prohibitions=") == 2
    assert ">cannot<" in svg
    assert "arbitrary_shell" in svg
    assert "researcher" in svg


def test_rendering_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    first = render_city_svg(build_city(tmp_path))
    second = render_city_svg(build_city(tmp_path))
    assert first == second
    assert build_city(tmp_path).to_dict()["schema"] == CITY_SCHEMA


def test_svg_is_well_formed_xml_and_both_themes_work(tmp_path: Path) -> None:
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    city = build_city(tmp_path)
    lab = render_city_svg(city, theme="lab")
    light = render_city_svg(city, theme="light")
    assert ET.fromstring(lab).tag.endswith("svg")
    assert ET.fromstring(light).tag.endswith("svg")
    assert lab.count("data-prohibitions=") == light.count("data-prohibitions=")


def test_a_single_program_file_can_be_a_city_root(tmp_path: Path) -> None:
    path = tmp_path / "board.kos"
    _write(tmp_path, "board.kos", PROGRAM_WITH_SWARM)
    city = build_city(path)
    assert sorted(p.name for p in city.residents) == ["editor", "researcher"]


def test_missing_root_and_empty_root_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CityError):
        build_city(tmp_path / "nope")
    with pytest.raises(CityError):
        build_city(tmp_path)
    bad = tmp_path / "bad.kos"
    bad.write_text("this is not a kosika program {{{", encoding="utf-8")
    with pytest.raises(CityError):
        build_city(tmp_path)


def test_a_city_with_no_agents_cannot_be_drawn(tmp_path: Path) -> None:
    (tmp_path / "plain.kos").write_text(
        "fn double(n: Number) -> Number {\n  return n * 2\n}\n",
        encoding="utf-8",
    )
    city = build_city(tmp_path)
    assert city.residents == ()
    with pytest.raises(CityError):
        render_city_svg(city)


def test_a_large_population_renders(tmp_path: Path) -> None:
    """The aggregator must survive a city far bigger than the examples."""
    for index in range(12):
        (tmp_path / f"dept{index}.kos").write_text(
            f"record Note {{ text: Text }}\n"
            f'model m {{ input: Note output: Note }}\n'
            f'tool t{index} {{ source mcp "s" operation: "o" access: observe output: Note }}\n'
            f"agent a{index} {{ model: m can: [t{index}] cannot: [arbitrary_shell] max_steps: 3 }}\n",
            encoding="utf-8",
        )
    city = build_city(tmp_path)
    assert len(city.residents) == 12
    svg = render_city_svg(city)
    assert svg.count("data-prohibitions=") == 12
    assert ET.fromstring(svg).tag.endswith("svg")
