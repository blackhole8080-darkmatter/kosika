# tests/test_mind_retrieve.py
import pytest

from kosika.knowledge_graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationType,
)
from kosika.mind import mind_hooks, retrieve

_VECTORS = {
    "light": [1.0, 0.0, 0.0],
    "green": [0.0, 1.0, 0.0],
    # 45 degrees off both, and out of the plane the graph lives in: close to
    # everything, near to nothing.
    "vague": [0.5, 0.5, 0.70710678],
}


def _embed(text: str) -> list[float]:
    for key, vec in _VECTORS.items():
        if key in text.lower():
            return vec
    return [0.0, 0.0, 0.0]


def _graph() -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.add_entity(
        Entity(
            id="photo",
            name="photosynthesis",
            entity_type=EntityType.CONCEPT,
            description="plants make food from light",
            embedding=[1.0, 0.0, 0.0],
        )
    )
    g.add_entity(
        Entity(
            id="chloro",
            name="chlorophyll",
            entity_type=EntityType.CONCEPT,
            description="the green pigment",
            embedding=[0.0, 1.0, 0.0],
        )
    )
    g.add_entity(
        Entity(
            id="sun",
            name="sunlight",
            entity_type=EntityType.CONCEPT,
            description="the energy source",
            embedding=[0.9, 0.1, 0.0],
        )
    )
    g.add_relationship(
        Relationship(
            id="r1", source="photo", target="chloro", relation_type=RelationType.PART_OF
        )
    )
    g.add_relationship(
        Relationship(
            id="r2", source="photo", target="sun", relation_type=RelationType.RELATED_TO
        )
    )
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


def test_a_weak_match_is_left_out_rather_than_folded_in():
    """Memory that barely fits is worse than no memory: it is what the model
    reads first, and it pulls the answer toward whatever the graph happens to
    hold. Measured on nomic-embed-text, an unrelated question scores about 0.42
    against these entities and an on-topic one about 0.95."""

    got = retrieve(_graph(), "a vague remark", _embed, limit=3)

    assert got.context == "" and got.sources == ()


def test_the_floor_can_be_lowered_when_the_graph_is_known_to_be_relevant():
    got = retrieve(_graph(), "a vague remark", _embed, limit=3, floor=0.4)

    assert "photosynthesis" in got.context
    assert set(got.sources) == {"photo", "chloro", "sun"}


def _add_entity(graph, entity_id, description="fact", *, name=None, root=False):
    graph.add_entity(
        Entity(
            id=entity_id,
            name=entity_id if name is None else name,
            entity_type=EntityType.CONCEPT,
            description=description,
            embedding=[1.0, 0.0, 0.0] if root else None,
        )
    )


def _link(graph, source, target, relation=RelationType.RELATED_TO):
    graph.add_relationship(
        Relationship(
            id=f"r{len(graph.relationships)}",
            source=source,
            target=target,
            relation_type=relation,
        )
    )


def test_default_budget_bounds_a_500_neighbour_graph():
    graph = KnowledgeGraph()
    _add_entity(graph, "root", root=True)
    for i in range(500):
        _add_entity(graph, f"neighbour{i}", "a complete related fact")
        _link(graph, "root", f"neighbour{i}")

    got = retrieve(graph, "light", _embed)

    assert len(got.context) <= 8000
    assert len(got.context.splitlines()) == 17
    assert got.context.splitlines()[0] == "- root: fact"
    assert got.sources == ("root",)


@pytest.mark.parametrize("budget", [37, 38])
def test_short_budget_skips_oversized_entries_and_counts_separators(budget):
    graph = KnowledgeGraph()
    _add_entity(graph, "root", root=True)
    _add_entity(graph, "oversized", "x" * 100)
    _add_entity(graph, "tiny", "ok")
    _link(graph, "root", "oversized")
    _link(graph, "root", "tiny")

    got = retrieve(graph, "light", _embed, max_context_chars=budget)

    assert got.context == "- root: fact\n  - relates to tiny: ok"
    assert len(got.context) <= budget
    assert got.sources == ("root",)


def test_a_line_that_only_fits_without_its_separator_is_omitted():
    graph = KnowledgeGraph()
    _add_entity(graph, "root", root=True)
    _add_entity(graph, "tiny", "ok")
    _link(graph, "root", "tiny")
    expected = "- root: fact\n  - relates to tiny: ok"

    got = retrieve(graph, "light", _embed, max_context_chars=len(expected) - 1)

    assert got.context == "- root: fact"
    assert got.sources == ("root",)


def test_roots_have_priority_and_sources_only_name_rendered_roots():
    graph = KnowledgeGraph()
    _add_entity(graph, "root", root=True)
    _add_entity(graph, "oversized", "x" * 200, root=True)
    _add_entity(graph, "second", root=True)
    _add_entity(graph, "small")
    _link(graph, "root", "small")
    _link(graph, "root", "second")
    expected = "- root: fact\n- second: fact\n  - relates to small: fact"

    got = retrieve(graph, "light", _embed, limit=3, max_context_chars=len(expected))

    assert got.context == expected
    assert got.sources == ("root", "second")


def test_neighbours_cannot_crowd_out_a_later_root():
    graph = KnowledgeGraph()
    _add_entity(graph, "first", root=True)
    _add_entity(graph, "second", "a longer primary fact", root=True)
    _add_entity(graph, "small")
    _link(graph, "first", "small")
    expected = "- first: fact\n- second: a longer primary fact"

    got = retrieve(graph, "light", _embed, limit=2, max_context_chars=len(expected))

    assert got.context == expected
    assert got.sources == ("first", "second")


def test_depth_two_is_relation_specific_deduplicated_and_in_insertion_order():
    graph = KnowledgeGraph()
    for entity_id in (
        "root",
        "z",
        "a",
        "shared",
        "part",
        "part_leaf",
        "mixed",
        "too_deep",
        "incoming",
        "ignored",
    ):
        _add_entity(graph, entity_id, root=entity_id == "root")
    _link(graph, "root", "root")
    _link(graph, "root", "z")
    _link(graph, "root", "a")
    _link(graph, "root", "z")  # parallel edges must not duplicate an entity
    _link(graph, "z", "root")  # cycle
    _link(graph, "z", "shared")
    _link(graph, "a", "shared")  # diamond
    _link(graph, "shared", "too_deep")
    _link(graph, "z", "mixed", RelationType.PART_OF)
    _link(graph, "root", "shared", RelationType.IS_A)
    _link(graph, "root", "part", RelationType.PART_OF)
    _link(graph, "part", "part_leaf", RelationType.PART_OF)
    _link(graph, "incoming", "root")
    _link(graph, "root", "ignored", RelationType.PRECEDES)
    expected = (
        "- root: fact\n"
        "  - relates to z: fact\n"
        "  - relates to a: fact\n"
        "  - relates to shared: fact\n"
        "  - relates to part: fact\n"
        "  - relates to part_leaf: fact"
    )

    for _ in range(3):
        got = retrieve(graph, "light", _embed, limit=1)
        assert got.context == expected
        assert got.sources == ("root",)


def test_neighbour_limit_is_global_across_roots_and_relations():
    graph = KnowledgeGraph()
    for entity_id in ("first", "second", "a", "b", "c"):
        _add_entity(graph, entity_id, root=entity_id in ("first", "second"))
    _link(graph, "first", "a")
    _link(graph, "first", "b", RelationType.PART_OF)
    _link(graph, "second", "c")

    got = retrieve(graph, "light", _embed, limit=2, max_neighbours=1)

    assert got.context == "- first: fact\n- second: fact\n  - relates to a: fact"
    assert got.sources == ("first", "second")


def test_zero_neighbours_keeps_only_primary_roots():
    got = retrieve(_graph(), "light", _embed, limit=1, max_neighbours=0)

    assert got.context == "- photosynthesis: plants make food from light"
    assert got.sources == ("photo",)


def _unexpected_embed(text):
    pytest.fail("embedding must not run for disabled retrieval or invalid limits")


@pytest.mark.parametrize("options", [{"limit": 0}, {"max_context_chars": 0}])
def test_zero_retrieval_budget_skips_embedding(options):
    got = retrieve(_graph(), "light", _unexpected_embed, **options)

    assert got.context == ""
    assert got.sources == ()


@pytest.mark.parametrize("options", [{"limit": 0}, {"max_context_chars": 0}])
def test_hooks_with_zero_retrieval_budget_skip_embedding(options):
    hooks = mind_hooks(_graph(), _unexpected_embed, **options)

    assert hooks.context({"request": "light"}) == ""


@pytest.mark.parametrize("api", [retrieve, mind_hooks])
@pytest.mark.parametrize("option", ["limit", "max_context_chars", "max_neighbours"])
@pytest.mark.parametrize("disabled", [False, True])
def test_public_apis_reject_negative_limits_immediately(api, option, disabled):
    options = {"max_context_chars": 0} if disabled else {}
    options[option] = -1
    with pytest.raises(ValueError):
        if api is retrieve:
            api(_graph(), "light", _unexpected_embed, **options)
        else:
            api(_graph(), _unexpected_embed, **options)


@pytest.mark.parametrize("field", ["name", "description"])
@pytest.mark.parametrize("giant_is_root", [False, True])
def test_giant_entity_fields_are_omitted_whole(field, giant_is_root):
    graph = KnowledgeGraph()
    _add_entity(graph, "giant", root=giant_is_root, **{field: "x" * 1_000_000})
    _add_entity(graph, "root", root=True)
    _add_entity(graph, "small")
    _link(graph, "root", "giant")
    _link(graph, "root", "small")

    got = retrieve(graph, "light", _embed)

    assert got.context == "- root: fact\n  - relates to small: fact"
    assert got.sources == ("root",)


def test_relationships_after_the_4096_entry_scan_budget_are_not_expanded():
    graph = KnowledgeGraph()
    for entity_id in ("root", "unconnected", "before_cutoff", "after_cutoff"):
        _add_entity(graph, entity_id, root=entity_id == "root")
    # Unrelated edges still consume the scan budget; do not filter before islice.
    for _ in range(4095):
        _link(graph, "unconnected", "unconnected", RelationType.PRECEDES)
    _link(graph, "root", "before_cutoff")
    _link(graph, "root", "after_cutoff")

    got = retrieve(graph, "light", _embed, limit=1)

    assert got.context == "- root: fact\n  - relates to before_cutoff: fact"
    assert got.sources == ("root",)


@pytest.mark.parametrize(
    "options, expected",
    [
        ({"max_context_chars": 45}, "- photosynthesis: plants make food from light"),
        ({"max_neighbours": 0}, "- photosynthesis: plants make food from light"),
        (
            {"max_neighbours": 1},
            "- photosynthesis: plants make food from light\n  - relates to sunlight: the energy source",
        ),
    ],
)
def test_hooks_context_honours_retrieval_overrides(options, expected):
    hooks = mind_hooks(_graph(), _embed, limit=1, **options)

    assert hooks.context({"question": "what does light do"}) == expected
