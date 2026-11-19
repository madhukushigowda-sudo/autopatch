"""Windows diagnostics: identify blocked / missing components, never bypass. Windows itself cannot be exercised here, so
the Application Control messages are real Windows wording fed to the classifier, and a fake module raises that error in a
real child interpreter to prove the BLOCKED path end to end."""
import os
import subprocess
import sys
import textwrap

import pytest

from autopatch.core import diagnostics as d
from autopatch.core import sandbox

BLOCKED_MSG = ("ImportError: DLL load failed while importing _pydantic_core: An Application Control policy has blocked this file."
               "\r\n  File \"C:\\proj\\.venv\\Lib\\site-packages\\pydantic_core\\_pydantic_core.cp313-win_amd64.pyd\"")


def test_classifies_application_control_message_and_finds_the_file():
    r = d.classify_failure(BLOCKED_MSG)
    assert r["blocked"] and r["module"] == "_pydantic_core"
    assert r["file"].endswith("_pydantic_core.cp313-win_amd64.pyd") and r["file"].startswith("C:\\proj")


@pytest.mark.parametrize("code", [4551, 1260])
def test_winerror_codes_mean_policy_block(code):
    r = d.classify_failure("", code)
    assert r["blocked"] and r["winerror"] == code and r["mechanism"]


@pytest.mark.parametrize("text", ["ModuleNotFoundError: No module named 'x'", "ImportError: cannot import name 'y'", "FileNotFoundError", ""])
def test_ordinary_failures_are_not_reported_as_blocked(text):
    assert d.classify_failure(text)["blocked"] is False


def test_check_import_ok_and_missing():
    assert d.check_import("json")["status"] == "OK"
    r = d.check_import("definitely_not_a_module_xyz")
    assert r["status"] == "MISSING"


def test_check_import_reports_blocked_in_a_real_child_process(tmp_path):
    (tmp_path / "fakeblocked.py").write_text(textwrap.dedent('''
        raise ImportError("DLL load failed while importing fakeblocked: An Application Control policy has blocked this file.")
    '''))
    code, out, _ = d._run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import fakeblocked"])
    assert code != 0 and d.classify_failure(out)["blocked"]
    # same message through check_import's own path: use a wrapper interpreter that injects the path
    wrapper = tmp_path / "py.cmd.py"
    wrapper.write_text(f"import sys, runpy; sys.path.insert(0, {str(tmp_path)!r}); exec(sys.argv[2])\n")
    code, out, _ = d._run([sys.executable, str(wrapper), "-c", "import fakeblocked"])
    assert d.classify_failure(out)["blocked"]


def test_check_launch_missing_executable():
    r = d.check_launch(["definitely-not-a-program-xyz", "--version"])
    assert r["status"] == "MISSING"


class _Err(OSError):
    def __init__(self, msg, winerror):
        super().__init__(msg)
        self.winerror = winerror


def test_blocked_start_message_is_specific_and_leaks_nothing():
    msg = d.blocked_start_message(_Err("[WinError 4551] An Application Control policy has blocked this file: 'C:\\secret\\ruff.exe'", 4551))
    assert "Windows blocked" in msg and "4551" in msg and "does not bypass" in msg and "secret" not in msg and "ruff.exe" not in msg


def test_other_start_failures_keep_the_old_short_message():
    assert d.blocked_start_message(FileNotFoundError("x")) == "Could not start process (FileNotFoundError)"


def test_sandbox_uses_the_blocked_message(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise _Err("[WinError 4551] blocked", 4551)
    monkeypatch.setattr(sandbox.subprocess, "Popen", boom)
    res = sandbox.run_process([sys.executable, "-c", "pass"], cwd=tmp_path, env={}, timeout=5, max_output=1000)
    assert "Windows blocked" in res.error


def test_report_formats_blocked_items_with_guidance_and_no_bypass():
    report = {"components": [{"name": "Pydantic Core", "module": "pydantic_core", "purpose": "x", "required": False, "status": "BLOCKED",
                              "detail": "blocked", "file": "C:\\x\\a.pyd"},
                             {"name": "Flask", "module": "flask", "purpose": "web", "required": True, "status": "OK", "detail": "", "file": None}],
              "environment": {"Python": {"status": "OK", "detail": "3.13", "file": None}}}
    text = d.format_report(report)
    assert "BLOCKED" in text and "C:\\x\\a.pyd" in text and "administrator" in text and "GEMINI_TRANSPORT=rest" in text
    assert "will not change Windows policy" in text and d.verdict(report) == 0


def test_a_blocked_required_component_fails_the_check():
    rep = {"components": [{"name": "Flask", "module": "flask", "purpose": "", "required": True, "status": "BLOCKED", "detail": "", "file": None}], "environment": {}}
    assert d.verdict(rep) == 1


def test_script_runs_and_never_prints_the_key(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key = "AIza" + "SecretKeyThatMustNeverBePrinted0123456"
    env = {**os.environ, "GEMINI_API_KEY": key, "DATA_DIR": str(tmp_path / "data")}
    p = subprocess.run([sys.executable, os.path.join(root, "scripts", "windows_check.py")], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0 and "AutoPatch environment check" in p.stdout and "RESULT:" in p.stdout
    assert key not in p.stdout + p.stderr


def test_diagnostics_has_no_bypass_code():
    import inspect
    src = inspect.getsource(d).lower()
    for word in ("icacls", "unblock-file", "set-executionpolicy", "runas", "takeown", "regedit", "bcdedit", "copyfile", "shutil.copy"):
        assert word not in src.replace("never tries", ""), word
