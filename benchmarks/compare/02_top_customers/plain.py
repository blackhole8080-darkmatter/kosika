def main(inputs, complete=None, tools=None):
    totals = {}
    for order in inputs["input"]["orders"]:
        totals[order["customer"]] = totals.get(order["customer"], 0) + order["total"]
    ranked = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)
    return {"names": [name for name, _ in ranked[:3]], "best_total": ranked[0][1]}
