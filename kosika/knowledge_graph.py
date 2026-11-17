"""Knowledge Representation and Reasoning System for Kosika Agents.

This system provides a structured way to represent, store, and reason over knowledge.
It enables agents to be smarter through better knowledge organization and retrieval.

Key features:
- Contextual hypergraph with n-ary relations (hyperedges)
- Backward-compatible triple view
- Semantic search and similarity
- Knowledge inference and reasoning
- Knowledge versioning and provenance
- Hierarchical knowledge organization
- Knowledge validation and consistency checking
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class EntityType(Enum):
    """Types of entities in the knowledge graph."""

    CONCEPT = "concept"  # Abstract concept
    INSTANCE = "instance"  # Specific instance
    PROPERTY = "property"  # Property or attribute
    EVENT = "event"  # Event or occurrence
    RULE = "rule"  # Rule or principle


class RelationType(Enum):
    """Types of relationships."""

    IS_A = "is_a"  # Taxonomic (instance is a type)
    PART_OF = "part_of"  # Meronomic (part-whole)
    RELATED_TO = "related_to"  # General association
    CAUSES = "causes"  # Causal relationship
    PRECEDES = "precedes"  # Temporal ordering
    SIMILAR_TO = "similar_to"  # Similarity
    OPPOSITE_OF = "opposite_of"  # Antonym
    PROPERTY_OF = "property_of"  # Attribute relationship
    INSTANCE_OF = "instance_of"  # Instantiation


@dataclass
class Entity:
    """An entity in the knowledge graph."""

    id: str
    name: str
    entity_type: EntityType
    description: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, Entity):
            return False
        return self.id == other.id


@dataclass
class Relationship:
    """A relationship between entities (backward-compat triple view)."""

    id: str
    source: str  # Entity ID
    target: str  # Entity ID
    relation_type: RelationType
    weight: float = 1.0  # Confidence/strength
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, Relationship):
            return False
        return self.id == other.id


@dataclass
class KnowledgeTriple:
    """A knowledge triple (subject, predicate, object) — backward compat."""

    subject: str  # Entity ID
    predicate: str  # Relation type
    object: str  # Entity ID
    confidence: float = 1.0
    provenance: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


# ──────────────────────────────────────────────────────────────
# NEW: Contextual Hypergraph (Kosika Maths SPEC §4)
# ──────────────────────────────────────────────────────────────


class ContextScope(Enum):
    """Scope of a fact's validity."""

    GLOBAL = "global"
    SESSION = "session"
    AGENT = "agent"
    DOMAIN = "domain"
    CUSTOM = "custom"


@dataclass
class Context:
    """Context metadata attached to every fact (SPEC §4)."""

    source: str  # Who asserted this (agent ID, system, etc.)
    timestamp: int  # Unix microseconds
    certainty: float = 1.0  # 0.0 – 1.0
    scope: ContextScope = ContextScope.GLOBAL
    version: int = 1  # Monotonic per source
    provenance: List[str] = field(default_factory=list)  # Derivation chain

    def __post_init__(self):
        self.certainty = max(0.0, min(1.0, self.certainty))


@dataclass
class Fact:
    """A single atomic fact: (head, relation, tail, context)."""

    head: str  # Entity ID
    relation: str  # Relation ID (e.g., "rel:part-of")
    tail: str  # Entity ID
    context: Context

    def content_hash(self) -> int:
        """Hash of fact content excluding context.version for deduplication."""
        import hashlib

        data = f"{self.head}|{self.relation}|{self.tail}|{self.context.source}|{self.context.timestamp}|{self.context.certainty}|{self.context.scope.value}|{'|'.join(self.context.provenance)}"
        return int(hashlib.sha256(data.encode()).hexdigest()[:16], 16)


@dataclass
class Hyperedge:
    """An n-ary relation: a set of facts sharing a context (SPEC §4).

    Examples:
    - Event: (agent, action, patient, instrument, time, location) as 6 facts
    - Rule: (pattern_facts..., action_fact) with shared context
    - Nested belief: (agent, believes, (subgraph...)) via reification
    """

    id: str
    facts: List[Fact]
    context: Context

    def fact_count(self) -> int:
        return len(self.facts)

    def to_triples(self) -> List[KnowledgeTriple]:
        """Backward-compat: project to triples (loses n-ary structure)."""
        return [
            KnowledgeTriple(
                subject=f.head,
                predicate=f.relation,
                object=f.tail,
                confidence=f.context.certainty,
                provenance=f.context.source,
                timestamp=datetime.fromtimestamp(f.context.timestamp / 1_000_000),
            )
            for f in self.facts
        ]


class KnowledgeGraph:
    """Knowledge graph for storing and reasoning over knowledge."""

    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.relationships: Dict[str, Relationship] = {}
        self.triples: List[KnowledgeTriple] = []
        self.hyperedges: Dict[str, Hyperedge] = {}
        self.index: Dict[str, Set[str]] = defaultdict(set)  # relation -> entities
        self.reverse_index: Dict[str, Set[str]] = defaultdict(
            set
        )  # entity -> relations
        # Fact indices for hypergraph queries: (head, relation, tail) -> fact hashes
        self._fact_index: Dict[Tuple[str, str, str], Set[int]] = defaultdict(set)
        self._all_facts: Dict[int, Fact] = {}

    def add_entity(self, entity: Entity) -> str:
        """Add an entity to the graph."""
        self.entities[entity.id] = entity
        return entity.id

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        return self.entities.get(entity_id)

    def add_relationship(self, relationship: Relationship) -> str:
        """Add a relationship to the graph."""
        self.relationships[relationship.id] = relationship

        # Update indices
        self.index[relationship.relation_type.value].add(relationship.source)
        self.index[relationship.relation_type.value].add(relationship.target)
        self.reverse_index[relationship.source].add(relationship.relation_type.value)
        self.reverse_index[relationship.target].add(relationship.relation_type.value)

        return relationship.id

    def add_triple(self, triple: KnowledgeTriple):
        """Add a knowledge triple."""
        self.triples.append(triple)

    def add_hyperedge(self, hyperedge: Hyperedge) -> str:
        """Add a hyperedge (n-ary relation) to the graph.

        Each fact within the hyperedge is indexed individually for querying,
        while the hyperedge groups them under a shared context.
        """
        self.hyperedges[hyperedge.id] = hyperedge
        for fact in hyperedge.facts:
            h = fact.content_hash()
            self._all_facts[h] = fact
            self._fact_index[(fact.head, fact.relation, fact.tail)].add(h)
        return hyperedge.id

    def get_hyperedge(self, edge_id: str) -> Optional[Hyperedge]:
        """Get a hyperedge by ID."""
        return self.hyperedges.get(edge_id)

    def query_facts(
        self,
        head: Optional[str] = None,
        relation: Optional[str] = None,
        tail: Optional[str] = None,
        min_certainty: Optional[float] = None,
        source: Optional[str] = None,
    ) -> List[Fact]:
        """Query facts across hyperedges with filters.

        Supports backward-compatible triple lookups by matching head/relation/tail.
        """
        results = []

        # Start with all facts or narrow via index
        candidate_hashes: Set[int] = set()
        if head is not None and relation is not None and tail is not None:
            candidate_hashes = self._fact_index.get(
                (head, relation, tail), set()
            ).copy()
        elif head is not None and relation is not None:
            for (h, r, t), hashes in self._fact_index.items():
                if h == head and r == relation:
                    candidate_hashes.update(hashes)
        elif head is not None:
            for (h, r, t), hashes in self._fact_index.items():
                if h == head:
                    candidate_hashes.update(hashes)
        elif relation is not None:
            for (h, r, t), hashes in self._fact_index.items():
                if r == relation:
                    candidate_hashes.update(hashes)
        elif tail is not None:
            for (h, r, t), hashes in self._fact_index.items():
                if t == tail:
                    candidate_hashes.update(hashes)
        else:
            candidate_hashes = set(self._all_facts.keys())

        for candidate in candidate_hashes:
            fact = self._all_facts.get(candidate)
            if fact is None:
                continue
            # The index narrowing above only covers some of the three
            # constraints (e.g. relation alone when head is unbound), so
            # every candidate is re-checked against all of them here.
            if head is not None and fact.head != head:
                continue
            if relation is not None and fact.relation != relation:
                continue
            if tail is not None and fact.tail != tail:
                continue
            if min_certainty is not None and fact.context.certainty < min_certainty:
                continue
            if source is not None and fact.context.source != source:
                continue
            results.append(fact)

        return results

    def find_hyperedges_with_context(
        self,
        source: Optional[str] = None,
        min_certainty: Optional[float] = None,
        scope: Optional[ContextScope] = None,
    ) -> List[Hyperedge]:
        """Find hyperedges matching context criteria."""
        results = []
        for edge in self.hyperedges.values():
            if source is not None and edge.context.source != source:
                continue
            if min_certainty is not None and edge.context.certainty < min_certainty:
                continue
            if scope is not None and edge.context.scope != scope:
                continue
            results.append(edge)
        return results

    def hyperedge_to_triples(self, edge_id: str) -> List[KnowledgeTriple]:
        """Convert a hyperedge to backward-compatible triples."""
        edge = self.hyperedges.get(edge_id)
        if edge is None:
            return []
        return edge.to_triples()

    def all_triples_from_hyperedges(self) -> List[KnowledgeTriple]:
        """Project all hyperedge facts as triples (backward compat)."""
        result = []
        for edge in self.hyperedges.values():
            result.extend(edge.to_triples())
        return result

    def get_relationships(
        self,
        entity_id: str,
        relation_type: Optional[RelationType] = None,
        direction: str = "outgoing",
    ) -> List[Relationship]:
        """Get relationships for an entity."""
        results = []
        for rel in self.relationships.values():
            if direction == "outgoing" and rel.source == entity_id:
                if relation_type is None or rel.relation_type == relation_type:
                    results.append(rel)
            elif direction == "incoming" and rel.target == entity_id:
                if relation_type is None or rel.relation_type == relation_type:
                    results.append(rel)
        return results

    def get_facts_for_entity(
        self,
        entity_id: str,
        direction: str = "outgoing",
    ) -> List[Fact]:
        """Get all facts where entity is head or tail."""
        results = []
        for fact in self._all_facts.values():
            if direction == "outgoing" and fact.head == entity_id:
                results.append(fact)
            elif direction == "incoming" and fact.tail == entity_id:
                results.append(fact)
        return results

    def find_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 5,
    ) -> Optional[List[str]]:
        """Find shortest path between entities using BFS."""
        if source_id not in self.entities or target_id not in self.entities:
            return None

        from collections import deque

        queue: deque[Tuple[str, List[str]]] = deque([(source_id, [])])
        visited: Set[str] = {source_id}

        while queue:
            current, path = queue.popleft()

            if current == target_id:
                return path + [current]

            if len(path) >= max_depth:
                continue

            # Get outgoing relationships from relationships AND hypergraph facts
            outgoing_rels = [
                rel.target
                for rel in self.get_relationships(current, direction="outgoing")
            ]
            outgoing_facts = [
                f.tail for f in self.get_facts_for_entity(current, direction="outgoing")
            ]
            all_targets = set(outgoing_rels + outgoing_facts)

            for target in all_targets:
                if target not in visited:
                    visited.add(target)
                    queue.append((target, path + [current]))

        return None

    def query(
        self,
        entity_type: Optional[EntityType] = None,
        relation_type: Optional[RelationType] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> List[Entity]:
        """Query entities with filters."""
        results = []

        for entity in self.entities.values():
            if entity_type and entity.entity_type != entity_type:
                continue

            if properties:
                match = True
                for key, value in properties.items():
                    if entity.properties.get(key) != value:
                        match = False
                        break
                if not match:
                    continue

            # Check if entity has the specified relation
            if relation_type:
                has_relation = any(
                    rel.relation_type == relation_type
                    for rel in self.get_relationships(entity.id)
                )
                # Also check hypergraph facts
                if not has_relation:
                    has_relation = any(
                        f.head == entity.id and f.relation == relation_type.value
                        for f in self._all_facts.values()
                    )
                if not has_relation:
                    continue

            results.append(entity)

        return results

    def semantic_search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """Semantic search using embeddings."""
        results = []

        for entity_id, entity in self.entities.items():
            if entity.embedding:
                similarity = self._cosine_similarity(query_embedding, entity.embedding)
                results.append((entity_id, similarity))

        # Sort by similarity and return top-k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        magnitude_a = math.sqrt(sum(x * x for x in a))
        magnitude_b = math.sqrt(sum(y * y for y in b))

        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0

        return dot_product / (magnitude_a * magnitude_b)

    def infer(
        self,
        entity_id: str,
        relation_type: RelationType,
        max_depth: int = 3,
    ) -> List[str]:
        """Infer related entities using transitive reasoning."""
        inferred = set()
        queue = [(entity_id, 0)]
        visited = {entity_id}

        while queue:
            current, depth = queue.pop(0)

            if depth >= max_depth:
                continue

            for rel in self.get_relationships(
                current, relation_type=relation_type, direction="outgoing"
            ):
                if rel.target not in visited:
                    visited.add(rel.target)
                    inferred.add(rel.target)
                    queue.append((rel.target, depth + 1))

        return list(inferred)

    def validate_consistency(self) -> List[str]:
        """Check knowledge graph for inconsistencies."""
        errors = []

        # Check for orphan entities (no relationships)
        for entity_id in self.entities:
            outgoing = self.get_relationships(entity_id, direction="outgoing")
            incoming = self.get_relationships(entity_id, direction="incoming")
            if not outgoing and not incoming:
                errors.append(f"Orphan entity: {entity_id}")

        # Check for circular is_a relationships
        for entity_id in self.entities:
            if self._has_cycle(entity_id, RelationType.IS_A):
                errors.append(
                    f"Circular is_a relationship detected involving: {entity_id}"
                )

        return errors

    def _has_cycle(self, start_id: str, relation_type: RelationType) -> bool:
        """Check for cycles starting from an entity."""
        visited = set()
        stack = [start_id]

        while stack:
            current = stack.pop()

            if current in visited:
                return True

            visited.add(current)

            for rel in self.get_relationships(
                current, relation_type=relation_type, direction="outgoing"
            ):
                stack.append(rel.target)

        return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge graph statistics."""
        return {
            "entities": len(self.entities),
            "relationships": len(self.relationships),
            "triples": len(self.triples),
            "hyperedges": len(self.hyperedges),
            "facts": len(self._all_facts),
            "entity_types": self._count_entity_types(),
            "relation_types": self._count_relation_types(),
        }

    def _count_entity_types(self) -> Dict[str, int]:
        """Count entities by type."""
        counts: Dict[str, int] = defaultdict(int)
        for entity in self.entities.values():
            counts[entity.entity_type.value] += 1
        return dict(counts)

    def _count_relation_types(self) -> Dict[str, int]:
        """Count relationships by type."""
        counts: Dict[str, int] = defaultdict(int)
        for rel in self.relationships.values():
            counts[rel.relation_type.value] += 1
        return dict(counts)


class KnowledgeBase:
    """Higher-level knowledge base with multiple graphs and inference."""

    def __init__(self):
        self.graphs: Dict[str, KnowledgeGraph] = {}
        self.active_graph: Optional[str] = None

    def create_graph(self, graph_id: str) -> KnowledgeGraph:
        """Create a new knowledge graph."""
        graph = KnowledgeGraph()
        self.graphs[graph_id] = graph
        if self.active_graph is None:
            self.active_graph = graph_id
        return graph

    def get_graph(self, graph_id: str) -> Optional[KnowledgeGraph]:
        """Get a knowledge graph by ID."""
        return self.graphs.get(graph_id)

    def set_active_graph(self, graph_id: str):
        """Set the active knowledge graph."""
        if graph_id in self.graphs:
            self.active_graph = graph_id

    def get_active_graph(self) -> Optional[KnowledgeGraph]:
        """Get the active knowledge graph."""
        if self.active_graph:
            return self.graphs.get(self.active_graph)
        return None

    def merge_graphs(self, source_id: str, target_id: str):
        """Merge one graph into another."""
        source = self.get_graph(source_id)
        target = self.get_graph(target_id)

        if not source or not target:
            raise ValueError("One or both graphs not found")

        # Merge entities
        for entity in source.entities.values():
            if entity.id not in target.entities:
                target.add_entity(entity)

        # Merge relationships
        for rel in source.relationships.values():
            if rel.id not in target.relationships:
                target.add_relationship(rel)

        # Merge triples
        for triple in source.triples:
            target.add_triple(triple)

        # Merge hyperedges
        for edge in source.hyperedges.values():
            if edge.id not in target.hyperedges:
                target.add_hyperedge(edge)

    def cross_graph_query(
        self,
        query: str,
        graph_ids: Optional[List[str]] = None,
    ) -> List[Tuple[str, str, Any]]:
        """Query across multiple graphs."""
        if graph_ids is None:
            graph_ids = list(self.graphs.keys())

        results = []

        for graph_id in graph_ids:
            graph = self.get_graph(graph_id)
            if not graph:
                continue

            # Simple entity name matching
            for entity in graph.entities.values():
                if query.lower() in entity.name.lower():
                    results.append((graph_id, entity.id, entity))

        return results


# Global knowledge base
_global_kb: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """Get the global knowledge base."""
    global _global_kb
    if _global_kb is None:
        _global_kb = KnowledgeBase()
    return _global_kb
