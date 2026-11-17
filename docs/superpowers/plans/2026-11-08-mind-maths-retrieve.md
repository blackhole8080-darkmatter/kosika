# Mind × Maths Retrieve/Think (Slice A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the maths brain's solved Fact knowledge into resonatable Mind memory, so a live runtime run folds it into the prompt before reasoning and reflect checks the answer — proven offline now, with a gated live demo for when Ollama is up.

**Architecture:** One `KnowledgeGraph` holds two representations: the Fact side (maths: `Fact`/`Hyperedge`, queried by KL) and the Entity side (`Entity`/`Relationship`, resonated over by `mind.retrieve`). No bridge exists. `maths_memory.project_facts` projects Fact triples onto the Entity side (one Entity per token, one Relationship per fact). `maths_mind_hooks` packages `project_facts` + the existing `mind_hooks` into one call the runtime already accepts via `mind=`. Nothing in `mind.py`, `runtime.py`, or `knowledge_graph.py` changes.

**Tech Stack:** Python 3.12, existing `kosika.knowledge_graph`, `kosika.mind`, `kosika.runtime`, `kosika.brain_skills`, `kosika.kl`, `kosika.graph_store`, `kosika.embeddings`, `kosika.model`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-08-mind-maths-design.md`

**Depends on:** Slice B (`docs/superpowers/plans/2026-11-08-mind-dispatch.md`) is built first per the approved staging, but Slice A does not import it — the subsumption rule is restated where needed so A stands alone.

## Global Constraints

- TDD: every production function gets a failing test first, watched fail for the right reason, then minimal code to pass.
- Deterministic wherever possible: the bridge and the offline wiring test use a stub `embed` and `ScriptedModelProvider` — no model, no Ollama. The live demo (A4) is the only model-dependent piece; it probes `/api/tags` and exits non-zero WITHOUT faking a run when Ollama is down.
- Impact check before each edit (text-search fallback — the GitNexus runner is down in this environment); warn on HIGH/CRITICAL. These tasks only CREATE files, so the blast radius is the new module's future callers (none yet).
- Only create `kosika/maths_memory.py`, `tests/test_maths_memory.py`, `tests/test_mind_maths_wiring.py`, `examples/mind_maths/demo.py`. Do not modify `knowledge_graph.py`, `kl.py`, `mind.py`, `runtime.py`, `brain_skills.py`, or `graph_store.py`.
- **No commits unless the user explicitly asks.** Each task ends at a green checkpoint; staging/commit is deferred to the user. Do not stage the user's in-progress `knowledge_graph.py` / `kl.py` or other modified files.
- Run tests from `projects/kosika` with `python -m pytest`.

## File Structure

- `kosika/maths_memory.py` — the bridge: `project_facts(graph, facts, embed)` (Fact side → Entity side) and `maths_mind_hooks(graph, embed, ...)` (project + the existing `mind_hooks`). One responsibility: make maths knowledge resonatable.
- `tests/test_maths_memory.py` — deterministic bridge unit tests (entities, relationships, mapping, weight, idempotency).
- `tests/test_mind_maths_wiring.py` — offline end-to-end: solve → project → `maths_mind_hooks` → `_ask_model` with a scripted provider; assert the maths memory reached the prompt and reflect refuses a bad answer.
- `examples/mind_maths/demo.py` — gated live run: real embeddings + real model, solve → project → persist → load → reason with/without Mind → visualize → trace + offline replay.

## Interfaces (verified)

- `knowledge_graph.Fact(head: str, relation: str, tail: str, context: Context)`; `Fact.content_hash() -> int` (stable for identical content incl. timestamp/certainty/source). `Context(source: str, timestamp: int, certainty: float = 1.0, ...)`.
- `knowledge_graph.Entity(id, name, entity_type: EntityType, description="", embedding: Optional[List[float]] = None, ...)`; `EntityType.CONCEPT`. `Relationship(id, source, target, relation_type: RelationType, weight=1.0, ...)`; `RelationType.IS_A` (`"is_a"`), `RelationType.RELATED_TO` (`"related_to"`).
- `KnowledgeGraph.add_entity(e)` overwrites by `e.id`; `add_relationship(r)` overwrites by `r.id` (so stable ids ⇒ idempotent). `query_facts() -> List[Fact]` (all facts, insertion order). `entities: Dict[str, Entity]`, `relationships: Dict[str, Relationship]`.
- `mind.mind_hooks(graph, embed, *, choices=None, verifier=None, limit=4, floor=RELEVANCE_FLOOR, max_context_chars=8000, max_neighbours=16) -> MindHooks(context, verify)`; `mind.RELEVANCE_FLOOR == 0.6`; `mind.retrieve` resonates over `IS_A`/`RELATED_TO`/`PART_OF`/`CAUSES`/`SIMILAR_TO` to depth 2.
- `runtime._ask_model(provider, model, arguments, schema, *, retries, verify_answer=None, mind=None) -> (answer: dict, reply: dict)`; folds `mind.context(arguments)` into the sent prompt JSON under `mind_context`; runs `mind.verify` (reflect); `reply["rejected"][i]["reason"]`, `reply["mind"]["label"|"confidence"|"retrieved"]`.
- `ast.ModelDecl(name="m", input="Ask", output="Answer")`; `model.ScriptedModelProvider([ModelResponse(content=json.dumps({...}))])` records `provider.requests`; `model.OpenAICompatibleProvider(base_url, model, protocol="ollama", timeout=600.0)`.
- `brain_skills.MATHS == "kosika.maths"`, `load_brain_skills(registry, graph, stack)`; maths `infer` `{"rules":[spec]}` → `{"inferred": N}` and **mutates the graph** with derived facts (`RuleEngine.infer` adds hyperedges, source `"system"`). A rule spec is `{id?, name?, pattern: <KL where body>, action: {head, relation, tail}}`.
- `skills_framework.SkillMessage(sender, recipient, type, payload)`, `SkillRegistry.route(msg) -> reply` (`reply.type` `"result"`/`"error"`, `reply.payload["value"]`/`["problem"]`). `memory_tiers.MemoryStack()`.
- `graph_store.save_graph(graph, path)` / `load_graph(path)` persist **only** entities + relationships (NOT facts). So after a reload, `query_facts()` is empty but the projected Entity side survives — run `mind_hooks` on the reloaded graph, and visualize from the in-memory solved graph.
- `knowledge_layout.render_knowledge(facts, *, title="", strategy="auto") -> {svg, html, digest, strategy, nodes, edges, count}`. `embeddings.ollama_embedder(model, base_url)` called as `embed([text])[0]`. `parser.parse`, `checker.check`, `runtime.run`/`replay`.

---

### Task A1: Project facts into entities

**Files:**
- Create: `kosika/maths_memory.py`
- Test: `tests/test_maths_memory.py`

**Interfaces:**
- Consumes: `KnowledgeGraph`, `Fact`, `Entity`, `EntityType`, `add_entity`.
- Produces: `project_facts(graph: KnowledgeGraph, facts: Iterable[Fact], embed: Callable[[str], Sequence[float]]) -> KnowledgeGraph` — returns the SAME graph; one `Entity` per unique head/tail token (id = token, name = token, `EntityType.CONCEPT`, description from the token's outgoing facts, embedding via `embed`). A2 adds the relationship loop; A3 adds `maths_mind_hooks`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_maths_memory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kosika.maths_memory'`.

- [ ] **Step 3: Write minimal implementation**

```python
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
    return graph
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_maths_memory.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_maths_memory.py tests/test_mind_retrieve.py -q`
Expected: PASS. No commit (user-gated).

---

### Task A2: Project facts into relationships (mapping, weight, idempotency)

**Files:**
- Modify: `kosika/maths_memory.py` (add the relationship loop to `project_facts`)
- Test: `tests/test_maths_memory.py`

**Interfaces:**
- Consumes: A1 `project_facts`; `Relationship`, `RelationType.IS_A`/`RELATED_TO`, `Fact.content_hash`, `Context.certainty`, `add_relationship`.
- Produces: `project_facts` now also adds one `Relationship` per fact — id `f"rel:{fact.content_hash()}"` (stable ⇒ idempotent), `is-a`/`is_a` → `IS_A` else `RELATED_TO`, `weight = fact.context.certainty`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_maths_memory.py`)

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_maths_memory.py -q`
Expected: FAIL — A1 creates no relationships, so `len(graph.relationships) == 2` fails (got 0); the mapping/weight tests fail on `StopIteration`/empty; the idempotency test fails on its first `== 2` assertion.

- [ ] **Step 3: Implement** — add the relationship loop in `project_facts`, immediately before `return graph`:

```python
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
```

(Remove the now-duplicated earlier `return graph` so the function ends with this one.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_maths_memory.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_maths_memory.py tests/test_mind_retrieve.py tests/test_knowledge_graph.py -q`
Expected: PASS. No commit.

---

### Task A3: One-call wiring + offline end-to-end proof

**Files:**
- Modify: `kosika/maths_memory.py` (add `maths_mind_hooks`; import `mind_hooks`, `MindHooks`, `RELEVANCE_FLOOR`)
- Test: `tests/test_mind_maths_wiring.py` (create)

**Interfaces:**
- Consumes: A1/A2 `project_facts`; `mind.mind_hooks`, `mind.MindHooks`, `mind.RELEVANCE_FLOOR`; `runtime._ask_model`; the offline rail (`ScriptedModelProvider`, `ModelResponse`, `ModelDecl`); maths `infer` via the bus.
- Produces: `maths_mind_hooks(graph, embed, *, choices=None, verifier=None, limit=4, floor=RELEVANCE_FLOOR, max_context_chars=8000, max_neighbours=16) -> MindHooks` — projects `graph.query_facts()` onto the Entity side, then returns `mind_hooks` over the same graph. This is the surface a host passes to `run(..., mind=...)` / `_ask_model(..., mind=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mind_maths_wiring.py
"""Solved maths knowledge reaching a model prompt, proven without a live model.

Solve (infer subsumption) -> project the facts -> maths_mind_hooks -> _ask_model
with a scripted provider. The sent prompt must carry the maths memory, and
reflect must refuse an answer outside the offered choices. Mirrors
tests/test_mind_runtime_wiring.py."""
from __future__ import annotations

import json

from kosika.ast import ModelDecl
from kosika.brain_skills import MATHS, load_brain_skills
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.maths_memory import maths_mind_hooks
from kosika.memory_tiers import MemoryStack
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.runtime import _ask_model
from kosika.skills_framework import SkillMessage, SkillRegistry

_SUBSUMPTION = {
    "id": "subsumption",
    "name": "is-a transitivity",
    "pattern": "(and (match ?x is-a ?y) (match ?y is-a ?z))",
    "action": {"head": "?x", "relation": "is-a", "tail": "?z"},
}
_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["label", "confidence"],
}
_CHOICES = ["yes", "no", "unsure"]


def _solved_graph() -> KnowledgeGraph:
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
    registry = SkillRegistry()
    load_brain_skills(registry, graph, MemoryStack())
    registry.route(
        SkillMessage("test", MATHS, "infer", {"rules": [_SUBSUMPTION]})
    )
    return graph


def _ask(provider, arguments, *, retries, mind):
    model = ModelDecl(name="m", input="Ask", output="Answer")
    return _ask_model(
        provider, model, dict(arguments), _SCHEMA, retries=retries, mind=mind
    )


def test_solved_maths_memory_is_folded_into_the_prompt():
    graph = _solved_graph()
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"label": "yes", "confidence": 0.8}))]
    )
    hooks = maths_mind_hooks(
        graph, embed=lambda text: [1.0, 0.0], choices=_CHOICES, limit=5
    )

    _ask(provider, {"text": "is sin a kind of function?"}, retries=0, mind=hooks)

    sent = json.loads(provider.requests[0].messages[1].content)
    assert "sin" in sent["mind_context"]
    assert "function" in sent["mind_context"]


def test_reflect_refuses_an_answer_outside_the_offered_choices():
    graph = _solved_graph()
    provider = ScriptedModelProvider(
        [
            ModelResponse(content=json.dumps({"label": "banana", "confidence": 0.5})),
            ModelResponse(content=json.dumps({"label": "yes", "confidence": 0.5})),
        ]
    )
    hooks = maths_mind_hooks(
        graph, embed=lambda text: [1.0, 0.0], choices=_CHOICES, limit=5
    )

    answer, reply = _ask(
        provider, {"text": "is sin a kind of function?"}, retries=1, mind=hooks
    )

    assert answer["label"] == "yes"
    assert len(provider.requests) == 2
    assert "was not offered" in reply["rejected"][0]["reason"]
    assert reply["mind"]["label"] == "yes"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_mind_maths_wiring.py -q`
Expected: FAIL — `ImportError: cannot import name 'maths_mind_hooks' from 'kosika.maths_memory'`.

- [ ] **Step 3: Implement** — add the import and the wiring function to `kosika/maths_memory.py`.

Add to the imports (after the `.knowledge_graph` import block):

```python
from .mind import MindHooks, RELEVANCE_FLOOR, mind_hooks
```

Append the function:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_mind_maths_wiring.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_maths_memory.py tests/test_mind_maths_wiring.py tests/test_mind_runtime_wiring.py tests/test_mind_retrieve.py -q`
Expected: PASS. No commit.

---

### Task A4: Gated live demo (solve → project → persist → reason → visualize → replay)

**Files:**
- Create: `examples/mind_maths/demo.py`

**Interfaces:**
- Consumes: `project_facts`, `mind_hooks`, `load_brain_skills`/`MATHS`, `ollama_embedder`, `OpenAICompatibleProvider`, `save_graph`/`load_graph`, `render_knowledge`, `parse`/`check`/`run`/`replay`.
- Produces: a real printed run when Ollama is up; a clear notice + non-zero exit when it is down. Writes `examples/mind_maths/out/graph.svg`, `mind.db`, `mind-trace.jsonl`.

- [ ] **Step 1: Write the demo**

```python
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
```

- [ ] **Step 2: Run the demo for real (gated)**

Run: `python examples/mind_maths/demo.py`
Expected when Ollama is DOWN: prints the "not reachable" notice and exits 1 (verify `echo $?` / `$LASTEXITCODE` is 1). This is the honest gated behavior — capture it.
Expected when Ollama is UP: prints inferred 3, projected 5 entities / 7 relationships, a real digest, the folded memory naming sin/function, two real model answers, and `trace replays offline: True`. Capture the real output and `out/graph.svg` to show the user.

- [ ] **Step 3: Checkpoint**

Run: `python -m pytest tests/test_maths_memory.py tests/test_mind_maths_wiring.py -q`
Expected: PASS (8 tests). No commit. Present the real demo output (or the honest gated notice) to the user.

---

## Self-Review

- **Spec coverage:** Slice A "facts → memory bridge" → A1 (entities) + A2 (relationships/mapping/weight/idempotency); "Mind retrieve/think wiring" → A3 (`maths_mind_hooks` + offline end-to-end proof); "live demo gated on Ollama, never faked" → A4. Bridge unit tests and the offline integration test both present. Persistence/replay covered in A4.
- **Placeholder scan:** none — every step has real code or a real command. The subsumption rule is restated in full in A3 and A4 (no "similar to Plan B" reference).
- **Type consistency:** `project_facts(graph, facts, embed) -> KnowledgeGraph` identical in A1/A2/A3; entity id = token, relationship id = `f"rel:{fact.content_hash()}"` (stable ⇒ idempotent, verified against `add_relationship` overwrite-by-id); `maths_mind_hooks` signature mirrors `mind_hooks` exactly and returns `MindHooks`; `_ask_model(provider, model, arguments, schema, *, retries, mind=...)` and the `reply["rejected"/"mind"]` shape match `tests/test_mind_runtime_wiring.py`.
- **Persistence reality:** `save_graph`/`load_graph` persist only entities + relationships (verified in `graph_store.py`), so A4 visualizes from the in-memory solved graph (facts present) and runs `mind_hooks` on the reloaded Entity side — it does NOT call `query_facts()` on the reloaded graph for the picture.
- **Determinism:** A1–A3 use a stub `embed` and `ScriptedModelProvider`; no Ollama. `infer` mutates the graph (verified `RuleEngine.infer` adds hyperedges), so `_solved_graph()` carries the derived `sin is-a function` facts into the projection. With `limit=5` all five entities are roots, so `sin` and `function` are unambiguously in the folded context.
- **Routing/impact:** these tasks only create files; no existing symbol is edited, so the text-search impact fallback is satisfied (no callers yet). The user's in-progress `knowledge_graph.py` / `kl.py` are read-only references here, never staged.