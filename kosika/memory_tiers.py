"""5-Tier Memory Architecture for Kosika Agents (Kosika Maths SPEC §5).

Implements the memory hierarchy:

    Working → Episodic → Semantic → Procedural → Collective

Knowledge flows upward by consolidation:

- Working:  seconds–minutes, fast LRU, volatile
- Episodic: hours–years, append-only, episodes, auto-summarize
- Semantic: indefinite, versioned, rule inference, diff/merge
- Procedural: skills, compiled rules, parametric, distilled from success
- Collective: CRDT-sync, governance-gated, network-wide

This module is opt-in alongside the existing declarative `memory.py`.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .knowledge_graph import Context, Fact, Hyperedge

# ──────────────────────────────────────────────────────────────
# Memory Types
# ──────────────────────────────────────────────────────────────


class MemoryTier(Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    COLLECTIVE = "collective"


# ──────────────────────────────────────────────────────────────
# Base Memory Interface
# ──────────────────────────────────────────────────────────────


class Memory:
    """Base class for all memory tiers."""

    tier: MemoryTier

    def add_fact(self, fact: Fact) -> str:
        """Add a fact. Returns fact content hash."""
        raise NotImplementedError

    def add_facts(self, facts: List[Fact]) -> List[str]:
        """Add multiple facts atomically."""
        return [self.add_fact(f) for f in facts]

    def query(
        self,
        *,
        head: Optional[str] = None,
        relation: Optional[str] = None,
        tail: Optional[str] = None,
        min_certainty: Optional[float] = None,
        source: Optional[str] = None,
    ) -> List[Fact]:
        """Query facts with filters.

        The parameters every tier understands, and none of them positional: a
        tier may add its own, and one that forwards keywords must stay a legal
        stand-in for this one. A tier that can narrow further
        adds its own on top of these, which is why the working tier also takes
        a time range.
        """
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError

    def flush(self) -> List[Fact]:
        """Flush all facts (used for consolidation upward)."""
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────
# Tier 1: Working Memory
# ──────────────────────────────────────────────────────────────


@dataclass
class WorkingMemoryConfig:
    capacity: int = 10_000  # Max facts
    ttl_seconds: float = 300.0  # Auto-evict after 5 minutes


class WorkingMemory(Memory):
    """Fast, small, volatile memory for immediate experience.

    Lifetime: seconds–minutes. Flushed to Episodic on context switch.
    """

    tier = MemoryTier.WORKING

    def __init__(self, config: Optional[WorkingMemoryConfig] = None):
        self.config = config or WorkingMemoryConfig()
        self._facts: OrderedDict[str, Fact] = (
            OrderedDict()
        )  # content_hash -> Fact (LRU order)
        self._access_times: Dict[str, float] = {}
        self._by_head: Dict[str, Set[str]] = defaultdict(set)
        self._by_relation: Dict[str, Set[str]] = defaultdict(set)
        self._by_tail: Dict[str, Set[str]] = defaultdict(set)
        self._by_source: Dict[str, Set[str]] = defaultdict(set)

    def add_fact(self, fact: Fact) -> str:
        h = str(fact.content_hash())

        # Evict if at capacity
        if len(self._facts) >= self.config.capacity and h not in self._facts:
            self._evict_lru()

        self._facts[h] = fact
        self._access_times[h] = time.time()
        # Move to end (most recently used)
        self._facts.move_to_end(h)

        # Update indices
        self._by_head[fact.head].add(h)
        self._by_relation[fact.relation].add(h)
        self._by_tail[fact.tail].add(h)
        self._by_source[fact.context.source].add(h)

        return h

    def _evict_lru(self):
        if not self._facts:
            return
        h, fact = self._facts.popitem(last=False)  # oldest
        self._access_times.pop(h, None)
        self._by_head[fact.head].discard(h)
        self._by_relation[fact.relation].discard(h)
        self._by_tail[fact.tail].discard(h)
        self._by_source[fact.context.source].discard(h)

    def _expire_ttl(self):
        now = time.time()
        expired = [
            h
            for h, t in self._access_times.items()
            if now - t > self.config.ttl_seconds
        ]
        for h in expired:
            if h in self._facts:
                fact = self._facts.pop(h)
                self._access_times.pop(h, None)
                self._by_head[fact.head].discard(h)
                self._by_relation[fact.relation].discard(h)
                self._by_tail[fact.tail].discard(h)
                self._by_source[fact.context.source].discard(h)

    def query(
        self,
        head: Optional[str] = None,
        relation: Optional[str] = None,
        tail: Optional[str] = None,
        min_certainty: Optional[float] = None,
        source: Optional[str] = None,
    ) -> List[Fact]:
        self._expire_ttl()

        candidates: Set[str] = set()
        if head is not None:
            candidates = self._by_head.get(head, set()).copy()
        elif relation is not None:
            candidates = self._by_relation.get(relation, set()).copy()
        elif tail is not None:
            candidates = self._by_tail.get(tail, set()).copy()
        elif source is not None:
            candidates = self._by_source.get(source, set()).copy()
        else:
            candidates = set(self._facts.keys())

        results = []
        for h in candidates:
            fact = self._facts.get(h)
            if fact is None:
                continue
            # Update access time
            self._access_times[h] = time.time()
            self._facts.move_to_end(h)

            if min_certainty is not None and fact.context.certainty < min_certainty:
                continue
            if source is not None and fact.context.source != source:
                continue
            if head is not None and fact.head != head:
                continue
            if relation is not None and fact.relation != relation:
                continue
            if tail is not None and fact.tail != tail:
                continue
            results.append(fact)
        return results

    def flush(self) -> List[Fact]:
        """Flush all facts — used to consolidate into Episodic."""
        all_facts = list(self._facts.values())
        self.clear()
        return all_facts

    def size(self) -> int:
        return len(self._facts)

    def clear(self):
        self._facts.clear()
        self._access_times.clear()
        self._by_head.clear()
        self._by_relation.clear()
        self._by_tail.clear()
        self._by_source.clear()


# ──────────────────────────────────────────────────────────────
# Tier 2: Episodic Memory
# ──────────────────────────────────────────────────────────────


@dataclass
class Episode:
    id: str
    start_time: float
    end_time: Optional[float]
    context: Context
    fact_hashes: List[str]
    summary: Optional[str] = None
    embedding: Optional[List[float]] = None


class EpisodicMemory(Memory):
    """Append-only, time-indexed, auto-summarizing memory.

    Lifetime: hours–years. Consolidates from Working memory.
    Clusters facts into episodes; summaries feed Semantic memory.
    """

    tier = MemoryTier.EPISODIC

    def __init__(self):
        self._facts: Dict[str, Fact] = {}  # content_hash -> Fact
        self._episodes: Dict[str, Episode] = {}
        self._current_episode_id: Optional[str] = None
        self._by_head: Dict[str, Set[str]] = defaultdict(set)
        self._by_relation: Dict[str, Set[str]] = defaultdict(set)
        self._by_tail: Dict[str, Set[str]] = defaultdict(set)
        self._by_source: Dict[str, Set[str]] = defaultdict(set)
        self._by_time: List[Tuple[float, str]] = []  # (timestamp, hash) sorted

    def start_episode(self, context: Context) -> str:
        import uuid

        episode_id = f"ep:{uuid.uuid4().hex}"
        episode = Episode(
            id=episode_id,
            start_time=time.time(),
            end_time=None,
            context=context,
            fact_hashes=[],
        )
        self._episodes[episode_id] = episode
        self._current_episode_id = episode_id
        return episode_id

    def end_episode(self) -> Optional[Episode]:
        if self._current_episode_id is None:
            return None
        episode = self._episodes[self._current_episode_id]
        episode.end_time = time.time()
        self._current_episode_id = None
        return episode

    def current_episode_id(self) -> Optional[str]:
        return self._current_episode_id

    def add_fact(self, fact: Fact) -> str:
        h = str(fact.content_hash())
        self._facts[h] = fact
        self._by_head[fact.head].add(h)
        self._by_relation[fact.relation].add(h)
        self._by_tail[fact.tail].add(h)
        self._by_source[fact.context.source].add(h)
        self._by_time.append((fact.context.timestamp / 1_000_000, h))

        # Track in current episode
        if self._current_episode_id:
            self._episodes[self._current_episode_id].fact_hashes.append(h)
        return h

    def add_facts(self, facts: List[Fact]) -> List[str]:
        return [self.add_fact(f) for f in facts]

    def query(
        self,
        head: Optional[str] = None,
        relation: Optional[str] = None,
        tail: Optional[str] = None,
        min_certainty: Optional[float] = None,
        source: Optional[str] = None,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
    ) -> List[Fact]:
        candidates: Set[str] = set()
        if head is not None:
            candidates = self._by_head.get(head, set()).copy()
        elif relation is not None:
            candidates = self._by_relation.get(relation, set()).copy()
        elif tail is not None:
            candidates = self._by_tail.get(tail, set()).copy()
        elif source is not None:
            candidates = self._by_source.get(source, set()).copy()
        else:
            candidates = set(self._facts.keys())

        results = []
        for h in candidates:
            fact = self._facts.get(h)
            if fact is None:
                continue
            if min_certainty is not None and fact.context.certainty < min_certainty:
                continue
            if source is not None and fact.context.source != source:
                continue
            if head is not None and fact.head != head:
                continue
            if relation is not None and fact.relation != relation:
                continue
            if tail is not None and fact.tail != tail:
                continue
            if (
                time_start is not None
                and fact.context.timestamp / 1_000_000 < time_start
            ):
                continue
            if time_end is not None and fact.context.timestamp / 1_000_000 > time_end:
                continue
            results.append(fact)
        return results

    def flush(self) -> List[Fact]:
        """Episodic does not flush upward directly; Semantic reads from Episodic."""
        return []

    def size(self) -> int:
        return len(self._facts)

    def clear(self):
        self._facts.clear()
        self._episodes.clear()
        self._current_episode_id = None
        self._by_head.clear()
        self._by_relation.clear()
        self._by_tail.clear()
        self._by_source.clear()
        self._by_time.clear()

    def list_episodes(self) -> List[Episode]:
        return list(self._episodes.values())

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        return self._episodes.get(episode_id)


# ──────────────────────────────────────────────────────────────
# Tier 3: Semantic Memory
# ──────────────────────────────────────────────────────────────


@dataclass
class GraphVersion:
    version: int
    merkle_root: str
    timestamp: float
    fact_count: int
    parents: List[int] = field(default_factory=list)


class SemanticMemory(Memory):
    """Versioned long-term knowledge graph with rule inference.

    Lifetime: indefinite. Receives distilled facts from Episodic.
    Supports diff/merge across agents.
    """

    tier = MemoryTier.SEMANTIC

    def __init__(self):
        self._facts: Dict[str, Fact] = {}
        self._hyperedges: Dict[str, Hyperedge] = {}
        self._version: int = 0
        self._history: List[GraphVersion] = []
        self._rules: List[dict] = []  # Forward-chaining rules
        self._by_head: Dict[str, Set[str]] = defaultdict(set)
        self._by_relation: Dict[str, Set[str]] = defaultdict(set)
        self._by_tail: Dict[str, Set[str]] = defaultdict(set)
        self._by_source: Dict[str, Set[str]] = defaultdict(set)

    def add_fact(self, fact: Fact) -> str:
        h = str(fact.content_hash())
        if h in self._facts:
            return h  # Idempotent
        self._facts[h] = fact
        self._by_head[fact.head].add(h)
        self._by_relation[fact.relation].add(h)
        self._by_tail[fact.tail].add(h)
        self._by_source[fact.context.source].add(h)
        self._bump_version()
        return h

    def add_hyperedge(self, hyperedge: Hyperedge) -> str:
        self._hyperedges[hyperedge.id] = hyperedge
        for fact in hyperedge.facts:
            self.add_fact(fact)
        self._bump_version()
        return hyperedge.id

    def _bump_version(self):
        self._version += 1
        # Record snapshot periodically (every 100 versions)
        if self._version % 100 == 0:
            self._snapshot()

    def _snapshot(self):

        data = f"{self._version}:{len(self._facts)}:{time.time()}"
        merkle = hashlib.sha256(data.encode()).hexdigest()[:16]
        self._history.append(
            GraphVersion(
                version=self._version,
                merkle_root=merkle,
                timestamp=time.time(),
                fact_count=len(self._facts),
                parents=[self._version - 1] if self._version > 0 else [],
            )
        )

    def query(
        self,
        head: Optional[str] = None,
        relation: Optional[str] = None,
        tail: Optional[str] = None,
        min_certainty: Optional[float] = None,
        source: Optional[str] = None,
    ) -> List[Fact]:
        candidates: Set[str] = set()
        if head is not None:
            candidates = self._by_head.get(head, set()).copy()
        elif relation is not None:
            candidates = self._by_relation.get(relation, set()).copy()
        elif tail is not None:
            candidates = self._by_tail.get(tail, set()).copy()
        elif source is not None:
            candidates = self._by_source.get(source, set()).copy()
        else:
            candidates = set(self._facts.keys())

        results = []
        for h in candidates:
            fact = self._facts.get(h)
            if fact is None:
                continue
            if min_certainty is not None and fact.context.certainty < min_certainty:
                continue
            if source is not None and fact.context.source != source:
                continue
            if head is not None and fact.head != head:
                continue
            if relation is not None and fact.relation != relation:
                continue
            if tail is not None and fact.tail != tail:
                continue
            results.append(fact)
        return results

    def add_rule(self, rule: dict):
        """Add a forward-chaining rule: {pattern: ..., action: ..., weight: ...}."""
        self._rules.append(rule)

    def infer(self, max_iterations: int = 10) -> int:
        """Forward-chaining inference until fixpoint or max iterations."""
        total_new = 0
        for _ in range(max_iterations):
            new_this_iter = 0
            for rule in self._rules:
                # Simplified: only Assert actions supported
                bindings = self._match_pattern(rule.get("pattern", {}))
                for binding in bindings:
                    new_fact = self._instantiate(rule.get("action", {}), binding)
                    if new_fact:
                        h = self.add_fact(new_fact)
                        if h not in self._facts:
                            continue
                        new_this_iter += 1
            total_new += new_this_iter
            if new_this_iter == 0:
                break
        return total_new

    def _match_pattern(self, pattern: dict) -> List[dict]:
        """Match a pattern against facts, returning binding sets."""
        # Simplified placeholder — returns empty (real impl unifies variables)
        return []

    def _instantiate(self, action: dict, binding: dict) -> Optional[Fact]:
        """Instantiate a fact from an action template and bindings."""
        return None

    def flush(self) -> List[Fact]:
        """Semantic does not flush upward; Collective pulls from Semantic."""
        return []

    def size(self) -> int:
        return len(self._facts)

    def clear(self):
        self._facts.clear()
        self._hyperedges.clear()
        self._version = 0
        self._history.clear()
        self._rules.clear()
        self._by_head.clear()
        self._by_relation.clear()
        self._by_tail.clear()
        self._by_source.clear()

    def version(self) -> int:
        return self._version

    def history(self) -> List[GraphVersion]:
        return list(self._history)

    def merge(
        self,
        other: "SemanticMemory",
        resolver: Optional[Callable[[Fact, Fact], Fact]] = None,
    ) -> Tuple[int, List[Tuple[Fact, Fact, str]]]:
        """Merge another SemanticMemory into this one.

        Returns (merged_count, conflicts).
        resolver: callable(local, remote) -> winning Fact (or raises for manual resolution).
        Default: last-writer-wins by timestamp.
        """
        if resolver is None:
            resolver = _last_writer_wins

        merged = 0
        conflicts: List[Tuple[Fact, Fact, str]] = []

        for h, remote_fact in other._facts.items():
            if h not in self._facts:
                self._facts[h] = remote_fact
                self._by_head[remote_fact.head].add(h)
                self._by_relation[remote_fact.relation].add(h)
                self._by_tail[remote_fact.tail].add(h)
                self._by_source[remote_fact.context.source].add(h)
                merged += 1
            else:
                local_fact = self._facts[h]
                try:
                    winner = resolver(local_fact, remote_fact)
                    if winner is remote_fact:
                        self._facts[h] = remote_fact
                        merged += 1
                except Exception as exc:
                    conflicts.append((local_fact, remote_fact, str(exc)))

        self._bump_version()
        return merged, conflicts


def _last_writer_wins(local: Fact, remote: Fact) -> Fact:
    """Default resolver: fact with newer timestamp wins."""
    if remote.context.timestamp >= local.context.timestamp:
        return remote
    return local


# ──────────────────────────────────────────────────────────────
# Tier 4: Procedural Memory
# ──────────────────────────────────────────────────────────────


@dataclass
class SkillParam:
    name: str
    param_type: str
    default: Optional[Any] = None


@dataclass
class SkillStep:
    action: str  # Function or sub-skill ID
    args: List[Any]
    condition: Optional[dict] = None  # Optional guard (fact pattern)


@dataclass
class Skill:
    id: str
    name: str
    parameters: List[SkillParam]
    steps: List[SkillStep]
    success_rate: float = 0.0
    usage_count: int = 0
    derived_from: List[str] = field(default_factory=list)  # Episode IDs


class ProceduralMemory(Memory):
    """Skills and compiled procedures.

    Lifetime: indefinite. Distilled from successful episodes.
    """

    tier = MemoryTier.PROCEDURAL

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def add_skill(self, skill: Skill):
        self._skills[skill.id] = skill

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Skill]:
        return list(self._skills.values())

    def record_success(self, skill_id: str):
        if skill_id in self._skills:
            skill = self._skills[skill_id]
            skill.usage_count += 1
            skill.success_rate = 0.9 * skill.success_rate + 0.1 * 1.0

    def record_failure(self, skill_id: str):
        if skill_id in self._skills:
            skill = self._skills[skill_id]
            skill.usage_count += 1
            skill.success_rate = 0.9 * skill.success_rate + 0.1 * 0.0

    def add_fact(self, fact: Fact) -> str:
        # Procedural memory stores skills, not raw facts
        return str(fact.content_hash())

    def query(self, **kwargs) -> List[Fact]:
        return []

    def flush(self) -> List[Fact]:
        return []

    def size(self) -> int:
        return len(self._skills)

    def clear(self):
        self._skills.clear()


# ──────────────────────────────────────────────────────────────
# Tier 5: Collective Memory (stub — full CRDT in Kosika Networks phase)
# ──────────────────────────────────────────────────────────────


@dataclass
class PeerState:
    id: str
    last_sync: float
    version_vector: Dict[str, int]  # source -> version
    pending: List[Fact] = field(default_factory=list)


class CollectiveMemory(Memory):
    """CRDT-synchronized collective memory across agents.

    Lifetime: network-wide. Governance-gated.
    """

    tier = MemoryTier.COLLECTIVE

    def __init__(self, local_id: str):
        self.local_id = local_id
        self._semantic = SemanticMemory()
        self._peers: Dict[str, PeerState] = {}
        self._sync_protocol = "crdt"  # crdt | event-sourcing | hybrid

    def add_peer(self, peer_id: str):
        self._peers[peer_id] = PeerState(
            id=peer_id,
            last_sync=0.0,
            version_vector={},
            pending=[],
        )

    def receive(self, from_peer: str, facts: List[Fact]):
        if from_peer not in self._peers:
            self.add_peer(from_peer)
        self._peers[from_peer].pending.extend(facts)

    def sync(self, peer_id: str) -> List[Fact]:
        """Return facts to send to peer since last sync."""
        peer = self._peers.get(peer_id)
        if peer is None:
            return []
        since = peer.last_sync
        facts = [
            f
            for f in self._semantic._facts.values()
            if f.context.timestamp / 1_000_000 > since
        ]
        peer.last_sync = time.time()
        return facts

    def apply_pending(self, resolver=None) -> Tuple[int, List[Tuple[Fact, Fact, str]]]:
        """Apply all pending facts from peers into local semantic memory."""
        all_pending: List[Fact] = []
        for peer in self._peers.values():
            all_pending.extend(peer.pending)
            peer.pending.clear()

        # Build a temporary semantic memory and merge
        temp = SemanticMemory()
        for f in all_pending:
            temp.add_fact(f)

        return self._semantic.merge(temp, resolver=resolver)

    def add_fact(self, fact: Fact) -> str:
        return self._semantic.add_fact(fact)

    def query(self, **kwargs) -> List[Fact]:
        return self._semantic.query(**kwargs)

    def flush(self) -> List[Fact]:
        return self._semantic.flush()

    def size(self) -> int:
        return self._semantic.size()

    def clear(self):
        self._semantic.clear()
        self._peers.clear()

    def local(self) -> SemanticMemory:
        return self._semantic


# ──────────────────────────────────────────────────────────────
# Memory Stack — orchestrates consolidation
# ──────────────────────────────────────────────────────────────


@dataclass
class ConsolidationReport:
    working_to_episodic: int = 0
    episodic_to_semantic: int = 0
    semantic_to_procedural: int = 0


class MemoryStack:
    """Orchestrates the 5-tier memory hierarchy.

    Consolidation pipeline: Working → Episodic → Semantic → Procedural → Collective.
    """

    def __init__(
        self,
        working: Optional[WorkingMemory] = None,
        episodic: Optional[EpisodicMemory] = None,
        semantic: Optional[SemanticMemory] = None,
        procedural: Optional[ProceduralMemory] = None,
        collective: Optional[CollectiveMemory] = None,
    ):
        self.working = working or WorkingMemory()
        self.episodic = episodic or EpisodicMemory()
        self.semantic = semantic or SemanticMemory()
        self.procedural = procedural or ProceduralMemory()
        self.collective = collective

    def add_experience(
        self, facts: List[Fact], episode_context: Optional[Context] = None
    ):
        """Add facts to Working memory and optionally start an episode."""
        self.working.add_facts(facts)

        if episode_context is not None:
            if self.episodic.current_episode_id() is None:
                self.episodic.start_episode(episode_context)
            for f in facts:
                self.episodic.add_fact(f)

    def consolidate(self) -> ConsolidationReport:
        """Run one consolidation cycle.

        1. Working → Episodic (flush working into current episode)
        2. Episodic → Semantic (promote high-certainty facts)
        3. Semantic → Procedural (distill skills — placeholder)
        """
        report = ConsolidationReport()

        # Working → Episodic
        working_facts = self.working.flush()
        if working_facts:
            # Ensure an episode exists
            if self.episodic.current_episode_id() is None:
                self.episodic.start_episode(
                    Context(source="system", timestamp=int(time.time() * 1_000_000))
                )
            self.episodic.add_facts(working_facts)
            report.working_to_episodic = len(working_facts)

        # Episodic → Semantic (promote high-certainty facts from recent episodes)
        for episode in self.episodic.list_episodes():
            if episode.summary is not None:
                # Already summarized — promote key facts
                for h in episode.fact_hashes:
                    fact = self.episodic._facts.get(h)
                    if fact and fact.context.certainty > 0.8:
                        self.semantic.add_fact(fact)
                        report.episodic_to_semantic += 1

        # Semantic → Procedural (placeholder: skill distillation)
        report.semantic_to_procedural = 0

        return report

    def size(self) -> Dict[MemoryTier, int]:
        return {
            MemoryTier.WORKING: self.working.size(),
            MemoryTier.EPISODIC: self.episodic.size(),
            MemoryTier.SEMANTIC: self.semantic.size(),
            MemoryTier.PROCEDURAL: self.procedural.size(),
            MemoryTier.COLLECTIVE: self.collective.size() if self.collective else 0,
        }

    def clear(self):
        self.working.clear()
        self.episodic.clear()
        self.semantic.clear()
        self.procedural.clear()
        if self.collective:
            self.collective.clear()
