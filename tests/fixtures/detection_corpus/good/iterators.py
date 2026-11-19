"""Small iterator helpers."""


def first(iterable, default=None):
    it = iter(iterable)
    try:
        return next(it)
    except StopIteration:
        return default


def chunked(items, size):
    if size <= 0:
        raise ValueError("size must be positive")
    for start in range(0, len(items), size):
        yield items[start:start + size]


def window(items, n):
    return [items[i:i + n] for i in range(len(items) - n + 1)]
