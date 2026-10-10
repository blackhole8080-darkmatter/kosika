"""The animation experiment: a scene described in Kosika, checked, and played."""

from __future__ import annotations

import contextlib
import copy
import io
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from kosika.cli import main as cli_main
from kosika.modules import load_program
from kosika.motion import check_scene, to_html, to_svg
from kosika.runtime import run
from kosika.testing import run_tests
from kosika.trace_replay import replay_trace

LOGO = Path(__file__).resolve().parent.parent / "examples" / "motion" / "logo.kos"
SVG = "{http://www.w3.org/2000/svg}"

SCENE: dict[str, Any] = {
    "width": 200,
    "height": 100,
    "seconds": 3,
    "background": "#101010",
    "shapes": [
        {
            "id": "ball",
            "kind": "circle",
            "x": 20,
            "y": 50,
            "size": 20,
            "color": "#e8813a",
            "text": "",
        },
        {
            "id": "word",
            "kind": "text",
            "x": 100,
            "y": 50,
            "size": 18,
            "color": "#ffffff",
            "text": "a < b & c",
        },
    ],
    "moves": [
        {
            "shape": "ball",
            "what": "x",
            "to": 180,
            "start": 0,
            "seconds": 2,
            "ease": "out",
        },
        {
            "shape": "ball",
            "what": "size",
            "to": 10,
            "start": 1,
            "seconds": 1,
            "ease": "both",
        },
        {
            "shape": "ball",
            "what": "size",
            "to": 40,
            "start": 2,
            "seconds": 1,
            "ease": "both",
        },
        {
            "shape": "word",
            "what": "fade",
            "to": 0,
            "start": 0,
            "seconds": 0,
            "ease": "linear",
        },
        {
            "shape": "word",
            "what": "turn",
            "to": 360,
            "start": 1,
            "seconds": 2,
            "ease": "in",
        },
    ],
}


def cli(arguments: list[str]) -> int:
    with (
        unittest.mock.patch("sys.argv", ["kosika", *arguments]),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        return int(cli_main())


def broken(**change: Any) -> list[str]:
    scene = copy.deepcopy(SCENE)
    for path, value in change.items():
        target: Any = scene
        *front, last = path.split("__")
        for part in front:
            target = target[int(part) if part.isdigit() else part]
        target[int(last) if last.isdigit() else last] = value
    return check_scene(scene)


class SceneCheckTests(unittest.TestCase):
    def test_a_good_scene_has_nothing_wrong(self) -> None:
        self.assertEqual(check_scene(SCENE), [])

    def test_each_mistake_is_said_in_words(self) -> None:
        cases = {
            "no shape has that id": broken(moves__0__shape="bal"),
            "use one of: circle, square, ring, text": broken(shapes__0__kind="star"),
            "use one of: x, y, size, turn, fade": broken(moves__0__what="spin"),
            "after the scene's 3 s": broken(moves__0__seconds=5),
            "a fade is from 0 to 1": broken(moves__3__to=2),
            "colour written like": broken(shapes__0__color="orange"),
            "Two shapes are both called 'ball'": broken(shapes__1__id="ball"),
            "width must be a number above 0": broken(width=0),
            "is of kind text and has no text": broken(shapes__1__text=""),
            "use one of: linear, in, out, both": broken(moves__0__ease="bounce"),
            "needs a number for x": broken(shapes__0__x="left"),
        }
        for wanted, found in cases.items():
            with self.subTest(wanted=wanted):
                self.assertTrue(any(wanted in problem for problem in found), found)

    def test_a_scene_that_is_not_a_scene_is_refused_plainly(self) -> None:
        self.assertEqual(len(check_scene("a logo")), 1)
        self.assertIn("at least one shape", check_scene({**SCENE, "shapes": []})[-1])
        with self.assertRaisesRegex(ValueError, "cannot be played"):
            to_svg({**SCENE, "shapes": []})


class PlayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ET.fromstring(to_svg(SCENE))

    def animations(self, shape: str) -> list[ET.Element]:
        group = next(g for g in self.root.iter(f"{SVG}g") if g.get("id") == shape)
        return [
            node
            for node in group.iter()
            if node.tag in (f"{SVG}animate", f"{SVG}animateTransform")
        ]

    def test_it_is_one_well_formed_picture_with_every_shape(self) -> None:
        ids = [g.get("id") for g in self.root.iter(f"{SVG}g") if g.get("id")]
        self.assertEqual(ids, ["ball", "word"])
        self.assertEqual(self.root.get("viewBox"), "0 0 200 100")
        text = next(self.root.iter(f"{SVG}text"))
        # Words are written out safely, not as markup.
        self.assertEqual(text.text, "a < b & c")

    def test_each_move_starts_from_where_the_shape_then_is(self) -> None:
        ball = self.animations("ball")
        slide = next(a for a in ball if a.get("type") == "translate")
        self.assertEqual((slide.get("from"), slide.get("to")), ("20 50", "180 50"))
        self.assertEqual((slide.get("begin"), slide.get("dur")), ("0s", "2s"))
        self.assertEqual(slide.get("fill"), "freeze")
        sizes = [a for a in ball if a.get("type") == "scale"]
        # 20 to 10, then 10 to 40: the second begins where the first ended.
        self.assertEqual(
            [(a.get("from"), a.get("to")) for a in sizes], [("1", "0.5"), ("0.5", "2")]
        )
        # A later move takes over from an earlier one; it is not added to it.
        self.assertTrue(all(a.get("additive") is None for a in ball))

    def test_fading_and_turning(self) -> None:
        word = self.animations("word")
        fade = next(a for a in word if a.tag == f"{SVG}animate")
        self.assertEqual(
            (fade.get("attributeName"), fade.get("from"), fade.get("to")),
            ("opacity", "1", "0"),
        )
        turn = next(a for a in word if a.get("type") == "rotate")
        self.assertEqual((turn.get("from"), turn.get("to")), ("0", "360"))
        self.assertEqual(turn.get("keySplines"), "0.42 0 1 1")

    def test_the_page_fetches_nothing_and_runs_no_script(self) -> None:
        page = to_html(SCENE, "a <scene>")
        self.assertIn("<title>a &lt;scene&gt;</title>", page)
        self.assertNotIn("<script", page)
        self.assertEqual(page.count("http"), 1)  # the SVG namespace, which is a name
        self.assertNotIn("href=", page)
        self.assertNotIn("src=", page)


class LogoProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def test_the_example_passes_its_own_tests_and_makes_a_playable_scene(self) -> None:
        program = load_program(LOGO)
        self.assertTrue(all(result.passed for result in run_tests(program)))
        scene = run(
            program, "main", self.root / "run.jsonl", inputs={"ask": {"dots": 12}}
        )
        self.assertEqual(check_scene(scene), [])
        self.assertEqual((len(scene["shapes"]), len(scene["moves"])), (16, 83))
        # The dots really are on a circle of radius 120 about the centre.
        for move_x, move_y in zip(
            [
                m
                for m in scene["moves"]
                if m["what"] == "x" and m["shape"].startswith("dot")
            ],
            [
                m
                for m in scene["moves"]
                if m["what"] == "y" and m["shape"].startswith("dot")
            ],
        ):
            distance = ((move_x["to"] - 320) ** 2 + (move_y["to"] - 190) ** 2) ** 0.5
            self.assertAlmostEqual(distance, 120, places=5)
        self.assertEqual(replay_trace(self.root / "run.jsonl"), scene)
        ET.fromstring(to_svg(scene))

    def test_the_command_writes_the_file_or_says_what_is_wrong(self) -> None:
        given = self.root / "in.json"
        given.write_text('{"ask": {"dots": 3}}', encoding="utf-8")
        out = self.root / "logo.html"
        self.assertEqual(
            cli(["motion", str(LOGO), "--input", str(given), "--out", str(out)]), 0
        )
        self.assertIn("<svg", out.read_text(encoding="utf-8"))
        bad = self.root / "bad.kos"
        bad.write_text(
            LOGO.read_text(encoding="utf-8").replace('kind: "ring"', 'kind: "star"'),
            encoding="utf-8",
        )
        other = self.root / "bad.html"
        self.assertEqual(
            cli(["motion", str(bad), "--input", str(given), "--out", str(other)]), 1
        )
        self.assertFalse(other.exists())


class TrigTests(unittest.TestCase):
    def test_angles_are_in_degrees_and_give_clean_numbers(self) -> None:
        source = (
            "record Out { values: Number[] }\n"
            "fn f() -> Out {\n"
            "  return {values: [0.sin(), 90.sin(), 180.cos(), 90.cos(), 60.cos(), 2.sqrt(), 9.sqrt()]}\n"
            "}\n"
            "workflow main() -> Out { out = f() }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            values = run(load_program(path), "main", None, inputs={})["values"]
        self.assertEqual(values, [0, 1, -1, 0, 0.5, 1.414213562, 3])


if __name__ == "__main__":
    unittest.main()
