# Mind × Maths — design (Slice B dispatcher + Slice A retrieve/think wiring)

**Date:** 2026-11-08
**Status:** approved in chat ("ok do it. show me first"); staged B → A.
**Initiative:** Kosika Brains — "kosika maths" as skills inside the brain; the brain
decides how maths is utilized. Extends the Mind layer
(`docs/superpowers/specs/2026-11-02-kosika-mind-layer-design.md`) to the maths
capability built in `KOSIKA_BRAINS_ROADMAP.md` (Phase 3.2 + Solve → Canvas).

## Goal

Let the brain itself decide when to use the maths capability, two ways:

- **Slice B — perceive (model-free, runnable now).** A deterministic dispatcher
  routes a natural-language request to the right maths capability and executes it
  on the existing skill bus, returning the real result. No model.
- **Slice A — retrieve/think (model-dependent live; offline test now).** The maths
  brain's solved knowledge becomes resonatable memory: a live runtime run folds it
  into the prompt before reasoning, and reflect checks the answer.

## What already exists (verified, do not rebuild)

- `kosika/skills_framework.py`: `SkillMessage(sender, recipient, type, payload)`,
  `SkillRegistry.route(msg) -> reply` (`reply.type` is `"result"` with
  `payload["value"]`, or `"error"` with `payload["problem"]`), `load_skill`,
  `tick_all`.
- `kosika/brain_skills.py`: `load_brain_skills(registry, graph, stack)` loads
  `kosika.maths` (`query`, `assert`, `infer`, `consolidate`, `visualize`) and
  `kosika.memory` (`remember`, `recall`, `consolidate`). Constants `MATHS`, `MEMORY`.
  - maths `query` payload `{"query": <KL text>}` → `_execute` grid
    `{columns, rows, count, ok, problem}`.
  - maths `visualize` payload `{head?, relation?, tail?, title?, strategy?}` →
    `{svg, html, digest, strategy, nodes, edges, count}`.
  - maths `infer` payload `{"rules": [spec]}` → `{inferred: N}`; a rule spec is
    `{id?, name?, pattern: <KL where body>, action: {head, relation, tail}}`.
  - memory `recall` payload `{head?, relation?, tail?, min_certainty?, source?}` →
    `{facts, count}`.
- `kosika/routing.py`: `Candidate(agent_id, title, about)`, `Route(agent_id, why)`,
  `by_words(candidates, request) -> Route | None` — deterministic word-overlap
  router (no model). This is the Mind spec's perceive hook, "exists for agents;
  the registry extends to components."
- `kosika/kl.py`: `match` accepts a **variable relation** (`(match ?x ?r ?y)` binds
  `?r`), so a catalogue query is `(query (where (match ?x ?r ?y)))`.
- `kosika/knowledge_graph.py`: one `KnowledgeGraph` holds **two representations**:
  - Fact side (maths): `Fact(head, relation, tail, context)`, `Context(source,
    timestamp, certainty)`, `Hyperedge`, `add_hyperedge`, `query_facts(...)`.
  - Entity side (Mind retrieve): `Entity(id, name, entity_type, description,
    embedding)`, `EntityType.CONCEPT`, `Relationship(id, source, target,
    relation_type, weight)`, `RelationType` (`IS_A="is_a"`, `RELATED_TO`, …),
    `add_entity`, `add_relationship`, `semantic_search`, `infer`.
  - **No bridge exists** between the two — Slice A adds it.
- `kosika/mind.py`: `mind_hooks(graph, embed, *, choices=None, verifier=None,
  limit=4, floor=RELEVANCE_FLOOR, ...) -> MindHooks(context, verify)`;
  `retrieve(graph, request, embed, ...) -> Retrieved(context, sources)`;
  `reflect(answer, *, choices, verifier) -> Verdict`.
- `kosika/runtime.py`: `run(program, workflow, trace_path, *, inputs,
  model_bindings, mind=None, ...)`; `_ask_model(provider, model, arguments, schema,
  *, retries, verify_answer=None, mind=None)` folds `mind.context` into the prompt
  as `mind_context` and runs `mind.verify` (reflect) on each answer.
- Offline rail (verified in `tests/test_mind_runtime_wiring.py`):
  `ScriptedModelProvider([ModelResponse(content=json.dumps({...}))])` records
  `provider.requests`; `embed=lambda text: [1.0, 0.0]`; `ModelDecl(name="m",
  input="Ask", output="Answer")` from `kosika.ast`. No Ollama needed.
- Live rail (verified in `mind_demo.py`): `ollama_embedder(model, base_url)` from
  `kosika.embeddings`; `OpenAICompatibleProvider(base_url, model,
  protocol="ollama", timeout=600.0)` from `kosika.model`; `save_graph`/`load_graph`
  from `kosika.graph_store`; `replay` from `kosika.runtime`. Ollama
  `nomic-embed-text` + `qwen2.5:7b` at `http://localhost:11434`.

## Slice B — model-free perceive dispatcher

**New module** `kosika/mind_dispatch.py`. Public surface:

```python
def dispatch(registry: SkillRegistry, request: str) -> dict:
    """Route a request to a maths capability and execute it on the bus.
    Returns {capability, why, ok, value} on a match, or
    {capability: None, why: "", ok: False, problem: "no capability matched ..."}
    when by_words finds no overlap. Never asks a model."""
```

- Capability `Candidate`s (agent_id = capability name), each `about` rich in its
  trigger words: `visualize`, `query`, `infer`, `recall`.
- `route = by_words(candidates, request)`; `None` → graceful no-match.
- Map the pick to a bus message with keyword-extracted slots:
  - `visualize` → `kosika.maths` `visualize` `{title: request}` (whole graph).
  - `query` → `kosika.maths` `query`; if the request names a relation token
    (e.g. `is-a`), `(query (where (match ?x <rel> ?y)))`, else the catalogue
    `(query (where (match ?x ?r ?y)))`.
  - `infer` → `kosika.maths` `infer` with a **named rule library** keyed by words
    (`transitive`/`is-a`/`subsumption` → the subsumption rule); no known rule →
    `{ok: False, problem: "infer needs a named rule ..."}` (no guessing).
  - `recall` → `kosika.memory` `recall` `{}` (all remembered facts).
- Return the router's `why` beside the real bus `value` (or `problem`).

**Tests** `tests/test_mind_dispatch.py` (deterministic, real bus + real engines).
**Demo** `examples/mind_dispatch/demo.py` — real run now: load the maths taxonomy,
then dispatch "draw the taxonomy" (SVG), "list the is-a links" (rows), "derive the
is-a hierarchy" (inferred), "recall what you know" (facts), and an unrelated
request (no-match). No Ollama.

## Slice A — facts → memory bridge + Mind retrieve/think wiring

**New module** `kosika/maths_memory.py`. Public surface:

```python
def project_facts(graph: KnowledgeGraph, facts: Iterable[Fact],
                  embed: Callable[[str], Sequence[float]]) -> KnowledgeGraph:
    """Project maths Fact triples into the Entity/Relationship graph Mind
    retrieve resonates over. One Entity per unique head/tail (description built
    from its facts, embedding via embed); one Relationship per fact
    ('is-a'/'is_a' -> IS_A, else RELATED_TO; weight = context.certainty).
    Idempotent on entity id. Returns the same graph."""
```

- Wire: `hooks = mind_hooks(project_facts(graph, graph.query_facts(), embed), embed)`
  → `run(program, ..., mind=hooks)` (or `_ask_model(..., mind=hooks)`), so solved
  maths knowledge is folded into the prompt before reasoning; reflect checks the
  answer.
- **Offline integration test** `tests/test_mind_maths_wiring.py` (runs now, no
  Ollama): solve (infer subsumption) → `project_facts` with a stub embed →
  `mind_hooks` → `_ask_model` with `ScriptedModelProvider`; assert the sent
  `mind_context` names a derived maths entity (e.g. `sin`/`function`), and reflect
  refuses an off-choice answer. Mirrors `test_mind_runtime_wiring.py`.
- **Bridge unit tests** `tests/test_maths_memory.py`: entities/relationships
  created, `is-a`→`IS_A`, unknown→`RELATED_TO`, certainty→weight, duplicate
  entities collapse, embeddings set.
- **Live demo (gated on Ollama)** `examples/mind_maths/demo.py`: real
  `nomic-embed-text` + `qwen2.5:7b`; solve → project → persist (`save_graph`) →
  load → retrieve → reason with/without Mind → visualize → trace + offline
  `replay`. Probes `/api/tags` first; if Ollama is down it prints a clear notice
  and exits non-zero WITHOUT faking a run.

## Rails (both slices)

- Deterministic tests for everything that can be deterministic; Slice A's live
  demo is the only model-dependent piece and is gated, never faked.
- Impact check before each edit (text-search fallback — the GitNexus runner is
  down in this environment); warn on HIGH/CRITICAL.
- Only new files + minimal caller edits; do not stage the user's in-progress
  `knowledge_graph.py` / `kl.py` or other modified files; nothing committed unless
  the user asks.
- TDD: every production function gets a failing test first, watched fail for the
  right reason, then minimal code to pass.

## Staging

1. **Slice B** first — build TDD, demo live now (no Ollama).
2. **Slice A** second — bridge + offline wiring test now; live demo script ready,
   run when the user starts Ollama.

## Out of scope

The general `std/mind.kos` component registry, the `/api/mind` dashboard, the
Canvas persistence shim, bounded self-improvement, and federation (Mind slices
2–4) are NOT built here. B is the perceive stage applied to the maths component
only; A is retrieve/think wiring for maths memory only.