"""Cognitive state management for Kosika - memory bubbles, assumptions, judgments.

This module provides explicit cognitive constructs that make AI reasoning
traceable and auditable. Unlike Python's implicit state management through
variables and dictionaries, Kosika's cognitive layer tracks:
- Memory bubbles with provenance and confidence
- Assumptions with invalidation conditions
- Judgments with evidence chains
- Personality policies that influence behavior

Key features:
- All cognitive items have clear provenance and timestamps
- Confidence scores and uncertainty tracking
- Permission scoping and access control
- Contradiction detection and resolution
- Traceable state changes
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from .ast import Program


class MemoryKind(Enum):
    """Types of memory bubbles."""
    EPISODIC = "episodic"  # Specific events
    SEMANTIC = "semantic"  # General knowledge
    PROCEDURAL = "procedural"  # How to do things
    PREFERENCE = "preference"  # User preferences
    HYPOTHESIS = "hypothesis"  # Tentative beliefs
    CONVERSATION = "conversation"  # Chat context
    RELATIONSHIP = "relationship"  # Entity relationships
    EXECUTION = "execution"  # Runtime history


class MemoryStatus(Enum):
    """Lifecycle states of memory items, in promotion order."""
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    TRUSTED = "trusted"
    CONTRADICTED = "contradicted"
    ARCHIVED = "archived"

    @property
    def rank(self) -> int:
        """The explicit lifecycle order; enum values are not ordinal."""
        return tuple(type(self)).index(self)


class RetentionPolicy(Enum):
    """How long memory should be retained."""
    EPHEMERAL = "ephemeral"  # Single session
    SESSION = "session"  # Current session only
    TEMPORARY = "temporary"  # Short-term (hours/days)
    PERMANENT = "permanent"  # Never expires


class PermissionScope(Enum):
    """Who can access this memory."""
    PRIVATE = "private"  # Only the owner
    SESSION = "session"  # All agents in session
    SHARED = "shared"  # Across sessions
    PUBLIC = "public"  # Any agent


#: Allowed words for each cognitive field, kept beside the enums they describe
#: so the checker and the parser speak with one voice.
MEMORY_KINDS: frozenset[str] = frozenset(kind.value for kind in MemoryKind)
MEMORY_SCOPES: frozenset[str] = frozenset(scope.value for scope in PermissionScope)
MEMORY_RETENTIONS: frozenset[str] = frozenset(
    policy.value for policy in RetentionPolicy
)
PERSONALITY_TONES: frozenset[str] = frozenset(
    {"precise", "casual", "formal", "friendly"}
)
PERSONALITY_UNCERTAINTY: frozenset[str] = frozenset(
    {"explicit", "implicit", "probabilistic"}
)
PERSONALITY_RISK: frozenset[str] = frozenset({"low", "medium", "high"})
PERSONALITY_DEPTH: frozenset[str] = frozenset({"brief", "detailed", "comprehensive"})
PERSONALITY_ATTENTION: frozenset[str] = frozenset({"focused", "broad", "adaptive"})


@dataclass
class MemoryBubble:
    """A bounded, contextual knowledge item with provenance."""
    id: str
    content: Any
    kind: MemoryKind
    source: str  # Where this memory came from
    confidence: float  # 0.0 to 1.0
    scope: PermissionScope
    retention: RetentionPolicy
    status: MemoryStatus = MemoryStatus.UNVERIFIED
    timestamp: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    owner: Optional[str] = None
    permissions: Set[str] = field(default_factory=set)
    evidence: List[str] = field(default_factory=list)
    version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if this memory has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def can_access(self, requester: str) -> bool:
        """Check if requester can access this memory."""
        if self.scope == PermissionScope.PUBLIC:
            return True
        if self.scope == PermissionScope.PRIVATE:
            return self.owner == requester
        if self.scope == PermissionScope.SESSION:
            return requester in self.permissions
        if self.scope == PermissionScope.SHARED:
            return True
        return False

    def add_evidence(self, evidence_id: str):
        """Add supporting evidence."""
        if evidence_id not in self.evidence:
            self.evidence.append(evidence_id)
            self.version += 1

    def update_confidence(self, new_confidence: float):
        """Update confidence score."""
        if 0.0 <= new_confidence <= 1.0:
            self.confidence = new_confidence
            self.version += 1

    def promote(self, new_status: MemoryStatus) -> None:
        """Move forward in the declared lifecycle without reverting state."""
        if not isinstance(new_status, MemoryStatus):
            raise TypeError("new_status must be a MemoryStatus")
        if new_status.rank > self.status.rank:
            self.status = new_status
            self.version += 1


@dataclass
class Assumption:
    """An explicit assumption with invalidation conditions."""
    id: str
    statement: str
    confidence: float
    source: str
    invalidated_by: List[str] = field(default_factory=list)
    is_valid: bool = True
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def invalidate(self, reason: str):
        """Invalidate this assumption."""
        self.is_valid = False
        self.invalidated_by.append(reason)

    def check(self, state: Dict[str, Any]) -> bool:
        """Check if assumption still holds given current state."""
        if not self.is_valid:
            return False
        # Check invalidation conditions
        for condition in self.invalidated_by:
            if state.get(condition, False):
                return False
        return True


@dataclass
class Judgment:
    """A judgment with evidence chain and confidence."""
    id: str
    claim: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    action: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    approved: bool = False
    approver: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if this judgment has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def add_evidence(self, evidence_id: str):
        """Add supporting evidence."""
        if evidence_id not in self.evidence:
            self.evidence.append(evidence_id)

    def add_alternative(self, alternative: str):
        """Add an alternative considered."""
        if alternative not in self.alternatives:
            self.alternatives.append(alternative)

    def approve(self, approver: str):
        """Mark this judgment as approved."""
        self.approved = True
        self.approver = approver


@dataclass
class PersonalityPolicy:
    """Personality settings that influence agent behavior."""
    id: str
    tone: str  # precise, casual, formal, friendly
    priorities: List[str]  # What the agent prioritizes
    uncertainty_style: str  # explicit, implicit, probabilistic
    risk_tolerance: str  # low, medium, high
    explanation_depth: str  # brief, detailed, comprehensive
    attention_style: str  # focused, broad, adaptive
    metadata: Dict[str, Any] = field(default_factory=dict)

    def should_be_precise(self) -> bool:
        """Check if agent should be precise."""
        return self.tone == "precise" or self.tone == "formal"

    def should_explain_uncertainty(self) -> bool:
        """Check if agent should explicitly state uncertainty."""
        return self.uncertainty_style == "explicit" or self.uncertainty_style == "probabilistic"

    def risk_tolerance_level(self) -> float:
        """Get risk tolerance as a number (0.0 to 1.0)."""
        mapping = {"low": 0.2, "medium": 0.5, "high": 0.8}
        return mapping.get(self.risk_tolerance, 0.5)


class CognitiveStore:
    """Central store for all cognitive items."""

    def __init__(self):
        self.memories: Dict[str, MemoryBubble] = {}
        self.assumptions: Dict[str, Assumption] = {}
        self.judgments: Dict[str, Judgment] = {}
        self.personalities: Dict[str, PersonalityPolicy] = {}
        self.contradictions: List[Dict[str, Any]] = []

    def add_memory(self, memory: MemoryBubble) -> str:
        """Add a memory bubble."""
        self.memories[memory.id] = memory
        self._check_contradictions(memory)
        return memory.id

    def get_memory(self, memory_id: str) -> Optional[MemoryBubble]:
        """Get a memory by ID."""
        memory = self.memories.get(memory_id)
        if memory and memory.is_expired():
            return None
        return memory

    def query_memories(
        self,
        kind: Optional[MemoryKind] = None,
        status: Optional[MemoryStatus] = None,
        min_confidence: float = 0.0,
        scope: Optional[PermissionScope] = None,
        requester: Optional[str] = None,
    ) -> List[MemoryBubble]:
        """Query memories with filters."""
        results = []
        for memory in self.memories.values():
            if memory.is_expired():
                continue
            if kind and memory.kind != kind:
                continue
            if status and memory.status != status:
                continue
            if memory.confidence < min_confidence:
                continue
            if scope and memory.scope != scope:
                continue
            if requester and not memory.can_access(requester):
                continue
            results.append(memory)
        return results

    def add_assumption(self, assumption: Assumption) -> str:
        """Add an assumption."""
        self.assumptions[assumption.id] = assumption
        return assumption.id

    def get_assumption(self, assumption_id: str) -> Optional[Assumption]:
        """Get an assumption by ID."""
        return self.assumptions.get(assumption_id)

    def check_assumptions(self, state: Dict[str, Any]) -> List[str]:
        """Check all assumptions against current state, return invalidated IDs."""
        invalidated = []
        for assumption in self.assumptions.values():
            if not assumption.check(state):
                invalidated.append(assumption.id)
        return invalidated

    def add_judgment(self, judgment: Judgment) -> str:
        """Add a judgment."""
        self.judgments[judgment.id] = judgment
        return judgment.id

    def get_judgment(self, judgment_id: str) -> Optional[Judgment]:
        """Get a judgment by ID."""
        judgment = self.judgments.get(judgment_id)
        if judgment and judgment.is_expired():
            return None
        return judgment

    def add_personality(self, personality: PersonalityPolicy) -> str:
        """Add a personality policy."""
        self.personalities[personality.id] = personality
        return personality.id

    def get_personality(self, personality_id: str) -> Optional[PersonalityPolicy]:
        """Get a personality policy by ID."""
        return self.personalities.get(personality_id)

    def _check_contradictions(self, new_memory: MemoryBubble):
        """Check for contradictions with existing memories."""
        for existing in self.memories.values():
            if existing.id == new_memory.id:
                continue
            if existing.kind != new_memory.kind:
                continue
            # Simple contradiction check: same kind, similar content, opposite confidence
            if (
                abs(existing.confidence + new_memory.confidence - 1.0) < 0.1
                and str(existing.content) != str(new_memory.content)
            ):
                self.contradictions.append(
                    {
                        "timestamp": time.time(),
                        "memory1": existing.id,
                        "memory2": new_memory.id,
                        "kind": existing.kind.value,
                    }
                )

    def get_contradictions(self) -> List[Dict[str, Any]]:
        """Get all detected contradictions."""
        return self.contradictions

    def cleanup_expired(self):
        """Remove expired items."""
        now = time.time()
        # Clean expired memories
        expired_memories = [
            mid for mid, m in self.memories.items()
            if m.expires_at and m.expires_at < now
        ]
        for mid in expired_memories:
            del self.memories[mid]

        # Clean expired judgments
        expired_judgments = [
            jid for jid, j in self.judgments.items()
            if j.expires_at and j.expires_at < now
        ]
        for jid in expired_judgments:
            del self.judgments[jid]

    def get_state_summary(self) -> Dict[str, Any]:
        """Get a summary of the cognitive state."""
        return {
            "memories": len(self.memories),
            "assumptions": len(self.assumptions),
            "judgments": len(self.judgments),
            "personalities": len(self.personalities),
            "contradictions": len(self.contradictions),
            "timestamp": time.time(),
        }


# Global cognitive store instance
_global_store: Optional[CognitiveStore] = None


def get_cognitive_store() -> CognitiveStore:
    """Get the global cognitive store instance."""
    global _global_store
    if _global_store is None:
        _global_store = CognitiveStore()
    return _global_store


def reset_cognitive_store():
    """Reset the global cognitive store (for testing)."""
    global _global_store
    _global_store = None


def load_cognitive(program: Program) -> CognitiveStore:
    """Build a fresh store from a program's cognitive declarations.

    The declarations in a `.kos` file (`memory_bubble`, `assumption`,
    `judgment`, `personality`) are checked statically and seed a store when a
    host asks for one. Nothing in a run reads them yet; see KOSIKA.md.
    """

    if not isinstance(program, Program):
        raise TypeError("load_cognitive expects a parsed Kosika program")
    store = CognitiveStore()
    for bubble in program.memory_bubbles:  # type: ignore[attr-defined]
        store.add_memory(
            MemoryBubble(
                id=bubble.name,
                content=bubble.content,
                kind=MemoryKind(bubble.kind),
                source=bubble.source,
                confidence=bubble.confidence,
                scope=PermissionScope(bubble.scope),
                retention=RetentionPolicy(bubble.retention),
            )
        )
    for assumption_decl in program.assumptions:  # type: ignore[attr-defined]
        store.add_assumption(
            Assumption(
                id=assumption_decl.name,
                statement=assumption_decl.statement,
                confidence=assumption_decl.confidence,
                source=assumption_decl.source,
                invalidated_by=list(assumption_decl.invalidated_by),
            )
        )
    for judgment_decl in program.judgments:  # type: ignore[attr-defined]
        store.add_judgment(
            Judgment(
                id=judgment_decl.name,
                claim=judgment_decl.claim,
                confidence=judgment_decl.confidence,
                evidence=list(judgment_decl.evidence),
                action=judgment_decl.action,
                approved=judgment_decl.approved,
                approver=judgment_decl.approver,
            )
        )
    for personality_decl in program.personalities:  # type: ignore[attr-defined]
        store.add_personality(
            PersonalityPolicy(
                id=personality_decl.name,
                tone=personality_decl.tone,
                priorities=list(personality_decl.priorities),
                uncertainty_style=personality_decl.uncertainty_style,
                risk_tolerance=personality_decl.risk_tolerance,
                explanation_depth=personality_decl.explanation_depth,
                attention_style=personality_decl.attention_style,
            )
        )
    return store
