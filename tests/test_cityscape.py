"""Tests for the isometric agent-cityscape renderer."""

from __future__ import annotations

import unittest
from pathlib import Path

from kosika.city import build_city
from kosika.cityscape import (
    CITYSCAPE_SCHEMA,
    building_floors,
    human_name,
    plan_cityscape,
    render_cityscape_svg,
)

ROOT = Path(__file__).parents[1] / "examples" / "city"

PROGRAM = """
record Note { text: Text }

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
  step first { let seen: Note = ask researcher(text: note.text) return seen }
  step second { let made: Note = ask editor(text: seen.text) return made }
}

swarm Board {
  agents: [researcher, editor]
  workflow: review
  max_rounds: 3
  require approval
}
"""


class CityscapeTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        directory = Path(self._tmp.name)
        (directory / "board.kos").write_text(PROGRAM, encoding="utf-8")
        self.city = build_city(directory)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_human_names_are_stable_and_distinct(self) -> None:
        first = human_name("researcher")
        self.assertEqual(first, human_name("researcher"))
        self.assertNotEqual(first, human_name("editor"))
        # Human names look like people: two capitalized words.
        given, family = first.split(" ")
        self.assertTrue(given[0].isupper() and family[0].isupper())

    def test_floors_follow_capabilities(self) -> None:
        researcher = self.city.resident("researcher")
        assert researcher is not None
        self.assertEqual(building_floors(researcher), 2)  # 1 tool + ground
        editor = self.city.resident("editor")
        assert editor is not None
        self.assertEqual(building_floors(editor), 1)  # no tools

    def test_plan_is_deterministic_and_schema_stamped(self) -> None:
        plan = plan_cityscape(self.city)
        self.assertEqual(plan["schema"], CITYSCAPE_SCHEMA)
        again = plan_cityscape(self.city)
        self.assertEqual(plan, again)
        self.assertEqual(plan["counts"]["buildings"], 2)
        self.assertEqual(plan["counts"]["roads"], 1)

    def test_svg_renders_people_blocks_and_roads(self) -> None:
        svg = render_cityscape_svg(self.city)
        self.assertIn(human_name("researcher"), svg)
        self.assertIn(human_name("editor"), svg)
        self.assertIn("Board", svg)
        # One road connects the two pipeline neighbors.
        self.assertIn("stroke-dasharray", svg)
        # Walls appear because both residents declare prohibitions.
        self.assertIn("#EF4444", svg)

    def test_example_city_renders_every_resident(self) -> None:
        city = build_city(ROOT)
        svg = render_cityscape_svg(city)
        for person in city.residents:
            self.assertIn(human_name(person.name), svg)


if __name__ == "__main__":
    unittest.main()
