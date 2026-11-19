"""Conditional definitions and optional features."""
import sys

if sys.version_info >= (3, 11):
    def parse_flag(value):
        return value.lower() in {"1", "true", "yes"}
else:
    def parse_flag(value):
        return str(value).lower() in {"1", "true", "yes"}

try:
    import ujson as json_impl
except ImportError:
    import json as json_impl


def dumps(obj):
    return json_impl.dumps(obj)
