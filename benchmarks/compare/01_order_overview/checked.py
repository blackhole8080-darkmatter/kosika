from pyhelpers import check

ORDER = {"customer": str, "items": str, "total": float}


def main(inputs, complete=None, tools=None):
    order = check(inputs, {"order": ORDER}, "inputs")["order"]
    count = len(order["items"].split(","))
    return {
        "greeting": "Hello, " + order["customer"].strip().upper() + "!",
        "item_count": count,
        "size": "large" if count >= 5 else "medium" if count >= 2 else "small",
        "odd_total": order["total"] % 2 == 1,
    }
