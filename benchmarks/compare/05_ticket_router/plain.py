import json

CHOICES = ["billing", "bug", "other"]
QUESTION = "Does the customer say the problem blocks their work?"


def main(inputs, complete=None, tools=None):
    text = inputs["ticket"]["text"]
    label = json.loads(
        complete(
            "Classify the text. Reply with JSON: label (one of the choices) and "
            "confidence from 0 to 1.",
            json.dumps({"text": text, "choices": CHOICES}),
        )
    )
    urgent = json.loads(
        complete(
            "Answer the question from the given text. Reply with JSON: answer "
            "(true or false), confidence from 0 to 1, and reason.",
            json.dumps({"question": QUESTION, "given": text}),
        )
    )
    return {
        "queue": label["label"] if label["confidence"] >= 0.6 else "triage",
        "escalate": urgent["answer"] and urgent["confidence"] >= 0.7,
        "reason": urgent["reason"],
    }
