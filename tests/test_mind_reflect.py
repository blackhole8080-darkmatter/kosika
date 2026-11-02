# tests/test_mind_reflect.py
from kosika.mind import make_reflect, reflect


def test_rejects_confidence_out_of_range():
    v = reflect({"label": "bug", "confidence": 7})
    assert not v.ok and any("confidence" in r for r in v.reasons)


def test_rejects_label_not_offered():
    v = reflect({"label": "invented", "confidence": 0.5}, choices=["bug", "feature"])
    assert not v.ok and any("label" in r for r in v.reasons)


def test_accepts_a_well_formed_answer():
    v = reflect({"label": "bug", "confidence": 0.5}, choices=["bug", "feature"])
    assert v.ok and v.reasons == ()


def test_cross_model_verifier_complaint_rejects():
    v = reflect({"label": "bug", "confidence": 0.5}, choices=["bug"],
                verifier=lambda a: "the second model disagrees")
    assert not v.ok


def test_make_reflect_gives_the_complaint_and_what_it_judged():
    reflect_on = make_reflect(choices=["bug", "feature"], verifier=None)
    complaint, judged = reflect_on({"label": "bug", "confidence": 0.5})
    assert complaint == "" and judged == {"label": "bug", "confidence": 0.5}
    complaint, judged = reflect_on({"label": "nope", "confidence": 0.5})
    assert "label" in complaint and judged == {}
