"""Run the project's own pytest suite in a child process and parse the results."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .sandbox import ProcResult, build_env, run_process

PLUGIN_DIR = Path(__file__).resolve().parent / "_plugin"
MAX_EVENT_LINES = 20000
MAX_EVENT_BYTES = 20 * 1024 * 1024
TAIL = 4000


def _has_pytest_config(workdir: Path) -> bool:
    """True if the project defines its own pytest configuration (we then honour it)."""
    def read(name: str) -> str:
        try:
            return (workdir / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    return (
        (workdir / "pytest.ini").is_file()
        or "[tool.pytest" in read("pyproject.toml")
        or "[pytest]" in read("tox.ini")
        or "[tool:pytest]" in read("setup.cfg")
    )


def _read_events(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        if not path.is_file() or path.stat().st_size > MAX_EVENT_BYTES:
            return events
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= MAX_EVENT_LINES:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # a half-written line from a killed process
                if isinstance(obj, dict) and "e" in obj:
                    events.append(obj)
    except OSError:
        pass
    return events


def _tail(text: str, n: int = TAIL) -> str:
    return text if len(text) <= n else "…" + text[-n:]


def summarize(events: list[dict], proc: ProcResult) -> dict:
    """Turn raw plugin events + process result into a stable summary (pure function; unit-testable)."""
    started: list[str] = []
    finished: set[str] = set()
    phases: dict[str, dict] = {}
    collected = 0
    collect_errors: list[dict] = []
    exitstatus = None

    for ev in events:
        kind = ev["e"]
        if kind == "collected":
            collected = int(ev.get("count", 0))
        elif kind == "start":
            if ev["nodeid"] not in started:
                started.append(ev["nodeid"])
        elif kind == "report":
            node = phases.setdefault(ev["nodeid"], {"duration": 0.0})
            node["duration"] += float(ev.get("duration") or 0.0)
            node[ev["when"]] = ev
            if ev["when"] == "teardown":
                finished.add(ev["nodeid"])
        elif kind == "collect_error":
            collect_errors.append(ev)
        elif kind == "finish":
            exitstatus = ev.get("exitstatus")

    tests: list[dict] = []
    failures: list[dict] = []
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0, "incomplete": 0}
    for nodeid, ph in phases.items():
        setup, call, teardown = ph.get("setup"), ph.get("call"), ph.get("teardown")
        outcome, source = "passed", None
        if setup and setup["outcome"] == "failed":
            outcome, source = "error", setup
        elif setup and setup["outcome"] == "skipped":
            outcome = "xfailed" if setup.get("wasxfail") is not None else "skipped"
        elif call is None:
            outcome = "incomplete"  # started but never finished (killed on timeout)
        elif call:
            if call["outcome"] == "failed":
                outcome, source = "failed", call
            elif call["outcome"] == "skipped":
                outcome = "xfailed" if call.get("wasxfail") is not None else "skipped"
            elif call.get("wasxfail") is not None:
                outcome = "xpassed"
            if teardown and teardown["outcome"] == "failed" and outcome == "passed":
                outcome, source = "error", teardown
        counts[{"passed": "passed", "failed": "failed", "error": "errors", "skipped": "skipped",
                "xfailed": "xfailed", "xpassed": "xpassed", "incomplete": "incomplete"}[outcome]] += 1
        item = {"nodeid": nodeid, "file": nodeid.split("::")[0], "outcome": outcome, "duration": round(ph["duration"], 4)}
        if source is not None:
            crash = source.get("crash") or {}
            exc = source.get("exc") or {}
            first_line = ((exc.get("message") or crash.get("message") or "").splitlines() or [""])[0][:200]
            item["message"] = f"{exc.get('type') or 'Error'}: {first_line}".strip()
            failures.append(
                {"nodeid": nodeid, "outcome": outcome, "phase": source["when"], "crash": crash,
                 "exc": source.get("exc") or {}, "captured": source.get("captured") or []}
            )
        tests.append(item)

    hung = [n for n in started if n not in finished] if proc.timed_out else []

    if proc.error:
        status = "not_run"
    elif proc.timed_out:
        status = "timeout"
    elif proc.returncode == 5 or (exitstatus is None and not events and proc.returncode == 5):
        status = "no_tests"
    elif exitstatus == 0 or (exitstatus is None and proc.returncode == 0):
        status = "passed"
    elif exitstatus == 1:
        status = "failed"
    elif exitstatus == 5:
        status = "no_tests"
    else:
        status = "error"  # collection errors, usage errors, internal errors, crash before start

    note = None
    if status == "error" and not events:
        if "No module named pytest" in proc.stderr:
            note = "pytest is not installed in the environment AutoPatch runs in."
        else:
            note = "pytest exited before running any test; see the captured output."

    return {
        "status": status,
        "exit_code": proc.returncode,
        "timed_out": proc.timed_out,
        "duration_s": proc.duration,
        "note": note or proc.error,
        "summary": {"total": len(tests), "collected": collected, **counts, "collection_errors": len(collect_errors)},
        "tests": tests[:2000],
        "failures": failures,
        "collect_errors": collect_errors,
        "hung_tests": hung,
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
        "output_truncated": proc.stdout_truncated or proc.stderr_truncated,
    }


def run_pytest(workdir: Path, run_dir: Path, timeout: float, max_output: int = 200_000) -> dict:
    """Run pytest inside ``workdir`` (a disposable COPY of the project)."""
    workdir, run_dir = Path(workdir), Path(run_dir)
    if not _has_pytest_config(workdir):
        # Without this, pytest would walk UP the folder tree and could pick up an unrelated pytest.ini.
        (workdir / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    events_path = run_dir / "events.jsonl"
    events_path.unlink(missing_ok=True)
    env = build_env(run_dir / "tmp", {"AUTOPATCH_EVENTS": str(events_path), "PYTHONPATH": str(PLUGIN_DIR)})
    cmd = [
        sys.executable, "-X", "utf8", "-m", "pytest",
        "-p", "no:cacheprovider", "-p", "_autopatch_report",
        "--tb=short", "-q", "--color=no", "-o", "addopts=", f"--rootdir={workdir}",
        "--continue-on-collection-errors",  # one broken test file must not hide every other result
    ]
    proc = run_process(cmd, cwd=workdir, timeout=timeout, env=env, max_output=max_output)
    result = summarize(_read_events(events_path), proc)
    result["command"] = "python -m pytest --tb=short -q (addopts overridden; rootdir = project copy)"
    return result
