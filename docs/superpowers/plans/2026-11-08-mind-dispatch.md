# Mind Dispatch (Slice B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A model-free perceive dispatcher that decides which maths capability a request maps to and executes it on the existing skill bus, returning the real result.

**Architecture:** Reuse `routing.by_words` (deterministic word-overlap) over a `Candidate` per maths capability; map the pick to a `SkillMessage` with keyword-extracted slots; route on `SkillRegistry`; return the bus value beside the router's `why`. No model, no new router.

**Tech Stack:** Python 3.12, existing `kosika.routing`, `kosika.skills_framework`, `kosika.brain_skills`, `kosika.kl`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-08-mind-maths-design.md`

## Global Constraints

- Deterministic / model-free: never call a model; `by_words` only. No-match returns a structured result, never a guess.
- TDD: every production function gets a failing test first, watched fail for the right reason, then minimal code to pass.
- Impact check before each edit (text-search fallback — GitNexus runner is down); warn on HIGH/CRITICAL.
- Only create `kosika/mind_dispatch.py`, `tests/test_mind_dispatch.py`, `examples/mind_dispatch/demo.py`. Do not modify `routing.py`, `brain_skills.py`, `knowledge_graph.py`, or `kl.py`.
- **No commits unless the user explicitly asks.** Each task ends at a green checkpoint; staging/commit is deferred to the user. Do not stage the user's in-progress files.
- Run tests from `projects/kosika` with `python -m pytest`.

## File Structure

- `kosika/mind_dispatch.py` — the dispatcher: capability `Candidate`s, `dispatch(registry, request)`, payload builders, named-rule library. One responsibility: perceive → route → execute.
- `tests/test_mind_dispatch.py` — deterministic tests over a real bus + real engines.
- `examples/mind_dispatch/demo.py` — real end-to-end run (no Ollama).

## Interfaces (verified)

- `routing.Candidate(agent_id: str, title: str, about: str)`, `routing.Route(agent_id, why)`, `routing.by_words(candidates: list[Candidate], request: str) -> Route | None`.
- `skills_framework.SkillMessage(sender, recipient, type, payload)`, `SkillRegistry.route(msg) -> reply`; `reply.type` ∈ `{"result","error"}`; `reply.payload["value"]` / `reply.payload["problem"]`.
- `brain_skills.MATHS == "kosika.maths"`, `brain_skills.MEMORY == "kosika.memory"`, `load_brain_skills(registry, graph, stack)`.
- maths `visualize` `{title?}` → `{svg, html, digest, strategy, nodes, edges, count}`; maths `query` `{"query": KL}` → `{columns, rows, count, ok, problem}`; maths `infer` `{"rules":[spec]}` → `{inferred}`; memory `recall` `{}` → `{facts, count}`.
- KL catalogue query `(query (where (match ?x ?r ?y)))` binds `?r`; filtered `(query (where (match ?x is-a ?y)))`.

---

### Task B1: Dispatcher skeleton + visualize path

**Files:**
- Create: `kosika/mind_dispatch.py`
- Test: `tests/test_mind_dispatch.py`

**Interfaces:**
- Consumes: `routing.by_words`, `Candidate`, `SkillMessage`, `SkillRegistry.route`, `brain_skills.MATHS`, `load_brain_skills`.
- Produces: `dispatch(registry: SkillRegistry, request: str) -> dict` with keys `capability` (str|None), `why` (str), `ok` (bool), `value` (Any|None), `problem` (str). Later tasks extend the capability set, not this envelope.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mind_dispatch.py
"""Model-free perceive dispatcher: the brain decides which maths capability a
request maps to, then executes it on the real bus. No model is asked."""
from __future__ import annotations

from kosika.brain_skills import MEMORY, load_brain_skills
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.memory_tiers import MemoryStack
from kosika.mind_dispatch import dispatch
from kosika.skills_framework import SkillMessage, SkillRegistry


def _registry():
    graph = KnowledgeGraph()
    ctx = Context(source="test", timestamp=0)
    graph.add_hyperedge(Hyperedge(id="tax", context=ctx, facts=[
        Fact("sin", "is-a", "trig-function", ctx),
        Fact("cos", "is-a", "trig-function", ctx),
        Fact("tan", "is-a", "trig-function", ctx),
        Fact("trig-function", "is-a", "function", ctx),
    ]))
    registry = SkillRegistry()
    load_brain_skills(registry, graph, MemoryStack())
    return registry, graph


def test_dispatch_draws_the_knowledge_graph():
    registry, _ = _registry()
    out = dispatch(registry, "draw the taxonomy")
    assert out["capability"] == "visualize"
    assert out["ok"] is True
    assert out["value"]["count"] == 4
    assert "<svg" in out["value"]["svg"]
    assert out["why"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kosika.mind_dispatch'`.

- [ ] **Step 3: Write minimal implementation**

```python
# kosika/mind_dispatch.py
"""Model-free perceive dispatcher (Mind layer, Slice B).

The brain decides which maths capability a request maps to, then executes it
on the skill bus and returns the real result. Reuses routing.by_words; never
asks a model. This is the Mind spec's perceive stage applied to the maths
component — the first component of the eventual registry, not the registry
itself.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from .brain_skills import MATHS
from .routing import Candidate, by_words
from .skills_framework import SkillMessage, SkillRegistry

_CANDIDATES = [
    Candidate(
        "visualize", "Draw the knowledge graph",
        "draw visualize visualise picture diagram graph show render taxonomy "
        "hierarchy picture of what you know",
    ),
]


def _message(capability: str, request: str) -> Tuple[str, str, Dict[str, Any]]:
    if capability == "visualize":
        return MATHS, "visualize", {"title": request}
    raise ValueError(f"no message mapping for capability {capability!r}")


def dispatch(registry: SkillRegistry, request: str) -> Dict[str, Any]:
    route = by_words(_CANDIDATES, request)
    if route is None:
        return {
            "capability": None, "why": "", "ok": False, "value": None,
            "problem": f"no capability matched request {request!r}",
        }
    recipient, type_, payload = _message(route.agent_id, request)
    reply = registry.route(SkillMessage("mind", recipient, type_, payload))
    return {
        "capability": route.agent_id,
        "why": route.why,
        "ok": reply.type == "result",
        "value": reply.payload.get("value"),
        "problem": reply.payload.get("problem", ""),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_mind_dispatch.py tests/test_brain_skills.py -q`
Expected: PASS. No commit (user-gated).

---

### Task B2: Query path (relation filter + catalogue)

**Files:**
- Modify: `kosika/mind_dispatch.py` (add a Candidate, a `_query_payload` helper, a `_message` branch)
- Test: `tests/test_mind_dispatch.py`

**Interfaces:**
- Consumes: B1 `dispatch` envelope; maths `query` grid; KL variable relation.
- Produces: `_query_payload(request: str) -> dict` (internal); `dispatch(..., "list the is-a links")` → capability `"query"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_mind_dispatch.py`)

```python
def test_dispatch_lists_is_a_links():
    registry, _ = _registry()
    out = dispatch(registry, "list the is-a links")
    assert out["capability"] == "query"
    assert out["ok"] is True
    assert out["value"]["count"] == 4
    assert out["value"]["columns"] == ["?x", "?y"]


def test_dispatch_catalogue_when_no_relation_named():
    registry, _ = _registry()
    out = dispatch(registry, "query everything you know")
    assert out["capability"] == "query"
    assert out["ok"] is True
    assert out["value"]["count"] == 4
    assert "?r" in out["value"]["columns"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: FAIL — `capability` is `None` (no query Candidate yet) so the assertions fail.

- [ ] **Step 3: Implement** — add `import re` at the top; add the query Candidate to `_CANDIDATES`; add the helper and branch.

Add to `_CANDIDATES` (after the visualize Candidate):

```python
    Candidate(
        "query", "Query the knowledge graph",
        "query list catalogue facts links relations is-a parent grandparent "
        "know knowledge everything what are which",
    ),
```

Add the helper (above `_message`):

```python
_REL_RE = re.compile(r"\b([a-z]+(?:-[a-z]+)+)\b")


def _query_payload(request: str) -> Dict[str, Any]:
    match = _REL_RE.search(request.lower())
    if match:
        relation = match.group(1)
        return {"query": f"(query (where (match ?x {relation} ?y)))"}
    return {"query": "(query (where (match ?x ?r ?y)))"}
```

Add the branch in `_message` (before the `raise`):

```python
    if capability == "query":
        return MATHS, "query", _query_payload(request)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_mind_dispatch.py tests/test_kl_tool.py -q`
Expected: PASS. No commit.

---

### Task B3: Infer path (named-rule library)

**Files:**
- Modify: `kosika/mind_dispatch.py` (add Candidate, `SUBSUMPTION` rule, `_message` branch returning a problem when no rule is named)
- Test: `tests/test_mind_dispatch.py`

**Interfaces:**
- Consumes: B1 envelope; maths `infer` `{rules:[spec]}`.
- Produces: `SUBSUMPTION: dict` rule spec; `dispatch(..., "derive the is-a hierarchy")` → capability `"infer"`, `value["inferred"] == 3`. `_message` may return `(None, None, problem)` to signal a capability-level refusal; `dispatch` handles that.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_dispatch_derives_transitive_is_a():
    registry, _ = _registry()
    out = dispatch(registry, "derive the is-a hierarchy")
    assert out["capability"] == "infer"
    assert out["ok"] is True
    assert out["value"]["inferred"] == 3


def test_infer_without_a_named_rule_asks_for_one():
    registry, _ = _registry()
    out = dispatch(registry, "infer something")
    assert out["capability"] == "infer"
    assert out["ok"] is False
    assert "named rule" in out["problem"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: FAIL — `capability` is `None` for both (no infer Candidate).

- [ ] **Step 3: Implement.** Add the infer Candidate to `_CANDIDATES`:

```python
    Candidate(
        "infer", "Derive new facts",
        "infer derive transitive subsumption prove hierarchy rules entail",
    ),
```

Add the named rule (module level):

```python
SUBSUMPTION = {
    "id": "subsumption",
    "name": "is-a transitivity",
    "pattern": "(and (match ?x is-a ?y) (match ?y is-a ?z))",
    "action": {"head": "?x", "relation": "is-a", "tail": "?z"},
}
_RULE_TRIGGERS = ("transitive", "is-a", "subsumption", "hierarchy")
```

Change `_message` to allow a capability-level refusal. Replace the visualize/query
branches' signature use by adding this infer branch before the `raise`, and make
`_message` return a 3-tuple where a refusal is `(None, None, problem)`:

```python
    if capability == "infer":
        if any(word in request.lower() for word in _RULE_TRIGGERS):
            return MATHS, "infer", {"rules": [SUBSUMPTION]}
        return None, None, "infer needs a named rule (try 'transitive' or 'is-a')"
```

Update `dispatch` to handle the refusal tuple (replace the body after the
`route is None` guard):

```python
    recipient, type_, payload = _message(route.agent_id, request)
    if recipient is None:
        return {
            "capability": route.agent_id, "why": route.why, "ok": False,
            "value": None, "problem": payload,
        }
    reply = registry.route(SkillMessage("mind", recipient, type_, payload))
    return {
        "capability": route.agent_id,
        "why": route.why,
        "ok": reply.type == "result",
        "value": reply.payload.get("value"),
        "problem": reply.payload.get("problem", ""),
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_mind_dispatch.py tests/test_brain_skills.py -q`
Expected: PASS. No commit.

---

### Task B4: Recall path + explicit no-match

**Files:**
- Modify: `kosika/mind_dispatch.py` (add recall Candidate + branch; import MEMORY)
- Test: `tests/test_mind_dispatch.py`

**Interfaces:**
- Consumes: B1 envelope; memory `recall`.
- Produces: `dispatch(..., "recall what you know")` → capability `"recall"`; unrelated request → `capability None`, `ok False`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_dispatch_recalls_remembered_facts():
    registry, _ = _registry()
    registry.route(SkillMessage("host", MEMORY, "remember", {
        "facts": [{"head": "alice", "relation": "parent", "tail": "bob"}],
    }))
    out = dispatch(registry, "recall what you know")
    assert out["capability"] == "recall"
    assert out["ok"] is True
    assert out["value"]["count"] >= 1


def test_unrelated_request_is_a_graceful_no_match():
    registry, _ = _registry()
    out = dispatch(registry, "banana helicopter nonsense")
    assert out["capability"] is None
    assert out["ok"] is False
    assert "no capability matched" in out["problem"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: FAIL — recall test gets `capability None` (no recall Candidate). The no-match test already passes (B1 guard); that is fine, it locks the behavior.

- [ ] **Step 3: Implement.** Change the import to `from .brain_skills import MATHS, MEMORY`. Add the recall Candidate to `_CANDIDATES`:

```python
    Candidate(
        "recall", "Recall remembered facts",
        "recall remember memory forget facts you know remembered",
    ),
```

Add the branch in `_message` before the `raise`:

```python
    if capability == "recall":
        return MEMORY, "recall", {}
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_mind_dispatch.py tests/test_brain_skills.py tests/test_skill_orchestration.py -q`
Expected: PASS. No commit.

---

### Task B5: Real demo (runnable now, no Ollama)

**Files:**
- Create: `examples/mind_dispatch/demo.py`

**Interfaces:**
- Consumes: `dispatch`, `load_brain_skills`, the taxonomy graph.
- Produces: a printed real run showing the brain deciding visualize / query / infer / recall / no-match.

- [ ] **Step 1: Write the demo**

```python
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
```

- [ ] **Step 2: Run the demo for real**

Run: `python examples/mind_dispatch/demo.py`
Expected: visualize → 4 edges + a digest; query → 4 rows; infer → inferred 3; recall → ≥0 facts; unrelated → "no capability matched". Capture the real output to show the user.

- [ ] **Step 3: Checkpoint**

Run: `python -m pytest tests/test_mind_dispatch.py -q`
Expected: PASS (7 tests). No commit. Present the real demo output to the user.

---

## Self-Review

- **Spec coverage:** Slice B "perceive dispatcher" → B1–B4; "real demo now" → B5; visualize/query/infer/recall + no-match all covered; named-rule infer covered; relation filter + catalogue covered.
- **Placeholder scan:** none — every step has real code or a real command.
- **Type consistency:** `dispatch` envelope keys (`capability/why/ok/value/problem`) identical across B1–B4; `_message` 3-tuple refusal `(None, None, problem)` introduced in B3 and handled in `dispatch` the same task; `MATHS`/`MEMORY` imported from `brain_skills`; `SUBSUMPTION` spec shape matches maths `infer` (`pattern` + `action`).
- **Routing reality:** `by_words` filters common words and splits `is-a` into `is`/`a`; demo requests therefore carry distinctive trigger words (draw/list/derive/recall). Relation filtering uses a hyphenated-token regex on the raw request, independent of `by_words` tokenization.