"""Measure static-detection precision and recall on the labelled corpus.

good/  : idiomatic code with no known bug. Every finding there is a false positive.
buggy/ : lines tagged '# <<BUG>>' are the ground truth. A finding on a tagged line is a true positive.

Usage: python scripts/measure_detection.py [--json] [--verbose]
Runs the real analysis pipeline (static stage only) through the app, so it measures what users get.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopatch import create_app  # noqa: E402
from autopatch.core import analysis as analysis_svc  # noqa: E402
from tests.helpers import upload_files  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "detection_corpus"
MARK = "# <<BUG>>"
OPTS = {"run_tests": False, "run_static": True, "run_security": False, "run_entrypoints": False}
MEDIUM_UP = {"Critical", "High", "Medium"}


def load_corpus() -> tuple[dict, set]:
    files, bugs = {}, set()
    for kind in ("good", "buggy"):
        for p in sorted((CORPUS / kind).glob("*.py")):
            rel = f"{kind}/{p.name}"
            text = p.read_text(encoding="utf-8")
            files[rel] = text
            for n, line in enumerate(text.splitlines(), 1):
                if kind == "buggy" and MARK in line:
                    bugs.add((rel, n))
    return files, bugs


def measure() -> dict:
    files, bugs = load_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app({"DATA_DIR": Path(tmp) / "d", "STORAGE_BACKEND": "local"})
        ext = app.extensions["autopatch"]
        pid = upload_files(app.test_client(), files)
        run = analysis_svc.start_analysis(ext["store"], ext["cfg"], pid, OPTS, background=False)
        found = ext["store"].find("findings", {"run_id": run["_id"]})
    rows = []
    for f in found:
        place = (f["file"], f["line"])
        rows.append({"file": f["file"], "line": f["line"], "rule": f.get("rule"), "severity": f["severity"],
                     "confidence": f["confidence"], "title": f["title"], "tp": place in bugs})

    def stats(subset):
        tp = sum(r["tp"] for r in subset)
        return {"findings": len(subset), "true_positive": tp, "false_positive": len(subset) - tp,
                "precision": round(tp / len(subset), 3) if subset else None}

    hit = {(r["file"], r["line"]) for r in rows if r["tp"]}
    out = {
        "good_files": sum(1 for k in files if k.startswith("good/")),
        "buggy_files": sum(1 for k in files if k.startswith("buggy/")),
        "planted_bugs": len(bugs),
        "bugs_found": len(hit),
        "recall": round(len(hit) / len(bugs), 3),
        "all_findings": stats(rows),
        "medium_or_higher": stats([r for r in rows if r["severity"] in MEDIUM_UP]),
        "false_positives_in_good": sum(1 for r in rows if r["file"].startswith("good/")),
        "missed": sorted(f"{f}:{n}" for f, n in bugs - hit),
        "false_positive_detail": [r for r in rows if not r["tp"]],
    }
    return out


if __name__ == "__main__":
    res = measure()
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1))
    else:
        print(f"corpus: {res['good_files']} good files, {res['buggy_files']} buggy files, {res['planted_bugs']} planted bugs")
        print(f"recall: {res['bugs_found']}/{res['planted_bugs']} = {res['recall']}")
        for k in ("all_findings", "medium_or_higher"):
            s = res[k]
            print(f"{k}: {s['findings']} findings, {s['true_positive']} TP, {s['false_positive']} FP, precision={s['precision']}")
        print(f"false positives in good/: {res['false_positives_in_good']}")
        print("missed:", ", ".join(res["missed"]) or "none")
        if "--verbose" in sys.argv:
            for r in res["false_positive_detail"]:
                print("  FP", r["file"], r["line"], r["rule"], r["severity"], r["confidence"], r["title"])
