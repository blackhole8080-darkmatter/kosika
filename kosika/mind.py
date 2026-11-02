"""The Mind layer: resonate before reasoning, reflect after, spread what stuck."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
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


def make_verify(
    *,
    choices: Sequence[str] | None = None,
    verifier: Callable[[dict], str] | None = None,
) -> Callable[[dict], tuple[str, list]]:
    def verify(answer: dict) -> tuple[str, list]:
        verdict = reflect(answer, choices=choices, verifier=verifier)
        if verdict.ok:
            return "", [answer]
        return "; ".join(verdict.reasons), []
    return verify


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
    verify: Callable[[dict], tuple[str, list]] | None = None


def mind_hooks(
    graph: KnowledgeGraph,
    embed: Callable[[str], Sequence[float]],
    *,
    choices: Sequence[str] | None = None,
    verifier: Callable[[dict], str] | None = None,
    limit: int = 4,
) -> MindHooks:
    def context(arguments: dict) -> str:
        text = " ".join(str(value) for value in arguments.values())
        return retrieve(graph, text, embed, limit=limit).context

    return MindHooks(context=context, verify=make_verify(choices=choices, verifier=verifier))
