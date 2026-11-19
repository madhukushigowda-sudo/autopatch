"""String helpers that use return values correctly."""
import re


def slugify(title):
    title = title.strip().lower()
    title = re.sub(r"[^a-z0-9]+", "-", title)
    return title.strip("-")


def shout(text):
    cleaned = text.strip()
    return cleaned.upper() + "!"


def pad(text, width):
    return text.ljust(width)


def lines(text):
    return [line.rstrip() for line in text.splitlines()]
