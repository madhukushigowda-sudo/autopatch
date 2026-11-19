"""Detection quality guard. Measured on a labelled corpus: good/ (idiomatic, no bugs) and buggy/ (tagged lines = ground truth).

The thresholds are the measured 'after' values of Backend V2. They stop precision from being "improved" by silently
dropping findings (recall and bug count are pinned) and stop noisy rules from creeping back (false positives are pinned).
"""
import sys
from pathlib import Path

import pytest

from tests.helpers import needs_ruff

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import measure_detection as md  # noqa: E402

pytestmark = needs_ruff


@pytest.fixture(scope="module")
def result():
    return md.measure()


def test_corpus_is_not_shrunk(result):
    assert result["planted_bugs"] >= 21 and result["good_files"] >= 14 and result["buggy_files"] >= 20


def test_recall_is_not_traded_for_precision(result):
    assert result["recall"] == 1.0, result["missed"]


def test_no_false_positive_at_medium_or_higher(result):
    assert result["medium_or_higher"]["false_positive"] == 0, result["false_positive_detail"]
    assert result["medium_or_higher"]["precision"] == 1.0


def test_good_code_only_gets_low_severity_notes(result):
    # Known remaining trade-off: narrow 'except X: pass' is still reported, as Low/Low.
    fps = [r for r in result["false_positive_detail"] if r["file"].startswith("good/")]
    assert len(fps) <= 3
    assert all(r["severity"] == "Low" and r["confidence"] == "Low" for r in fps), fps


def test_overall_precision_floor(result):
    assert result["all_findings"]["precision"] >= 0.81


def test_ap001_downgraded_not_suppressed(analyze):
    files = {"a.py": "def f(d):\n    try:\n        return d['k']\n    except KeyError:\n        pass\n\n"
                     "def g(x):\n    try:\n        x()\n    except Exception:\n        pass\n"}
    _, findings, _ = analyze(files, {"run_tests": False, "run_security": False, "run_entrypoints": False})
    ap = {f["line"]: f for f in findings if f["rule"] == "AP001"}
    assert ap[4]["severity"] == "Low"       # narrow: still reported
    assert ap[10]["severity"] == "Medium"   # broad: unchanged


def test_new_rules_fire(analyze):
    files = {"a.py": "def f(s, items):\n    s.strip()\n    items = items.sort()\n    return items\n"}
    _, findings, _ = analyze(files, {"run_tests": False, "run_security": False, "run_entrypoints": False})
    assert {(f["rule"], f["line"]) for f in findings} >= {("AP004", 2), ("AP005", 3)}


def test_ignore_marker(analyze):
    files = {"a.py": "def f(s):\n    s.strip()  # autopatch: ignore\n    s.lower()  # autopatch: ignore[AP999]\n    return s\n"}
    _, findings, _ = analyze(files, {"run_tests": False, "run_security": False, "run_entrypoints": False})
    lines = {f["line"] for f in findings if f["rule"] == "AP004"}
    assert lines == {3}  # a marker for another code does not hide this rule


def test_ignore_marker_covers_ruff(analyze):
    files = {"a.py": "def f(x):\n    return x == None  # autopatch: ignore\n"}
    _, findings, _ = analyze(files, {"run_tests": False, "run_security": False, "run_entrypoints": False})
    assert [f for f in findings if f["rule"] == "E711"] == []


def test_test_files_get_lower_confidence(analyze):
    body = "def test_a():\n    x = 1\n    x.strip()\n"
    _, findings, _ = analyze({"test_a.py": body, "lib.py": "def f(x):\n    x.strip()\n"},
                             {"run_tests": False, "run_security": False, "run_entrypoints": False})
    by = {f["file"]: f for f in findings if f["rule"] == "AP004"}
    assert by["lib.py"]["confidence"] == "High" and by["test_a.py"]["confidence"] == "Medium"
