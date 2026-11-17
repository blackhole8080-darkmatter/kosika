# tests/test_mind_dispatch.py
"""Model-free perceive dispatcher: the brain decides which maths capability a
request maps to, then executes it on the real bus. No model is asked."""
from __future__ import annotations

from kosika.brain_skills import MEMORY, load_brain_skills
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.memory_tiers import MemoryStack
from kosika.mind_dispatch import dispatch
from kosika.skills_framework import SkillMessage, SkillRegistry


def _registry():
    graph = KnowledgeGraph()
    ctx = Context(source="test", timestamp=0)
    graph.add_hyperedge(Hyperedge(id="tax", context=ctx, facts=[
        Fact("sin", "is-a", "trig-function", ctx),
        Fact("cos", "is-a", "trig-function", ctx),
        Fact("tan", "is-a", "trig-function", ctx),
        Fact("trig-function", "is-a", "function", ctx),
    ]))
    registry = SkillRegistry()
    load_brain_skills(registry, graph, MemoryStack())
    return registry, graph


def test_dispatch_draws_the_knowledge_graph():
    registry, _ = _registry()
    out = dispatch(registry, "draw the taxonomy")
    assert out["capability"] == "visualize"
    assert out["ok"] is True
    assert out["value"]["count"] == 4
    assert "<svg" in out["value"]["svg"]
    assert out["why"]


def test_dispatch_lists_is_a_links():
    registry, _ = _registry()
    out = dispatch(registry, "list the is-a links")
    assert out["capability"] == "query"
    assert out["ok"] is True
    assert out["value"]["count"] == 4
    assert out["value"]["columns"] == ["?x", "?y"]


def test_dispatch_catalogue_when_no_relation_named():
    registry, _ = _registry()
    out = dispatch(registry, "query everything you know")
    assert out["capability"] == "query"
    assert out["ok"] is True
    assert out["value"]["count"] == 4
    assert "?r" in out["value"]["columns"]


def test_dispatch_derives_transitive_is_a():
    registry, _ = _registry()
    out = dispatch(registry, "derive the is-a hierarchy")
    assert out["capability"] == "infer"
    assert out["ok"] is True
    assert out["value"]["inferred"] == 3


def test_infer_without_a_named_rule_asks_for_one():
    registry, _ = _registry()
    out = dispatch(registry, "infer something")
    assert out["capability"] == "infer"
    assert out["ok"] is False
    assert "named rule" in out["problem"]


def test_dispatch_recalls_remembered_facts():
    registry, _ = _registry()
    registry.route(SkillMessage("host", MEMORY, "remember", {
        "facts": [{"head": "alice", "relation": "parent", "tail": "bob"}],
    }))
    out = dispatch(registry, "recall what you know")
    assert out["capability"] == "recall"
    assert out["ok"] is True
    assert out["value"]["count"] >= 1


def test_unrelated_request_is_a_graceful_no_match():
    registry, _ = _registry()
    out = dispatch(registry, "banana helicopter nonsense")
    assert out["capability"] is None
    assert out["ok"] is False
    assert "no capability matched" in out["problem"]