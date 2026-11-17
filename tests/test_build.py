"""Building a project: what is written, and that another machine agrees.

The property worth holding is the one a pack already promises one level up: a
build is named by its content. So these tests build the same project twice, and
then build a copy of it in another folder with its own signing key, and hold
all three to the same hash.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.build import BuildError, build, manifest_payload
from kosika.modules import load_program
from kosika.project import ProjectError, init_project, read_project, read_toml
from kosika.signature import verify

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "closures.kos"


def run_cli(*arguments: str) -> tuple[int, str, str]:
    """Run the command line the way a person does, and collect what it said."""

    out, err = io.StringIO(), io.StringIO()
    with (
        patch.object(sys, "argv", ["kosika", *arguments]),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = cli.main()
    return code, out.getvalue(), err.getvalue()


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name) / "project"
        self.folder.mkdir()
        shutil.copyfile(SAMPLE, self.folder / "main.kos")
        self.workflow = load_program(self.folder / "main.kos").workflows[0].name
        self.write_project()

    def write_project(
        self, workflow: str | None = None, modules: tuple[str, ...] = ()
    ) -> None:
        lines = ['name = "demo"', 'entry = "main.kos"']
        if modules:
            quoted = ", ".join(f'"{name}"' for name in modules)
            lines.append(f"modules = [{quoted}]")
        lines.append("")
        for name in ("pack", "quiet", "check"):
            lines += [
                f"[targets.{name}]",
                f'workflow = "{workflow or self.workflow}"',
                'output = "pack"',
                "",
            ]
        (self.folder / "kosika.toml").write_text("\n".join(lines), encoding="utf-8")

    def test_every_target_is_built_and_the_pack_is_written(self) -> None:
        built = build(self.folder)
        self.assertTrue(built.changed)
        self.assertEqual(len(built.build_hash), 64)
        self.assertEqual(built.targets, ("pack", "quiet", "check"))
        self.assertEqual(built.files, ("program.kpack",))
        self.assertEqual(built.out, self.folder / "build")
        self.assertIsNotNone(built.signer)
        manifest = json.loads((built.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "kosika.build/v1")
        self.assertEqual(manifest["project"], "demo")
        self.assertEqual(manifest["program_hash"], built.program_hash)
        # What was written is the pack, carrying the program the project names.
        packed = json.loads((built.out / "program.kpack").read_text(encoding="utf-8"))
        self.assertEqual(packed["schema"], "kosika.pack/v1")
        self.assertEqual(packed["program_hash"], built.program_hash)

    def test_building_again_writes_nothing(self) -> None:
        first = build(self.folder)
        written = (first.out / "manifest.json").read_bytes()
        second = build(self.folder)
        self.assertFalse(second.changed)
        self.assertEqual(second.build_hash, first.build_hash)
        self.assertEqual((second.out / "manifest.json").read_bytes(), written)

    def test_another_machine_agrees_on_the_hash_and_not_the_signer(self) -> None:
        first = build(self.folder)
        elsewhere = Path(self._dir.name) / "other"
        shutil.copytree(
            self.folder,
            elsewhere,
            ignore=shutil.ignore_patterns("build", ".kosika"),
        )
        second = build(elsewhere)
        self.assertTrue(second.changed)
        self.assertEqual(second.build_hash, first.build_hash)
        self.assertNotEqual(second.signer, first.signer)
        self.assertEqual(
            (second.out / "program.kpack").read_bytes(),
            (first.out / "program.kpack").read_bytes(),
        )

    def test_the_signature_covers_what_was_built(self) -> None:
        built = build(self.folder)
        manifest = json.loads((built.out / "manifest.json").read_text(encoding="utf-8"))
        signature = manifest["signature"]
        public = bytes.fromhex(signature["public_key"])
        made = bytes.fromhex(signature["signature"])
        self.assertTrue(verify(public, manifest_payload(manifest), made))
        tampered = dict(manifest)
        tampered["program_hash"] = "0" * 64
        self.assertFalse(verify(public, manifest_payload(tampered), made))

    def test_a_target_naming_a_missing_workflow_is_refused(self) -> None:
        self.write_project(workflow="no_such_workflow")
        with self.assertRaises(BuildError) as caught:
            build(self.folder)
        self.assertIn("no_such_workflow", str(caught.exception))
        self.assertFalse((self.folder / "build").exists())

    def test_a_program_that_does_not_check_is_refused(self) -> None:
        (self.folder / "main.kos").write_text("workflow main( {\n", encoding="utf-8")
        with self.assertRaises(BuildError):
            build(self.folder)
        self.assertFalse((self.folder / "build").exists())

    def test_a_declared_module_the_program_does_not_load_is_refused(self) -> None:
        self.write_project(modules=("ghost.kos",))
        with self.assertRaises(BuildError) as caught:
            build(self.folder)
        self.assertIn("ghost.kos", str(caught.exception))

    def test_an_unknown_output_shape_is_refused(self) -> None:
        (self.folder / "kosika.toml").write_text(
            'name = "demo"\nentry = "main.kos"\n\n[targets.exe]\nworkflow = "main"\n'
            'output = "binary"\n',
            encoding="utf-8",
        )
        with self.assertRaises(ProjectError) as caught:
            read_project(self.folder)
        self.assertIn("binary", str(caught.exception))

    def test_the_cli_builds_and_prints_the_manifest(self) -> None:
        code, printed, _ = run_cli("build", str(self.folder), "--json")
        self.assertEqual(code, 0)
        manifest = json.loads(printed)
        self.assertEqual(manifest["schema"], "kosika.build/v1")
        self.assertTrue(manifest["changed"])
        self.assertEqual(manifest["targets"][0]["name"], "pack")

    def test_the_cli_reports_a_broken_project_without_a_traceback(self) -> None:
        (self.folder / "kosika.toml").unlink()
        code, _, complaint = run_cli("build", str(self.folder))
        self.assertEqual(code, 2)
        self.assertIn("kosika.toml", complaint)


class InitTests(unittest.TestCase):
    """Starting a project: what a folder gets, and what it refuses to overwrite."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name) / "Greeter App"
        self.folder.mkdir()

    def test_an_empty_folder_becomes_a_project_that_builds(self) -> None:
        started = init_project(self.folder)
        self.assertEqual(started.project.entry, "main.kos")
        self.assertEqual(
            [path.name for path in started.written], ["main.kos", "kosika.toml"]
        )
        self.assertEqual(started.project.name, "greeter-app")
        self.assertEqual(
            [(target.name, target.workflow) for target in started.project.targets],
            [("main", started.workflow)],
        )
        built = build(self.folder)
        self.assertTrue(built.changed)

    def test_a_program_that_is_already_here_is_used_and_left_alone(self) -> None:
        shutil.copyfile(SAMPLE, self.folder / "pipeline.kos")
        before = (self.folder / "pipeline.kos").read_bytes()
        started = init_project(self.folder)
        self.assertEqual(started.project.entry, "pipeline.kos")
        self.assertEqual([path.name for path in started.written], ["kosika.toml"])
        self.assertEqual((self.folder / "pipeline.kos").read_bytes(), before)
        written = (self.folder / "kosika.toml").read_text(encoding="utf-8")
        self.assertIn(f'workflow = "{started.workflow}"', written)

    def test_two_programs_and_no_main_kos_is_refused(self) -> None:
        shutil.copyfile(SAMPLE, self.folder / "a.kos")
        shutil.copyfile(SAMPLE, self.folder / "b.kos")
        with self.assertRaises(ProjectError) as caught:
            init_project(self.folder)
        self.assertIn("main.kos", str(caught.exception))
        self.assertFalse((self.folder / "kosika.toml").exists())

    def test_an_existing_project_file_is_not_replaced_without_asking(self) -> None:
        init_project(self.folder)
        written = (self.folder / "kosika.toml").read_bytes()
        with self.assertRaises(ProjectError) as caught:
            init_project(self.folder)
        self.assertIn("--force", str(caught.exception))
        self.assertEqual((self.folder / "kosika.toml").read_bytes(), written)
        replaced = init_project(self.folder, force=True)
        self.assertEqual(replaced.project.name, "greeter-app")

    def test_a_folder_that_is_not_there_is_refused(self) -> None:
        with self.assertRaises(ProjectError):
            init_project(Path(self._dir.name) / "missing")

    def test_the_cli_starts_a_project_then_builds_it(self) -> None:
        code, printed, complaint = run_cli("init", str(self.folder), "--json")
        self.assertEqual(code, 0, complaint)
        started = json.loads(printed)
        self.assertEqual(started["written"], ["main.kos", "kosika.toml"])
        self.assertEqual(started["project"], "greeter-app")
        code, printed, complaint = run_cli("build", str(self.folder), "--json")
        self.assertEqual(code, 0, complaint)
        self.assertTrue(json.loads(printed)["changed"])

    def test_the_cli_refuses_to_start_twice(self) -> None:
        init_project(self.folder)
        code, _, complaint = run_cli("init", str(self.folder))
        self.assertEqual(code, 2)
        self.assertIn("already here", complaint)


class ProjectFileTests(unittest.TestCase):
    def test_a_missing_project_file_says_what_to_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ProjectError) as caught:
                read_project(Path(folder))
        self.assertIn("kosika.toml", str(caught.exception))


class TomlSubsetTests(unittest.TestCase):
    """The reader used on 3.10, held to the same file the current one reads."""

    def test_tables_lists_strings_comments_and_booleans(self) -> None:
        data = read_toml(
            "# demo\n"
            'name = "demo"   # the project\n'
            "entry = 'main.kos'\n"
            'modules = ["lib/a.kos", "lib/b.kos"]\n'
            "[targets.pack]\n"
            'workflow = "main"\n'
            "[policy]\n"
            "allow_shell = false\n"
            "retries = 3\n"
        )
        self.assertEqual(data["name"], "demo")
        self.assertEqual(data["entry"], "main.kos")
        self.assertEqual(data["modules"], ["lib/a.kos", "lib/b.kos"])
        self.assertEqual(data["targets"]["pack"]["workflow"], "main")
        self.assertIs(data["policy"]["allow_shell"], False)
        self.assertEqual(data["policy"]["retries"], 3)

    def test_a_line_without_key_equals_value_is_refused(self) -> None:
        with self.assertRaises(ProjectError):
            read_toml("name\n")

    def test_a_key_set_twice_is_refused(self) -> None:
        with self.assertRaises(ProjectError):
            read_toml('name = "a"\nname = "b"\n')

    def test_the_same_file_reads_the_same_way_as_tomllib(self) -> None:
        text = (
            'name = "demo"\nentry = "main.kos"\n\n[targets.pack]\nworkflow = "main"\n'
        )
        from kosika.project import load_toml

        self.assertEqual(read_toml(text), load_toml(text))


if __name__ == "__main__":
    unittest.main()
