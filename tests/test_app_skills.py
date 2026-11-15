"""The app's Skills section: what it lists, what a request would pick, and
that only a skill Kosika can read is saved."""

from __future__ import annotations

from kosika.app import AppError
from tests.test_app import Fixture

LISTS = (
    "# How a list for a person is written\n"
    "When: todo, shopping list\n"
    "Files: *.txt\n"
    'Check: every line starts with "- "\n'
    "\n"
    "One thing on a line.\n"
)


class SkillsSectionTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.skills = self.root / "desk" / "skills"
        self.skills.mkdir()
        (self.skills / "lists.md").write_text(LISTS, encoding="utf-8")
        (self.skills / "broken.md").write_text("# No words pick this\n\nBody.\n")
        self.studio = self.app()
        self.addCleanup(self.studio.close)

    def place(self, title: str) -> dict:
        return next(
            place for place in self.studio.skills()["places"] if place["title"] == title
        )

    def test_skills_are_listed_by_the_folder_they_serve(self) -> None:
        desk = self.place("desk")
        self.assertEqual(desk["id"], "desk/main.kos")
        self.assertEqual(desk["agents"], ["desk"])
        broken, lists = desk["skills"]
        self.assertEqual(lists["name"], "lists")
        self.assertEqual(lists["summary"], "How a list for a person is written")
        self.assertEqual(lists["when"], ["todo", "shopping list"])
        self.assertEqual(lists["files"], ["*.txt"])
        self.assertEqual(len(lists["checks"]), 1)
        self.assertEqual(lists["text"], LISTS)
        # A note that cannot be read is listed with the reason, not hidden.
        self.assertEqual(broken["name"], "broken")
        self.assertIn("When:", broken["error"])
        self.assertEqual(self.place("writer")["skills"], [])
        # Programs at the top of the folder share one place.
        self.assertIn("broken", self.place("this folder")["agents"])

    def test_a_request_shows_which_skills_it_would_pick(self) -> None:
        (self.skills / "broken.md").unlink()
        got = self.studio.try_skills("desk/main.kos", "write my shopping list")
        self.assertEqual(
            got,
            {"picked": [{"name": "lists", "why": "the request says: shopping list"}]},
        )
        self.assertEqual(
            self.studio.try_skills("desk/main.kos", "hello"), {"picked": []}
        )
        self.assertEqual(
            self.studio.try_skills("writer/main.kos", "a todo"), {"picked": []}
        )

    def test_an_unreadable_note_is_said_when_trying(self) -> None:
        with self.assertRaises(AppError) as caught:
            self.studio.try_skills("desk/main.kos", "a todo")
        self.assertIn("broken.md", str(caught.exception))

    def test_a_skill_is_checked_without_being_written(self) -> None:
        good = self.studio.check_skill("page-forms", LISTS)
        self.assertTrue(good["ok"])
        self.assertEqual(good["when"], ["todo", "shopping list"])
        self.assertFalse((self.skills / "page-forms.md").exists())
        for name in ("", "Has Space", "../up", "a/b", "x" * 41, "-dash"):
            self.assertFalse(self.studio.check_skill(name, LISTS)["ok"], name)
        self.assertIn("When:", self.studio.check_skill("ok", "# T\n\nBody\n")["error"])
        self.assertIn(
            "64 KiB", self.studio.check_skill("ok", LISTS + "x" * 70000)["error"]
        )

    def test_only_a_readable_skill_is_saved(self) -> None:
        made = self.studio.save_skill("writer/main.kos", "notes", LISTS)
        self.assertTrue(made["saved"])
        written = self.root / "writer" / "skills" / "notes.md"
        self.assertEqual(written.read_bytes(), LISTS.encode("utf-8"))
        self.assertEqual(
            [skill["name"] for skill in self.place("writer")["skills"]], ["notes"]
        )
        with self.assertRaises(AppError):
            self.studio.save_skill("writer/main.kos", "notes", "# No When line\n")
        self.assertEqual(written.read_bytes(), LISTS.encode("utf-8"))
        with self.assertRaises(AppError):
            self.studio.save_skill("writer/main.kos", "../../escape", LISTS)
        self.assertFalse((self.root / "escape.md").exists())
        with self.assertRaises(AppError):
            self.studio.save_skill("nobody/main.kos", "notes", LISTS)
