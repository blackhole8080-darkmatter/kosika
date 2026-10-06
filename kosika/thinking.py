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

A step may end `with evidence` (before any `using`). The model must then quote
the text it was given, and every quote is checked against that text; see
`kosika.evidence` for the rule and for what it does not prove.
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
from .evidence import INSTRUCTIONS as EVIDENCE_INSTRUCTIONS
from .evidence import KEY as EVIDENCE_KEY
from .evidence import KINDS as EVIDENCE_KINDS

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


def _choose_model(
    program: Program, call: ThinkCall, step: Step, named: str | None
) -> ModelDecl:
    declared = [model for model in program.models if model.provider is None]
    if named is not None:
        for model in declared:
            if model.name == named:
                return model
        known = ", ".join(sorted(model.name for model in declared)) or "none"
        raise ThinkingError(
            f"Step {step.name!r} uses unknown model {named!r}; "
            f"declared model(s): {known}"
        )
    if len(declared) == 1:
        return declared[0]
    defaults = [model for model in declared if model.default]
    if len(defaults) == 1:
        return defaults[0]
    if len(defaults) > 1:
        names = ", ".join(sorted(model.name for model in defaults))
        raise ThinkingError(
            f"Step {step.name!r} needs a model for {call.verb}: this program "
            f"marks several models as default ({names})"
        )
    if not declared:
        raise ThinkingError(
            f"Step {step.name!r} needs a model for {call.verb}: declare one "
            "with `model assistant`"
        )
    names = ", ".join(sorted(model.name for model in declared))
    raise ThinkingError(
        f"Step {step.name!r} needs a model for {call.verb}: this program "
        f"declares several ({names}), so add `using NAME` or mark one "
        "`default model NAME`"
    )


def lower_thinking(program: Program) -> Program:
    """Replace every thinking step with a typed model call.

    Programs without a thinking step or a general model are returned
    unchanged, so they keep the same tree and the same hash.
    """

    uses_general = any(is_general(model) for model in program.models)
    thinks = any(
        isinstance(call, ThinkCall)
        for workflow in program.workflows
        for step in workflow.steps
        for call in (step.call, step.fallback)
    )
    if not uses_general and not thinks:
        return program

    needed: set[str] = set()
    if uses_general:
        needed.update((GENERAL_INPUT, GENERAL_OUTPUT))
    generated: dict[str, ModelDecl] = {}

    def lower_call(step: Step, call: Any, named: str | None = None) -> Any:
        if not isinstance(call, ThinkCall):
            return call
        base = _choose_model(program, call, step, named or call.model)
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
        instruction = _INSTRUCTIONS[call.verb]
        kind = None
        if call.evidence:
            kind = EVIDENCE_KINDS.get(call.verb)
            if kind is None:
                raise ThinkingError(
                    f"Step {step.name!r} cannot give evidence: {call.verb} "
                    "writes new text, so there is nothing to quote"
                )
            if call.verb == "extract" and any(
                field.name == EVIDENCE_KEY
                for record in program.records
                if record.name == output_record
                for field in record.fields
            ):
                raise ThinkingError(
                    f"Step {step.name!r} cannot give evidence: record "
                    f"{output_record!r} has its own field named {EVIDENCE_KEY!r}"
                )
            name += "/evidence"
            instruction += EVIDENCE_INSTRUCTIONS[kind]
        generated.setdefault(
            name,
            ModelDecl(
                name,
                input_record,
                output_record,
                base.deployment,
                base.parameters,
                provider=base.name,
                instruction=instruction,
                verb=call.verb,
                evidence=kind,
            ),
        )
        return ModelCall(name, _arguments(call))

    workflows = []
    for workflow in program.workflows:
        steps = []
        for step in workflow.steps:
            call = lower_call(step, step.call)
            fallback = lower_call(step, step.fallback)
            voters: tuple[str, ...] = ()
            asked = step.call
            if isinstance(asked, ThinkCall) and asked.more_models:
                # `using a, b`: each listed model gets its own generated
                # model of the same shape, and they take turns in the vote.
                if step.votes == 0:
                    raise ThinkingError(
                        f"Step {step.name!r} needs a model for {asked.verb}: it "
                        "lists several, which is only for a step that ends "
                        "`vote N`"
                    )
                voters = (
                    call.model_name,
                    *(
                        lower_call(step, asked, name).model_name
                        for name in asked.more_models
                    ),
                )
                if len(set(voters)) != len(voters):
                    raise ThinkingError(
                        f"Step {step.name!r} needs a model for {asked.verb}: it "
                        "lists the same model twice"
                    )
            if isinstance(step.fallback, ThinkCall) and step.fallback.more_models:
                raise ThinkingError(
                    f"Step {step.name!r} needs a model for "
                    f"{step.fallback.verb}: its fallback lists several models"
                )
            if call is step.call and fallback is step.fallback:
                steps.append(step)
            else:
                steps.append(replace(step, call=call, fallback=fallback, voters=voters))
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
