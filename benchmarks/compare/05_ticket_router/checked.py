from pyhelpers import ask_json, check

CHOICES = ["billing", "bug", "other"]
QUESTION = "Does the customer say the problem blocks their work?"


def confident(answer, what):
    if not 0 <= answer["confidence"] <= 1:
        raise ValueError(f"{what} confidence is not from 0 to 1")
    return answer


def main(inputs, complete=None, tools=None):
    text = check(inputs, {"ticket": {"text": str}}, "inputs")["ticket"]["text"]
    label = confident(
        ask_json(
            complete,
            "Classify the text. Reply with JSON: label (one of the choices) and "
            "confidence from 0 to 1.",
            {"text": text, "choices": CHOICES},
            {"label": str, "confidence": float},
        ),
        "label",
    )
    if label["label"] not in CHOICES:
        raise ValueError(f"label {label['label']!r} is not one of the choices")
    urgent = confident(
        ask_json(
            complete,
            "Answer the question from the given text. Reply with JSON: answer "
            "(true or false), confidence from 0 to 1, and reason.",
            {"question": QUESTION, "given": text},
            {"answer": bool, "confidence": float, "reason": str},
        ),
        "decision",
    )
    return {
        "queue": label["label"] if label["confidence"] >= 0.6 else "triage",
        "escalate": urgent["answer"] and urgent["confidence"] >= 0.7,
        "reason": urgent["reason"],
    }
