"""The value tree a declaration body parses to, and what it does to a hash."""

from __future__ import annotations

import pytest

from kosika.ast import (
    DeclList,
    DeclMap,
    DeclRef,
    ModelDecl,
    Program,
    program_payload,
)
from kosika.parser import ParseError, parse


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


def _config(source: str) -> tuple[tuple[str, object], ...]:
    return parse(source).models[0].config


def test_every_literal_form() -> None:
    assert _config(
        "model m {\n"
        '  base: "qwen2.5:7b"\n'
        "  limits: { context: 8000, results: 6.5 }\n"
        '  sources: ["a", "b"]\n'
        "  cache: on\n"
        "  fallback: none\n"
        "  embed: nomic\n"
        "}\n"
    ) == (
        ("base", "qwen2.5:7b"),
        ("limits", DeclMap((("context", 8000), ("results", 6.5)))),
        ("sources", DeclList(("a", "b"))),
        ("cache", True),
        ("fallback", None),
        ("embed", DeclRef("nomic")),
    )


def test_off_is_false_and_integers_stay_whole() -> None:
    assert _config("model m {\n  cache: off\n  limits: { context: 1e3 }\n}\n") == (
        ("cache", False),
        ("limits", DeclMap((("context", 1000.0),))),
    )


def test_a_whole_number_and_a_decimal_are_distinct_values() -> None:
    # The spec's table (`…-design.md:74`): `40`, `40.0` and `1e3` are three
    # different values, canonicalising to `40`, `40.0` and `1000.0`. Pinning
    # the Python types here, rather than comparing them at run time, because
    # `40 == 40.0` is true in Python and the distinctness that matters is in
    # the payload slice 2 emits.
    ((_, limits),) = _config("model m {\n  limits: { context: 40, results: 40.0 }\n}\n")
    assert isinstance(limits, DeclMap)
    (context_key, context), (results_key, results) = limits.entries
    assert (context_key, results_key) == ("context", "results")
    assert type(context) is int and context == 40
    assert type(results) is float and results == 40.0


def test_lists_and_maps_nest_in_each_other() -> None:
    assert _config('model m {\n  sources: [{ a: [1, { b: on }] }, "x"]\n}\n') == (
        (
            "sources",
            DeclList(
                (
                    DeclMap((("a", DeclList((1, DeclMap((("b", True),))))),)),
                    "x",
                )
            ),
        ),
    )


def test_a_negative_number_is_one_value() -> None:
    assert _config("model m {\n  limits: { context: -8000 }\n}\n") == (
        ("limits", DeclMap((("context", -8000),))),
    )


def test_the_existing_fields_are_untouched() -> None:
    program = parse(
        "record Q { text: Text }\n"
        "record A { text: Text }\n"
        "model m {\n"
        '  input: Q\n  output: A\n  deployment: "small"\n'
        "  parameters: {\n"
        "    temperature: 0.2\n"
        "    max_output_tokens: 200\n"
        '    stop: ["END"]\n'
        "  }\n"
        '  base: "x"\n'
        "}\n"
    )
    model = program.models[0]
    assert (model.input, model.output, model.deployment) == ("Q", "A", "small")
    assert model.parameters.temperature == 0.2
    assert model.parameters.stop == ("END",)
    assert model.config == (("base", "x"),)


def test_a_key_it_does_not_know_is_still_refused() -> None:
    with pytest.raises(ParseError, match=r"Invalid model field 'nope'"):
        parse("model m {\n  nope: 1\n}\n")


def test_a_key_twice_is_refused() -> None:
    with pytest.raises(ParseError, match=r"Invalid or duplicate model field 'base'"):
        parse('model m {\n  base: "x"\n  base: "y"\n}\n')


def test_a_key_twice_inside_a_map_is_refused() -> None:
    with pytest.raises(ParseError, match=r"Invalid or duplicate model field 'a'"):
        parse("model m {\n  limits: { a: 1, a: 2 }\n}\n")


def test_a_value_that_is_not_one_is_refused() -> None:
    with pytest.raises(ParseError, match=r"^Expected value at 2:9"):
        parse("model m {\n  base: }\n}\n")


def test_nesting_has_a_limit() -> None:
    deep = "[" * 33 + "]" * 33
    with pytest.raises(ParseError, match=r"nesting exceeds 32"):
        parse(f"model m {{\n  sources: {deep}\n}}\n")


def test_empty_containers_parse() -> None:
    program = parse('model m {\n  sources: []\n  limits: {}\n  base: "x"\n}\n')
    assert program.models[0].config == (
        ("sources", DeclList(())),
        ("limits", DeclMap(())),
        ("base", "x"),
    )


def test_nested_empty_containers_parse() -> None:
    program = parse("model m {\n  limits: { a: [], b: {} }\n}\n")
    assert program.models[0].config == (
        ("limits", DeclMap((("a", DeclList(())), ("b", DeclMap(()))))),
    )


def test_a_trailing_comma_is_refused() -> None:
    with pytest.raises(ParseError, match=r"Expected value"):
        parse("model m {\n  sources: [1,]\n}\n")
