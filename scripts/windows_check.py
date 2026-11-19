"""Find out what is blocked or missing on this computer (read-only; changes nothing, bypasses nothing).

    python scripts\\windows_check.py              # components + environment
    python scripts\\windows_check.py --network    # also one tiny Gemini request (no project code is sent)

Safe to paste the output when asking for help: the API key and file contents are never printed.
Exit code 0 = every required component works, 1 = something required is blocked or missing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopatch.core import diagnostics as d  # noqa: E402


def main() -> int:
    report = d.run_all(ROOT, network="--network" in sys.argv)
    print(d.format_report(report))
    return d.verdict(report)


if __name__ == "__main__":
    sys.exit(main())
