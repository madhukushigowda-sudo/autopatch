"""Small shared helpers."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def utcnow_iso() -> str:
    """UTC timestamp as ISO-8601 text (stored as text so Mongo and the local store agree)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_valid_id(value) -> bool:
    """Project/run ids are uuid4 hex strings. Validating them stops path tricks via the URL."""
    return isinstance(value, str) and bool(_ID_RE.match(value))
