"""Evidence: a model's answer must quote the text it was given.

A thinking step written `with evidence` asks the model for quotes alongside
its answer, and Kosika checks that every quote really appears in the text the
step was given. An answer whose quote cannot be found is rejected like any
other broken answer, so the step fails or its `otherwise` runs.

What is and is not proved:

* A quote that passes was copied from the input. The model did not make up
  the words it points at.
* Nothing proves the quote *supports* the answer. A model can still quote a
  real sentence and draw a wrong conclusion from it. Evidence narrows the
  room for an invented answer; it does not close it.

The rule a step follows is named by its kind:

`fields`
    For `extract`. Evidence maps each field of the record to a quote, or null.
    Every field the model filled needs a quote.
`quotes`
    For `classify`. Evidence is a list with at least one quote.
`quotes_if_true`
    For `decide`. Evidence is a list of quotes. A `true` answer needs at least
    one; a `false` answer may have none, because the absence of a statement
    cannot be quoted.

Matching ignores letter case, runs of whitespace, and the difference between
straight and curly quotation marks. Nothing else is forgiven: a paraphrase is
not a quote.
"""

from __future__ import annotations

import unicodedata
from typing import Any

#: The key that carries the quotes in a model's answer and in a trace.
KEY = "evidence"

#: Argument that holds the text a step was given, by evidence kind.
SOURCES = {"fields": "text", "quotes": "text", "quotes_if_true": "given"}

KINDS = {"extract": "fields", "classify": "quotes", "decide": "quotes_if_true"}

INSTRUCTIONS = {
    "fields": (
        " Also set evidence to an object with one entry per field: a short "
        "exact quote, copied character for character from the text, that "
        "states that field's value. Use null as the quote, and leave the "
        "field empty, when the text does not state it."
    ),
    "quotes": (
        " Also set evidence to a list of one or more short exact quotes, "
        "copied character for character from the text, that support the label."
    ),
    "quotes_if_true": (
        " Also set evidence to a list of short exact quotes, copied character "
        "for character from the given text, that support the answer. A true "
        "answer needs at least one quote."
    ),
}

_MARKS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})
_MINIMUM = 2


class EvidenceError(RuntimeError):
    """A model's evidence is missing or does not appear in the input."""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_MARKS).casefold()
    return " ".join(text.split())


def answer_schema(
    kind: str, output_schema: dict[str, Any], fields: list[str]
) -> dict[str, Any]:
    """The schema a model is asked to fill: the record plus its evidence."""

    quote = {"type": "string"}
    if kind == "fields":
        evidence: dict[str, Any] = {
            "type": "object",
            "properties": {
                name: {"anyOf": [quote, {"type": "null"}]} for name in fields
            },
            "required": list(fields),
            "additionalProperties": False,
        }
    else:
        evidence = {"type": "array", "items": quote}
    schema = dict(output_schema)
    schema["properties"] = {**output_schema.get("properties", {}), KEY: evidence}
    schema["required"] = [*output_schema.get("required", []), KEY]
    return schema


def _filled(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _found(quote: Any, source: str, what: str) -> None:
    if not isinstance(quote, str):
        raise EvidenceError(f"{what} has no quote")
    wanted = normalize(quote).strip("\"'. …")
    if len(wanted.replace(" ", "")) < _MINIMUM:
        raise EvidenceError(f"{what} has a quote too short to check: {quote!r}")
    if wanted not in source:
        raise EvidenceError(
            f"{what} quotes {quote!r}, which does not appear in the given text"
        )


def verify(
    kind: str, arguments: dict[str, Any], result: dict[str, Any], evidence: Any
) -> None:
    """Raise `EvidenceError` unless the evidence backs the answer as required."""

    if kind not in SOURCES:
        raise EvidenceError(f"Unknown evidence kind {kind!r}")
    given = arguments.get(SOURCES[kind])
    if not isinstance(given, str):
        raise EvidenceError("Evidence needs the text the step was given")
    source = normalize(given)
    if kind == "fields":
        if not isinstance(evidence, dict):
            raise EvidenceError("Evidence must give a quote for each field")
        for name, value in result.items():
            if _filled(value):
                _found(evidence.get(name), source, f"Field {name!r}")
        return
    if not isinstance(evidence, list):
        raise EvidenceError("Evidence must be a list of quotes")
    for quote in evidence:
        _found(quote, source, "The answer")
    needs_quote = kind == "quotes" or result.get("answer") is True
    if needs_quote and not evidence:
        raise EvidenceError("The answer has no quote from the given text")


def split(answer: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Separate a model's answer into the record and its evidence."""

    result = dict(answer)
    return result, result.pop(KEY, None)
