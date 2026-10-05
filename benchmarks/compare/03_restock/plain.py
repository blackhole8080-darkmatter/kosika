def main(inputs, complete=None, tools=None):
    request = inputs["request"]
    stock = dict(request["stock"])
    for delivery in request["deliveries"]:
        stock[delivery["item"]] = stock.get(delivery["item"], 0) + delivery["amount"]
    low = sorted(
        name for name, amount in stock.items() if amount < request["threshold"]
    )
    return {"stock": stock, "low": low}
