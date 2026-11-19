"""Entry-point execution: traceback parsing and status classification."""
from types import SimpleNamespace

from autopatch.core.entrypoints import classify, parse_traceback, pick_entry_points, run_entry_point
from autopatch.core.indexer import index_project

TB = '''Traceback (most recent call last):
  File "{w}/main.py", line 4, in <module>
    print(divide(1, 0))
  File "{w}/calc.py", line 2, in divide
    return a / b
ZeroDivisionError: division by zero
'''


def test_parse_traceback_extracts_frames_type_and_relative_paths(tmp_path):
    tb = parse_traceback(TB.format(w=tmp_path), tmp_path)
    assert tb["type"] == "ZeroDivisionError" and tb["message"] == "division by zero"
    assert [(f["rel"], f["line"], f["func"]) for f in tb["frames"]] == [("main.py", 4, "<module>"), ("calc.py", 2, "divide")]
    assert tb["frames"][1]["code"] == "return a / b"


def test_parse_traceback_uses_the_last_exception_in_a_chain(tmp_path):
    text = ('Traceback (most recent call last):\n  File "a.py", line 1, in <module>\n    x\nKeyError: 1\n\n'
            'During handling of the above exception, another exception occurred:\n\n'
            'Traceback (most recent call last):\n  File "a.py", line 3, in <module>\n    y\nValueError: bad\n')
    tb = parse_traceback(text, tmp_path)
    assert tb["type"] == "ValueError" and tb["frames"][0]["line"] == 3


def test_frames_outside_the_project_have_no_relative_path(tmp_path):
    text = 'Traceback (most recent call last):\n  File "/usr/lib/python3/json/__init__.py", line 9, in loads\n    x\nValueError: v\n'
    assert parse_traceback(text, tmp_path)["frames"][0]["rel"] is None


def test_no_traceback_returns_none(tmp_path):
    assert parse_traceback("just some warning text\n", tmp_path) is None
    assert parse_traceback("", tmp_path) is None


def test_classification_rules():
    ok = SimpleNamespace(error=None, timed_out=False, returncode=0, stderr="")
    assert classify(ok, None) == "ok"
    assert classify(SimpleNamespace(error="x", timed_out=False, returncode=None, stderr=""), None) == "not_run"
    assert classify(SimpleNamespace(error=None, timed_out=True, returncode=-9, stderr=""), None) == "timeout"
    eof = {"type": "EOFError", "message": "EOF when reading a line"}
    assert classify(SimpleNamespace(error=None, timed_out=False, returncode=1, stderr=""), eof) == "needs_input"
    assert classify(SimpleNamespace(error=None, timed_out=False, returncode=1, stderr=""), {"type": "KeyError", "message": "k"}) == "exception"
    assert classify(SimpleNamespace(error=None, timed_out=False, returncode=2, stderr="usage: main.py path"), None) == "needs_arguments"
    assert classify(SimpleNamespace(error=None, timed_out=False, returncode=1, stderr="boom"), None) == "exit_nonzero"


def _project(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def go(tmp_path, files, rel="main.py", timeout=10):
    root = _project(tmp_path / "w", files)
    return run_entry_point(root, rel, tmp_path / "run", timeout, 100_000)


def test_runs_ok_and_crashing_scripts(tmp_path):
    assert go(tmp_path, {"main.py": "print('hello')\n"})["status"] == "ok"
    res = go(tmp_path / "b", {"calc.py": "def d(a, b):\n    return a / b\n",
                              "main.py": "from calc import d\nprint(d(1, 0))\n"})
    assert res["status"] == "exception"
    assert res["traceback"]["type"] == "ZeroDivisionError"
    assert any(f["rel"] == "calc.py" and f["line"] == 2 for f in res["traceback"]["frames"])


def test_timeout_is_inconclusive_not_an_exception(tmp_path):
    res = go(tmp_path, {"main.py": "import time\nwhile True:\n    time.sleep(0.1)\n"}, timeout=1)
    assert res["status"] == "timeout" and res["traceback"] is None


def test_script_waiting_for_input_or_arguments_is_not_a_crash(tmp_path):
    assert go(tmp_path / "a", {"main.py": "input('x')\n"})["status"] == "needs_input"
    args = {"main.py": "import argparse\np = argparse.ArgumentParser()\np.add_argument('path')\np.parse_args()\n"}
    assert go(tmp_path / "b", args)["status"] == "needs_arguments"


def test_src_layout_is_importable(tmp_path):
    res = go(tmp_path, {"src/pkg/__init__.py": "VALUE = 5\n", "main.py": "import pkg\nprint(pkg.VALUE)\n"})
    assert res["status"] == "ok" and "5" in res["stdout_tail"]


def test_pick_entry_points_skips_tests_setup_and_unguarded_apps(tmp_path):
    root = _project(tmp_path, {
        "main.py": "print(1)\n",
        "tool.py": "if __name__ == '__main__':\n    pass\n",
        "setup.py": "if __name__ == '__main__':\n    pass\n",
        "app.py": "x = 1\n",
        "test_x.py": "if __name__ == '__main__':\n    pass\n",
    })
    picks = pick_entry_points(index_project(root), 10)
    assert sorted(picks) == ["main.py", "tool.py"]
    assert pick_entry_points(index_project(root), 1) == picks[:1]
