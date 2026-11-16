# tests/test_mind_replay.py
"""A run the Mind layer touched still replays offline from its trace alone."""

import json
from pathlib import Path

from kosika.checker import check
from kosika.graph_store import load_graph, save_graph
from kosika.knowledge_graph import Entity, EntityType, KnowledgeGraph
from kosika.mind import mind_hooks
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.runtime import replay, run

SOURCE = """
record Ticket { text: Text }
record Out { queue: Text }

model assistant

fn build(label: Choice) -> Out {
  return {queue: label.label}
}

workflow main(ticket: Ticket) -> Out {
  label = classify ticket.text into ["billing", "other"] retry 1
  out   = build(label)
}
"""

INPUTS = {"ticket": {"text": "My card was charged twice."}}
GOOD = {"label": "billing", "confidence": 0.9}


def _graph(tmp_path: Path) -> KnowledgeGraph:
    graph = KnowledgeGraph()
    graph.add_entity(
        Entity(
            id="billing",
            name="double charge",
            entity_type=EntityType.CONCEPT,
            description="a card charged twice is a billing complaint",
            embedding=[1.0, 0.0],
        )
    )
    db = tmp_path / "mind.db"
    save_graph(graph, db)
    return load_graph(db)


def _program():
    program = parse(SOURCE)
    check(program)
    return program


def test_a_mind_bound_trace_replays_without_asking_anyone(tmp_path):
    trace = tmp_path / "trace.jsonl"
    provider = ScriptedModelProvider([ModelResponse(content=json.dumps(GOOD))])
    hooks = mind_hooks(
        _graph(tmp_path), embed=lambda text: [1.0, 0.0], choices=["billing", "other"]
    )

    ran = run(
        _program(),
        "main",
        trace,
        inputs=INPUTS,
        model_bindings={"assistant": provider},
        mind=hooks,
    )

    events = [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    returned = [event for event in events if event.get("event") == "model_return"]
    assert len(returned) == 1
    assert returned[0]["mind"]["label"] == "billing"
    assert "double charge" in returned[0]["mind"]["retrieved"]

    assert replay(trace) == ran


def test_a_run_without_mind_replays_too(tmp_path):
    trace = tmp_path / "plain.jsonl"
    provider = ScriptedModelProvider([ModelResponse(content=json.dumps(GOOD))])

    ran = run(
        _program(), "main", trace, inputs=INPUTS, model_bindings={"assistant": provider}
    )

    events = [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert (
        "mind"
        not in [event for event in events if event.get("event") == "model_return"][0]
    )

    assert replay(trace) == ran


CHECKED = """
record Msg { text: Text }
record Draft { text: Text }

model writer {
  input: Msg
  output: Draft
}

fn short_enough(reply: Draft, most: Msg) -> Text {
  if reply.text.length() > 12 { return "Too long." }
  return ""
}

workflow main(msg: Msg) -> Draft {
  reply = use_model writer(msg.text) verify short_enough(reply, msg) retry 1
}
"""


def test_the_programs_own_recorded_check_survives_a_mind_bound_replay(tmp_path):
    trace = tmp_path / "checked.jsonl"
    program = parse(CHECKED)
    check(program)
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"text": "Short."}))]
    )
    hooks = mind_hooks(_graph(tmp_path), embed=lambda text: [1.0, 0.0])

    ran = run(
        program,
        "main",
        trace,
        inputs={"msg": {"text": "Say hi."}},
        model_bindings={"writer": provider},
        mind=hooks,
    )

    returned = [
        event
        for event in (
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if event.get("event") == "model_return"
    ]
    assert returned[0]["verified"][1]["result"] == ""
    assert "double charge" in returned[0]["mind"]["retrieved"]

    assert ran == {"text": "Short."}
    assert replay(trace) == ran
