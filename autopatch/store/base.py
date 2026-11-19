"""Repository abstraction.

The rest of the app only talks to this interface, so MongoDB Atlas and the
local JSON fallback are interchangeable. Documents are plain JSON-compatible
dicts with a string ``_id`` (uuid4 hex) and ISO-8601 text timestamps.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class Store(ABC):
    backend: str = "abstract"
    fallback_reason: Optional[str] = None  # set when we wanted Mongo but had to fall back

    @abstractmethod
    def insert(self, collection: str, doc: dict) -> str:
        """Insert a document and return its id."""

    @abstractmethod
    def get(self, collection: str, doc_id: str) -> Optional[dict]:
        """Return a copy of the document or None."""

    @abstractmethod
    def find(
        self,
        collection: str,
        flt: Optional[dict] = None,
        sort: Optional[tuple[str, int]] = None,
        limit: int = 0,
    ) -> list[dict]:
        """Equality-filter query. ``sort`` is (field, 1 | -1)."""

    @abstractmethod
    def update(self, collection: str, doc_id: str, fields: dict[str, Any]) -> bool:
        """Set fields on a document. Returns False if it does not exist."""

    @abstractmethod
    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document. Returns False if it did not exist."""

    def describe(self) -> dict:
        return {"backend": self.backend, "fallback_reason": self.fallback_reason}
