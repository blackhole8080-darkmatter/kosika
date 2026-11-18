"""Studio's Kosika AI page: reading the agent's report, and Keep/Drop's guard."""

from __future__ import annotations

import pytest

from kosika import ai_page


def test_report_is_read_from_either_shape() -> None:
    assert ai_page.report_of("{'ask': 'x', 'passed': 1}\nTrace: run.jsonl") == {"ask": "x", "passed": 1}
    assert ai_page.report_of('Model m answers\n{"ask": "x"}') == {"ask": "x"}
    assert ai_page.report_of("Workflow failed: no") is None


def test_an_empty_ask_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_page, "HOME", tmp_path)
    with pytest.raises(ai_page.AIError):
        ai_page.ask("   ")
    with pytest.raises(ai_page.AIError):
        ai_page.mark({"id": "nope", "verdict": "maybe"})
    assert ai_page.runs()["runs"] == []
