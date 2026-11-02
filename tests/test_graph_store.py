from pathlib import Path

from kosika.graph_store import load_graph, save_graph
from kosika.knowledge_graph import (
    Entity, EntityType, KnowledgeGraph, Relationship, RelationType,
)


def _graph() -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.add_entity(Entity(id="photo", name="photosynthesis", entity_type=EntityType.CONCEPT,
                        description="plants make food from light", embedding=[1.0, 0.0]))
    g.add_entity(Entity(id="chloro", name="chlorophyll", entity_type=EntityType.CONCEPT,
                        description="the green pigment", embedding=[0.0, 1.0]))
    g.add_relationship(Relationship(id="r1", source="photo", target="chloro",
                                    relation_type=RelationType.PART_OF, weight=0.9))
    return g


def test_round_trip_keeps_entities_relationships_and_embeddings(tmp_path: Path):
    path = tmp_path / "mind.db"
    save_graph(_graph(), path)
    back = load_graph(path)
    assert set(back.entities) == {"photo", "chloro"}
    assert back.entities["photo"].embedding == [1.0, 0.0]
    rels = back.get_relationships("photo")
    assert len(rels) == 1 and rels[0].target == "chloro"
    assert rels[0].weight == 0.9


def test_load_over_stale_file_replaces_not_merges(tmp_path: Path):
    path = tmp_path / "mind.db"
    save_graph(_graph(), path)
    fresh = KnowledgeGraph()
    fresh.add_entity(Entity(id="only", name="only", entity_type=EntityType.CONCEPT))
    save_graph(fresh, path)
    assert set(load_graph(path).entities) == {"only"}
