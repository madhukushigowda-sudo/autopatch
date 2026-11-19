"""Storage factory: MongoDB Atlas when configured and reachable, otherwise local JSON."""
from __future__ import annotations

import logging

from .base import Store
from .local_store import LocalStore

log = logging.getLogger(__name__)

__all__ = ["Store", "LocalStore", "create_store"]


def create_store(cfg) -> Store:
    mode = cfg.STORAGE_BACKEND
    local_dir = cfg.DATA_DIR / "db"

    if mode == "local":
        return LocalStore(local_dir)  # chosen on purpose: no fallback reason
    if mode not in ("auto", "mongo"):
        raise RuntimeError(f"STORAGE_BACKEND must be auto, mongo or local (got {mode!r})")

    if not cfg.MONGODB_URI:
        return LocalStore(local_dir, fallback_reason="MONGODB_URI is not set")

    try:
        from .mongo_store import MongoStore

        return MongoStore(cfg.MONGODB_URI, cfg.MONGODB_DB, cfg.MONGODB_TIMEOUT_MS)
    except ImportError:
        reason = "pymongo is not installed"
    except Exception as exc:  # connection/auth/DNS errors
        # Only the exception type is reported: messages can contain host names or credentials.
        reason = f"MongoDB connection failed ({type(exc).__name__})"
    log.warning("Using local storage instead of MongoDB: %s", reason)
    return LocalStore(local_dir, fallback_reason=reason)
