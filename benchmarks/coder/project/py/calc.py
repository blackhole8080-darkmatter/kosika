def add(a, b):
    return a + b


def average(numbers):
    # Bug: an empty list should give 0, not fail.
    return sum(numbers) / len(numbers)


def clamp(value, low, high):
    return max(low, min(value, high))
