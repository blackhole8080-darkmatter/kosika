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

from kosika import app as studio
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
            "use one of: linear, in, out, both, bounce, back": broken(
                moves__0__ease="wobble"
            ),
            "needs a color written like": broken(moves__0__what="color"),
            "repeats 2.5 times": broken(moves__0__repeat=2.5),
            "ends at 4 s, after the scene's 3 s": broken(moves__0__repeat=2),
            "repeats without end and lasts no time": broken(moves__3__repeat=0),
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
        self.assertEqual(slide.get("values"), "20 0;180 0")
        self.assertEqual((slide.get("begin"), slide.get("dur")), ("0s", "2s"))
        self.assertEqual(slide.get("fill"), "freeze")
        sizes = [a for a in ball if a.get("type") == "scale"]
        # 20 to 10, then 10 to 40: the second begins where the first ended.
        self.assertEqual([a.get("values") for a in sizes], ["1;0.5", "0.5;2"])
        # A later move takes over from an earlier one; it is not added to it.
        self.assertTrue(all(a.get("additive") is None for a in ball))

    def test_fading_and_turning(self) -> None:
        word = self.animations("word")
        fade = next(a for a in word if a.tag == f"{SVG}animate")
        self.assertEqual(
            (fade.get("attributeName"), fade.get("values")), ("opacity", "1;0")
        )
        turn = next(a for a in word if a.get("type") == "rotate")
        self.assertEqual(turn.get("values"), "0;360")
        self.assertEqual(turn.get("keySplines"), "0.42 0 1 1")

    def played(self, *moves: dict[str, Any]) -> list[ET.Element]:
        scene = {**SCENE, "moves": list(moves)}
        self.assertEqual(check_scene(scene), [])
        self.root = ET.fromstring(to_svg(scene))
        return self.animations("ball")

    def test_going_across_and_going_down_do_not_undo_each_other(self) -> None:
        across = {"shape": "ball", "what": "x", "to": 180, "start": 0, "seconds": 2}
        down = {"shape": "ball", "what": "y", "to": 90, "start": 0, "seconds": 2}
        self.played(across, down)
        outer = next(g for g in self.root.iter(f"{SVG}g") if g.get("id") == "ball")
        inner = outer.find(f"{SVG}g")
        assert inner is not None
        # Each has a group of its own: x on the outer one, y on the next.
        self.assertEqual(outer.get("transform"), "translate(20 0)")
        self.assertEqual(inner.get("transform"), "translate(0 50)")
        self.assertEqual(
            outer.find(f"{SVG}animateTransform").get("values"), "20 0;180 0"
        )  # type: ignore[union-attr]
        self.assertEqual(
            inner.find(f"{SVG}animateTransform").get("values"), "0 50;0 90"
        )  # type: ignore[union-attr]

    def test_a_bounce_overshoots_and_settles(self) -> None:
        (move,) = self.played(
            {
                "shape": "ball",
                "what": "x",
                "to": 120,
                "start": 0,
                "seconds": 1,
                "ease": "bounce",
            }
        )
        self.assertEqual(move.get("values"), "20 0;132 0;115 0;120 0")
        self.assertEqual(move.get("keyTimes"), "0;0.6;0.82;1")
        self.assertEqual(len(str(move.get("keySplines")).split(";")), 3)

    def test_there_and_back_leaves_the_shape_where_it_was(self) -> None:
        pulse, after = self.played(
            {
                "shape": "ball",
                "what": "size",
                "to": 40,
                "start": 0,
                "seconds": 1,
                "ease": "back",
            },
            {"shape": "ball", "what": "size", "to": 10, "start": 1, "seconds": 1},
        )
        self.assertEqual(pulse.get("values"), "1;2;1")
        # The next move starts from the size the shape came back to.
        self.assertEqual(after.get("values"), "1;0.5")

    def test_a_move_can_repeat_a_number_of_times_or_without_end(self) -> None:
        twice, forever = self.played(
            {
                "shape": "ball",
                "what": "turn",
                "to": 360,
                "start": 0,
                "seconds": 1,
                "repeat": 2,
            },
            {
                "shape": "ball",
                "what": "fade",
                "to": 0.3,
                "start": 0,
                "seconds": 1,
                "ease": "back",
                "repeat": 0,
            },
        )
        self.assertEqual(twice.get("repeatCount"), "2")
        self.assertEqual(forever.get("repeatCount"), "indefinite")
        once = self.played(
            {"shape": "ball", "what": "turn", "to": 9, "start": 0, "seconds": 1}
        )
        self.assertIsNone(once[0].get("repeatCount"))

    def test_a_shape_can_change_colour(self) -> None:
        (move,) = self.played(
            {
                "shape": "ball",
                "what": "color",
                "to": 0,
                "color": "#00ff00",
                "start": 1,
                "seconds": 1,
            }
        )
        self.assertEqual(
            (move.get("attributeName"), move.get("values")), ("fill", "#e8813a;#00ff00")
        )
        # The colour change is on the shape itself, not on a group.
        circle = next(self.root.iter(f"{SVG}circle"))
        self.assertIs(circle.find(f"{SVG}animate"), move)

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
        self.assertEqual((len(scene["shapes"]), len(scene["moves"])), (16, 82))
        self.assertEqual(
            {m["ease"] for m in scene["moves"]}, {"out", "linear", "back", "bounce"}
        )
        self.assertEqual(sum(1 for m in scene["moves"] if m["repeat"] == 0), 1)
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


class StudioSceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = studio.App(LOGO.parent)

    def test_a_program_that_describes_a_scene_gets_a_scene_tab(self) -> None:
        view = studio.describe(self.app.agent("logo.kos"), detail=True)
        self.assertTrue(view["scene"])
        self.assertIn('"dots": 12', view["example_input"])
        researcher = LOGO.parent.parent / "agents" / "researcher"
        other = studio.describe(studio.App(researcher).agent("main.kos"), detail=True)
        self.assertFalse(other["scene"])

    def test_studio_gives_the_picture_or_the_problems(self) -> None:
        made = self.app.scene("logo.kos", {"ask": {"dots": 6}})
        self.assertTrue(made["ok"])
        self.assertEqual((made["shapes"], made["moves"], made["seconds"]), (10, 46, 5))
        ET.fromstring(made["svg"])
        self.assertIn("<svg", made["page"])
        too_many = self.app.scene("logo.kos", {"ask": {"dots": 500}})
        self.assertFalse(too_many["ok"])
        self.assertIn("at most 400 shapes", too_many["problems"][0])
        with self.assertRaises(studio.AppError):
            self.app.scene("logo.kos", {"wrong": 1})

    def test_the_page_can_play_scrub_and_save_a_scene(self) -> None:
        page = studio.PAGE.read_text(encoding="utf-8")
        for needed in (
            '"Scene": sceneTab',
            "/api/scene",
            "setCurrentTime",
            "Save as a web file",
            "Design it",
        ):
            self.assertIn(needed, page)
        self.assertNotIn("innerHTML", page)

    def test_studios_own_mark_is_a_kosika_program_that_is_run(self) -> None:
        self.assertTrue(all(r.passed for r in run_tests(load_program(studio.BRAND))))
        brand = self.app.brand()
        self.assertTrue(brand["ok"], brand)
        root = ET.fromstring(brand["svg"])
        ids = [g.get("id") for g in root.iter(f"{SVG}g") if g.get("id")]
        # Halo, photon ring, twelve particles, the core, then the K.
        self.assertEqual(len(ids), 16)
        self.assertEqual(ids[-2:], ["core", "letter"])
        # It sits on Studio's own background, so it brings none of its own.
        self.assertEqual(list(root.findall(f"{SVG}rect")), [])
        self.assertIn("<rect", to_svg(SCENE).split("\n")[1])
        # Made once, then kept.
        self.assertIs(self.app.brand(), brand)
        page = studio.PAGE.read_text(encoding="utf-8")
        self.assertIn("/api/brand", page)
        self.assertIn("prefers-reduced-motion", page)

    def test_the_page_draws_a_run_as_a_moving_diagram(self) -> None:
        page = studio.PAGE.read_text(encoding="utf-8")
        for needed in (
            "function runStage(agent)",
            "stage.apply(event)",
            "stage.finish(failed === true)",
            # It must keep count and finish when the page is not being drawn.
            "setTimeout(() => { over = true;",
            'class: "lead clamp"',
        ):
            self.assertIn(needed, page)
        # Still no web address anywhere in the page, and nothing set as markup.
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("innerHTML", page)

    def test_the_page_can_replay_a_run_switch_theme_and_jump_anywhere(self) -> None:
        page = studio.PAGE.read_text(encoding="utf-8")
        for needed in (
            "function replayPanel(agent, story)",
            "Nothing runs and no model is asked.",
            # A chosen theme wins over the computer's setting, both ways.
            ':root:not([data-theme="light"])',
            ':root[data-theme="dark"]',
            'localStorage.setItem("kosika-theme"',
            "async function openJump()",
            'event.key.toLowerCase() === "k"',
        ):
            self.assertIn(needed, page)
        # Each set of dark colours is written for both cases: once for the
        # computer's setting, once for a theme the person chose. The Cinema
        # look brought its own pair, so this asks for both cases, not a
        # particular number of them.
        self.assertGreaterEqual(page.count(':root[data-theme="dark"] {'), 2)
        self.assertGreaterEqual(page.count(':root:not([data-theme="light"]) {'), 2)

    def test_a_scene_a_model_took_part_in_is_checked_and_drawn_the_same(self) -> None:
        drawn = self.app.draw_scene("logo.kos", SCENE)
        self.assertTrue(drawn["ok"])
        self.assertEqual(drawn["shapes"], 2)
        refused = self.app.draw_scene("logo.kos", {**SCENE, "shapes": []})
        self.assertEqual(refused["ok"], False)
        self.assertFalse(self.app.draw_scene("logo.kos", "not a scene")["ok"])

    def test_every_pattern_of_the_animator_plays(self) -> None:
        animator = LOGO.parent.parent / "agents" / "animator" / "main.kos"
        source = animator.read_text(encoding="utf-8") + (
            "\nworkflow made(plan: Plan) -> Scene {\n  scene = build(plan)\n}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            program = load_program(path)
            for pattern, count in (
                ("ring", 18),
                ("grid", 35),
                ("wave", 28),
                ("burst", 48),
                ("ring", 3),
                ("grid", 60),
                ("wave", 3),
            ):
                with self.subTest(pattern=pattern, count=count):
                    plan = {
                        "pattern": pattern,
                        "count": count,
                        "color": "#e8813a",
                        "accent": "#f4e9df",
                        "background": "#1a1410",
                        "title": "A title",
                        # Shorter than any pattern needs: the scene must
                        # be made long enough all the same.
                        "seconds": 3,
                    }
                    scene = run(program, "made", None, inputs={"plan": plan})
                    self.assertEqual(check_scene(scene), [])
                    self.assertGreaterEqual(len(scene["shapes"]), count + 1)
                    ET.fromstring(to_svg(scene))


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
