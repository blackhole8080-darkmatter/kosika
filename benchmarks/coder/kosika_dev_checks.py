"""The checks of kosika_dev.json: whether a change to Kosika itself works.

    python kosika_dev_checks.py trim-ends

Each runs in the task's folder, which holds a copy of Kosika, and imports
that copy, never the Kosika this benchmark lives in. A check runs small
Kosika programs through the parser, the checker and the runtime, so a
method only passes when the language itself can use it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

sys.path.insert(0, os.getcwd())

from kosika.checker import check  # noqa: E402
from kosika.parser import parse  # noqa: E402
from kosika.runtime import run  # noqa: E402


def value_of(expression: str, type_name: str) -> Any:
    source = (
        f"record Out {{ value: {type_name} }}\n"
        f"fn f() -> Out {{\nreturn {{value: {expression}}}\n}}\n"
        "workflow main() -> Out { out = f() }\n"
    )
    program = parse(source)
    check(program)
    return run(program, "main", None, inputs={})["value"]


def same(expression: str, type_name: str, expected: Any) -> None:
    got = value_of(expression, type_name)
    assert got == expected and type(got) is type(expected), f"{expression} gave {got!r}, not {expected!r}"


def trim_ends() -> None:
    same('"  ab c \\t\\n".trim_start()', "Text", "ab c \t\n")
    same('"\\n\\t ab c  ".trim_end()', "Text", "\n\t ab c")
    same('"".trim_start()', "Text", "")
    same('"   ".trim_end()', "Text", "")
    same('"abc".trim_start()', "Text", "abc")


def last_index_of() -> None:
    same('"banana".last_index_of("an").or(default: -1)', "Number", 3)
    same('"banana".last_index_of("b").or(default: -1)', "Number", 0)
    same('"banana".last_index_of("x").or(default: -1)', "Number", -1)
    same('"banana".last_index_of("a").exists()', "Bool", True)
    same('"banana".last_index_of("nan").or(default: -1)', "Number", 2)


def scene_fade_in() -> None:
    import pathlib
    import tempfile

    program = (
        'import "std/scene.kos"\n'
        "record Out { what: Text to: Number start: Number seconds: Number ease: Text shape: Text ok: Bool }\n"
        "fn f() -> Out {\n"
        '  let m = fade_in("dot", 1, 2)\n'
        '  let film = scene(100, 100, 4, "#ffffff", [shape("dot", "circle", 10, 10, 5, "#000000")], [hidden("dot"), m])\n'
        "  return {what: m.what, to: m.to, start: m.start, seconds: m.seconds, ease: m.ease, shape: m.shape, ok: scene_problems(film).is_empty()}\n"
        "}\n"
        "workflow main() -> Out { out = f() }\n"
    )
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder) / "check.kos"
        path.write_text(program, encoding="utf-8")
        from kosika.modules import load_program

        got = run(load_program(str(path)), "main", None, inputs={})
    want = {"what": "fade", "to": 1, "start": 1, "seconds": 2, "ease": "out", "shape": "dot", "ok": True}
    for key, value in want.items():
        assert got[key] == value, f"fade_in(\"dot\", 1, 2).{key} is {got[key]!r}, not {value!r}"
    ran = subprocess.run(
        [sys.executable, "-m", "kosika.cli", "test", "kosika/std/scene.kos"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert ran.returncode == 0, ran.stdout[-600:] + ran.stderr[-600:]
    tests = ran.stdout.count("PASS")
    assert tests >= 4, f"scene.kos has {tests} passing tests; it needs one more for fade_in"
    assert "fade_in" in pathlib.Path("kosika/std/scene.kos").read_text(encoding="utf-8").split("test \"", 1)[1], "no test calls fade_in"


CHECKS = {"trim-ends": trim_ends, "last-index-of": last_index_of, "scene-fade-in": scene_fade_in}

if __name__ == "__main__":
    CHECKS[sys.argv[1]]()
