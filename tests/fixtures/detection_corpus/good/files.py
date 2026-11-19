"""File helpers using context managers and logging."""
import logging
import os
import shutil

log = logging.getLogger(__name__)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def safe_remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("could not remove %s: %s", path, exc)


def copy_all(paths, dest):
    copied = []
    for p in paths:
        try:
            copied.append(shutil.copy(p, dest))
        except OSError:
            log.exception("copy failed for %s", p)
    return copied
