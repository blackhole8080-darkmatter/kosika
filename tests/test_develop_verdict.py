"""The verdict has to be able to say no.

A coder whose own tests are green has not necessarily done what it was asked:
the method may never have been written, and a test that passes before the
change proves nothing about it. develop.kos checks both now, and this is that
check, run the way a real run runs it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "examples" / "agents" / "coder" / "daily"
REQUEST = "add a Number method `Number.is_even()`"
FILES = ["kosika/a.py", "tests/test_x.py"]
BEFORE = "def f():\n    return 1\n"
AFTER = "def f():\n    return 1\n\n\ndef is_even(n):\n    return n % 2 == 0\n"
PROVES_IT = (
    "from kosika.a import is_even\n\n\ndef test_is_even():\n"
    "    assert is_even(2) and not is_even(3)\n"
)
PROVES_NOTHING = "from kosika.a import f\n\n\ndef test_f():\n    assert f() == 1\n"


def load_driver():
    spec = importlib.util.spec_from_file_location("daily_driver_for_verdict", DAILY / "daily.py")
    driver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = driver
    spec.loader.exec_module(driver)
    return driver


def judged(work: Path, name: str, after: str | None, test: str) -> dict:
    """A run folder as the driver leaves it: before/ and kosika/ side by side."""
    place = work / name
    for tree in ("before", "kosika"):
        (place / tree / "kosika").mkdir(parents=True)
        (place / tree / "tests").mkdir(parents=True)
        (place / tree / "kosika" / "a.py").write_text(BEFORE, encoding="utf-8")
    if after is not None:
        (place / "kosika" / "kosika" / "a.py").write_text(after, encoding="utf-8")
    (place / "kosika" / "tests" / "test_x.py").write_text(test, encoding="utf-8")
    driver = load_driver()
    driver.WORK = work
    ran = {"state": "done", "said": "The tests pass.", "error": "", "seconds": 3}
    return driver.judge_in_kosika(place, REQUEST, FILES, ran, "2026-11-14")


def failed_checks(result: dict) -> list[dict]:
    return [check for check in result["checks"] if not check["ok"]]


def test_a_change_that_writes_the_method_and_proves_it_passes(tmp_path):
    result = judged(tmp_path, "2026-11-14-1", AFTER, PROVES_IT)
    assert result["passed"] is True
    names = [check["name"] for check in result["checks"]]
    assert any("is_even" in name and "names" in name for name in names)
    assert "the new test fails without the change" in names
    assert "passed its checks" in result["report"]


def test_a_method_that_was_never_written_does_not_pass(tmp_path):
    result = judged(tmp_path, "2026-11-14-2", None, PROVES_IT)
    assert result["passed"] is False
    failed = failed_checks(result)
    assert any("has not got" in check["output"] and "is_even" in check["output"] for check in failed)


def test_a_test_that_passes_without_the_change_does_not_pass(tmp_path):
    result = judged(tmp_path, "2026-11-14-3", AFTER, PROVES_NOTHING)
    assert result["passed"] is False
    failed = failed_checks(result)
    assert any("proves nothing" in check["output"] for check in failed)
