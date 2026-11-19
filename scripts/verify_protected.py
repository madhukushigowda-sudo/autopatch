"""Compare the protected Gemini / patch-engine files with the approved baseline (and optionally other builds).

    python scripts/verify_protected.py                       # baseline in tests/fixtures/protected_hashes.json
    python scripts/verify_protected.py --against ../gemini2/autopatch-dev ../frontend1/autopatch-dev
    python scripts/verify_protected.py --markdown            # table for docs/PROTECTED-FILES-REPORT.md

Exit code 0 only if every protected file is byte-identical everywhere it is compared.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = json.loads((ROOT / "tests" / "fixtures" / "protected_hashes.json").read_text(encoding="utf-8"))


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def main(argv: list[str]) -> int:
    others, md = [], "--markdown" in argv
    if "--against" in argv:
        others = [Path(a).resolve() for a in argv[argv.index("--against") + 1:] if not a.startswith("--")]
    cols = ["baseline"] + [o.parent.name for o in others]
    rows, bad = [], 0
    for rel, want in BASELINE.items():
        cur = sha(ROOT / rel)
        cells = [cur == want] + [sha(o / rel) == want for o in others]
        bad += cells.count(False)
        rows.append((rel, want, cur, cells))
    if md:
        print("| File | Baseline SHA-256 | This build SHA-256 | " + " | ".join(f"= {c}" for c in cols) + " |")
        print("|---|---|---|" + "---|" * len(cols))
        for rel, want, cur, cells in rows:
            print(f"| `{rel}` | `{want}` | `{cur}` | " + " | ".join("identical" if c else "**DIFFERENT**" for c in cells) + " |")
    else:
        for rel, _want, cur, cells in rows:
            print(("OK       " if all(cells) else "DIFFERENT") + f"  {rel}  {cur}")
        print("\nCompared: this build vs baseline" + "".join(f", vs {c}" for c in cols[1:]))
    print(("\nRESULT: all protected files are byte-identical." if not bad else f"\nRESULT: {bad} mismatch(es)."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
