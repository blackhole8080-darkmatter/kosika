"""`use_model ... with evidence`: every model step can be held to its quotes.

A thinking step written `with evidence` already requires the model to quote the
text it was given (see `kosika.evidence`). This pass gives the same guarantee
to an ordinary `use_model` call, whose output shape the author declared: the
answer must arrive with quotes that appear in the text the step passed.

The mechanism is the one thinking steps already use. A call that asks for
evidence is named after the model it asks plus ``/evidence``, and a generated
model declaration stands in for it with ``provider`` pointing at the real
model. Everything downstream - schema, verification, trace, replay - already
reads evidence from a model declaration, so nothing else has to change, and a
program that asks for no evidence keeps exactly the same tree and hash.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .ast import ModelCall, ModelDecl, Program, Step
from .evidence import INSTRUCTIONS as EVIDENCE_INSTRUCTIONS
from .evidence import KEY as EVIDENCE_KEY

#: The rule a plain model step's quotes satisfy. See `kosika.evidence.SOURCES`.
KIND = "input"


def _asks_for_evidence(program: Program) -> bool:
    return any(
        isinstance(call, ModelCall) and call.evidence is not None
        for workflow in program.workflows
        for step in workflow.steps
        for call in (
            step.call,
            step.fallback,
            *(branch for _, branch in step.branches),
        )
    )


def lower_model_evidence(program: Program) -> Program:
    """Replace every evidenced model call with a call of a generated model."""

    if not _asks_for_evidence(program):
        return program
    declared = {model.name: model for model in program.models}
    taken = set(declared)
    generated: dict[str, ModelDecl] = {}

    def lower(step: Step, call: Any) -> Any:
        if not isinstance(call, ModelCall) or call.evidence is None:
            return call
        base = declared.get(call.model_name)
        if base is None:
            # An undeclared model is the checker's failure to report, with its
            # own message and code; guessing here would hide it.
            return call
        if base.evidence is not None:
            raise ValueError(
                f"Step {step.name!r} cannot give evidence: model "
                f"{base.name!r} already answers with quotes"
            )
        if base.plain:
            raise ValueError(
                f"Step {step.name!r} cannot give evidence: model "
                f"{base.name!r} answers in plain words, so there is no record "
                "to carry a quote"
            )
        output = next(
            (record for record in program.records if record.name == base.output),
            None,
        )
        if output is not None and any(
            field.name == EVIDENCE_KEY for field in output.fields
        ):
            raise ValueError(
                f"Step {step.name!r} cannot give evidence: record "
                f"{base.output!r} has its own field named {EVIDENCE_KEY!r}"
            )
        name = f"{base.name}/evidence"
        generated.setdefault(
            name,
            ModelDecl(
                name,
                base.input,
                base.output,
                base.deployment,
                base.parameters,
                provider=base.name,
                instruction=(base.instruction or "") + EVIDENCE_INSTRUCTIONS[KIND],
                verb=base.verb,
                evidence=KIND,
                default=base.default,
            ),
        )
        return ModelCall(name, call.arguments)

    workflows = []
    for workflow in program.workflows:
        steps = []
        for step in workflow.steps:
            call = lower(step, step.call)
            fallback = lower(step, step.fallback)
            branches = tuple(
                (pattern, lower(step, branch)) for pattern, branch in step.branches
            )
            if (
                call is step.call
                and fallback is step.fallback
                and all(new[1] is old[1] for new, old in zip(branches, step.branches))
            ):
                steps.append(step)
            else:
                steps.append(
                    replace(
                        step,
                        call=call,
                        fallback=fallback,
                        branches=branches,
                    )
                )
        workflows.append(replace(workflow, steps=tuple(steps)))
    return replace(
        program,
        workflows=workflows,
        models=[
            *program.models,
            *(model for name, model in generated.items() if name not in taken),
        ],
    )


__all__ = ["KIND", "lower_model_evidence"]
