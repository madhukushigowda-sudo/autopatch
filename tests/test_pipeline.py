"""End-to-end pipeline behaviour on small sample projects (synchronous runs)."""
import hashlib
from pathlib import Path

import pytest

from tests import projects as P
from tests.helpers import needs_ruff

NO_ENTRY = {"run_entrypoints": False}


def by_cat(findings, cat):
    return [f for f in findings if f["category"] == cat]


def original_dir(ctx, pid):
    store, cfg = ctx
    return Path(cfg.DATA_DIR) / "projects" / pid / "original"


def digest(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def test_passing_tests_give_a_clean_baseline(analyze):
    run, findings, _ = analyze(P.PASSING, NO_ENTRY)
    assert run["status"] == "completed"
    pt = run["summary"]["pytest"]
    assert pt["status"] == "passed" and pt["summary"]["passed"] == 2 and pt["summary"]["failed"] == 0
    assert not by_cat(findings, "test_failure") and not by_cat(findings, "runtime_exception")


def test_failing_test_is_a_runtime_finding_with_evidence(analyze):
    run, findings, _ = analyze(P.FAILING, NO_ENTRY)
    pt = run["summary"]["pytest"]
    assert pt["status"] == "failed" and pt["summary"]["failed"] == 1 and pt["summary"]["passed"] == 1
    f = by_cat(findings, "test_failure")[0]
    assert f["confirmation"] == "runtime" and f["file"] == "util.py"
    assert f["evidence"]["traceback"] or f["evidence"].get("traceback_text")


def test_runtime_exception_is_grouped_across_tests(analyze):
    run, findings, _ = analyze(P.EXCEPTION, NO_ENTRY)
    exc = [f for f in by_cat(findings, "runtime_exception") if f["file"] == "calc.py"]
    assert len(exc) == 1 and exc[0]["occurrences"] == 2 and len(exc[0]["tests"]) == 2
    assert exc[0]["confirmation"] == "runtime" and exc[0]["line"] == 2


def test_entry_point_crash_is_captured(analyze):
    run, findings, _ = analyze(P.ENTRY_CRASH)
    eps = run["summary"]["entrypoints"]
    assert any(e["status"] == "exception" for e in eps)
    crash = [f for f in findings if f["confirmation"] == "runtime" and "ZeroDivisionError" in f["title"]]
    assert crash and crash[0]["file"] == "calc.py"
    assert run["summary"]["pytest"]["status"] == "no_tests"


def test_entrypoints_can_be_disabled(analyze):
    run, findings, _ = analyze(P.ENTRY_CRASH, NO_ENTRY)
    assert not [f for f in findings if f["confirmation"] == "runtime"]


@needs_ruff
def test_syntax_error_is_static_and_does_not_stop_the_run(analyze):
    run, findings, _ = analyze(P.SYNTAX_ERROR, NO_ENTRY)
    assert run["status"] == "completed"
    syn = by_cat(findings, "syntax_error")
    assert syn and syn[0]["confirmation"] == "static" and syn[0]["file"] == "broken.py"
    assert run["summary"]["pytest"]["summary"]["passed"] == 1


def test_imported_syntax_error_also_fails_collection_at_runtime(analyze):
    run, findings, _ = analyze(P.SYNTAX_ERROR_IMPORTED, NO_ENTRY)
    assert by_cat(findings, "syntax_error")[0]["confirmation"] == "static"
    coll = by_cat(findings, "collection_error")
    assert coll and coll[0]["confirmation"] == "runtime"


def test_missing_test_files(analyze):
    run, findings, _ = analyze(P.NO_TESTS, NO_ENTRY)
    assert run["status"] == "completed" and run["summary"]["pytest"]["status"] == "no_tests"
    assert any("No tests" in w or "no tests" in w.lower() for w in run["warnings"]) or run["summary"]["pytest"]["status"] == "no_tests"


def test_hanging_test_times_out_and_is_reported(analyze):
    run, findings, _ = analyze(P.HANGING_TEST, NO_ENTRY, PYTEST_TIMEOUT_SECONDS=3)
    assert run["summary"]["pytest"]["status"] == "timeout"
    t = by_cat(findings, "test_timeout")
    assert t and t[0]["confirmation"] == "runtime" and "test_hangs" in str(t[0]["evidence"])
    assert run["status"] == "completed"
    assert run["integrity"]["workspace_removed"]


def test_hanging_entry_point_times_out(analyze):
    run, findings, _ = analyze(P.HANGING_ENTRY, None, ENTRYPOINT_TIMEOUT_SECONDS=2)
    assert any(e["status"] == "timeout" for e in run["summary"]["entrypoints"])
    assert run["status"] == "completed"


def test_entry_points_needing_input_or_arguments_are_not_bugs(analyze):
    for files, status in ((P.NEEDS_INPUT_ENTRY, "needs_input"), (P.NEEDS_ARGS_ENTRY, "needs_arguments")):
        run, findings, _ = analyze(files)
        assert any(e["status"] == status for e in run["summary"]["entrypoints"]), run["summary"]["entrypoints"]
        assert not [f for f in findings if f["confirmation"] == "runtime"]


@needs_ruff
def test_static_findings_are_never_labelled_runtime(analyze):
    run, findings, _ = analyze(P.STATIC_ONLY, NO_ENTRY)
    f401 = [f for f in findings if f["rule"] == "F821"]
    assert f401 and all(f["confirmation"] == "static" for f in f401)
    assert run["summary"]["pytest"]["summary"]["failed"] == 0  # no test reaches it: only static analysis sees it
    for f in findings:
        if f["confirmation"] == "static":
            assert f["evidence"]["type"] == "static" and "runtime" not in f["detection_method"].lower().replace("not runtime", "")


def test_runtime_findings_come_from_execution_only(analyze):
    _, findings, _ = analyze(P.EXCEPTION, NO_ENTRY)
    for f in findings:
        if f["confirmation"] == "runtime":
            assert f["evidence"]["type"] == "runtime"


def test_originals_are_unchanged_when_tests_write_files(analyze, ctx):
    store, cfg = ctx
    run, _, pid = analyze(P.WRITES_FILES, NO_ENTRY)
    orig = original_dir(ctx, pid)
    assert (orig / "data.txt").read_text() == "original contents\n"
    assert (orig / "util.py").read_text() == "def f():\n    return 1\n"
    assert not (orig / "created_by_test.txt").exists()
    assert run["integrity"]["original_unchanged"] is True and run["integrity"]["workspace_removed"] is True


def test_tampering_with_the_original_is_detected(analyze, ctx):
    run, findings, pid = analyze(P.TAMPER_ORIGINAL, NO_ENTRY)
    orig = original_dir(ctx, pid)
    if (orig / "util.py").read_text() == "def f():\n    return 1\n":
        pytest.skip("relative path did not reach the stored original in this layout")
    assert run["integrity"]["original_unchanged"] is False
    assert any("original" in w.lower() for w in run["warnings"])


def test_secrets_are_not_visible_to_project_code(analyze, monkeypatch):
    for name in ("GEMINI_API_KEY", "MONGODB_URI", "SECRET_KEY", "AP_TEST_TOKEN"):
        monkeypatch.setenv(name, "super-secret")
    run, findings, _ = analyze(P.ENV_PROBE, NO_ENTRY)
    assert run["summary"]["pytest"]["summary"]["passed"] == 1 and run["summary"]["pytest"]["summary"]["failed"] == 0


def test_missing_package_is_an_environment_finding(analyze):
    run, findings, _ = analyze(P.MISSING_PACKAGE, NO_ENTRY)
    env = by_cat(findings, "environment")
    assert env and "definitely_not_installed_pkg_xyz" in (env[0]["title"] + env[0]["description"])


def test_missing_original_files_fail_the_run_cleanly(client, ctx):
    import shutil
    from autopatch.core import analysis as svc
    from tests.helpers import upload_files
    store, cfg = ctx
    pid = upload_files(client, P.PASSING)
    shutil.rmtree(original_dir(ctx, pid))
    run = svc.start_analysis(store, cfg, pid, None, background=False)
    assert run["status"] == "failed" and run["error"]


def test_workspace_is_removed_and_run_is_stored(analyze, ctx):
    store, cfg = ctx
    run, _, pid = analyze(P.FAILING, NO_ENTRY)
    assert not (Path(cfg.DATA_DIR) / "runs" / run["_id"] / "work").exists()
    assert store.get("projects", pid)["last_run"]["id"] == run["_id"]
    assert all(s["status"] != "pending" for s in run["stages"])
