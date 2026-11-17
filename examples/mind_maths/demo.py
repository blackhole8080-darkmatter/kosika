"""Live: the maths brain's solved knowledge becomes Mind memory.

Solve a taxonomy (infer is-a transitivity), project the facts onto the Entity
side, persist and reload, then ask a real model with and without the Mind layer
so you can see the memory change the prompt and the answer. Visualize the solved
graph and replay the trace offline. Gated on Ollama: if it is down, this prints a
notice and exits non-zero WITHOUT faking a run.

    python examples/mind_maths/demo.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kosika.brain_skills import MATHS, load_brain_skills
from kosika.checker import check
from kosika.embeddings import ollama_embedder
from kosika.graph_store import load_graph, save_graph
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.knowledge_layout import render_knowledge
from kosika.maths_memory import project_facts
from kosika.memory_tiers import MemoryStack
from kosika.mind import mind_hooks
from kosika.model import OpenAICompatibleProvider
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.skills_framework import SkillMessage, SkillRegistry

OLLAMA = "http://localhost:11434"
CHAT_MODEL = "qwen2.5:7b"
EMBED_MODEL = "nomic-embed-text"
WORK = Path(__file__).resolve().parent / "out"

_SUBSUMPTION = {
    "id": "subsumption",
    "name": "is-a transitivity",
    "pattern": "(and (match ?x is-a ?y) (match ?y is-a ?z))",
    "action": {"head": "?x", "relation": "is-a", "tail": "?z"},
}

SOURCE = """
record Question { text: Text }
record Answer { label: Text sure: Number }

model assistant

fn routed(label: Choice) -> Answer {
  return {label: label.label, sure: label.confidence}
}

workflow main(q: Question) -> Answer {
  label  = classify q.text into ["yes", "no", "unsure"]
  result = routed(label)
}
"""

QUESTION = "Is sin a kind of function?"


def ollama_up() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=6) as reply:
            json.load(reply)
        return True
    except Exception:
        return False


def taxonomy() -> KnowledgeGraph:
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
    return graph


def main() -> int:
    if not ollama_up():
        print(
            f"Ollama is not reachable at {OLLAMA}.\n"
            f"Start it and pull {CHAT_MODEL} + {EMBED_MODEL}, then re-run.\n"
            "This demo does not fake a model run."
        )
        return 1

    WORK.mkdir(parents=True, exist_ok=True)
    embed = ollama_embedder(EMBED_MODEL, OLLAMA)
    one = lambda text: embed([text])[0]  # noqa: E731

    graph = taxonomy()
    registry = SkillRegistry()
    load_brain_skills(registry, graph, MemoryStack())
    solved = registry.route(
        SkillMessage("demo", MATHS, "infer", {"rules": [_SUBSUMPTION]})
    )
    print(f"solved: inferred {solved.payload['value']['inferred']} new is-a facts")

    project_facts(graph, graph.query_facts(), one)
    print(f"projected: {len(graph.entities)} entities, {len(graph.relationships)} relationships")

    picture = render_knowledge(graph.query_facts(), title="maths taxonomy")
    (WORK / "graph.svg").write_text(picture["svg"], encoding="utf-8")
    print(f"visualized: {picture['count']} edges, digest {picture['digest']} -> out/graph.svg")

    db = WORK / "mind.db"
    save_graph(graph, db)
    back = load_graph(db)
    print(f"persisted + reloaded: {len(back.entities)} entities survive (facts do not persist)")

    hooks = mind_hooks(back, one, choices=["yes", "no", "unsure"])
    folded = hooks.context({"text": QUESTION})
    print(f"\nquestion: {QUESTION}\nthe Mind layer folds this memory into the prompt:")
    for line in folded.splitlines():
        print(f"  {line}")

    program = parse(SOURCE)
    check(program)
    provider = OpenAICompatibleProvider(
        base_url=OLLAMA, model=CHAT_MODEL, protocol="ollama", timeout=600.0
    )
    inputs = {"q": {"text": QUESTION}}
    plain = run(program, "main", None, inputs=inputs, model_bindings={"assistant": provider})
    trace = WORK / "mind-trace.jsonl"
    with_mind = run(
        program, "main", trace, inputs=inputs,
        model_bindings={"assistant": provider}, mind=hooks,
    )
    print(f"\nanswered without memory: {json.dumps(plain, sort_keys=True)}")
    print(f"answered with memory   : {json.dumps(with_mind, sort_keys=True)}")
    print(f"trace replays offline  : {replay(trace) == with_mind}")
    print(f"\nartifacts under {WORK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
