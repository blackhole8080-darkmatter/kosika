"""Composable brain skills for the orchestration bus (roadmap Phase 3.2).

Two capabilities wrap engines the brains already have:

    kosika.maths   knowledge_graph + KL   query, assert, infer, consolidate, visualize
    kosika.memory  memory_tiers           remember, recall, consolidate

Each is a Skill whose handler is `implementation(type, payload)`, so the
registry routes a SkillMessage to it. They compose: maths `consolidate`
routes a `remember` message to memory, handing over what it knows; memory
`consolidate` (also its background tick) runs the tier pipeline upward. A
host loads both with `load_brain_skills(registry, graph, stack)` and drives
background processing with `registry.tick_all(dt)`.

These are native, in-process skills. `like` (similarity recall) lives in
memory.py's MemoryStore and needs an embedding model, so it is not here.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .kl import Rule, RuleEngine, parse_query
from .knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from .knowledge_tools import _execute
from .memory_tiers import MemoryStack
from .skills_framework import (
    Skill,
    SkillKind,
    SkillMessage,
    SkillOutput,
    SkillParameter,
    SkillRegistry,
    SkillStatus,
)

MATHS = "kosika.maths"
MEMORY = "kosika.memory"


def _fact_dict(fact: Fact) -> Dict[str, Any]:
    return {
        "head": fact.head,
        "relation": fact.relation,
        "tail": fact.tail,
        "source": fact.context.source,
        "certainty": fact.context.certainty,
    }


def _to_fact(data: Dict[str, Any], default_source: str) -> Fact:
    context = Context(
        source=str(data.get("source", default_source)),
        timestamp=int(data.get("timestamp", time.time() * 1_000_000)),
        certainty=float(data.get("certainty", 1.0)),
    )
    return Fact(str(data["head"]), str(data["relation"]), str(data["tail"]), context)


def _parameters() -> List[SkillParameter]:
    return [
        SkillParameter("type", "Text", description="the message type"),
        SkillParameter(
            "payload", "Record", required=False, description="the message body"
        ),
    ]


def maths_skill(registry: SkillRegistry, graph: KnowledgeGraph) -> Skill:
    """Wrap the knowledge graph and KL as a skill that can compose outward."""

    def handle(type: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        payload = payload or {}
        if type == "query":
            return _execute(graph, payload["query"])
        if type == "assert":
            fact = _to_fact(payload, MATHS)
            graph.add_hyperedge(
                Hyperedge(
                    id=f"assert:{fact.content_hash()}",
                    facts=[fact],
                    context=fact.context,
                )
            )
            return {"asserted": True, "fact": _fact_dict(fact)}
        if type == "infer":
            engine = RuleEngine(graph)
            for spec in payload.get("rules", []):
                # A rule pattern is a KL `where` body; reuse the public query
                # parser to turn its text into the pattern AST the engine wants.
                pattern = parse_query(f"(query (where {spec['pattern']}))").pattern
                engine.add_rule(
                    Rule(
                        id=str(spec.get("id", spec.get("name", "rule"))),
                        name=str(spec.get("name", "rule")),
                        pattern=pattern,
                        action={"type": "assert", "fact_pattern": spec["action"]},
                    )
                )
            return {"inferred": engine.infer()}
        if type == "consolidate":
            facts = [_fact_dict(f) for f in graph.query_facts()]
            reply = registry.route(
                SkillMessage(MATHS, MEMORY, "remember", {"facts": facts, "source": MATHS})
            )
            if reply.type == "error":
                return {"remembered": 0, "problem": reply.payload["problem"]}
            return {"remembered": reply.payload["value"]["remembered"], "facts": len(facts)}
        if type == "visualize":
            from .knowledge_layout import render_knowledge

            filters = {
                key: payload[key]
                for key in ("head", "relation", "tail")
                if key in payload
            }
            facts = graph.query_facts(**filters)
            return render_knowledge(
                facts,
                title=str(payload.get("title", "")),
                strategy=str(payload.get("strategy", "auto")),
            )
        raise ValueError(f"unknown maths message type {type!r}")

    return Skill(
        id=MATHS,
        name="Maths",
        kind=SkillKind.KNOWLEDGE,
        description="Query, assert and infer over the knowledge graph with KL; "
        "consolidate what it knows into memory, and visualize it as a labelled graph.",
        parameters=_parameters(),
        output=SkillOutput("Record"),
        implementation=handle,
        status=SkillStatus.VALIDATED,
        tags=["maths", "knowledge", "kl"],
    )


def memory_skill(stack: MemoryStack) -> Skill:
    """Wrap the 5-tier memory stack as a skill: remember, recall, consolidate."""

    def handle(type: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        payload = payload or {}
        if type == "remember":
            source = str(payload.get("source", MEMORY))
            facts = [_to_fact(f, source) for f in payload.get("facts", [])]
            context = Context(source=source, timestamp=int(time.time() * 1_000_000))
            stack.add_experience(facts, context)
            return {"remembered": len(facts)}
        if type == "recall":
            filters = {
                key: payload[key]
                for key in ("head", "relation", "tail", "min_certainty", "source")
                if key in payload
            }
            seen: Dict[int, Fact] = {}
            for tier in (stack.working, stack.episodic, stack.semantic):
                for fact in tier.query(**filters):
                    seen[fact.content_hash()] = fact
            facts = [_fact_dict(f) for f in seen.values()]
            return {"facts": facts, "count": len(facts)}
        if type == "consolidate":
            return _report(stack)
        raise ValueError(f"unknown memory message type {type!r}")

    return Skill(
        id=MEMORY,
        name="Memory",
        kind=SkillKind.LEARNING,
        description="Remember and recall facts across the memory tiers; "
        "consolidate working memory upward.",
        parameters=_parameters(),
        output=SkillOutput("Record"),
        implementation=handle,
        status=SkillStatus.VALIDATED,
        tags=["memory", "tiers"],
    )


def _report(stack: MemoryStack) -> Dict[str, Any]:
    report = stack.consolidate()
    return {
        "working_to_episodic": report.working_to_episodic,
        "episodic_to_semantic": report.episodic_to_semantic,
        "semantic_to_procedural": report.semantic_to_procedural,
    }


def load_brain_skills(
    registry: SkillRegistry, graph: KnowledgeGraph, stack: MemoryStack
) -> SkillRegistry:
    """Load memory then maths, so maths can route to memory. Memory's
    background tick consolidates the tiers; `registry.tick_all(dt)` drives it."""
    registry.load_skill(memory_skill(stack), tick=lambda dt: _report(stack))
    registry.load_skill(maths_skill(registry, graph))
    return registry