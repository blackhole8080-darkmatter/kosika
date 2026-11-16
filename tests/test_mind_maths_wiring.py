# tests/test_mind_maths_wiring.py
"""Solved maths knowledge reaching a model prompt, proven without a live model.

Solve (infer subsumption) -> project the facts -> maths_mind_hooks -> _ask_model
with a scripted provider. The sent prompt must carry the maths memory, and
reflect must refuse an answer outside the offered choices. Mirrors
tests/test_mind_runtime_wiring.py."""
from __future__ import annotations

import json

from kosika.ast import ModelDecl
from kosika.brain_skills import MATHS, load_brain_skills
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.maths_memory import maths_mind_hooks
from kosika.memory_tiers import MemoryStack
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.runtime import _ask_model
from kosika.skills_framework import SkillMessage, SkillRegistry

_SUBSUMPTION = {
    "id": "subsumption",
    "name": "is-a transitivity",
    "pattern": "(and (match ?x is-a ?y) (match ?y is-a ?z))",
    "action": {"head": "?x", "relation": "is-a", "tail": "?z"},
}
_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["label", "confidence"],
}
_CHOICES = ["yes", "no", "unsure"]


def _solved_graph() -> KnowledgeGraph:
    graph = KnowledgeGraph()
    ctx = Context(source="maths-brain", timestamp=0)
    graph.add_hyperedge(
        Hyperedge(
            id="tax",
            context=ctx,
            facts=[
                Fact("sin", "is-a", "trig-function", ctx),
                Fact("cos", "is-a", "trig-function", ctx),
                Fact("tan", "is-a", "trig-function", ctx),
                Fact("trig-function", "is-a", "function", ctx),
            ],
        )
    )
    registry = SkillRegistry()
    load_brain_skills(registry, graph, MemoryStack())
    registry.route(
        SkillMessage("test", MATHS, "infer", {"rules": [_SUBSUMPTION]})
    )
    return graph


def _ask(provider, arguments, *, retries, mind):
    model = ModelDecl(name="m", input="Ask", output="Answer")
    return _ask_model(
        provider, model, dict(arguments), _SCHEMA, retries=retries, mind=mind
    )


def test_solved_maths_memory_is_folded_into_the_prompt():
    graph = _solved_graph()
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"label": "yes", "confidence": 0.8}))]
    )
    hooks = maths_mind_hooks(
        graph, embed=lambda text: [1.0, 0.0], choices=_CHOICES, limit=5
    )

    _ask(provider, {"text": "is sin a kind of function?"}, retries=0, mind=hooks)

    sent = json.loads(provider.requests[0].messages[1].content)
    assert "sin" in sent["mind_context"]
    assert "function" in sent["mind_context"]


def test_reflect_refuses_an_answer_outside_the_offered_choices():
    graph = _solved_graph()
    provider = ScriptedModelProvider(
        [
            ModelResponse(content=json.dumps({"label": "banana", "confidence": 0.5})),
            ModelResponse(content=json.dumps({"label": "yes", "confidence": 0.5})),
        ]
    )
    hooks = maths_mind_hooks(
        graph, embed=lambda text: [1.0, 0.0], choices=_CHOICES, limit=5
    )

    answer, reply = _ask(
        provider, {"text": "is sin a kind of function?"}, retries=1, mind=hooks
    )

    assert answer["label"] == "yes"
    assert len(provider.requests) == 2
    assert "was not offered" in reply["rejected"][0]["reason"]
    assert reply["mind"]["label"] == "yes"