"""Live demonstration of the Kosika Mind layer, Slice 1.

Runs the whole cognitive loop for real: build a knowledge graph with genuine
nomic-embed-text vectors, persist it in SQLite, load it back, resonate across
fields, then ask qwen2.5:7b through the ordinary runtime with the Mind folded
in -- and show the difference the loop makes to the prompt and the answer.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kosika.checker import check
from kosika.embeddings import ollama_embedder
from kosika.graph_store import load_graph, save_graph
from kosika.knowledge_graph import Entity, EntityType, KnowledgeGraph, Relationship, RelationType
from kosika.mind import RELEVANCE_FLOOR, mind_hooks, propagate, reflect
from kosika.model import OpenAICompatibleProvider
from kosika.parser import parse
from kosika.runtime import replay, run

OLLAMA = "http://localhost:11434"
CHAT_MODEL = "qwen2.5:7b"
EMBED_MODEL = "nomic-embed-text"
WORK = Path(__file__).resolve().parent / ".mind-demo"

SOURCE = """
record Ticket { text: Text }
record Routing { kind: Text sure: Number }

model assistant

fn routed(label: Choice) -> Routing {
  return {kind: label.label, sure: label.confidence}
}

workflow main(ticket: Ticket) -> Routing {
  label  = classify ticket.text into ["bug", "idea", "praise"]
  result = routed(label)
}
"""

TICKET = "When I run the digest agent it answers about chlorophyll and light even though I asked about a build failure."

# A ticket this graph really does hold memory for: it scores 0.65 against
# `photosynthesis`, above the 0.6 floor, where TICKET scores 0.53 and is left out.
ON_TOPIC = "Plants in the greenhouse dashboard are not being summarised from light readings."


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def build_graph(embed) -> KnowledgeGraph:
    """Two fields, genuinely linked: a plant's chemistry and a compiler's."""

    facts = [
        ("photo", "photosynthesis", "plants make food from light", [1.0, 0.0]),
        ("chloro", "chlorophyll", "the green pigment that catches light", [0.0, 1.0]),
        ("sun", "sunlight", "the energy a plant runs on", [0.9, 0.1]),
        ("digest", "digest agent", "a Kosika agent that reads and summarises", [0.2, 0.8]),
        ("lexer", "lexer", "turns source text into tokens", [0.7, 0.3]),
    ]
    graph = KnowledgeGraph()
    for entity_id, name, description, fallback in facts:
        graph.add_entity(
            Entity(
                id=entity_id,
                name=name,
                entity_type=EntityType.CONCEPT,
                description=description,
                embedding=embed([description])[0],
            )
        )
    graph.add_relationship(Relationship(id="r1", source="photo", target="chloro", relation_type=RelationType.PART_OF, weight=0.9))
    graph.add_relationship(Relationship(id="r2", source="photo", target="sun", relation_type=RelationType.CAUSES, weight=0.8))
    graph.add_relationship(Relationship(id="r3", source="digest", target="lexer", relation_type=RelationType.RELATED_TO, weight=0.6))
    # the bridge between the two fields, which is what resonance walks
    graph.add_relationship(Relationship(id="r4", source="digest", target="photo", relation_type=RelationType.SIMILAR_TO, weight=0.5))
    return graph


def main() -> int:
    started = time.monotonic()
    WORK.mkdir(exist_ok=True)
    db = WORK / "mind.db"

    rule("0. The models this run actually uses")
    with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=60) as reply:
        have = {model["name"].split(":")[0] for model in json.load(reply)["models"]}
    print(f"  chat    {CHAT_MODEL:22} {'present' if 'qwen2.5' in have else 'MISSING'}")
    print(f"  embed   {EMBED_MODEL:22} {'present' if 'nomic-embed-text' in have else 'MISSING'}")

    embed = ollama_embedder(EMBED_MODEL, OLLAMA)

    rule("1. Build a knowledge graph with real embeddings, then persist it")
    graph = build_graph(embed)
    save_graph(graph, db)
    print(f"  wrote {db}")
    print(f"  {db.stat().st_size:,} bytes, {len(graph.entities)} entities, {len(graph.relationships)} relationships")
    print(f"  a real vector, first 4 of {len(graph.entities['photo'].embedding)} dims: "
          f"{[round(x, 4) for x in graph.entities['photo'].embedding[:4]]}")

    rule("2. Load it back from SQLite -- the graph outlives the run")
    back = load_graph(db)
    print(f"  entities reloaded : {sorted(back.entities)}")
    print(f"  relationships     : {len(back.relationships)}")
    print(f"  vector survived   : {back.entities['photo'].embedding == graph.entities['photo'].embedding}")

    hooks = mind_hooks(back, embed=lambda text: embed([text])[0], choices=["bug", "idea", "praise"])

    rule("3. Perceive + retrieve: resonance walks across fields, not just to the nearest hit")
    asked = {"text": TICKET}
    context = hooks.context(asked)
    best = back.semantic_search(embed([TICKET])[0], top_k=1)[0]
    print(f"  the question: {TICKET}\n")
    print(f"  closest memory: {back.entities[best[0]].name} at {best[1]:.3f}, floor is {RELEVANCE_FLOOR}")
    if not context:
        print("  nothing cleared the floor, so the model is handed no memory at all --")
        print("  a half-fit recollection read first would pull the answer off-topic.")
    print("  what the Mind layer hands the model before it reasons:")
    for line in context.splitlines():
        print(f"    {line}")

    rule("4. Act: the same program, with and without the Mind layer")
    program = parse(SOURCE)
    check(program)
    provider = OpenAICompatibleProvider(base_url=OLLAMA, model=CHAT_MODEL, protocol="ollama", timeout=600.0)

    print("  (a) plain run, no Mind -- the program alone")
    plain = run(program, "main", None, inputs={"ticket": asked}, model_bindings={"assistant": provider})
    print(f"      answered: {json.dumps(plain, sort_keys=True)}")

    print("\n  (b) with the Mind layer -- retrieve folded in, reflect checking the answer")
    trace = WORK / "mind-trace.jsonl"
    with_mind = run(program, "main", trace, inputs={"ticket": asked}, model_bindings={"assistant": provider}, mind=hooks)
    print(f"      answered: {json.dumps(with_mind, sort_keys=True)}")

    print("\n  (c) a ticket this graph does hold memory for -- the floor lets it through")
    print(f"      {ON_TOPIC}")
    folded = hooks.context({"text": ON_TOPIC})
    print("      what the Mind layer folded into the prompt:")
    for line in folded.splitlines():
        print(f"        {line}")
    on_topic_trace = WORK / "mind-trace-on-topic.jsonl"
    on_topic_plain = run(
        program, "main", None,
        inputs={"ticket": {"text": ON_TOPIC}}, model_bindings={"assistant": provider},
    )
    on_topic = run(
        program, "main", on_topic_trace,
        inputs={"ticket": {"text": ON_TOPIC}}, model_bindings={"assistant": provider}, mind=hooks,
    )
    print(f"      answered without the memory: {json.dumps(on_topic_plain, sort_keys=True)}")
    print(f"      answered with it           : {json.dumps(on_topic, sort_keys=True)}")
    print(f"      and that trace replays offline: {replay(on_topic_trace) == on_topic}")

    rule("5. Prove the memory really reached the model, from the trace it wrote")
    sent = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    returned = next((event for event in sent if event.get("event") == "model_return"), None)
    called = next((event for event in sent if event.get("event") == "model_call"), None)
    print(f"  trace lines             : {len(sent)}")
    print(f"  the question as recorded: {sorted(called['arguments']) if called else None}")
    print("    (the recorded call keeps only the program's own fields, so a replay can")
    print("     check it against the schema; the memory goes in beside the answer.)")
    if returned is not None:
        mind_field = returned.get("mind") or {}
        print(f"  the model returned      : {returned.get('result')}")
        print(f"  reflect judged it       : label={mind_field.get('label')} confidence={mind_field.get('confidence')}")
        print("  the memory it resonated with, recorded in the trace:")
        for line in (mind_field.get("retrieved") or "").splitlines():
            print(f"    {line}")

    rule("6. Replay: that trace runs again offline, with no model to ask")
    again = replay(trace)
    print(f"  replayed answer: {json.dumps(again, sort_keys=True)}")
    print(f"  same as the live run: {again == with_mind}")

    rule("7. Reflect: the answer is checked, and a bad one is refused")
    good = reflect({"label": "bug", "confidence": 0.9}, choices=["bug", "idea", "praise"])
    bad = reflect({"label": "hallucination", "confidence": 0.9}, choices=["bug", "idea", "praise"])
    overconfident = reflect({"label": "bug", "confidence": 7}, choices=["bug", "idea", "praise"])
    print(f"  a well-formed answer : ok={good.ok}  {good.reasons}")
    print(f"  a label not offered  : ok={bad.ok}")
    for reason in bad.reasons:
        print(f"      -> {reason}")
    print(f"  confidence out of range: ok={overconfident.ok}")
    for reason in overconfident.reasons:
        print(f"      -> {reason}")

    rule("8. Spread: what stuck goes to declared peers, as files they can read")
    peers = [WORK / "node-alpha" / "inbox", WORK / "node-beta" / "inbox"]
    written = propagate({"rule": "a digest agent that answers about chlorophyll was misrouted; check its retrieve step"}, peers)
    print(f"  wrote to peers: {written}")
    for peer in peers:
        for delta in sorted(peer.glob("mind-delta-*.json")):
            print(f"    {delta.name}: {delta.read_text(encoding='utf-8')[:90]}")

    rule("Done")
    print(f"  the whole loop took {time.monotonic() - started:.1f}s against local models")
    print(f"  artifacts under {WORK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
