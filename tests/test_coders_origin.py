"""A kept coder run needs studio's remote even in the installed app, whose
code is a plain copy of the repo rather than a git checkout."""

from pathlib import Path

from kosika import coders


def test_origin_of_a_plain_copy_is_studios_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KOSIKA_ORIGIN", raising=False)
    assert coders._origin(tmp_path) == coders.ORIGIN


def test_origin_can_be_pointed_elsewhere(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KOSIKA_ORIGIN", "https://example.invalid/fork")
    assert coders._origin(tmp_path) == "https://example.invalid/fork"


def test_origin_of_a_checkout_is_its_remote() -> None:
    repo = Path(coders.__file__).resolve().parent.parent
    if (repo / ".git").exists():
        assert coders._origin(repo).strip()
