# tests/test_mind_runtime_live.py
"""The Mind loop on a live model. Skipped when Ollama is not running."""

import urllib.error
import urllib.request

import pytest

from kosika.embeddings import ollama_embedder
from kosika.graph_store import load_graph, save_graph
from kosika.knowledge_graph import Entity, EntityType, KnowledgeGraph
from kosika.mind import mind_hooks

OLLAMA = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


pytestmark = pytest.mark.skipif(not _ollama_up(), reason="needs a running Ollama")


def test_live_retrieve_reaches_the_model(tmp_path):
    graph = KnowledgeGraph()
    graph.add_entity(
        Entity(
            id="photo",
            name="photosynthesis",
            entity_type=EntityType.CONCEPT,
            description="plants make food from light",
            embedding=ollama_embedder(EMBED_MODEL, OLLAMA)(
                ["plants make food from light"]
            )[0],
        )
    )
    db = tmp_path / "mind.db"
    save_graph(graph, db)

    embedder = ollama_embedder(EMBED_MODEL, OLLAMA)
    hooks = mind_hooks(load_graph(db), embed=lambda text: embedder([text])[0])

    context = hooks.context({"text": "how do plants make food from light"})
    assert "photosynthesis" in context
