"""The Mind layer: resonate before reasoning, reflect after, spread what stuck."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Sequence

from .knowledge_graph import KnowledgeGraph, RelationType

_RESONATE = (
    RelationType.RELATED_TO, RelationType.IS_A, RelationType.PART_OF,
    RelationType.CAUSES, RelationType.SIMILAR_TO,
)

#: The least cosine similarity an entity needs to be worth handing to a model.
#: Measured with nomic-embed-text on a five-entity graph: a question the graph
#: really answers scored 0.95, a question it half-touches scored 0.53, and a
#: question with nothing to do with it scored 0.42. Below this line the hit is
#: noise, and noise read first is worse than silence -- it pulls the answer
#: toward whatever the graph happens to hold.
RELEVANCE_FLOOR = 0.6


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
    floor: float = RELEVANCE_FLOOR,
    max_context_chars: int = 8000,
    max_neighbours: int = 16,
) -> Retrieved:
    """Bound context and graph expansion; semantic search still scans entities."""
    if limit < 0 or max_context_chars < 0 or max_neighbours < 0:
        raise ValueError("retrieval limits must be non-negative")
    if limit == 0 or max_context_chars == 0:
        return Retrieved("", ())

    hits = graph.semantic_search(list(embed(request)), top_k=limit)
    kept = [entity_id for entity_id, score in hits if score >= floor]
    if not kept:
        return Retrieved("", ())
    lines: list[str] = []
    sources: list[str] = []
    rendered: set[str] = set()
    remaining = max_context_chars

    def append_entity(entity_id: str, prefix: str) -> bool:
        nonlocal remaining
        if entity_id in rendered:
            return False
        entity = graph.entities.get(entity_id)
        if entity is None:
            return False
        # Measure before formatting so an oversized fact is never copied.
        size = len(prefix) + len(entity.name) + 2 + len(entity.description) + bool(lines)
        if size > remaining:
            return False
        lines.append(f"{prefix}{entity.name}: {entity.description}")
        remaining -= size
        rendered.add(entity_id)
        return True

    # Reserve space for primary hits before considering any neighbours.
    for entity_id in kept:
        if append_entity(entity_id, "- "):
            sources.append(entity_id)
        if remaining == 0:
            break
    if not sources or remaining == 0 or max_neighbours == 0:
        return Retrieved("\n".join(lines), tuple(sources))

    # Bound the scan itself, including unrelated entries, preserving insertion order.
    adjacency: dict[tuple[str, RelationType], list[str]] = {}
    for edge in islice(graph.relationships.values(), 4096):
        if edge.relation_type in _RESONATE:
            adjacency.setdefault((edge.source, edge.relation_type), []).append(edge.target)

    neighbours = 0
    for entity_id in sources:
        for relation in _RESONATE:
            queue = deque([(entity_id, 0)])
            visited = {entity_id}
            while queue:
                current, depth = queue.popleft()
                if depth >= 2:
                    continue
                for other in adjacency.get((current, relation), ()):
                    if other in visited:
                        continue
                    visited.add(other)
                    queue.append((other, depth + 1))
                    if append_entity(other, "  - relates to "):
                        neighbours += 1
                        if neighbours == max_neighbours or remaining == 0:
                            return Retrieved("\n".join(lines), tuple(sources))
    return Retrieved("\n".join(lines), tuple(sources))


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...]


def reflect(
    answer: dict,
    *,
    choices: Sequence[str] | None = None,
    verifier: Callable[[dict], str] | None = None,
) -> Verdict:
    reasons: list[str] = []
    confidence = answer.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            reasons.append(f"confidence must be a number in [0,1], got {confidence!r}")
    label = answer.get("label")
    if choices is not None and label is not None and label not in choices:
        reasons.append(f"label {label!r} was not offered; offered: {', '.join(choices)}")
    if verifier is not None:
        complaint = verifier(answer)
        if complaint.strip():
            reasons.append(f"the verifying model refused: {complaint.strip()}")
    return Verdict(not reasons, tuple(reasons))


def make_reflect(
    *,
    choices: Sequence[str] | None = None,
    verifier: Callable[[dict], str] | None = None,
) -> Callable[[dict], tuple[str, dict]]:
    """The reflect check, as something that judges one answer.

    It gives the complaint, empty when the answer stands, and what it judged,
    which the trace keeps under its own name. It is deliberately not the shape
    of the program's own check: that one is a recorded function call, and a
    replay proves it again, which the Mind layer is not.
    """

    def reflect_on(answer: dict) -> tuple[str, dict]:
        verdict = reflect(answer, choices=choices, verifier=verifier)
        if verdict.ok:
            return "", {"label": answer.get("label"), "confidence": answer.get("confidence")}
        return "; ".join(verdict.reasons), {}

    return reflect_on


def propagate(delta: dict, peer_dirs: Sequence[Path]) -> tuple[str, ...]:
    written: list[str] = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for peer in peer_dirs:
        peer.mkdir(parents=True, exist_ok=True)
        target = peer / f"mind-delta-{stamp}-{len(written)}.json"
        target.write_text(json.dumps(delta, sort_keys=True), encoding="utf-8")
        written.append(peer.parent.name)
    return tuple(written)


@dataclass(frozen=True)
class MindHooks:
    """The two ways the Mind layer reaches into a model call.

    `context` turns a step's arguments into memory to fold into the prompt;
    `verify` is the reflect check the runtime runs on each answer. Either may
    be None, so a caller can retrieve without reflecting, or the reverse.
    """

    context: Callable[[dict], str] | None = None
    verify: Callable[[dict], tuple[str, dict]] | None = None


def mind_hooks(
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
    if limit < 0 or max_context_chars < 0 or max_neighbours < 0:
        raise ValueError("retrieval limits must be non-negative")

    def context(arguments: dict) -> str:
        text = " ".join(str(value) for value in arguments.values())
        return retrieve(
            graph, text, embed, limit=limit, floor=floor,
            max_context_chars=max_context_chars, max_neighbours=max_neighbours,
        ).context

    return MindHooks(context=context, verify=make_reflect(choices=choices, verifier=verifier))
