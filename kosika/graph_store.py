"""Persist a knowledge graph in SQLite so it outlives the run."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .knowledge_graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationType,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, entity_type TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}',
  embedding TEXT);
CREATE TABLE IF NOT EXISTS relationships(
  id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
  relation_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
  properties TEXT NOT NULL DEFAULT '{}');
"""


def _enum(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def save_graph(graph: KnowledgeGraph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA)
        con.execute("DELETE FROM entities")
        con.execute("DELETE FROM relationships")
        con.executemany(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                (
                    e.id,
                    e.name,
                    _enum(e.entity_type),
                    e.description,
                    json.dumps(e.properties),
                    json.dumps(e.embedding),
                )
                for e in graph.entities.values()
            ),
        )
        con.executemany(
            "INSERT INTO relationships VALUES (?,?,?,?,?,?)",
            (
                (
                    r.id,
                    r.source,
                    r.target,
                    _enum(r.relation_type),
                    r.weight,
                    json.dumps(r.properties),
                )
                for r in graph.relationships.values()
            ),
        )
        con.commit()
    finally:
        con.close()


def load_graph(path: Path) -> KnowledgeGraph:
    graph = KnowledgeGraph()
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA)
        for row in con.execute(
            "SELECT id,name,entity_type,description,properties,embedding FROM entities"
        ):
            graph.add_entity(
                Entity(
                    id=row[0],
                    name=row[1],
                    entity_type=EntityType(row[2]),
                    description=row[3],
                    properties=json.loads(row[4]),
                    embedding=json.loads(row[5]),
                )
            )
        for row in con.execute(
            "SELECT id,source,target,relation_type,weight,properties FROM relationships"
        ):
            graph.add_relationship(
                Relationship(
                    id=row[0],
                    source=row[1],
                    target=row[2],
                    relation_type=RelationType(row[3]),
                    weight=row[4],
                    properties=json.loads(row[5]),
                )
            )
    finally:
        con.close()
    return graph
