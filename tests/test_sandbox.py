"""Restricted process runner: timeouts, process-tree kill, output caps, environment stripping."""
import os
import sys
import time

import pytest

from autopatch.core.sandbox import ENV_ALLOWLIST, build_env, run_process

PY = sys.executable


def is_running(pid: int) -> bool:
    """True if the process is alive. A killed-but-unreaped zombie (state Z) counts as dead."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True  # no /proc (e.g. macOS): signal 0 succeeded, so treat as alive


def run(code, tmp_path, timeout=20, **kw):
    env = build_env(tmp_path / "scratch")
    return run_process([PY, "-c", code], cwd=tmp_path, timeout=timeout, env=env, **kw)


def test_captures_stdout_stderr_and_exit_code(tmp_path):
    r = run("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)", tmp_path)
    assert (r.returncode, r.stdout.strip(), r.stderr.strip(), r.timed_out) == (3, "out", "err", False)


def test_timeout_terminates_the_process_quickly(tmp_path):
    t0 = time.monotonic()
    r = run("import time; time.sleep(60)", tmp_path, timeout=1)
    assert r.timed_out is True
    assert time.monotonic() - t0 < 8
    assert r.returncode != 0


@pytest.mark.skipif(os.name == "nt", reason="process-group kill is verified on POSIX; Windows uses taskkill /T (untested here)")
def test_timeout_kills_the_whole_process_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(120)\n"
    )
    r = run(code, tmp_path, timeout=2)
    assert r.timed_out
    child = int(pidfile.read_text())
    time.sleep(0.3)
    assert not is_running(child)  # the grandchild is gone too


@pytest.mark.skipif(os.name == "nt", reason="POSIX behaviour")
def test_background_children_are_cleaned_up_after_normal_exit(tmp_path):
    pidfile = tmp_path / "bg.pid"
    code = (
        "import subprocess, sys\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
    )
    r = run(code, tmp_path)
    assert r.returncode == 0 and not r.timed_out
    time.sleep(0.3)
    assert not is_running(int(pidfile.read_text()))


def test_output_flood_is_capped_without_blocking(tmp_path):
    t0 = time.monotonic()
    r = run("import sys\nfor _ in range(2000): sys.stdout.write('x' * 5000)\n", tmp_path, max_output=10_000)
    assert r.stdout_truncated and len(r.stdout) <= 10_000
    assert r.returncode == 0 and time.monotonic() - t0 < 15


def test_server_secrets_are_not_passed_to_children(tmp_path, monkeypatch):
    secrets = {
        "GEMINI_API_KEY": "g-secret", "MONGODB_URI": "mongodb://u:p@h/db", "SECRET_KEY": "flask-secret",
        "AWS_SECRET_ACCESS_KEY": "aws", "MY_SERVICE_TOKEN": "tok", "DB_PASSWORD": "pw",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    r = run("import os; print('\\n'.join(sorted(os.environ)))", tmp_path)
    names = set(r.stdout.split())
    assert not names & set(secrets)
    assert "g-secret" not in r.stdout and "mongodb" not in r.stdout
    assert names <= set(ENV_ALLOWLIST) | {"TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "PYTHONIOENCODING",
                                          "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
                                          "PYTHONHASHSEED", "NO_COLOR", "LC_CTYPE"}


def test_home_and_temp_point_into_the_scratch_folder(tmp_path):
    env = build_env(tmp_path / "scratch")
    for key in ("HOME", "TEMP", "TMP", "TMPDIR", "USERPROFILE"):
        assert env[key] == str(tmp_path / "scratch")


def test_stdin_is_closed(tmp_path):
    r = run("input()", tmp_path)
    assert r.returncode != 0 and "EOFError" in r.stderr


def test_missing_executable_returns_an_error_instead_of_raising(tmp_path):
    r = run_process(["definitely-not-a-real-program-xyz"], cwd=tmp_path, timeout=5, env=build_env(tmp_path / "s"))
    assert r.error and r.returncode is None
