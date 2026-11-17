# tests/test_mind_runtime_wiring.py
"""The Mind loop folded into one model call, proven without a live model."""

import json

import pytest

from kosika.ast import ModelDecl
from kosika.graph_store import load_graph, save_graph
from kosika.knowledge_graph import Entity, EntityType, KnowledgeGraph
from kosika.mind import mind_hooks
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.runtime import RejectedAnswer, _ask_model


def _graph(tmp_path):
    graph = KnowledgeGraph()
    graph.add_entity(
        Entity(
            id="photo",
            name="photosynthesis",
            entity_type=EntityType.CONCEPT,
            description="plants make food from light",
            embedding=[1.0, 0.0],
        )
    )
    db = tmp_path / "mind.db"
    save_graph(graph, db)
    return load_graph(db)


def _ask(provider, arguments, schema, *, retries, mind=None, verify_answer=None):
    model = ModelDecl(name="m", input="Ask", output="Answer")
    return _ask_model(
        provider,
        model,
        dict(arguments),
        schema,
        retries=retries,
        verify_answer=verify_answer,
        mind=mind,
    )


def test_retrieve_folds_memory_into_the_prompt(tmp_path):
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"label": "bug", "confidence": 0.5}))]
    )
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["label", "confidence"],
    }
    hooks = mind_hooks(
        _graph(tmp_path), embed=lambda text: [1.0, 0.0], choices=["bug", "feature"]
    )

    _ask(provider, {"text": "what does light do"}, schema, retries=0, mind=hooks)

    sent = json.loads(provider.requests[0].messages[1].content)
    assert "photosynthesis" in sent["mind_context"]


def test_reflect_refuses_a_bad_answer_then_takes_the_asking(tmp_path):
    provider = ScriptedModelProvider(
        [
            ModelResponse(content=json.dumps({"label": "invented", "confidence": 0.4})),
            ModelResponse(content=json.dumps({"label": "bug", "confidence": 0.4})),
        ]
    )
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["label", "confidence"],
    }
    hooks = mind_hooks(
        _graph(tmp_path), embed=lambda text: [1.0, 0.0], choices=["bug", "feature"]
    )

    answer, reply = _ask(provider, {"text": "triage"}, schema, retries=1, mind=hooks)

    assert answer["label"] == "bug"
    assert len(provider.requests) == 2
    assert "was not offered" in reply["rejected"][0]["reason"]
    assert reply["mind"]["label"] == "bug"
    assert reply["mind"]["confidence"] == 0.4
    assert "photosynthesis" in reply["mind"]["retrieved"]


def test_no_mind_leaves_the_prompt_as_the_program_wrote_it():
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"label": "bug"}))]
    )
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    }

    _ask(provider, {"text": "triage"}, schema, retries=0)

    assert json.loads(provider.requests[0].messages[1].content) == {"text": "triage"}


def test_the_program_check_speaks_before_reflect(tmp_path):
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"label": "bug", "confidence": 2}))]
    )
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["label", "confidence"],
    }
    hooks = mind_hooks(_graph(tmp_path), embed=lambda text: [1.0, 0.0])

    def program_check(answer):
        return ("the program wants a shorter label", [])

    with pytest.raises(RejectedAnswer) as refused:
        _ask(
            provider,
            {"text": "triage"},
            schema,
            retries=0,
            mind=hooks,
            verify_answer=program_check,
        )

    assert "shorter label" in str(refused.value)
    assert "confidence must be a number" not in str(refused.value)


def test_reflect_still_gets_a_say_when_the_program_check_is_silent(tmp_path):
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"label": "bug", "confidence": 2}))]
    )
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["label", "confidence"],
    }
    hooks = mind_hooks(_graph(tmp_path), embed=lambda text: [1.0, 0.0])

    def program_check(answer):
        return ("", ["the program signed off"])

    with pytest.raises(RejectedAnswer) as refused:
        _ask(
            provider,
            {"text": "triage"},
            schema,
            retries=0,
            mind=hooks,
            verify_answer=program_check,
        )

    assert "reflected and refused" in str(refused.value)
    assert "confidence must be a number" in str(refused.value)
