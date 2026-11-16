# Kosika Brains — Unified Roadmap

**Goal:** Transform the existing `projects/kosika` codebase into a **modular intelligence substrate** where "Kosika Maths", "Kosika Networks", and other capabilities are **first-class skills** orchestrated by a skill-aware runtime.

---

## Current State (projects/kosika/)

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| **Language Runtime** | `runtime.py`, `agent.py` | ✅ Mature | Workflow + agent execution, tracing, approvals |
| **Knowledge Graph** | `knowledge_graph.py` | ✅ Functional | Entities, relations, triples, semantic search, inference |
| **Memory** | `memory.py` | ✅ Declarative | `recall`/`remember`/`like`, embedding similarity |
| **Networks** | `network.py` | ✅ Manifest-based | Nodes, links, memory graphs, policies, tabs |
| **Skills** | `skills.py` | ✅ Deterministic | Catalog, router, selection, notes, checks |
| **Intelligence** | `intelligence.py` | ✅ Adaptive | Pattern extraction, error prediction, resource optimization |
| **Stdlib** | `std/*.kos` | ✅ Rich | Math, NN, science, paths (A*), search (BM25), evidence |

---

## Missing Pieces (from Kosika Maths SPEC)

| SPEC Concept | Current Gap | Priority |
|--------------|-------------|----------|
| **Contextual Hypergraph** (n-ary, not triples) | Only RDF-style triples | 🔴 High |
| **5-Tier Memory** (Working→Episodic→Semantic→Procedural→Collective) | Only 1-tier (declarative) | 🔴 High |
| **Kosika Logic (KL)** query language | Only Python API queries | 🟡 Medium |
| **Upper Ontology** (BFO-aligned) | Ad-hoc entity types | 🟡 Medium |
| **Versioning/Migration** (Merkle, schema version) | None | 🟡 Medium |
| **Conflict Resolution** (CRDT, governance) | None | 🟡 Medium |
| **Neural-Symbolic Bridge** | Separate `nn.kos` | 🟢 Low |

---

## Architecture: Skills as Brain Regions

```
┌─────────────────────────────────────────────────────────────┐
│                    KOSIKA AGENT RUNTIME                     │
│  (workflow execution, tracing, approvals, model calls)     │
└───────────────────────────┬─────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ kosika.  │ │ kosika.  │ │ kosika.  │
        │ maths    │ │ memory   │ │ networks │
        │ skill    │ │ skill    │ │ skill    │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
        ┌────┴─────┐ ┌────┴─────┐ ┌────┴─────┐
        │ Hypergraph│ │ 5-Tier   │ │ Manifest │
        │ + KL     │ │ Memory   │ │ + Sync   │
        └──────────┘ └──────────┘ └──────────┘
```

---

## Phase 1: Core Data Model Upgrade (Week 1-2)

### 1.1 Replace Triple Store with Contextual Hypergraph
**File:** `kosika/knowledge_graph.py`

```python
# NEW: Hyperedge = n-ary relation (event, rule, compound concept)
@dataclass
class Hyperedge:
    id: str
    facts: List[Fact]           # Each fact: (head, relation, tail, context)
    context: Context

@dataclass
class Fact:
    head: str
    relation: str
    tail: str
    context: Context            # source, time, certainty, scope, version

@dataclass
class Context:
    source: str
    timestamp: int              # unix micros
    certainty: float            # 0.0-1.0
    scope: ContextScope
    version: int
    provenance: List[str]

# Keep backward-compat: triples = hyperedges with 1 fact
```

### 1.2 Implement 5-Tier Memory
**File:** `kosika/memory.py` → **new** `kosika/memory_tiers.py`

```python
class WorkingMemory:      # Seconds-minutes, LRU, small capacity
class EpisodicMemory:     # Hours-years, append-only, episodes, auto-summarize
class SemanticMemory:     # Indefinite, versioned, rule inference, diff/merge
class ProceduralMemory:   # Skills, compiled rules, parametric
class CollectiveMemory:   # CRDT-sync, governance-gated
class MemoryStack:        # Orchestrates consolidation: Working → Episodic → Semantic → Procedural → Collective
```

### 1.3 Add Upper Ontology
**File:** `kosika/ontology.py` (new)

```python
# Core BFO-aligned types
Entity → Continuant → IndependentContinuant → MaterialEntity/ImmaterialEntity
                    → DependentContinuant → Quality/Role/Function
         → Occurrent → Process/Event/TemporalRegion

# Core relations with axioms
part-of (transitive), participates-in, causes, knows, communicates-with, version-of, governed-by
```

---

## Phase 2: Kosika Logic (KL) Query Language (Week 2-3)

### 2.1 Parser + Interpreter
**File:** `kosika/kl.py` (new)

```python
# KTF syntax (S-expression)
(query
  (find ?agent ?skill ?prof)
  (where
    (has-skill ?agent ?skill)
    (proficiency ?agent ?skill ?prof)
    (>= ?prof 0.8))
  (order-by (desc ?prof))
  (limit 10))

# Operators: match, filter, project, aggregate, join, union, diff, recurse, infer, counterfactual
# Modes: materialized, on-demand, streaming, probabilistic, causal
```

### 2.2 Integrate with Runtime
- Add `kl_query` tool to stdlib
- Expose in agent skills
- Compile to optimized query plans

---

## Phase 3: Skill Orchestration (Week 3-4)

### 3.1 Skill Registry Enhancement
**File:** `kosika/skills.py` → extend

```python
class SkillRegistry:
    def load_skill(self, skill: Skill)          # Native or WASM
    def route(self, msg: SkillMessage)          # Cross-skill calls
    def tick_all(self, dt)                      # Background processing
```

### 3.2 Wrap Existing Capabilities as Skills

| Skill | Wraps | Message Types |
|-------|-------|---------------|
| `kosika.maths` | `knowledge_graph.py` + KL | `query`, `infer`, `assert`, `consolidate` |
| `kosika.memory` | `memory.py` + `memory_tiers.py` | `recall`, `remember`, `like`, `consolidate` |
| `kosika.networks` | `network.py` | `sync`, `discover`, `publish`, `subscribe` |
| `kosika.reasoning` | `intelligence.py` | `predict`, `optimize`, `analyze` |
| `kosika.perception` | (new) | `encode`, `segment`, `detect` |
| `kosika.action` | (new) | `plan`, `execute`, `monitor` |

---

## Phase 4: Distributed Brains (Week 4-5)

### 4.1 Network Sync for Collective Memory
- CRDT-based fact merging in `CollectiveMemory`
- Gossip protocol over `kosika.networks` skill
- Governance policy language (Rego-like)

### 4.2 Multi-Agent Coordination
- Shared `CollectiveMemory` across agents
- Skill-level message routing (not just tool calls)
- Emergent behavior from skill composition

---

## Phase 5: Developer Experience (Ongoing)

| Tool | Purpose |
|------|---------|
| `kosika brain init` | Scaffold agent with skill manifest |
| `kosika brain repl` | Interactive KL query shell |
| `kosika brain visualize` | Graph/Mermaid export |
| `kosika brain benchmark` | Query/inference/sync perf |

---

## Migration Strategy

1. **Don't break existing `.kos` programs** — hypergraph is internal; triples remain valid API
2. **Add KL as optional query tool** — `use_model kl_query(...)` or native tool
3. **Memory tiers opt-in** — `memory customers of CustomerNote tier=semantic`
4. **Skills backward-compat** — existing `skills.py` router works unchanged

---

## Progress (as of 2026-11-08)

### ✅ Phase 1.1: Hypergraph Upgrade — DONE
**File:** `kosika/knowledge_graph.py`
- Added `Context`, `ContextScope`, `Fact`, `Hyperedge` dataclasses (SPEC §4)
- `KnowledgeGraph` now stores hyperedges alongside triples
- `add_hyperedge()`, `query_facts()`, `find_hyperedges_with_context()`, `hyperedge_to_triples()`, `all_triples_from_hyperedges()`, `get_facts_for_entity()`
- `find_path()` traverses both relationships AND hypergraph facts
- `get_statistics()` includes hyperedges and facts count
- `KnowledgeBase.merge_graphs()` merges hyperedges
- Backward-compatible: triples API unchanged
- Verified: imports OK, functional test passed

### ✅ Phase 1.2: 5-Tier Memory — DONE
**File:** `kosika/memory_tiers.py` (new)
- `WorkingMemory`: LRU + TTL, indices by head/relation/tail/source
- `EpisodicMemory`: episodes with start/end time, fact tracking
- `SemanticMemory`: versioned, Merkle snapshots, diff/merge with resolvers
- `ProceduralMemory`: skills, success rate tracking
- `CollectiveMemory`: peer sync, pending facts, CRDT-ready
- `MemoryStack`: consolidation pipeline (Working→Episodic→Semantic→Procedural)
- Verified: imports OK, consolidation test passed

### ✅ Phase 2.1: Kosika Logic (KL) Parser + Interpreter — DONE
**File:** `kosika/kl.py` (new)
- KTF S-expression parser (`parse_ktf`, `parse_query`)
- AST: Query, MatchPattern, And/Or/Not/Filter/Exists/ForAll, FactPattern, FilterExpr
- `KLInterpreter`: executes queries with projection, ordering, limit/offset, aggregates
- `RuleEngine`: forward-chaining inference
- Verified: query test passed (3 agents with rust skill, Alice knows bob)

### ✅ Phase 1.3: Upper Ontology — DONE
**File:** `kosika/ontology.py` (new)
- BFO-aligned core: Entity → Continuant/Occurrent → MaterialEntity/ImmaterialEntity/Agent/Device/Skill/Knowledge/Context
- Core relations with axioms: part-of (transitive), has-part, participates-in, causes (transitive), knows, communicates-with (symmetric), version-of (transitive), derives-from (transitive), governed-by, compliant-with
- `is_subtype()` for hierarchy checking
- `validate_fact()` for domain/range constraint checking
- `DomainOntology` + `OntologyRegistry` for extensible domains
- Example domains: robotics, research
- Global registry with auto-registered domains
- Verified: all ontology tests passed

### ✅ Integration Test — DONE
Verified hypergraph + memory tiers + KL + ontology work together:
- Ontology validation of facts
- Hyperedge storage + KL querying
- Memory consolidation pipeline
- All cross-module interactions correct

---

## Current File Map

```
projects/kosika/
├── kosika/
│   ├── knowledge_graph.py      # Hypergraph + triples compat (MODIFIED)
│   ├── memory.py               # Declarative memory (existing, kept)
│   ├── memory_tiers.py         # 5-tier memory stack (NEW)
│   ├── ontology.py             # Upper ontology + domains (NEW)
│   ├── kl.py                   # Kosika Logic parser/interpreter (NEW)
│   ├── skills.py               # Skill catalog/router (existing)
│   ├── intelligence.py         # Adaptive execution (existing)
│   ├── network.py              # Network manifests (existing)
│   ├── runtime.py              # Workflow + agent runtime (existing)
│   ├── agent.py                # Agent loop (existing)
│   ├── std/
│   │   ├── math.kos, nn.kos, science_*.kos, paths.kos, etc.
│   └── ...
├── native/                     # Go runtime (existing)
├── tests/                      # Existing test suite
└── KOSIKA_BRAINS_ROADMAP.md    # This file (NEW)
```

---

## Next Immediate Steps

| Priority | Task | File | Estimate |
|----------|------|------|----------|
| 1 | **KL Runtime Integration** — Add `kl_query` tool to stdlib, expose KL queries as a callable tool | `kosika/kl.py`, `kosika/builtin_methods.py` | 1-2 days |
| 2 | **Skill Orchestration** — Enhance `skills.py` with `SkillRegistry` to route messages between skills | `kosika/skills.py` | 2-3 days |
| 3 | **Wrap memory tiers as skill** — Expose `MemoryStack` via existing `memory.py` tool bindings | `kosika/memory.py` | 1-2 days |
| 4 | **Networks skill** — Wrap `network.py` for cross-agent sync of `CollectiveMemory` | `kosika/network.py` | 2-3 days |
| 5 | **Developer experience** — `kosika brain repl`, `visualize`, `benchmark` CLI commands | `kosika/cli.py` | 3-5 days |

---

## Design Principles Preserved

1. **Backward compatibility** — All existing `.kos` programs, triples, and memory APIs continue to work unchanged.
2. **Opt-in progression** — Hypergraph, 5-tier memory, and KL are additive; existing code doesn't need to use them.
3. **Skills as brain regions** — Each capability (maths, memory, networks, reasoning, etc.) is a composable skill with a clear interface.
4. **Deterministic** — No model guesses in skill selection or query evaluation; everything is explicit and inspectable.

---

*This roadmap aligns the existing mature Kosika codebase with the Kosika Maths SPEC, treating SPEC concepts as **skills** in the Kosika Brains architecture.*