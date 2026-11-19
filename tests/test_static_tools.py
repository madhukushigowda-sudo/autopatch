"""Ruff / Bandit runners and the AST pattern checks."""
import json
import sys

import pytest

from autopatch.core import tools
from autopatch.core.patterns import scan_file, scan_project
from autopatch.core.tools import parse_bandit_json, parse_ruff_json, run_bandit, run_ruff
from tests.helpers import needs_bandit, needs_ruff


def write(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- Ruff
def test_parse_ruff_json_relativises_paths_and_drops_files_outside_the_project(tmp_path):
    raw = json.dumps([
        {"code": "F821", "message": "Undefined name `x`", "filename": str(tmp_path / "pkg" / "a.py"),
         "location": {"row": 3, "column": 5}, "end_location": {"row": 3, "column": 6}, "url": "https://docs.astral.sh/x"},
        {"code": "F821", "message": "elsewhere", "filename": "/somewhere/else.py", "location": {"row": 1, "column": 1}},
    ])
    items = parse_ruff_json(raw, tmp_path)
    assert len(items) == 1 and (items[0]["file"], items[0]["line"], items[0]["code"]) == ("pkg/a.py", 3, "F821")


def test_parse_ruff_json_rejects_garbage():
    with pytest.raises(ValueError):
        parse_ruff_json('{"not": "a list"}', ".")


@needs_ruff
def test_ruff_runs_for_real_and_finds_bugs(tmp_path):
    root = write(tmp_path / "w", {"a.py": "def f():\n    return missing\n\ndef g(x=[]):\n    return x\n"})
    res = run_ruff(root, tmp_path / "scratch", 30)
    assert res["status"] == "ok" and res["version"]
    codes = {i["code"] for i in res["raw"]}
    assert {"F821", "B006"} <= codes
    assert all(i["file"] == "a.py" for i in res["raw"])


@needs_ruff
def test_ruff_ignores_the_projects_own_config(tmp_path):
    root = write(tmp_path / "w", {"a.py": "def f():\n    return missing\n", "ruff.toml": "[lint]\nselect = ['I']\n"})
    assert any(i["code"] == "F821" for i in run_ruff(root, tmp_path / "scratch", 30)["raw"])  # --isolated


def test_ruff_unavailable_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_find", lambda name: None)
    res = run_ruff(tmp_path, tmp_path / "s", 5)
    assert res["status"] == "unavailable" and "pip install ruff" in res["error"] and res["raw"] == []


# ---------------------------------------------------------------- Bandit
BANDIT_SAMPLE = {
    "results": [
        {"code": "10 eval(x)\n", "filename": "./app.py", "issue_confidence": "HIGH", "issue_severity": "MEDIUM",
         "issue_cwe": {"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"},
         "issue_text": "Use of possibly insecure function - consider using safer ast.literal_eval.",
         "line_number": 10, "line_range": [10], "more_info": "https://bandit.readthedocs.io/", "test_id": "B307",
         "test_name": "blacklist"},
        {"code": "x", "filename": "/outside/project.py", "issue_confidence": "LOW", "issue_severity": "LOW",
         "issue_text": "ignored", "line_number": 1, "test_id": "B000", "test_name": "n"},
    ],
    "errors": [{"filename": "./bad.py", "reason": "syntax error while parsing AST from file"}],
}


def test_parse_bandit_json(tmp_path):
    items, errors = parse_bandit_json(json.dumps(BANDIT_SAMPLE), tmp_path)
    assert len(items) == 1
    it = items[0]
    assert (it["file"], it["line"], it["code"], it["bandit_severity"], it["bandit_confidence"], it["cwe"]) == \
        ("app.py", 10, "B307", "MEDIUM", "HIGH", 78)
    assert errors and "bad.py" in errors[0]


def test_bandit_unavailable_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_find", lambda name: None)
    res = run_bandit(tmp_path, tmp_path / "s", 5)
    assert res["status"] == "unavailable" and "pip install bandit" in res["error"]


def test_bandit_wiring_with_a_stand_in_executable(tmp_path, monkeypatch):
    """Exercises the subprocess + exit-code-1 + JSON path using a fake 'bandit' (the real tool may be absent)."""
    fake = (
        "import json, sys\n"
        "if '--version' in sys.argv:\n    print('bandit-standin 0.0'); sys.exit(0)\n"
        f"print(json.dumps({json.dumps(BANDIT_SAMPLE)}))\nsys.exit(1)\n"  # bandit exits 1 when it finds issues
    )
    monkeypatch.setattr(tools, "_find", lambda name: (sys.executable, "-c", fake) if name == "bandit" else None)
    res = run_bandit(write(tmp_path / "w", {"app.py": "x = 1\n"}), tmp_path / "s", 20)
    assert res["status"] == "ok" and res["count"] == 1 and res["version"].startswith("bandit-standin")
    assert res["raw"][0]["code"] == "B307"


def test_bandit_nonzero_exit_other_than_1_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_find", lambda name: (sys.executable, "-c", "import sys; sys.exit(2)"))
    assert run_bandit(tmp_path, tmp_path / "s", 20)["status"] == "error"


@needs_bandit
def test_bandit_runs_for_real(tmp_path):
    root = write(tmp_path / "w", {"app.py": "import subprocess\nsubprocess.call(input(), shell=True)\n"})
    res = run_bandit(root, tmp_path / "s", 60)
    assert res["status"] == "ok" and any(i["code"].startswith("B60") for i in res["raw"])


# ---------------------------------------------------------------- AST patterns
def test_ast_patterns_find_the_documented_cases(tmp_path):
    src = (
        "def a():\n    try:\n        x()\n    except ValueError:\n        pass\n\n"
        "def b(n):\n    return n / 0\n\n"
        "def c():\n    return 1\n    print('never')\n"
    )
    p = tmp_path / "m.py"
    p.write_text(src)
    got = {(i["code"], i["line"], i["function"]) for i in scan_file(p, "m.py")}
    assert got == {("AP001", 4, "a"), ("AP002", 8, "b"), ("AP003", 12, "c")}


def test_ast_patterns_have_no_false_positives_on_clean_code(tmp_path):
    p = tmp_path / "clean.py"
    p.write_text(
        "def f(x, y):\n    if y == 0:\n        raise ValueError('zero')\n    return x / y\n\n"
        "def g():\n    try:\n        return int('1')\n    except ValueError:\n        return 0\n\n"
        "def h(items):\n    for i in items:\n        if i:\n            return i\n    return None\n"
    )
    assert scan_file(p, "clean.py") == []


def test_ast_patterns_skip_unparsable_files(tmp_path):
    write(tmp_path, {"bad.py": "def f(:\n", "ok.py": "x = 1 / 0\n"})
    assert [i["file"] for i in scan_project(tmp_path, ["bad.py", "ok.py"])] == ["ok.py"]
