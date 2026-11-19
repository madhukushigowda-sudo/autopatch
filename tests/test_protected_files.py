"""The Gemini and patch engine files must stay byte-identical to the verified baseline unless a change is approved separately."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = json.loads((Path(__file__).parent / "fixtures" / "protected_hashes.json").read_text(encoding="utf-8"))


def test_protected_engine_files_are_byte_identical_to_the_baseline():
    changed = [f for f, digest in EXPECTED.items() if hashlib.sha256((ROOT / f).read_bytes()).hexdigest() != digest]
    assert not changed, f"protected file(s) changed without an approved engine proposal: {changed}"
    assert len(EXPECTED) == 6
