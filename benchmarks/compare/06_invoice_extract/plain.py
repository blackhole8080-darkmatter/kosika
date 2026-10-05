import json


def main(inputs, complete=None, tools=None):
    invoice = json.loads(
        complete(
            "Extract the invoice from the text. Reply with JSON: customer, lines "
            "(each with item and amount), and stated_total.",
            json.dumps({"text": inputs["email"]["text"]}),
        )
    )
    lines = invoice["lines"]
    total = sum(line["amount"] for line in lines)
    return {
        "customer": invoice["customer"],
        "total": total,
        "matches": total == invoice["stated_total"],
        "largest": max(lines, key=lambda line: line["amount"])["item"]
        if lines
        else "none",
    }
