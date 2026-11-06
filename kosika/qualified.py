"""Holding a run to what has actually been qualified.

`kosika virtual-model eval` records what a model, or a profile with a model,
answered correctly on a fixed question set, and refuses the combinations that
showed a wrong answer. This module carries that record from the ledger to the
moment it matters: before a run asks a model anything.

It deliberately checks only what the ledger can state —— a model with no
recorded evaluation, one whose evaluation showed a wrong answer to a user, and
one whose evaluation failed to finish. It says nothing about questions the
recorded run did not ask, which is the honest limit of the evidence: a pass
means "this model was not caught being wrong on that set", not "this model is
right".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ast import ModelCall, Program
from .virtual_eval import read_ledger

#: Where evaluations are kept unless the command says otherwise.
DEFAULT_STATE = Path(".kosika/virtual-models.json")


@dataclass(frozen=True)
class Verdict:
    """Why a run may or may not go ahead."""

    models: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.reasons

    def as_text(self) -> str:
        if self.allowed:
            return "qualified: " + ", ".join(self.models)
        return "; ".join(self.reasons)


def override_names(value: str | None) -> tuple[str, ...]:
    """The models named by `--model`, which may be `fast,strong=big`."""

    if not value:
        return ()
    names: list[str] = []
    for part in value.split(","):
        name = part.split("=", 1)[1] if "=" in part else part
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def asked_models(program: Program, override: str | None = None) -> tuple[str, ...]:
    """The models this program will actually ask, by the name that is asked.

    A step may name a generated model — one made for a thinking step or for
    `with evidence` — whose answers come from the model it was made from, so
    the provider is followed to the model a person configured.
    """

    named = override_names(override)
    if named:
        return named
    declared = {model.name: model for model in program.models}
    asked: list[str] = []
    for workflow in program.workflows:
        for step in workflow.steps:
            calls = [step.call, step.fallback, *(c for _, c in step.branches)]
            for call in calls:
                if not isinstance(call, ModelCall):
                    continue
                model = declared.get(call.model_name)
                seen = 0
                while model is not None and model.provider and seen < 8:
                    model = declared.get(model.provider)
                    seen += 1
                name = (model.deployment or model.name) if model is not None else call.model_name
                if name not in asked:
                    asked.append(name)
    return tuple(asked)


def qualify(state: str | Path | None, models: tuple[str, ...]) -> Verdict:
    """Whether every model the run will ask has a recorded pass."""

    if not models:
        # A program that asks no model has nothing to qualify.
        return Verdict(models=(), reasons=())
    entries = read_ledger(state or DEFAULT_STATE)
    reasons: list[str] = []
    for model in models:
        recorded = [
            entry
            for entry in entries
            if entry.get("model") == model and isinstance(entry.get("counts"), dict)
        ]
        if not recorded:
            reasons.append(
                f"no recorded evaluation of {model}: run `kosika virtual-model eval` "
                "on it first, or drop --require-qualified"
            )
            continue
        latest = recorded[-1]
        counts = latest["counts"]
        asked = latest.get("asked", 0)
        wrong = counts.get("wrong shown", 0)
        failed = counts.get("failed", 0)
        if not isinstance(asked, int) or asked <= 0:
            reasons.append(f"the evaluation of {model} recorded no answers")
        elif wrong:
            reasons.append(f"{model} showed {wrong} wrong answer(s) when evaluated")
        elif failed:
            reasons.append(f"{model} failed {failed} answer(s) when evaluated")
    return Verdict(models=models, reasons=tuple(reasons))


__all__ = [
    "DEFAULT_STATE",
    "Verdict",
    "asked_models",
    "override_names",
    "qualify",
]
