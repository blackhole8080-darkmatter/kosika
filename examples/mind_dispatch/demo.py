"""The brain decides how maths is used — model-free perceive dispatch.

Loads the maths taxonomy, then hands plain requests to the dispatcher. Watch
it pick a capability by words and execute it on the bus: draw the graph, list
links, derive transitivity, recall memory, and refuse an unrelated request.
No model is asked.

    python examples/mind_dispatch/demo.py
"""
from __future__ import annotations

from kosika.brain_skills import load_brain_skills
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.memory_tiers import MemoryStack
from kosika.mind_dispatch import dispatch
from kosika.skills_framework import SkillRegistry


def taxonomy() -> KnowledgeGraph:
    graph = KnowledgeGraph()
    ctx = Context(source="maths-brain", timestamp=0)
    graph.add_hyperedge(Hyperedge(id="tax", context=ctx, facts=[
        Fact("sin", "is-a", "trig-function", ctx),
        Fact("cos", "is-a", "trig-function", ctx),
        Fact("tan", "is-a", "trig-function", ctx),
        Fact("trig-function", "is-a", "function", ctx),
    ]))
    return graph


def main() -> None:
    registry = SkillRegistry()
    load_brain_skills(registry, taxonomy(), MemoryStack())
    print("The brain decides which maths capability each request maps to:\n")
    for request in (
        "draw the taxonomy",
        "list the is-a links",
        "derive the is-a hierarchy",
        "recall what you know",
        "banana helicopter nonsense",
    ):
        out = dispatch(registry, request)
        print(f"  request: {request!r}")
        print(f"    capability: {out['capability']}  ok: {out['ok']}")
        if out["capability"] == "visualize" and out["ok"]:
            print(f"    drew {out['value']['count']} edges, digest {out['value']['digest']}")
        elif out["capability"] == "query" and out["ok"]:
            print(f"    rows: {out['value']['rows']}")
        elif out["capability"] == "infer" and out["ok"]:
            print(f"    inferred: {out['value']['inferred']}")
        elif out["capability"] == "recall" and out["ok"]:
            print(f"    recalled: {out['value']['count']} facts")
        else:
            print(f"    problem: {out['problem']}")
        print()


if __name__ == "__main__":
    main()
