"""The Mind layer: resonate before reasoning, reflect after, spread what stuck."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .knowledge_graph import KnowledgeGraph, RelationType

_RESONATE = (
    RelationType.RELATED_TO, RelationType.IS_A, RelationType.PART_OF,
    RelationType.CAUSES, RelationType.SIMILAR_TO,
)


@dataclass(frozen=True)
class Retrieved:
    context: str
    sources: tuple[str, ...]


def retrieve(
    graph: KnowledgeGraph,
    request: str,
    embed: Callable[[str], Sequence[float]],
    *,
    limit: int = 4,
) -> Retrieved:
    hits = graph.semantic_search(list(embed(request)), top_k=limit)
    kept = [entity_id for entity_id, score in hits if score > 0.0]
    if not kept:
        return Retrieved("", ())
    lines: list[str] = []
    for entity_id in kept:
        entity = graph.entities[entity_id]
        lines.append(f"- {entity.name}: {entity.description}")
        for relation in _RESONATE:
            for other in graph.infer(entity_id, relation, max_depth=2):
                neighbour = graph.entities.get(other)
                if neighbour:
                    lines.append(f"  - relates to {neighbour.name}: {neighbour.description}")
    return Retrieved("\n".join(lines), tuple(kept))
