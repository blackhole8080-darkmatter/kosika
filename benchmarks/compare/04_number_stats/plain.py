def main(inputs, complete=None, tools=None):
    values = inputs["raw"]["values"]
    numbers = []
    for value in values:
        try:
            numbers.append(float(value))
        except ValueError:
            pass
    return {
        "count": len(numbers),
        "invalid": len(values) - len(numbers),
        "average": sum(numbers) / len(numbers) if numbers else None,
        "largest": max(numbers) if numbers else None,
    }
