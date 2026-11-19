"""Analysis pipeline (Stage 2).

For one uploaded project, in a background thread (never inside a request):

    prepare -> structure -> tests -> static -> security -> entrypoints -> findings -> cleanup

* The original upload is NEVER executed or modified. Everything runs in a disposable COPY
  under DATA_DIR/runs/<run_id>/work, which is deleted at the end.
* A SHA-256 digest of the original files is taken before and after, and stored with the run
  as evidence that the originals were not changed.
* This is a restricted local prototype, not a hardened sandbox (see sandbox.py).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path, PurePosixPath

from ..utils import is_valid_id, utcnow_iso
from . import findings as F
from . import patterns as P
from . import runtime_findings as RF
from .entrypoints import pick_entry_points, run_entry_point
from .projects import original_dir
from .pytest_runner import run_pytest
from .tools import run_bandit, run_ruff

log = logging.getLogger(__name__)

STAGES = [
    ("prepare", "Prepare isolated copy"),
    ("structure", "Parse project structure"),
    ("tests", "Run pytest tests"),
    ("static", "Static analysis (Ruff + AST patterns)"),
    ("security", "Security scan (Bandit)"),
    ("entrypoints", "Run entry points"),
    ("findings", "Build findings"),
    ("cleanup", "Clean up"),
]
DEFAULT_OPTIONS = {"run_tests": True, "run_static": True, "run_security": True, "run_entrypoints": True}
EXECUTION_NOTE = (
    "Restricted local prototype, not a hardened sandbox: the project's tests and entry points were executed "
    "in a child process on this machine, in a temporary copy, with a timeout and without the server's secrets."
)


class AnalysisError(Exception):
    """An analysis could not be started or completed. The message is safe to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- filesystem helpers
def tree_digest(root: Path) -> str:
    """SHA-256 over every file's relative path and content (order independent of the OS)."""
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = Path(dirpath, name)
            h.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            try:
                h.update(hashlib.sha256(path.read_bytes()).digest())
            except OSError:
                h.update(b"<unreadable>")
    return h.hexdigest()


def _on_rm_error(func, path, _exc):  # Windows: read-only files make rmtree fail
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path: Path) -> bool:
    """Delete a directory tree; returns True if it is gone."""
    path = Path(path)
    if path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_rm_error)
        else:  # pragma: no cover
            shutil.rmtree(path, onerror=_on_rm_error)
    return not path.exists()


# ---------------------------------------------------------------- context for finding builders
def build_ctx(index: dict, original: Path) -> F.Ctx:
    graph = index["graph"]
    node_path = {n["id"]: n["path"] for n in graph["nodes"]}
    all_files = set(index["all_files"])
    test_files = set(index["tests"]["files"]) | {p for p in all_files if PurePosixPath(p).name == "conftest.py"}
    counts: Counter = Counter()
    where: dict[str, tuple[str, int]] = {}
    ranges: dict[str, list[tuple[int, int, str]]] = {}
    for f in index["files"]:
        ranges[f["path"]] = [(fn["lineno"], fn["end_lineno"], fn["qualname"]) for fn in f["functions"]]
        if f["is_test"]:
            continue
        for fn in f["functions"]:
            counts[fn["name"]] += 1
            where[fn["name"]] = (f["path"], fn["lineno"])
    return F.Ctx(
        original_dir=Path(original),
        project_files=all_files,
        test_files=test_files,
        imported_files={node_path[e["target"]] for e in graph["edges"] if e["target"] in node_path},
        entry_files=set(index["entry_points"]),
        module_names={n["id"].split(".")[0] for n in graph["nodes"]},
        python_version=sys.version.split()[0],
        functions={n: where[n] for n, c in counts.items() if c == 1},
        func_ranges=ranges,
    )


def group_duplicates(items: list[dict]) -> list[dict]:
    """Several tests failing at the same line with the same exception are ONE bug, shown once."""
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for f in items:
        nodeid = f["evidence"].get("nodeid")
        f.setdefault("tests", [nodeid] if nodeid else [])
        if f["category"] == "runtime_exception" and f["file"] and f["line"] and nodeid:
            key = (f["file"], f["line"], f["rule"])
            if key in seen:
                seen[key]["tests"].append(nodeid)
                continue
            seen[key] = f
        out.append(f)
    for f in out:
        f["occurrences"] = max(1, len(f.get("tests", [])))
        if f["occurrences"] > 1:
            f["description"] += f" ({f['occurrences']} tests fail the same way.)"
    return out


# ---------------------------------------------------------------- run bookkeeping
class _Recorder:
    def __init__(self, store, run: dict):
        self.store, self.run_id = store, run["_id"]
        self.stages = run["stages"]
        self._t0: dict[str, float] = {}

    def _flush(self, current=None):
        self.store.update("runs", self.run_id, {"stages": self.stages, "current_stage": current})

    def _get(self, sid):
        return next(s for s in self.stages if s["id"] == sid)

    def start(self, sid):
        s = self._get(sid)
        s["status"] = "running"
        self._t0[sid] = time.monotonic()
        self._flush(sid)

    def finish(self, sid, status="done", detail=None):
        s = self._get(sid)
        s["status"], s["detail"] = status, detail
        s["duration_s"] = round(time.monotonic() - self._t0.get(sid, time.monotonic()), 2)
        self._flush(None)

    def skip(self, sid, detail):
        s = self._get(sid)
        s["status"], s["detail"], s["duration_s"] = "skipped", detail, 0
        self._flush(None)


def _trim_pytest(res: dict) -> dict:
    keep = ("status", "exit_code", "timed_out", "duration_s", "note", "summary", "tests", "hung_tests",
            "stdout_tail", "stderr_tail", "output_truncated", "command")
    return {k: res[k] for k in keep if k in res}


def _tool_summary(res: dict) -> dict:
    return {k: res.get(k) for k in ("tool", "status", "version", "count", "error", "duration_s")}


def _trim_entry(rec: dict) -> dict:
    return {k: rec[k] for k in ("file", "status", "exit_code", "duration_s", "timed_out", "stdout_tail",
                                "stderr_tail", "output_truncated", "note")}


# ---------------------------------------------------------------- the pipeline
def execute_run(store, cfg, run_id: str) -> None:  # noqa: C901 - one linear pipeline, kept in one place
    run = store.get("runs", run_id)
    project = store.get("projects", run["project_id"])
    opts = run["options"]
    rec = _Recorder(store, run)
    max_out = int(cfg.MAX_OUTPUT_KB) * 1024
    run_dir = Path(cfg.DATA_DIR) / "runs" / run_id
    work = run_dir / "work"
    original = original_dir(cfg, project["_id"])
    summary: dict = {"pytest": None, "ruff": None, "bandit": None, "ast_patterns": None, "entrypoints": []}
    items: list[dict] = []
    missing: dict[str, list[str]] = {}
    digest_before = digest_after = None
    status, error, final = "failed", None, []
    t0 = time.monotonic()
    store.update("runs", run_id, {"status": "running", "started_at": utcnow_iso()})

    try:
        # -- prepare
        rec.start("prepare")
        if not original.is_dir():
            raise AnalysisError("The project's files are no longer on disk. Upload the project again.", 404)
        digest_before = tree_digest(original)
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(original, work)
        rec.finish("prepare", detail=f"Copied {project['file_count']} file(s) to a temporary working folder")
        index = project["map"]
        ctx = build_ctx(index, original)

        # -- structure (syntax errors found by Stage 1's parser; static)
        rec.start("structure")
        syntax_files = {e["path"] for e in index["syntax_errors"]}
        for err in index["syntax_errors"]:
            items.append(F.from_syntax_error({"msg": err["msg"], "line": err["line"]}, err["path"], ctx))
        rec.finish("structure", detail=f"{len(syntax_files)} file(s) with syntax/decoding errors")

        # -- tests (runtime)
        if opts["run_tests"]:
            rec.start("tests")
            res = run_pytest(work, run_dir, cfg.PYTEST_TIMEOUT_SECONDS, max_out)
            summary["pytest"] = _trim_pytest(res)
            for fail in res["failures"]:
                pkg = RF.missing_module(fail["exc"].get("type"), fail["exc"].get("message"), ctx)
                if pkg:
                    missing.setdefault(pkg, []).append(fail["nodeid"])
                else:
                    items.append(RF.from_test_failure(fail, ctx))
            for ce in res["collect_errors"]:
                pkg = RF.missing_module(ce["exc"].get("type"), ce["exc"].get("message"), ctx)
                if pkg:
                    missing.setdefault(pkg, []).append(ce["nodeid"] or "test collection")
                else:
                    items.append(RF.from_collect_error(ce, ctx))
            if res["timed_out"]:
                for nodeid in (res["hung_tests"] or [None])[:3]:
                    items.append(RF.from_hang(nodeid, cfg.PYTEST_TIMEOUT_SECONDS, ctx))
            s = res["summary"]
            detail = {
                "no_tests": "No tests were found",
                "timeout": f"Timed out after {cfg.PYTEST_TIMEOUT_SECONDS:g}s",
                "error": res.get("note") or "pytest could not complete",
                "not_run": res.get("note") or "pytest could not be started",
            }.get(res["status"], f"{s['passed']} passed, {s['failed']} failed, {s['errors']} error(s), {s['skipped']} skipped")
            rec.finish("tests", detail=detail)
        else:
            rec.skip("tests", "Disabled for this run")

        # -- static: Ruff + AST patterns
        if opts["run_static"]:
            rec.start("static")
            ruff = run_ruff(work, run_dir / "tmp", cfg.TOOL_TIMEOUT_SECONDS)
            summary["ruff"] = _tool_summary(ruff)
            for it in P.drop_ignored(work, ruff["raw"]):
                if it["file"] not in syntax_files:  # unparsable files already have a syntax finding
                    items.append(F.from_ruff(it, ctx))
            parsable = [f["path"] for f in index["files"] if not f["error"]]
            patterns = P.scan_project(work, parsable)
            summary["ast_patterns"] = {"status": "ok", "count": len(patterns)}
            items += [F.from_pattern(p, ctx) for p in patterns]
            rec.finish("static", detail=f"Ruff: {ruff['status']} ({ruff['count']} item(s)); AST patterns: {len(patterns)}")
        else:
            rec.skip("static", "Disabled for this run")

        # -- security: Bandit (static)
        if opts["run_security"]:
            rec.start("security")
            bandit = run_bandit(work, run_dir / "tmp", cfg.TOOL_TIMEOUT_SECONDS)
            summary["bandit"] = _tool_summary(bandit)
            kept = [b for b in bandit["raw"] if b["file"] not in ctx.test_files]  # asserts/fixtures in tests are noise
            items += [F.from_bandit(b, ctx) for b in kept]
            rec.finish("security", detail=f"Bandit: {bandit['status']}" + (f" ({len(kept)} item(s))" if bandit["status"] == "ok" else ""))
        else:
            rec.skip("security", "Disabled for this run")

        # -- entry points (runtime)
        if opts["run_entrypoints"]:
            rec.start("entrypoints")
            picks = pick_entry_points(index, int(cfg.MAX_ENTRYPOINTS))
            for rel in picks:
                result = run_entry_point(work, rel, run_dir, cfg.ENTRYPOINT_TIMEOUT_SECONDS, max_out)
                summary["entrypoints"].append(_trim_entry(result))
                if result["status"] == "exception":
                    tb = result["traceback"]
                    pkg = RF.missing_module(tb["type"], tb["message"], ctx)
                    if pkg:
                        missing.setdefault(pkg, []).append(rel)
                    else:
                        items.append(RF.from_entry_exception(rel, tb, ctx, "with no arguments and no input (stdin closed)"))
            rec.finish("entrypoints", detail=f"Ran {len(picks)} entry point(s)" if picks else "No runnable entry points found")
        else:
            rec.skip("entrypoints", "Disabled for this run")

        # -- findings
        rec.start("findings")
        for pkg, places in missing.items():
            items.append(RF.from_missing_package(pkg, sorted(set(places)), ctx))
        final = F.finalize(group_duplicates(items), run_id, project["_id"])
        truncated = max(0, len(final) - int(cfg.MAX_FINDINGS))
        final = final[: int(cfg.MAX_FINDINGS)]
        for f in final:
            f["project_name"] = project["name"]
            store.insert("findings", f)
        summary["findings_truncated"] = truncated
        rec.finish("findings", detail=f"{len(final)} finding(s)")
        status = "completed"
    except AnalysisError as exc:
        error = str(exc)
        log.warning("Analysis %s failed: %s", run_id, error)
    except Exception:  # noqa: BLE001 - never leak internals to the UI; the log has the traceback
        error = "The analysis failed unexpectedly. See the server log for details."
        log.exception("Analysis %s crashed", run_id)
    finally:
        cleaned = False
        try:
            rec.start("cleanup")
            cleaned = remove_tree(run_dir)
            digest_after = tree_digest(original) if original.is_dir() else None
            rec.finish("cleanup", status="done" if cleaned else "failed",
                       detail="Temporary working folder removed" if cleaned else "Could not remove the temporary folder")
        except Exception:  # noqa: BLE001
            log.exception("Cleanup of run %s failed", run_id)
        integrity = {
            "digest_before": digest_before, "digest_after": digest_after,
            "original_unchanged": digest_before is not None and digest_before == digest_after,
            "workspace_removed": cleaned,
        }
        warnings = []
        if digest_before is not None and digest_before != digest_after:
            warnings.append("The original uploaded files changed during the analysis. Treat this project as untrusted.")
        counts = F.summarize(final)
        finished = utcnow_iso()
        store.update("runs", run_id, {
            "status": status, "error": error, "finished_at": finished, "duration_s": round(time.monotonic() - t0, 2),
            "summary": summary, "findings_summary": counts, "integrity": integrity, "warnings": warnings,
            "current_stage": None, "execution_note": EXECUTION_NOTE,
        })
        store.update("projects", project["_id"], {"last_run": {
            "id": run_id, "status": status, "finished_at": finished, "findings": counts,
            "pytest": (summary["pytest"] or {}).get("summary"),
        }})


# ---------------------------------------------------------------- starting runs
_state_lock = threading.Lock()
_active: dict[str, str] = {}  # run_id -> project_id (one analysis at a time keeps the machine responsive)


def normalize_options(raw) -> dict:
    opts = dict(DEFAULT_OPTIONS)
    if isinstance(raw, dict):
        for key in DEFAULT_OPTIONS:
            if isinstance(raw.get(key), bool):
                opts[key] = raw[key]
    return opts


def start_analysis(store, cfg, project_id: str, options=None, background: bool = True) -> dict:
    if not is_valid_id(project_id) or not store.get("projects", project_id):
        raise AnalysisError("Project not found.", 404)
    project = store.get("projects", project_id)
    run = {
        "_id": uuid.uuid4().hex, "project_id": project_id, "project_name": project["name"],
        "status": "queued", "options": normalize_options(options), "created_at": utcnow_iso(),
        "stages": [{"id": sid, "label": label, "status": "pending", "detail": None, "duration_s": None} for sid, label in STAGES],
        "current_stage": None, "summary": None, "findings_summary": None, "integrity": None, "error": None,
        "warnings": [], "execution_note": EXECUTION_NOTE,
    }
    with _state_lock:
        if _active:
            raise AnalysisError("Another analysis is still running. Wait for it to finish.", 409)
        _active[run["_id"]] = project_id
    try:
        store.insert("runs", run)
    except Exception:
        with _state_lock:
            _active.pop(run["_id"], None)
        raise

    def _main():
        try:
            execute_run(store, cfg, run["_id"])
        finally:
            with _state_lock:
                _active.pop(run["_id"], None)

    if background:
        threading.Thread(target=_main, daemon=True, name=f"analysis-{run['_id'][:6]}").start()
    else:
        _main()
    return store.get("runs", run["_id"])


def recover_interrupted_runs(store) -> int:
    """After a restart, runs that were 'running' can never finish: mark them failed."""
    n = 0
    for r in store.find("runs"):
        if r.get("status") in ("queued", "running"):
            store.update("runs", r["_id"], {"status": "failed", "finished_at": utcnow_iso(), "current_stage": None,
                                            "error": "Interrupted: the server restarted while this analysis was running."})
            n += 1
    return n


# ---------------------------------------------------------------- overview (stored results only)
def build_overview(store) -> dict:
    projects = store.find("projects")
    runs = store.find("runs", sort=("created_at", -1))
    latest: dict[str, dict] = {}
    for r in runs:
        if r["status"] == "completed" and r["project_id"] not in latest:
            latest[r["project_id"]] = r
    by_sev = dict.fromkeys(F.SEVERITIES, 0)
    by_conf = dict.fromkeys(F.CONFIRMATIONS, 0)
    total = 0
    for r in latest.values():
        fs = r.get("findings_summary") or {}
        total += fs.get("total", 0)
        for k, v in (fs.get("by_severity") or {}).items():
            by_sev[k] = by_sev.get(k, 0) + v
        for k, v in (fs.get("by_confirmation") or {}).items():
            by_conf[k] = by_conf.get(k, 0) + v
    recent = [
        {k: r.get(k) for k in ("_id", "project_id", "project_name", "status", "created_at", "finished_at", "error")}
        | {"findings_total": (r.get("findings_summary") or {}).get("total"), "pytest": ((r.get("summary") or {}).get("pytest") or {}).get("summary")}
        for r in runs[:8]
    ]
    return {
        "projects": len(projects), "analyses": len(runs),
        "analyses_completed": sum(1 for r in runs if r["status"] == "completed"),
        "projects_analyzed": len(latest),
        "findings_total": total, "by_severity": by_sev, "by_confirmation": by_conf,
        "recent": recent,
        "basis": "Counts use the most recent completed analysis of each project.",
    }
