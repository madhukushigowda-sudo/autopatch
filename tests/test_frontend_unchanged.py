"""The approved AutoPatch frontend (templates, CSS, JavaScript, Patchy artwork) must stay byte-identical.

The fixture holds the SHA-256 of every file under templates/ and static/ as approved. Backend work must not change them.
If a frontend change is ever approved on purpose, regenerate tests/fixtures/frontend_hashes.json in that same change."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = json.loads((ROOT / "tests" / "fixtures" / "frontend_hashes.json").read_text(encoding="utf-8"))


def _current():
    out = {}
    for base in ("templates", "static"):
        for p in sorted((ROOT / base).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                out[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_no_frontend_file_changed_added_or_removed():
    cur = _current()
    assert sorted(cur) == sorted(BASELINE), {"added": sorted(set(cur) - set(BASELINE)), "removed": sorted(set(BASELINE) - set(cur))}
    assert [k for k in cur if cur[k] != BASELINE[k]] == []
