from pyhelpers import check

ORDERS = {"orders": [{"customer": str, "total": float}]}


def main(inputs, complete=None, tools=None):
    orders = check(inputs, {"input": ORDERS}, "inputs")["input"]["orders"]
    totals = {}
    for order in orders:
        totals[order["customer"]] = totals.get(order["customer"], 0) + order["total"]
    ranked = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)
    return {"names": [name for name, _ in ranked[:3]], "best_total": ranked[0][1]}
