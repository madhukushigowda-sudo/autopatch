"""Statistics with guarded division."""


def mean(values):
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)


def ratio(a, b):
    return a / b if b else None


def percent(part, whole):
    if whole == 0:
        return 0
    return 100 * part / whole
