"""Local JSON-file store: the fallback when MongoDB is unavailable.

One JSON file per collection, written atomically (temp file + os.replace) and
guarded by a lock. Designed for a single-process local demo, not for several
server processes sharing one directory.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .base import Store

log = logging.getLogger(__name__)
_COLLECTION_RE = re.compile(r"^[a-z_]{1,40}$")


class LocalStore(Store):
    backend = "local"

    def __init__(self, directory, fallback_reason: Optional[str] = None):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, dict]] = {}
        self.fallback_reason = fallback_reason

    # ---- internals -------------------------------------------------
    def _path(self, collection: str) -> Path:
        if not _COLLECTION_RE.match(collection):
            raise ValueError(f"Invalid collection name: {collection!r}")
        return self._dir / f"{collection}.json"

    def _load(self, collection: str) -> dict[str, dict]:
        if collection in self._cache:
            return self._cache[collection]
        path = self._path(collection)
        data: dict[str, dict] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("collection file is not a JSON object")
            except (ValueError, OSError) as exc:
                # Keep the broken file for inspection instead of silently overwriting it.
                backup = path.with_suffix(f".corrupt-{int(time.time())}")
                try:
                    path.replace(backup)
                except OSError:
                    pass
                log.warning("Local store file %s was unreadable (%s); moved to %s", path.name, exc, backup.name)
                data = {}
        self._cache[collection] = data
        return data

    def _flush(self, collection: str) -> None:
        path = self._path(collection)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=f".{collection}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._cache[collection], handle, ensure_ascii=False)
            # Windows can briefly lock the target (antivirus/indexer): retry a few times.
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ---- Store API -------------------------------------------------
    def insert(self, collection: str, doc: dict) -> str:
        with self._lock:
            data = self._load(collection)
            new = copy.deepcopy(doc)
            doc_id = new.get("_id") or uuid.uuid4().hex
            new["_id"] = doc_id
            if doc_id in data:
                raise ValueError(f"Duplicate _id in {collection}: {doc_id}")
            data[doc_id] = new
            self._flush(collection)
            return doc_id

    def get(self, collection: str, doc_id: str) -> Optional[dict]:
        with self._lock:
            doc = self._load(collection).get(doc_id)
            return copy.deepcopy(doc) if doc is not None else None

    def find(self, collection, flt=None, sort=None, limit=0) -> list[dict]:
        with self._lock:
            docs = list(self._load(collection).values())
            if flt:
                docs = [d for d in docs if all(d.get(k) == v for k, v in flt.items())]
            if sort:
                field, direction = sort
                docs.sort(key=lambda d: (d.get(field) is None, d.get(field)), reverse=direction < 0)
            if limit:
                docs = docs[:limit]
            return copy.deepcopy(docs)

    def update(self, collection: str, doc_id: str, fields: dict[str, Any]) -> bool:
        with self._lock:
            data = self._load(collection)
            if doc_id not in data:
                return False
            data[doc_id].update(copy.deepcopy(fields))
            self._flush(collection)
            return True

    def delete(self, collection: str, doc_id: str) -> bool:
        with self._lock:
            data = self._load(collection)
            if doc_id not in data:
                return False
            del data[doc_id]
            self._flush(collection)
            return True
