"""Remembered analyses.

What an analysis reports follows from the text, the path it is read under and
the code that reads it, so an answer is kept rather than computed twice. These
tests pin that the kept answer is the one a fresh analysis gives, that a repeat
does not append a second entry, and that a stand-in lexer is never bypassed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kosika import diagnostics
from kosika.diagnostics import analyze

REPO_ROOT = Path(__file__).resolve().parent.parent

CLEAN = 'record Note {\n  text: Text\n}\n\nfn main() -> Text {\n  return "hi"\n}\n'

BROKEN = 'record Note {\n  text: Text\n}\n\nfn main() -> Number {\n  return "hi"\n}\n'

UNPARSEABLE = "fn main() -> Number {\n  return 1\n"

_ANALYSE = (
    "import json, sys\n"
    "from kosika.diagnostics import analyze\n"
    "sources = json.loads(sys.stdin.read())\n"
    "answers = [[item.to_dict() for item in analyze(text, path='sample.kos')] "
    "for text in sources]\n"
    "print(json.dumps(answers, sort_keys=True))\n"
)


def in_a_new_process(
    sources: list[str], cache: str | Path
) -> list[list[dict[str, object]]]:
    """Analyse ``sources`` in a process of its own, remembering under ``cache``."""

    done = subprocess.run(
        [sys.executable, "-c", _ANALYSE],
        input=json.dumps(sources),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "KOSIKA_ANALYSIS_CACHE": str(cache),
            "PYTHONIOENCODING": "utf-8",
        },
        timeout=180,
    )
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert isinstance(found, list) and len(found) == len(sources)
    return found


def store_lines(cache: Path) -> int:
    return sum(
        len([line for line in path.read_text(encoding="utf-8").splitlines() if line])
        for path in sorted(cache.glob("*.jsonl"))
    )


def codes(answer: list[dict[str, object]]) -> list[str]:
    return [str(item["code"]) for item in answer]


def test_a_kept_answer_is_the_answer_a_fresh_analysis_gives(tmp_path: Path) -> None:
    sources = [BROKEN, CLEAN, UNPARSEABLE]
    cached = in_a_new_process(sources, tmp_path / "warm")
    fresh = in_a_new_process(sources, "0")
    assert cached == fresh
    assert codes(cached[0]) and codes(cached[0])[0].startswith("check/")
    assert cached[1] == []
    assert codes(cached[2]) and codes(cached[2])[0].startswith("parse/")


def test_a_repeat_question_does_not_add_a_second_answer(tmp_path: Path) -> None:
    cache = tmp_path / "store"
    first = in_a_new_process([BROKEN], cache)
    assert store_lines(cache) == 1
    second = in_a_new_process([BROKEN], cache)
    assert store_lines(cache) == 1, (
        "a remembered answer was asked for and answered again"
    )
    assert first == second


def test_each_different_text_is_its_own_answer(tmp_path: Path) -> None:
    cache = tmp_path / "store"
    first = in_a_new_process([BROKEN, CLEAN, UNPARSEABLE], cache)
    assert store_lines(cache) == 3
    second = in_a_new_process([BROKEN, CLEAN, UNPARSEABLE], cache)
    assert store_lines(cache) == 3
    assert first == second


def test_the_store_is_named_for_the_code_that_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KOSIKA_ANALYSIS_CACHE", str(tmp_path))
    analyze(BROKEN, path="sample.kos")
    named = [path.name for path in tmp_path.glob("*.jsonl")]
    assert named == [f"{diagnostics.analyser_digest()}.jsonl"]


def test_the_path_a_source_is_read_under_is_part_of_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KOSIKA_ANALYSIS_CACHE", str(tmp_path))
    first = analyze(BROKEN, path="one.kos")
    second = analyze(BROKEN, path="two.kos")
    assert first[0].path == "one.kos"
    assert second[0].path == "two.kos"
    assert store_lines(tmp_path) == 2


def test_turning_the_store_off_writes_nothing(tmp_path: Path) -> None:
    off = tmp_path / "off"
    assert in_a_new_process([BROKEN], "0") == in_a_new_process([BROKEN], "0")
    assert store_lines(off) == 0
    assert not (tmp_path / "0").exists()


def test_a_stand_in_lexer_sees_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A replaced step may answer differently for the same text, so nothing may
    # be replayed while one is in place.
    monkeypatch.setenv("KOSIKA_ANALYSIS_CACHE", str(tmp_path))
    real = diagnostics.lex
    calls: list[str] = []

    def counting(source: str) -> list[object]:
        calls.append(source)
        return real(source)

    monkeypatch.setattr(diagnostics, "lex", counting)
    analyze(BROKEN, path="sample.kos")
    analyze(BROKEN, path="sample.kos")
    assert len(calls) == 2


def test_an_answer_is_a_fresh_list_each_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KOSIKA_ANALYSIS_CACHE", str(tmp_path))
    first = analyze(BROKEN, path="sample.kos")
    second = analyze(BROKEN, path="sample.kos")
    assert first == second
    assert first is not second


def test_a_diagnostic_survives_the_store_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KOSIKA_ANALYSIS_CACHE", str(tmp_path))
    for source in (BROKEN, CLEAN, UNPARSEABLE):
        for diagnostic in analyze(source, path="sample.kos"):
            assert (
                diagnostics._diagnostic_from_json(
                    diagnostics._diagnostic_json(diagnostic)
                )
                == diagnostic
            )
