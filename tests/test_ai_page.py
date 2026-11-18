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


def _zip_of(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as packed:
        for name, text in files.items():
            packed.writestr("kosika-agents-main/" + name, text)
    return buffer.getvalue()


def test_kosika_ai_keeps_its_own_copy_of_kosika_agents_main(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_page, "HOME", tmp_path)
    monkeypatch.delenv("KOSIKA_AI_AGENTS_HOME", raising=False)
    asked = []
    monkeypatch.setattr(ai_page, "_download", lambda url: asked.append(url) or _zip_of({"agents/ai/main.kos": "v1"}))
    assert ai_page.fetch_agents() == ""
    assert ai_page.ai_program().read_text() == "v1"
    assert ai_page.ai_agents() == tmp_path / "agents"
    # A fresh copy is not fetched again; an old one is, when asked.
    assert ai_page.fetch_agents() == "" and len(asked) == 1
    monkeypatch.setattr(ai_page, "_download", lambda url: _zip_of({"agents/ai/main.kos": "v2"}))
    assert ai_page.fetch_agents(force=True) == ""
    assert ai_page.ai_program().read_text() == "v2"


def test_a_failed_fetch_keeps_the_copy_it_had(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_page, "HOME", tmp_path)
    monkeypatch.delenv("KOSIKA_AI_AGENTS_HOME", raising=False)
    monkeypatch.setattr(ai_page, "_download", lambda url: _zip_of({"agents/ai/main.kos": "kept"}))
    ai_page.fetch_agents()

    def offline(url):
        raise OSError("no network")

    monkeypatch.setattr(ai_page, "_download", offline)
    assert "could not fetch" in ai_page.fetch_agents(force=True)
    assert ai_page.ai_program().read_text() == "kept"
    monkeypatch.setattr(ai_page, "_download", lambda url: _zip_of({"README.md": "no agent"}))
    assert "no agents/ai/main.kos" in ai_page.fetch_agents(force=True)
    assert ai_page.ai_program().read_text() == "kept"


def test_a_named_copy_is_never_fetched_over(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KOSIKA_AI_AGENTS_HOME", str(tmp_path))
    monkeypatch.setattr(ai_page, "_download", lambda url: pytest.fail("fetched over a named copy"))
    assert ai_page.fetch_agents(force=True) == ""
    assert ai_page.own_agents() == tmp_path.resolve()
