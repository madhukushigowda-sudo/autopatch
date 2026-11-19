"""MongoDB Atlas store (PyMongo).

pymongo is imported lazily so the app still starts on machines where it is not
installed; the factory then falls back to the local store and reports why.
"""
from __future__ import annotations

from typing import Any, Optional

from .base import Store


class MongoStore(Store):
    backend = "mongo"

    def __init__(self, uri: str, db_name: str, timeout_ms: int = 3000):
        from pymongo import MongoClient  # lazy import

        self._client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        self._client.admin.command("ping")  # fail fast if Atlas is unreachable
        self._db = self._client[db_name]

    def insert(self, collection: str, doc: dict) -> str:
        import uuid

        new = dict(doc)
        new["_id"] = new.get("_id") or uuid.uuid4().hex
        self._db[collection].insert_one(new)
        return new["_id"]

    def get(self, collection: str, doc_id: str) -> Optional[dict]:
        return self._db[collection].find_one({"_id": doc_id})

    def find(self, collection, flt=None, sort=None, limit=0) -> list[dict]:
        cursor = self._db[collection].find(flt or {})
        if sort:
            cursor = cursor.sort(sort[0], sort[1])
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    def update(self, collection: str, doc_id: str, fields: dict[str, Any]) -> bool:
        return self._db[collection].update_one({"_id": doc_id}, {"$set": fields}).matched_count > 0

    def delete(self, collection: str, doc_id: str) -> bool:
        return self._db[collection].delete_one({"_id": doc_id}).deleted_count > 0
