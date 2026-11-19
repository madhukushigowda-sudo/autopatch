"""Context managers and cleanup."""
from contextlib import contextmanager


@contextmanager
def opened(path):
    fh = open(path, "rb")
    try:
        yield fh
    finally:
        fh.close()


def count_bytes(path):
    with opened(path) as fh:
        return len(fh.read())


def first_line(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            return line.rstrip("\n")
    return ""
