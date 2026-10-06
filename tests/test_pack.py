"""Packs: carry a program to another device and prove it is the same."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from kosika import cli
from kosika.modules import load_program
from kosika.pack import (
    PackError,
    check_pack,
    make_pack,
    program_hash,
    read_pack,
    to_text,
    unpack,
    write_pack,
)

TYPES = """
export [Ticket, Out]
record Ticket { text: Text }
record Out { queue: Text }
"""

BODY = """
model assistant

tool lookup {
  source mcp "crm"
  operation: "find"
  access: observe
  input: Ticket
  output: Out
}

tool refund {
  source mcp "bank"
  operation: "refund"
  access: execute
  input: Ticket
  output: Out
}

agent helper {
  can: [lookup, refund]
  max_steps: 2
}

fn build(label: Choice) -> Out {
  return {queue: label.label}
}

workflow main(ticket: Ticket) -> Out {
  label = classify ticket.text into ["billing", "bug"]
  out   = build(label)
}

test "billing" {
  answers assistant: [{label: "billing", confidence: 0.9}]
  result = main({text: "charged twice"})
  expect result.queue == "billing"
}
"""

MAIN = 'import "shared/types.kos"\n' + BODY
SINGLE = TYPES.replace("export [Ticket, Out]", "") + BODY


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.project = self.root / "project"
        (self.project / "shared").mkdir(parents=True)
        (self.project / "shared" / "types.kos").write_bytes(
            TYPES.replace("\n", "\r\n").encode("utf-8")
        )
        (self.project / "main.kos").write_text(MAIN, encoding="utf-8")
        self.pack = self.root / "helper.kpack"
        write_pack(self.project / "main.kos", self.pack)

    def tampered(self, change: Callable[[dict[str, Any]], None]) -> Path:
        pack = json.loads(self.pack.read_text("utf-8"))
        change(pack)
        path = self.root / "tampered.kpack"
        path.write_text(json.dumps(pack), encoding="utf-8")
        return path

    def test_a_pack_holds_every_module_and_the_program_hash(self) -> None:
        pack = make_pack(self.project / "main.kos")
        self.assertEqual(sorted(pack["modules"]), ["main.kos", "shared/types.kos"])
        self.assertEqual(
            pack["program_hash"], program_hash(load_program(self.project / "main.kos"))
        )

    def test_checking_reports_what_the_device_must_grant(self) -> None:
        report = check_pack(self.pack)
        self.assertEqual(report.needs["models"], ["assistant"])
        self.assertEqual(
            [(t["name"], t["access"]) for t in report.needs["tools"]],
            [("lookup", "observe"), ("refund", "execute")],
        )
        text = to_text(report)
        self.assertIn("tool refund from bank (changes the world)", text)
        self.assertIn("1 included; add --test", text)

    def test_the_tests_inside_a_pack_can_be_run(self) -> None:
        report = check_pack(self.pack, run_tests=True)
        self.assertEqual([t["passed"] for t in report.tests], [True])
        self.assertIn("1 passed, 0 failed", to_text(report))

    def test_unpacking_gives_the_same_program_byte_for_byte(self) -> None:
        target = self.root / "other-device"
        report = unpack(self.pack, target)
        for name in ("main.kos", "shared/types.kos"):
            self.assertEqual(
                (target / name).read_bytes(), (self.project / name).read_bytes()
            )
        self.assertEqual(
            program_hash(load_program(target / "main.kos")), report.program_hash
        )

    def test_unpacking_refuses_a_folder_that_is_not_empty(self) -> None:
        target = self.root / "busy"
        target.mkdir()
        (target / "note.txt").write_text("mine", encoding="utf-8")
        with self.assertRaisesRegex(PackError, "not empty"):
            unpack(self.pack, target)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["note.txt"])

    def test_a_changed_module_is_rejected(self) -> None:
        def change(pack: dict[str, Any]) -> None:
            pack["modules"]["main.kos"] = pack["modules"]["main.kos"].replace(
                '"billing", "bug"', '"billing", "other"'
            )

        with self.assertRaisesRegex(PackError, "does not match its recorded hash"):
            check_pack(self.tampered(change))

    def test_a_changed_module_with_a_fixed_up_hash_fails_the_program_hash(
        self,
    ) -> None:
        def change(pack: dict[str, Any]) -> None:
            text = pack["modules"]["main.kos"].replace(
                '"billing", "bug"', '"billing", "other"'
            )
            pack["modules"]["main.kos"] = text
            pack["module_hashes"]["main.kos"] = sha(text)

        with self.assertRaisesRegex(PackError, "different program"):
            check_pack(self.tampered(change))

    def test_a_module_path_that_escapes_the_folder_is_rejected(self) -> None:
        for bad in ("../evil.kos", "/abs.kos", "C:/x.kos", "a\\b.kos", "notes.txt"):
            with self.subTest(path=bad):

                def change(pack: dict[str, Any], bad: str = bad) -> None:
                    pack["modules"][bad] = "record R { a: Text }"
                    pack["module_hashes"][bad] = "0" * 64

                with self.assertRaisesRegex(PackError, "unsafe"):
                    read_pack(self.tampered(change))

    def test_an_extra_unused_module_is_rejected(self) -> None:
        def change(pack: dict[str, Any]) -> None:
            text = "record Spare { a: Text }"
            pack["modules"]["spare.kos"] = text
            pack["module_hashes"]["spare.kos"] = sha(text)

        with self.assertRaisesRegex(PackError, "does not use"):
            check_pack(self.tampered(change))

    def test_other_files_are_not_packs(self) -> None:
        other = self.root / "x.json"
        other.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(PackError, "Not a kosika.pack/v1 pack"):
            read_pack(other)
        with self.assertRaisesRegex(PackError, "Cannot read pack"):
            read_pack(self.root / "missing.kpack")

    def test_a_pack_carries_no_bindings(self) -> None:
        pack = json.loads(self.pack.read_text("utf-8"))
        self.assertEqual(
            sorted(pack),
            ["entry", "module_hashes", "modules", "needs", "program_hash", "schema"],
        )


class CommandTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kosika", *arguments]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue() + err.getvalue()

    def test_make_check_and_unpack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.kos"
            source.write_text(SINGLE, encoding="utf-8")
            pack = root / "a.kpack"
            code, output = self.invoke("pack", "make", str(source), "--out", str(pack))
            self.assertEqual(code, 0, output)
            self.assertIn("Program  ", output)

            code, output = self.invoke("pack", "check", str(pack), "--test")
            self.assertEqual(code, 0, output)
            self.assertIn("1 passed, 0 failed", output)

            code, output = self.invoke("pack", "check", str(pack), "--json")
            self.assertEqual(json.loads(output)["needs"]["models"], ["assistant"])

            code, output = self.invoke("pack", "unpack", str(pack), str(root / "out"))
            self.assertEqual(code, 0, output)
            self.assertTrue((root / "out" / "main.kos").exists())

            pack.write_text(
                pack.read_text("utf-8").replace("charged twice", "charged thrice"),
                encoding="utf-8",
            )
            code, output = self.invoke("pack", "check", str(pack))
            self.assertEqual(code, 2)
            self.assertIn("Pack rejected", output)


if __name__ == "__main__":
    unittest.main()
