from pyhelpers import ask_json, check

INVOICE = {
    "customer": str,
    "lines": [{"item": str, "amount": float}],
    "stated_total": float,
}


def main(inputs, complete=None, tools=None):
    text = check(inputs, {"email": {"text": str}}, "inputs")["email"]["text"]
    invoice = ask_json(
        complete,
        "Extract the invoice from the text. Reply with JSON: customer, lines "
        "(each with item and amount), and stated_total.",
        {"text": text},
        INVOICE,
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
