"""Simple matrix routines."""


def transpose(m):
    return [list(row) for row in zip(*m)]


def identity(n):
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]


def multiply(a, b):
    bt = transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) for col in bt] for row in a]
