"""The value tree a declaration body parses to, and what it does to a hash."""

from __future__ import annotations

from kosika.ast import (
    DeclList,
    DeclMap,
    DeclRef,
    ModelDecl,
    Program,
    program_payload,
)


def test_a_model_without_config_hashes_as_it_did_before() -> None:
    payload = program_payload(Program(models=[ModelDecl("m", "Question", "Answer")]))
    assert "config" not in str(payload)


def test_config_keeps_the_order_it_was_written_in() -> None:
    model = ModelDecl(
        "specialist",
        "Question",
        "Answer",
        config=(
            ("base", "qwen2.5:7b"),
            ("limits", DeclMap((("context", 8000), ("results", 6)))),
            ("sources", DeclList(("KOSIKA.md",))),
            ("cache", True),
            ("fallback", None),
            ("embed", DeclRef("nomic")),
        ),
    )
    payload = program_payload(Program(models=[model]))
    assert payload["models"][0]["config"] == [
        ["base", "qwen2.5:7b"],
        ["limits", {"entries": [["context", 8000], ["results", 6]]}],
        ["sources", {"items": ["KOSIKA.md"]}],
        ["cache", True],
        ["fallback", None],
        ["embed", {"name": "nomic"}],
    ]
