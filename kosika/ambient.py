"""The clock and chance: two things a program may ask the world for.

    workflow main(msg: Msg) -> Out {
      today = now                     // Moment
      roll  = random 1 to 6           // RandomNumber, both ends included
      out   = build(today, roll)
    }

A pure function can have neither: the same arguments must give the same
result. So `now` and `random` are workflow steps. Each is lowered to an
ordinary tool call, which means what the clock said and what the dice gave
are written in the trace like any tool result. Replaying a run reads them
from the trace, so it sees the same moment and the same number, and a run
stays something that can be proved after the fact.

Neither needs a grant. The time and a random number tell a program nothing
it could misuse and change nothing. In a test, script them like any
read-only tool so the test means the same thing tomorrow:

    answers clock_now: [{date: "2026-10-09", time: "14:05", weekday: "Friday",
                         year: 2026, month: 10, day: 9, hour: 14, minute: 5,
                         epoch: 1791554700}]
    answers random_number: [{value: 4}]
"""

from __future__ import annotations

import random as _random
import time as _time
from dataclasses import replace
from typing import Any, Callable

from .ast import AgentDecl, Field, Program, RecordDecl, ToolCall, ToolDecl

#: The agent these steps run through. It can use these two tools only.
AGENT = "world"
CLOCK_TOOL = "clock_now"
RANDOM_TOOL = "random_number"
MOMENT = "Moment"
RANGE = "RandomRange"
NUMBER = "RandomNumber"
MAX_RANDOM_SPAN = 1_000_000_000

RECORDS: dict[str, tuple[Field, ...]] = {
    MOMENT: (
        Field("date", "Text"),
        Field("time", "Text"),
        Field("weekday", "Text"),
        Field("year", "Number"),
        Field("month", "Number"),
        Field("day", "Number"),
        Field("hour", "Number"),
        Field("minute", "Number"),
        Field("epoch", "Number"),
    ),
    RANGE: (Field("low", "Number"), Field("high", "Number")),
    NUMBER: (Field("value", "Number"),),
}
_TOOLS = {
    CLOCK_TOOL: ToolDecl(CLOCK_TOOL, "world:clock", "now", "observe", MOMENT, None),
    RANDOM_TOOL: ToolDecl(
        RANDOM_TOOL, "world:random", "number", "observe", NUMBER, RANGE
    ),
}
_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _calls(program: Program) -> set[str]:
    """Which of the two tools the program's workflows use."""

    used: set[str] = set()
    for workflow in program.workflows:
        for step in workflow.steps:
            for call in (
                step.call,
                step.fallback,
                *(branch for _, branch in step.branches),
            ):
                if isinstance(call, ToolCall) and call.agent_name == AGENT:
                    used.add(call.tool_name)
    return used & set(_TOOLS)


def supplied_names(program: Program) -> set[str]:
    """The names a module may use because it has `now` or `random` steps."""

    used = _calls(program)
    if not used:
        return set()
    names = {AGENT, *used}
    if CLOCK_TOOL in used:
        names.add(MOMENT)
    if RANDOM_TOOL in used:
        names |= {RANGE, NUMBER}
    return names


def lower_ambient(program: Program) -> Program:
    """Add the records, tools and agent that `now` and `random` stand for.

    A program with neither step is returned unchanged, so it keeps its tree
    and its hash. A name the program already declares is left as the program
    wrote it; the checker then says so if it does not fit.
    """

    used = _calls(program)
    if not used:
        return program
    known = {record.name for record in program.records}
    wanted = [MOMENT] if CLOCK_TOOL in used else []
    wanted += [RANGE, NUMBER] if RANDOM_TOOL in used else []
    taken = {tool.name for tool in program.tools}
    if any(agent.name == AGENT for agent in program.agents):
        return program
    return replace(
        program,
        records=[
            *program.records,
            *(RecordDecl(name, RECORDS[name]) for name in wanted if name not in known),
        ],
        tools=[
            *program.tools,
            *(_TOOLS[name] for name in sorted(used) if name not in taken),
        ],
        agents=[
            *program.agents,
            AgentDecl(AGENT, tuple(sorted(used)), (), max_steps=1),
        ],
    )


def moment(seconds: float | None = None) -> dict[str, Any]:
    """This moment, in the computer's own time zone, as a Moment record."""

    stamp = _time.time() if seconds is None else seconds
    local = _time.localtime(stamp)
    return {
        "date": _time.strftime("%Y-%m-%d", local),
        "time": _time.strftime("%H:%M", local),
        "weekday": _WEEKDAYS[local.tm_wday],
        "year": local.tm_year,
        "month": local.tm_mon,
        "day": local.tm_mday,
        "hour": local.tm_hour,
        "minute": local.tm_min,
        "epoch": int(stamp),
    }


def bind_ambient_tools(
    program: Program,
    *,
    clock: Callable[[], float] | None = None,
    chance: _random.Random | None = None,
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind `now` and `random` for the programs that use them."""

    dice = chance or _random.SystemRandom()

    def tell_time(_tool: ToolDecl, _arguments: dict[str, Any]) -> dict[str, Any]:
        return moment(clock() if clock is not None else None)

    def draw(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        low, high = arguments["low"], arguments["high"]
        if low != int(low) or high != int(high):
            raise ValueError("random needs whole numbers: random 1 to 6")
        if high < low:
            raise ValueError(
                f"random needs the smaller number first, got {low} to {high}"
            )
        if high - low > MAX_RANDOM_SPAN:
            raise ValueError("random can span at most a thousand million numbers")
        return {"value": dice.randint(int(low), int(high))}

    bound = {CLOCK_TOOL: tell_time, RANDOM_TOOL: draw}
    declared = {tool.name: tool for tool in program.tools}
    return {
        name: bound[name]
        for name in bound
        if name in declared and declared[name].source.startswith("world:")
    }
