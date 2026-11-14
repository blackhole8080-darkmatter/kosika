"""Skills in the open SKILL.md format: read beside Kosika's own notes, and
Kosika's notes written out so other AI apps can use them too."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika.skills import (
    SkillCatalogError,
    load_open_skill,
    load_skill_note,
    load_skill_notes,
    open_skill_text,
    pick_skills,
)

ROOT = Path(__file__).resolve().parent.parent

OPEN = """---
name: pdf-forms
description: Fill in PDF forms and read their fields. Use when a request mentions a PDF form or fillable fields.
---

Read the fields first, then fill them one by one.
Never flatten the form unless asked.
"""

WITH_HEADING = """---
name: release-notes
description: >
  Write release notes from merged changes,
  grouped by kind.
---

# Release notes, the house way
When: release notes, changelog

Group changes as Added, Fixed, Changed.
"""

NOTE = """# How a small Python file is written
When: python, .py, script
Files: *.py
Check: starts with "\\"\\"\\""

Begin with a docstring.
Keep functions short.
"""


class OpenSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, relative: str, text: str) -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_open_skill_is_read_from_its_front_matter(self) -> None:
        skill = load_open_skill(self.write("pdf-forms/SKILL.md", OPEN))
        self.assertEqual(skill.skill_id, "pdf-forms")
        self.assertTrue(skill.summary.startswith("Fill in PDF forms"))
        self.assertIn("form", skill.keywords)
        self.assertIn("fillable", skill.keywords)
        self.assertNotIn("when", skill.keywords)
        # Without a heading, the first line is know-how, not lost.
        self.assertTrue(skill.instructions.startswith("Read the fields first"))
        self.assertIn("Never flatten", skill.instructions)

    def test_a_when_line_and_a_folded_description_are_kept(self) -> None:
        skill = load_open_skill(self.write("release-notes/SKILL.md", WITH_HEADING))
        self.assertEqual(skill.summary, "Write release notes from merged changes, grouped by kind.")
        self.assertEqual(skill.keywords, ("release notes", "changelog"))
        self.assertTrue(skill.instructions.startswith("# Release notes, the house way"))

    def test_front_matter_and_a_description_are_needed(self) -> None:
        with self.assertRaisesRegex(SkillCatalogError, "must begin with ---"):
            load_open_skill(self.write("a/SKILL.md", "Just text\n"))
        with self.assertRaisesRegex(SkillCatalogError, "needs a description"):
            load_open_skill(self.write("b/SKILL.md", "---\nname: b\n---\nText\n"))

    def test_a_folder_holds_both_kinds_and_both_are_picked(self) -> None:
        self.write("python.md", NOTE)
        self.write("pdf-forms/SKILL.md", OPEN)
        catalog = load_skill_notes(self.tmp)
        self.assertEqual(sorted(s.skill_id for s in catalog), ["pdf-forms", "python"])
        picked = pick_skills(catalog, "please fill this PDF form for me")
        self.assertEqual(picked.ids[:1], ("pdf-forms",))

    def test_a_note_written_out_reads_back_the_same(self) -> None:
        note = load_skill_note(self.write("python.md", NOTE))
        text = open_skill_text(note)
        self.assertTrue(text.startswith("---\nname: python\ndescription: "))
        self.assertIn("Use when: python, .py, script", text)
        again = load_open_skill(self.write("out/python/SKILL.md", text))
        self.assertEqual(again.skill_id, note.skill_id)
        self.assertEqual(again.summary, note.summary)
        self.assertEqual(again.keywords, note.keywords)
        self.assertEqual(again.files, note.files)
        self.assertEqual(again.checks, note.checks)
        self.assertEqual(again.instructions, note.instructions)

    def test_the_cli_exports_a_folder_of_notes(self) -> None:
        self.write("notes/python.md", NOTE)
        out = self.tmp / "exported"
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        done = subprocess.run(
            [sys.executable, "-m", "kosika.cli", "skills-export", str(self.tmp / "notes"), "--out", str(out)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "python/SKILL.md")
        self.assertEqual(load_open_skill(out / "python" / "SKILL.md").keywords, ("python", ".py", "script"))

    def test_the_hands_skills_export_cleanly(self) -> None:
        notes = ROOT / "examples" / "agents" / "hands" / "skills"
        for note in load_skill_notes(notes):
            again = load_open_skill(self.write(f"h/{note.skill_id}/SKILL.md", open_skill_text(note)))
            self.assertEqual((again.summary, again.keywords, again.instructions), (note.summary, note.keywords, note.instructions))


if __name__ == "__main__":
    unittest.main()
