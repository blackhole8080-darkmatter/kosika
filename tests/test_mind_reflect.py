# tests/test_mind_reflect.py
from kosika.mind import make_verify, reflect


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


def test_make_verify_matches_runtime_contract():
    answer = {"label": "bug", "confidence": 0.5}
    verify = make_verify(choices=["bug", "feature"], verifier=None)
    complaint, checked = verify(answer)
    assert complaint == "" and checked == [answer]
    complaint, _ = verify({"label": "nope", "confidence": 0.5})
    assert "label" in complaint
