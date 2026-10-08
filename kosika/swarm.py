"""Executable swarm runs, behind one authenticated approval.

A declared swarm is not executable on its own. This module makes one invocable
without pretending to define the harder parts of swarm semantics: there is no
message protocol, no scheduling policy, and no consensus algorithm here.

What a run *does* mean, stated plainly:

* One approval authorizes exactly one swarm run. The approval is bound to a
  plan hash covering the swarm, its workflow, its members, its round cap, and
  the checked program. It is claimed once in the persistent ledger before any
  member executes, so a second run of the same approval is refused.
* The swarm executes its declared workflow once per review round, up to
  ``max_rounds``. Rounds are numbered from one and run in declaration order.
* The run terminates at ``fixed_point`` when a round produces a result identical
  to the previous round, or at ``max_rounds``. It reports which.
* The ledger records the outcome. A completed run stores the hash of its final
  result; a failed round stores the failure type. Either way the approval is
  spent, because members may already have acted.
* One shared execution budget bounds the whole swarm rather than each round, so
  ``max_steps``, ``max_model_calls``, and ``max_tool_calls`` are aggregate.
* Every executed step is recorded as observed. A member agent's declared
  ``can`` and ``cannot`` are enforced by the runtime that executes the workflow,
  so joining a swarm grants no authority a member did not already declare.

What it does *not* mean: that the members agreed on anything. There is no
consensus, no quorum, and no shared state between rounds. A swarm here is a
reviewed, budgeted, replayable way to run one workflow under a human gate, and
nothing more.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .approval_ledger import ApprovalLedgerError
from .ast import Program, program_payload
from .board import BoardStore, bind_board_tools
from .checker import check
from .execution import ExecutionControl, ExecutionLimits
from .runtime import ExecutionSession, LiveEvents, run

SWARM_RUN_SCHEMA = "kosika.swarm-run/v1"


class SwarmRunError(RuntimeError):
    """A swarm cannot run, or its run cannot be authorized."""

    def __init__(self, message: str, code: str = "swarm/run") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SwarmApproval:
    """A human approval for exactly one swarm run.

    ``plan_hash`` must equal :func:`swarm_plan_hash` for the swarm and program
    under consideration. An approval for a different plan, or an empty
    ``approved_by``, is refused rather than reinterpreted.
    """

    plan_hash: str
    approved_by: str
    reason: str = ""


@dataclass(frozen=True)
class SwarmRound:
    index: int
    result: Any
    steps: int
    changed: bool


@dataclass(frozen=True)
class SwarmRunResult:
    swarm: str
    workflow: str
    agents: tuple[str, ...]
    max_rounds: int
    rounds: tuple[SwarmRound, ...]
    terminated_by: str
    plan_hash: str
    approved_by: str
    result: Any
    counts: dict[str, int]
    events: tuple[dict[str, Any], ...] = ()
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = SWARM_RUN_SCHEMA
        payload.pop("events", None)
        return payload


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def program_hash(program: Program) -> str:
    """The same identity a trace records, so a run binds to a checked program."""

    encoded = json.dumps(program_payload(program), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def swarm_plan_hash(program: Program, swarm_name: str) -> str:
    """Bind a swarm, its workflow, its members, its cap, and the program."""

    swarm = _find_swarm(program, swarm_name)
    payload = {
        "swarm": swarm.name,
        "agents": list(swarm.agents),
        "workflow": swarm.workflow,
        "max_rounds": swarm.max_rounds,
        "approval_required": swarm.approval_required,
        "program": program_hash(program),
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _find_swarm(program: Program, name: str):
    for swarm in program.swarms:
        if swarm.name == name:
            return swarm
    known = ", ".join(sorted(item.name for item in program.swarms)) or "none"
    raise SwarmRunError(f"Unknown swarm {name!r}; declared swarm(s): {known}")


def run_swarm(
    program: Program,
    swarm_name: str,
    *,
    approval: SwarmApproval | None,
    ledger: Any,
    inputs: dict[str, Any] | None = None,
    model_bindings: Any = None,
    tool_bindings: Any = None,
    mcp_commands: Any = None,
    manifest: Any = None,
    limits: ExecutionLimits | None = None,
    session: ExecutionSession | None = None,
    collect_events: bool = True,
    on_event: Any = None,
) -> SwarmRunResult:
    """Run a declared swarm's workflow under one authenticated approval."""

    check(program)
    swarm = _find_swarm(program, swarm_name)

    if not swarm.approval_required:
        raise SwarmRunError(
            f"Swarm {swarm.name!r} does not require approval, so it is not runnable",
            code="swarm/approval-not-required",
        )

    expected = swarm_plan_hash(program, swarm_name)
    if approval is None:
        raise SwarmRunError(
            f"Swarm {swarm.name!r} requires approval for plan {expected}",
            code="swarm/approval-required",
        )
    if not approval.approved_by.strip():
        raise SwarmRunError(
            "A swarm approval must name who approved it",
            code="swarm/approval-unattributed",
        )
    if approval.plan_hash != expected:
        raise SwarmRunError(
            f"Swarm approval is for plan {approval.plan_hash} but this swarm "
            f"and program hash to plan {expected}",
            code="swarm/approval-plan-mismatch",
        )
    if ledger is None:
        raise SwarmRunError(
            "A swarm run requires a persistent approval ledger",
            code="swarm/ledger-required",
        )

    action = hashlib.sha256(f"swarm:{expected}".encode("utf-8")).hexdigest()
    try:
        ledger.claim(action, expected, approval.approved_by, f"swarm:{swarm.name}")
    except ApprovalLedgerError as exc:
        raise SwarmRunError(str(exc), code="swarm/approval-claimed") from exc

    control = ExecutionControl(limits or ExecutionLimits())
    control.scope = (f"swarm:{swarm.name}",)
    # A watcher is told about every round's events as they happen. It
    # changes nothing about the run.
    events: list[dict[str, Any]] = [] if on_event is None else LiveEvents(on_event)
    rounds: list[SwarmRound] = []
    previous: bytes | None = None
    final: Any = None
    terminated_by = "max_rounds"

    if program.boards:
        # One board for the whole swarm run, so a later round can read what an
        # earlier round posted. This is how the members talk to each other.
        tool_bindings = {
            **bind_board_tools(program, BoardStore()),
            **(tool_bindings or {}),
        }

    for index in range(1, swarm.max_rounds + 1):
        if on_event is not None:
            try:
                on_event({"event": "swarm_round", "round": index}, 0)
            except Exception:  # noqa: BLE001 - a display must not break a run
                pass
        try:
            outcome = run(
                program,
                swarm.workflow,
                None,
                mcp_commands=mcp_commands,
                manifest=manifest,
                inputs=inputs,
                model_bindings=model_bindings,
                tool_bindings=tool_bindings,
                session=session,
                _events_out=events,
                _control=control,
            )
        except Exception as exc:
            # The claim is already spent: members may have acted before the
            # failure, so the approval is recorded as failed, never released.
            try:
                ledger.finish(action, error=exc)
            except ApprovalLedgerError:
                pass
            raise SwarmRunError(
                f"Swarm {swarm.name!r} round {index} failed: {exc}",
                code="swarm/round-failed",
            ) from exc

        final = outcome
        encoded = _canonical(outcome)
        changed = previous is None or encoded != previous
        rounds.append(
            SwarmRound(
                index=index,
                result=outcome,
                steps=int(control.counts["steps"]),
                changed=changed,
            )
        )
        if not changed:
            terminated_by = "fixed_point"
            break
        previous = encoded

    digest = hashlib.sha256(
        _canonical(
            {
                "swarm": swarm.name,
                "workflow": swarm.workflow,
                "plan": expected,
                "rounds": [
                    {
                        "index": item.index,
                        "result": item.result,
                        "changed": item.changed,
                    }
                    for item in rounds
                ],
                "terminated_by": terminated_by,
            }
        )
    ).hexdigest()

    try:
        ledger.finish(action, result=final)
    except ApprovalLedgerError as exc:
        raise SwarmRunError(str(exc), code="swarm/ledger") from exc

    return SwarmRunResult(
        swarm=swarm.name,
        workflow=swarm.workflow,
        agents=tuple(swarm.agents),
        max_rounds=swarm.max_rounds,
        rounds=tuple(rounds),
        terminated_by=terminated_by,
        plan_hash=expected,
        approved_by=approval.approved_by,
        result=final,
        counts=dict(sorted(control.counts.items())),
        events=tuple(events) if collect_events else (),
        digest=digest,
    )
