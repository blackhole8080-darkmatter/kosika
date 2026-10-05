"""Thinking steps: `classify`, `extract`, `summarize`, and `decide`.

Each is one workflow step that asks a model and binds a typed, checked answer:

    label   = classify ticket.text into ["billing", "bug", "other"]
    invoice = extract Invoice from ticket.text
    brief   = summarize ticket.text
    refund  = decide "Is this refund within policy?" given ticket.text

They are not a second execution path. `lower_thinking` rewrites every thinking
step into an ordinary typed model call against a generated model declaration,
so the checker, budgets, traces, replay, and host bindings treat it exactly
like `use_model`. A generated model borrows the host binding of the declared
model it names in `provider`; it grants nothing by itself.

What the language guarantees about an answer, beyond its record shape:

* `classify` returns a label that is one of the choices given.
* `classify` and `decide` return a confidence from 0 through 1.

A confidence is the model's own estimate, not a measured probability.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .ast import (
    Field,
    ModelCall,
    ModelDecl,
    Program,
    RecordDecl,
    Step,
    ThinkCall,
)
from .builtin_methods import did_you_mean

#: A general model declared as `model name` takes and returns plain text.
GENERAL_INPUT = "Prompt"
GENERAL_OUTPUT = "Reply"

#: Records the language supplies. A program may declare a record with one of
#: these names only if its fields are identical.
BUILT_IN_RECORDS: dict[str, tuple[Field, ...]] = {
    GENERAL_INPUT: (Field("text", "Text"),),
    GENERAL_OUTPUT: (Field("text", "Text"),),
    "Choice": (Field("label", "Text"), Field("confidence", "Number")),
    "Summary": (Field("text", "Text"),),
    "Decision": (
        Field("answer", "Bool"),
        Field("confidence", "Number"),
        Field("reason", "Text"),
    ),
    "ClassifyRequest": (Field("text", "Text"), Field("choices", "Text[]")),
    "ExtractRequest": (Field("text", "Text"),),
    "SummarizeRequest": (Field("text", "Text"),),
    "DecideRequest": (Field("question", "Text"), Field("given", "Text")),
}

_INSTRUCTIONS = {
    "classify": (
        "Classify the text. Set label to exactly one of the given choices, "
        "copied character for character, and confidence to your confidence "
        "from 0 to 1."
    ),
    "extract": (
        "Extract the requested fields from the text. Use only what the text "
        "states; do not invent values."
    ),
    "summarize": "Summarize the text briefly and faithfully, adding nothing.",
    "decide": (
        "Answer the question using only the given text. Set answer to true or "
        "false, confidence to your confidence from 0 to 1, and reason to one "
        "sentence that cites the given text."
    ),
}

VERBS = tuple(_INSTRUCTIONS)


class ThinkingError(ValueError):
    """A thinking step cannot be turned into a model call."""


def is_general(model: ModelDecl) -> bool:
    return (
        model.provider is None
        and model.input == GENERAL_INPUT
        and model.output == GENERAL_OUTPUT
    )


def _shape(verb: str, record: str | None) -> tuple[str, str]:
    if verb == "classify":
        return "ClassifyRequest", "Choice"
    if verb == "extract":
        assert record is not None
        return "ExtractRequest", record
    if verb == "summarize":
        return "SummarizeRequest", "Summary"
    return "DecideRequest", "Decision"


def _arguments(call: ThinkCall) -> tuple[tuple[str, Any], ...]:
    if call.verb == "classify":
        return (("text", call.subject), ("choices", call.extra))
    if call.verb == "decide":
        return (("question", call.subject), ("given", call.extra))
    return (("text", call.subject),)


def _choose_model(program: Program, call: ThinkCall, step: Step) -> ModelDecl:
    declared = [model for model in program.models if model.provider is None]
    if call.model is not None:
        for model in declared:
            if model.name == call.model:
                return model
        known = ", ".join(sorted(model.name for model in declared)) or "none"
        raise ThinkingError(
            f"Step {step.name!r} uses unknown model {call.model!r}; "
            f"declared model(s): {known}"
        )
    if len(declared) == 1:
        return declared[0]
    if not declared:
        raise ThinkingError(
            f"Step {step.name!r} needs a model for {call.verb}: declare one "
            "with `model assistant`"
        )
    names = ", ".join(sorted(model.name for model in declared))
    raise ThinkingError(
        f"Step {step.name!r} needs a model for {call.verb}: this program "
        f"declares several ({names}), so add `using NAME`"
    )


def lower_thinking(program: Program) -> Program:
    """Replace every thinking step with a typed model call.

    Programs without a thinking step or a general model are returned
    unchanged, so they keep the same tree and the same hash.
    """

    uses_general = any(is_general(model) for model in program.models)
    thinks = any(
        isinstance(step.call, ThinkCall)
        for workflow in program.workflows
        for step in workflow.steps
    )
    if not uses_general and not thinks:
        return program

    needed: set[str] = set()
    if uses_general:
        needed.update((GENERAL_INPUT, GENERAL_OUTPUT))
    generated: dict[str, ModelDecl] = {}
    workflows = []
    for workflow in program.workflows:
        steps = []
        for step in workflow.steps:
            call = step.call
            if not isinstance(call, ThinkCall):
                steps.append(step)
                continue
            base = _choose_model(program, call, step)
            input_record, output_record = _shape(call.verb, call.record)
            known = {record.name for record in program.records}
            if call.verb == "extract" and output_record not in known:
                raise ThinkingError(
                    f"Step {step.name!r} extracts unknown record "
                    f"{output_record!r}.{did_you_mean(output_record, known)}"
                )
            needed.add(input_record)
            if output_record in BUILT_IN_RECORDS:
                needed.add(output_record)
            name = f"{base.name}/{call.verb}"
            if call.verb == "extract":
                name += f"/{output_record}"
            generated.setdefault(
                name,
                ModelDecl(
                    name,
                    input_record,
                    output_record,
                    base.deployment,
                    base.parameters,
                    provider=base.name,
                    instruction=_INSTRUCTIONS[call.verb],
                    verb=call.verb,
                ),
            )
            steps.append(replace(step, call=ModelCall(name, _arguments(call))))
        workflows.append(replace(workflow, steps=tuple(steps)))

    declared = {record.name: record for record in program.records}
    records = list(program.records)
    for name in sorted(needed):
        fields = BUILT_IN_RECORDS[name]
        existing = declared.get(name)
        if existing is None:
            records.append(RecordDecl(name, fields))
        elif existing.fields != fields:
            shape = ", ".join(f"{field.name}: {field.type_name}" for field in fields)
            raise ThinkingError(
                f"Record {name!r} is built in as {{{shape}}}; rename the "
                f"record declared in this program"
            )
    taken = {model.name for model in program.models}
    return replace(
        program,
        records=records,
        workflows=workflows,
        models=[
            *program.models,
            *(model for name, model in generated.items() if name not in taken),
        ],
    )


def check_answer(
    model: ModelDecl, arguments: dict[str, Any], result: dict[str, Any]
) -> None:
    """Enforce what a thinking step promises beyond its record shape."""

    if model.verb == "classify" and result["label"] not in arguments["choices"]:
        raise ThinkingAnswerError(
            f"classify answered {result['label']!r}, which is not one of the "
            f"choices {arguments['choices']!r}"
        )
    if model.verb in ("classify", "decide") and not 0 <= result["confidence"] <= 1:
        raise ThinkingAnswerError(
            f"{model.verb} answered confidence {result['confidence']!r}, "
            "which is not from 0 through 1"
        )


class ThinkingAnswerError(RuntimeError):
    """A model's answer broke a guarantee of the thinking step that asked."""
