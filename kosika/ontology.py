"""Upper Ontology for Kosika Maths (SPEC §7).

BFO-aligned core ontology with extensible domains.

Core hierarchy:

    Entity
    ├── Continuant (endures through time)
    │   ├── IndependentContinuant
    │   │   ├── MaterialEntity (object, agent, device)
    │   │   └── ImmaterialEntity (information, code, policy)
    │   └── DependentContinuant (quality, role, function)
    └── Occurrent (unfolds in time)
        ├── Process (action, computation, workflow)
        ├── Event (instantaneous change)
        └── TemporalRegion (interval, instant)

Core relations with axioms:
- part-of (transitive), has-part (inverse of part-of)
- participates-in (inverse: has-participant)
- causes (transitive), enables, prevents
- knows, believes, intends
- communicates-with (symmetric), shares-with
- version-of (transitive), derives-from (transitive)
- governed-by, compliant-with

Domain ontologies import core and add types/relations. No monkey-patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────
# Entity Types (Core)
# ──────────────────────────────────────────────────────────────


class CoreEntityType(Enum):
    """BFO-aligned core entity types."""

    # Top level
    ENTITY = "entity"
    CONTINUANT = "continuant"
    OCCURRENT = "occurrent"

    # Continuant branches
    INDEPENDENT_CONTINUANT = "independentcontinuant"
    DEPENDENT_CONTINUANT = "dependentcontinuant"

    # Independent continuants
    MATERIAL_ENTITY = "materialentity"
    IMMATERIAL_ENTITY = "immaterialentity"

    # Dependent continuants
    QUALITY = "quality"
    ROLE = "role"
    FUNCTION = "function"

    # Occurrent branches
    PROCESS = "process"
    EVENT = "event"
    TEMPORAL_REGION = "temporalregion"

    # Agent-specific (common extensions)
    AGENT = "agent"
    DEVICE = "device"
    SKILL = "skill"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"


# Parent map for subtype checking
_ENTITY_PARENTS: Dict[CoreEntityType, Optional[CoreEntityType]] = {
    CoreEntityType.ENTITY: None,
    CoreEntityType.CONTINUANT: CoreEntityType.ENTITY,
    CoreEntityType.OCCURRENT: CoreEntityType.ENTITY,
    CoreEntityType.INDEPENDENT_CONTINUANT: CoreEntityType.CONTINUANT,
    CoreEntityType.DEPENDENT_CONTINUANT: CoreEntityType.CONTINUANT,
    CoreEntityType.MATERIAL_ENTITY: CoreEntityType.INDEPENDENT_CONTINUANT,
    CoreEntityType.IMMATERIAL_ENTITY: CoreEntityType.INDEPENDENT_CONTINUANT,
    CoreEntityType.QUALITY: CoreEntityType.DEPENDENT_CONTINUANT,
    CoreEntityType.ROLE: CoreEntityType.DEPENDENT_CONTINUANT,
    CoreEntityType.FUNCTION: CoreEntityType.DEPENDENT_CONTINUANT,
    CoreEntityType.PROCESS: CoreEntityType.OCCURRENT,
    CoreEntityType.EVENT: CoreEntityType.OCCURRENT,
    CoreEntityType.TEMPORAL_REGION: CoreEntityType.OCCURRENT,
    CoreEntityType.AGENT: CoreEntityType.MATERIAL_ENTITY,
    CoreEntityType.DEVICE: CoreEntityType.MATERIAL_ENTITY,
    CoreEntityType.SKILL: CoreEntityType.FUNCTION,
    CoreEntityType.KNOWLEDGE: CoreEntityType.IMMATERIAL_ENTITY,
    CoreEntityType.CONTEXT: CoreEntityType.IMMATERIAL_ENTITY,
}


def is_subtype(sub: CoreEntityType, super_: CoreEntityType) -> bool:
    """Check if `sub` is a subtype of `super_` in the core hierarchy."""
    if sub == super_:
        return True
    current: Optional[CoreEntityType] = sub
    while current is not None:
        parent = _ENTITY_PARENTS.get(current)
        if parent == super_:
            return True
        current = parent
    return False


# ──────────────────────────────────────────────────────────────
# Relations (Core)
# ──────────────────────────────────────────────────────────────


class RelationAxiom(Enum):
    """Axioms that can apply to relations."""

    TRANSITIVE = "transitive"
    SYMMETRIC = "symmetric"
    FUNCTIONAL = "functional"
    INVERSE_FUNCTIONAL = "inverse_functional"
    INVERSE_OF = "inverse_of"
    SUB_RELATION_OF = "sub_relation_of"
    DOMAIN = "domain"
    RANGE = "range"
    PROPERTY_CHAIN = "property_chain"


@dataclass
class CoreRelation:
    """A core relation definition with axioms."""

    id: str  # e.g., "rel:part-of"
    label: str  # e.g., "part-of"
    domain: Optional[CoreEntityType] = None  # Expected head type
    range: Optional[CoreEntityType] = None  # Expected tail type
    axioms: List[RelationAxiom] = field(default_factory=list)
    inverse: Optional[str] = None  # Inverse relation id

    def has_axiom(self, axiom: RelationAxiom) -> bool:
        return axiom in self.axioms


# Core relations registry
CORE_RELATIONS: Dict[str, CoreRelation] = {}

_core_relation_counter = 0


def _register_core(
    label: str,
    domain: Optional[CoreEntityType] = None,
    range: Optional[CoreEntityType] = None,
    axioms: Optional[List[RelationAxiom]] = None,
    inverse: Optional[str] = None,
) -> str:
    global _core_relation_counter
    _core_relation_counter += 1
    rel_id = f"rel:{label}"
    rel = CoreRelation(
        id=rel_id,
        label=label,
        domain=domain,
        range=range,
        axioms=axioms or [],
        inverse=inverse,
    )
    CORE_RELATIONS[rel_id] = rel
    return rel_id


# ── Register core relations ──
REL_PART_OF = _register_core(
    "part-of",
    domain=CoreEntityType.CONTINUANT,
    range=CoreEntityType.CONTINUANT,
    axioms=[RelationAxiom.TRANSITIVE],
    inverse="rel:has-part",
)
_register_core(
    "has-part",
    domain=CoreEntityType.CONTINUANT,
    range=CoreEntityType.CONTINUANT,
    axioms=[RelationAxiom.TRANSITIVE],
    inverse="rel:part-of",
)

REL_PARTICIPATES_IN = _register_core(
    "participates-in",
    domain=CoreEntityType.CONTINUANT,
    range=CoreEntityType.OCCURRENT,
    inverse="rel:has-participant",
)
_register_core(
    "has-participant",
    domain=CoreEntityType.OCCURRENT,
    range=CoreEntityType.CONTINUANT,
    inverse="rel:participates-in",
)

REL_CAUSES = _register_core(
    "causes",
    domain=CoreEntityType.OCCURRENT,
    range=CoreEntityType.OCCURRENT,
    axioms=[RelationAxiom.TRANSITIVE],
)
_register_core(
    "enables", domain=CoreEntityType.CONTINUANT, range=CoreEntityType.PROCESS
)
_register_core(
    "prevents", domain=CoreEntityType.CONTINUANT, range=CoreEntityType.PROCESS
)

REL_KNOWS = _register_core(
    "knows",
    domain=CoreEntityType.AGENT,
    range=CoreEntityType.KNOWLEDGE,
)
_register_core("believes", domain=CoreEntityType.AGENT, range=CoreEntityType.KNOWLEDGE)
_register_core("intends", domain=CoreEntityType.AGENT, range=CoreEntityType.PROCESS)

_register_core(
    "communicates-with",
    domain=CoreEntityType.AGENT,
    range=CoreEntityType.AGENT,
    axioms=[RelationAxiom.SYMMETRIC],
)
_register_core(
    "shares-with", domain=CoreEntityType.AGENT, range=CoreEntityType.KNOWLEDGE
)

REL_VERSION_OF = _register_core(
    "version-of",
    domain=CoreEntityType.KNOWLEDGE,
    range=CoreEntityType.KNOWLEDGE,
    axioms=[RelationAxiom.TRANSITIVE],
)
REL_DERIVES_FROM = _register_core(
    "derives-from",
    domain=CoreEntityType.KNOWLEDGE,
    range=CoreEntityType.KNOWLEDGE,
    axioms=[RelationAxiom.TRANSITIVE],
)

_register_core(
    "governed-by", domain=CoreEntityType.ENTITY, range=CoreEntityType.IMMATERIAL_ENTITY
)
_register_core(
    "compliant-with",
    domain=CoreEntityType.ENTITY,
    range=CoreEntityType.IMMATERIAL_ENTITY,
)


def get_core_relation(rel_id: str) -> Optional[CoreRelation]:
    """Get a core relation by ID."""
    return CORE_RELATIONS.get(rel_id)


def validate_fact(
    head_type: CoreEntityType, rel_id: str, tail_type: CoreEntityType
) -> List[str]:
    """Validate a fact against core relation domain/range constraints.

    Returns list of error messages (empty if valid).
    """
    errors: List[str] = []
    rel = get_core_relation(rel_id)
    if rel is None:
        return errors  # Unknown relation — allow (user-defined)

    if rel.domain is not None and not is_subtype(head_type, rel.domain):
        errors.append(
            f"Head type {head_type.value} is not a subtype of relation domain {rel.domain.value}"
        )
    if rel.range is not None and not is_subtype(tail_type, rel.range):
        errors.append(
            f"Tail type {tail_type.value} is not a subtype of relation range {rel.range.value}"
        )
    return errors


# ──────────────────────────────────────────────────────────────
# Domain Ontology Extension
# ──────────────────────────────────────────────────────────────


@dataclass
class DomainOntology:
    """A domain-specific ontology that extends core."""

    id: str  # e.g., "domain:robotics"
    label: str  # e.g., "Robotics"
    imports: List[str] = field(default_factory=list)  # Core + other domains
    entities: Dict[str, CoreEntityType] = field(
        default_factory=dict
    )  # entity_id -> type
    relations: Dict[str, CoreRelation] = field(default_factory=dict)
    axioms: List[dict] = field(default_factory=list)  # Custom axioms

    def add_entity(self, entity_id: str, entity_type: CoreEntityType):
        self.entities[entity_id] = entity_type

    def add_relation(self, relation: CoreRelation):
        self.relations[relation.id] = relation

    def add_axiom(self, axiom: dict):
        self.axioms.append(axiom)


class OntologyRegistry:
    """Registry of core + domain ontologies."""

    def __init__(self):
        self.domains: Dict[str, DomainOntology] = {}
        self._merged_cache: Optional[Dict[str, Any]] = None

    def register_domain(self, domain: DomainOntology):
        self.domains[domain.id] = domain
        self._merged_cache = None  # Invalidate

    def get_domain(self, domain_id: str) -> Optional[DomainOntology]:
        return self.domains.get(domain_id)

    def merged(self) -> Dict[str, Any]:
        """Get merged view: core entities + relations + all domains."""
        if self._merged_cache is not None:
            return self._merged_cache

        merged: Dict[str, Any] = {
            "entities": {},  # entity_id -> CoreEntityType
            "relations": dict(CORE_RELATIONS),
            "axioms": [],
        }

        for domain in self.domains.values():
            for eid, etype in domain.entities.items():
                merged["entities"][eid] = etype
            for rid, rel in domain.relations.items():
                merged["relations"][rid] = rel
            merged["axioms"].extend(domain.axioms)

        self._merged_cache = merged
        return merged

    def get_relation(self, rel_id: str) -> Optional[CoreRelation]:
        """Get a relation from merged view (core + domains)."""
        merged = self.merged()
        return merged["relations"].get(rel_id)

    def is_subtype(self, sub: CoreEntityType, super_: CoreEntityType) -> bool:
        return is_subtype(sub, super_)

    def validate_fact(
        self,
        head_type: CoreEntityType,
        rel_id: str,
        tail_type: CoreEntityType,
    ) -> List[str]:
        """Validate a fact against merged ontology."""
        rel = self.get_relation(rel_id)
        if rel is None:
            return []  # User-defined relation — allow

        errors = []
        if rel.domain is not None and not is_subtype(head_type, rel.domain):
            errors.append(
                f"Head type {head_type.value} not in domain {rel.domain.value} for {rel_id}"
            )
        if rel.range is not None and not is_subtype(tail_type, rel.range):
            errors.append(
                f"Tail type {tail_type.value} not in range {rel.range.value} for {rel_id}"
            )
        return errors


# ──────────────────────────────────────────────────────────────
# Example Domains
# ──────────────────────────────────────────────────────────────


def robotics_domain() -> DomainOntology:
    """Robotics domain ontology."""

    dom = DomainOntology(
        id="domain:robotics",
        label="Robotics",
        imports=["core"],
    )

    # Entities
    dom.add_entity("robot:Robot", CoreEntityType.AGENT)
    dom.add_entity("robot:Sensor", CoreEntityType.DEVICE)
    dom.add_entity("robot:Actuator", CoreEntityType.DEVICE)
    dom.add_entity("robot:Task", CoreEntityType.PROCESS)
    dom.add_entity("robot:Environment", CoreEntityType.MATERIAL_ENTITY)

    # Relations
    dom.add_relation(
        CoreRelation(
            id="rel:has-sensor",
            label="has-sensor",
            domain=CoreEntityType.AGENT,
            range=CoreEntityType.DEVICE,
            inverse="rel:sensor-of",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:sensor-of",
            label="sensor-of",
            domain=CoreEntityType.DEVICE,
            range=CoreEntityType.AGENT,
            inverse="rel:has-sensor",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:has-actuator",
            label="has-actuator",
            domain=CoreEntityType.AGENT,
            range=CoreEntityType.DEVICE,
            inverse="rel:actuator-of",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:performs",
            label="performs",
            domain=CoreEntityType.AGENT,
            range=CoreEntityType.PROCESS,
            inverse="rel:performed-by",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:operates-in",
            label="operates-in",
            domain=CoreEntityType.AGENT,
            range=CoreEntityType.MATERIAL_ENTITY,
            inverse="rel:hosts",
        )
    )

    return dom


def research_domain() -> DomainOntology:
    """Scientific research domain ontology."""

    dom = DomainOntology(
        id="domain:research",
        label="Scientific Research",
        imports=["core"],
    )

    dom.add_entity("research:Hypothesis", CoreEntityType.KNOWLEDGE)
    dom.add_entity("research:Experiment", CoreEntityType.PROCESS)
    dom.add_entity("research:Dataset", CoreEntityType.IMMATERIAL_ENTITY)
    dom.add_entity("research:Publication", CoreEntityType.IMMATERIAL_ENTITY)
    dom.add_entity("research:Researcher", CoreEntityType.AGENT)

    dom.add_relation(
        CoreRelation(
            id="rel:proposes",
            label="proposes",
            domain=CoreEntityType.AGENT,
            range=CoreEntityType.KNOWLEDGE,
            inverse="rel:proposed-by",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:tests",
            label="tests",
            domain=CoreEntityType.PROCESS,
            range=CoreEntityType.KNOWLEDGE,
            inverse="rel:tested-by",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:produces",
            label="produces",
            domain=CoreEntityType.PROCESS,
            range=CoreEntityType.IMMATERIAL_ENTITY,
            inverse="rel:produced-by",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:cites",
            label="cites",
            domain=CoreEntityType.IMMATERIAL_ENTITY,
            range=CoreEntityType.IMMATERIAL_ENTITY,
            axioms=[RelationAxiom.TRANSITIVE],
            inverse="rel:cited-by",
        )
    )
    dom.add_relation(
        CoreRelation(
            id="rel:authored-by",
            label="authored-by",
            domain=CoreEntityType.IMMATERIAL_ENTITY,
            range=CoreEntityType.AGENT,
            inverse="rel:author-of",
        )
    )

    return dom


# ──────────────────────────────────────────────────────────────
# Global registry
# ──────────────────────────────────────────────────────────────

_global_registry: Optional[OntologyRegistry] = None


def get_ontology_registry() -> OntologyRegistry:
    """Get the global ontology registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = OntologyRegistry()
        # Register example domains
        _global_registry.register_domain(robotics_domain())
        _global_registry.register_domain(research_domain())
    return _global_registry
