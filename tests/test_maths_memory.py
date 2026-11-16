# tests/test_maths_memory.py
"""The maths Fact side projected onto the Entity side Mind retrieve resonates
over. Deterministic: a stub embed, real graph, no model."""
from __future__ import annotations

from kosika.knowledge_graph import (
    Context,
    EntityType,
    Fact,
    Hyperedge,
    KnowledgeGraph,
    RelationType,
)
from kosika.maths_memory import project_facts


def _embed(text: str) -> list[float]:
    return [1.0, 0.0]


def _facts() -> list[Fact]:
    ctx = Context(source="test", timestamp=0, certainty=0.9)
    return [
        Fact("sin", "is-a", "trig-function", ctx),
        Fact("trig-function", "is-a", "function", ctx),
    ]


def test_project_returns_the_same_graph_with_an_entity_per_token():
    graph = KnowledgeGraph()

    returned = project_facts(graph, _facts(), _embed)

    assert returned is graph
    assert set(graph.entities) == {"sin", "trig-function", "function"}
    sin = graph.entities["sin"]
    assert sin.name == "sin"
    assert sin.entity_type == EntityType.CONCEPT
    assert sin.embedding == [1.0, 0.0]
    assert "is-a trig-function" in sin.description


def test_project_adds_a_relationship_per_fact():
    graph = KnowledgeGraph()
    project_facts(graph, _facts(), _embed)
    assert len(graph.relationships) == 2


def test_is_a_maps_to_the_is_a_relation_type():
    graph = KnowledgeGraph()
    project_facts(graph, _facts(), _embed)
    assert all(
        rel.relation_type == RelationType.IS_A
        for rel in graph.relationships.values()
    )


def test_an_unknown_relation_maps_to_related_to():
    graph = KnowledgeGraph()
    ctx = Context(source="test", timestamp=0)
    project_facts(graph, [Fact("alice", "parent", "bob", ctx)], _embed)
    rel = next(iter(graph.relationships.values()))
    assert rel.relation_type == RelationType.RELATED_TO
    assert rel.source == "alice" and rel.target == "bob"


def test_certainty_becomes_the_relationship_weight():
    graph = KnowledgeGraph()
    ctx = Context(source="test", timestamp=0, certainty=0.42)
    project_facts(graph, [Fact("a", "is-a", "b", ctx)], _embed)
    assert next(iter(graph.relationships.values())).weight == 0.42


def test_reprojecting_the_same_facts_is_idempotent():
    graph = KnowledgeGraph()
    facts = _facts()
    project_facts(graph, facts, _embed)
    assert len(graph.entities) == 3 and len(graph.relationships) == 2
    project_facts(graph, facts, _embed)
    assert len(graph.entities) == 3 and len(graph.relationships) == 2