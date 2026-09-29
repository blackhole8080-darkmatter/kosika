"""Tests for agent prohibitions in design manifests and renders.

An agent's ``cannot`` list had no representation in a design at all. These
tests pin the behaviour that now carries it, including the cases where no
wall should be drawn.
"""

from __future__ import annotations

import re

from kosika.design import (
    PROHIBITION_LABEL,
    build_design_from_program,
    render_design_svg,
)
from kosika.parser import parse as parse_program

PROGRAM = """
record Note { text: Text }
model m { input: Note output: Note }
tool t {
  source mcp "s"
  operation: "o"
  access: observe
  output: Note
}
agent worker {
  model: m
  can: [t]
  cannot: [arbitrary_shell, write_files]
  max_steps: 5
}
"""

BARE_AGENT = """
record Note { text: Text }
model m { input: Note output: Note }
agent bare { model: m }
"""

NO_AGENT = """
record Note { text: Text }
fn double(n: Number) -> Number {
  return n * 2
}
"""


def _design(source: str):
    return build_design_from_program(parse_program(source), "d", "D")


def test_agent_nodes_carry_capabilities_and_ceiling() -> None:
    agent = next(node for node in _design(PROGRAM).nodes if node["kind"] == "agent")
    assert agent["can"] == ["t"]
    assert agent["cannot"] == ["arbitrary_shell", "write_files"]
    assert agent["max_steps"] == 5


def test_svg_draws_one_wall_per_prohibited_agent() -> None:
    svg = render_design_svg(_design(PROGRAM))
    assert svg.count("data-prohibitions=") == 1
    assert 'id="prohibition"' in svg
    # The full list stays machine-readable on the node even though the drawn
    # caption is shortened to fit a 160px box.
    assert 'data-prohibitions="arbitrary_shell, write_files"' in svg
    caption = re.search(r"walls: ([^<]*)</text>", svg).group(1)
    assert caption.startswith("arbitrary_shell, write")
    assert caption.endswith("…")
    assert len(caption) == PROHIBITION_LABEL
    assert ">cannot<" in svg


def test_walls_appear_in_every_theme() -> None:
    design = _design(PROGRAM)
    for theme in ("lab", "light"):
        svg = render_design_svg(design, theme=theme)
        assert svg.count("data-prohibitions=") == 1, theme
        assert ">cannot<" in svg, theme


def test_an_agent_with_no_prohibitions_draws_no_wall() -> None:
    svg = render_design_svg(_design(BARE_AGENT))
    assert svg.count("data-prohibitions=") == 0
    assert ">cannot<" not in svg


def test_a_design_without_agents_draws_no_wall_or_legend_entry() -> None:
    svg = render_design_svg(_design(NO_AGENT))
    assert svg.count("data-prohibitions=") == 0
    assert ">cannot<" not in svg


def test_rendering_stays_deterministic() -> None:
    design = _design(PROGRAM)
    assert render_design_svg(design) == render_design_svg(design)
