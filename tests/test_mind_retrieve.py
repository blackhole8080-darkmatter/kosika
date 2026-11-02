# tests/test_mind_retrieve.py
from kosika.knowledge_graph import (
    Entity, EntityType, KnowledgeGraph, Relationship, RelationType,
)
from kosika.mind import retrieve

_VECTORS = {"light": [1.0, 0.0], "green": [0.0, 1.0]}


def _embed(text: str) -> list[float]:
    for key, vec in _VECTORS.items():
        if key in text.lower():
            return vec
    return [0.0, 0.0]


def _graph() -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.add_entity(Entity(id="photo", name="photosynthesis", entity_type=EntityType.CONCEPT,
                        description="plants make food from light", embedding=[1.0, 0.0]))
    g.add_entity(Entity(id="chloro", name="chlorophyll", entity_type=EntityType.CONCEPT,
                        description="the green pigment", embedding=[0.0, 1.0]))
    g.add_entity(Entity(id="sun", name="sunlight", entity_type=EntityType.CONCEPT,
                        description="the energy source", embedding=[0.9, 0.1]))
    g.add_relationship(Relationship(id="r1", source="photo", target="chloro",
                                    relation_type=RelationType.PART_OF))
    g.add_relationship(Relationship(id="r2", source="photo", target="sun",
                                    relation_type=RelationType.RELATED_TO))
    return g


def test_retrieve_names_the_hit_and_its_cross_field_neighbours():
    got = retrieve(_graph(), "how does light feed a plant", _embed, limit=1)
    assert "photosynthesis" in got.context
    # resonance: neighbours reached by infer, not just the nearest hit
    assert "chlorophyll" in got.context and "sunlight" in got.context
    assert got.sources == ("photo",)


def test_retrieve_with_no_embedding_match_is_empty_not_error():
    got = retrieve(_graph(), "zzz nothing", _embed, limit=1)
    assert got.context == "" and got.sources == ()
