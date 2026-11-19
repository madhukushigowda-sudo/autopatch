"""Load settings from a file, falling back to defaults."""
import json
import os

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

DEFAULTS = {"debug": False, "retries": 3}


def load(path):
    settings = dict(DEFAULTS)
    try:
        with open(path, "rb") as fh:
            if path.endswith(".toml") and tomllib:
                settings.update(tomllib.load(fh))
            else:
                settings.update(json.load(fh))
    except FileNotFoundError:
        pass
    return settings


def env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default
