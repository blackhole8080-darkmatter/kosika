import re

from pyhelpers import check

NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def main(inputs, complete=None, tools=None):
    values = check(inputs, {"raw": {"values": [str]}}, "inputs")["raw"]["values"]
    numbers = [float(value) for value in values if NUMBER.fullmatch(value.strip())]
    return {
        "count": len(numbers),
        "invalid": len(values) - len(numbers),
        "average": sum(numbers) / len(numbers) if numbers else None,
        "largest": max(numbers) if numbers else None,
    }
