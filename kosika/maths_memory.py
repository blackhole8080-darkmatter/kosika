# kosika/maths_memory.py
"""Bridge the maths Fact side to the Entity side Mind retrieve resonates over.

A KnowledgeGraph holds two representations: Fact/Hyperedge triples (queried by
KL) and Entity/Relationship nodes (resonated over by mind.retrieve). The maths
brain solves on the Fact side; the Mind layer remembers on the Entity side.
project_facts carries solved knowledge across, and maths_mind_hooks packages
the projection with the existing mind_hooks so a runtime run folds maths memory
into the prompt before reasoning. Nothing here changes mind.py or the graph.
"""
from __future__ import annotations

from typing import Callable, Iterable, Sequence

from .knowledge_graph import (
    Entity,
    EntityType,
    Fact,
    KnowledgeGraph,
    Relationship,
    RelationType,
)
from .mind import MindHooks, RELEVANCE_FLOOR, mind_hooks


def project_facts(
    graph: KnowledgeGraph,
    facts: Iterable[Fact],
    embed: Callable[[str], Sequence[float]],
) -> KnowledgeGraph:
    """One Entity per unique head/tail token, embedded from its own facts.
    Idempotent on entity id. Returns the same graph."""
    facts = list(facts)
    outgoing: dict[str, list[str]] = {}
    tokens: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        for token in (fact.head, fact.tail):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
        outgoing.setdefault(fact.head, []).append(f"{fact.relation} {fact.tail}")
    for token in tokens:
        phrases = outgoing.get(token, [])
        description = "; ".join(dict.fromkeys(phrases)) if phrases else token
        graph.add_entity(
            Entity(
                id=token,
                name=token,
                entity_type=EntityType.CONCEPT,
                description=description,
                embedding=list(embed(f"{token} {description}")),
            )
        )
    for fact in facts:
        relation_type = (
            RelationType.IS_A
            if fact.relation in ("is-a", "is_a")
            else RelationType.RELATED_TO
        )
        graph.add_relationship(
            Relationship(
                id=f"rel:{fact.content_hash()}",
                source=fact.head,
                target=fact.tail,
                relation_type=relation_type,
                weight=fact.context.certainty,
            )
        )
    return graph


def maths_mind_hooks(
    graph: KnowledgeGraph,
    embed: Callable[[str], Sequence[float]],
    *,
    choices: Sequence[str] | None = None,
    verifier: Callable[[dict], str] | None = None,
    limit: int = 4,
    floor: float = RELEVANCE_FLOOR,
    max_context_chars: int = 8000,
    max_neighbours: int = 16,
) -> MindHooks:
    """Project the graph's solved facts into memory, then hand back the Mind
    hooks the runtime already accepts via `mind=`. Retrieve resonates over the
    maths knowledge; reflect checks the answer against `choices`/`verifier`."""
    project_facts(graph, graph.query_facts(), embed)
    return mind_hooks(
        graph,
        embed,
        choices=choices,
        verifier=verifier,
        limit=limit,
        floor=floor,
        max_context_chars=max_context_chars,
        max_neighbours=max_neighbours,
    )