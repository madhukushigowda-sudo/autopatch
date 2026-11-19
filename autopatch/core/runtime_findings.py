"""Findings built from things that were actually OBSERVED while running code (confirmation="runtime")."""
from __future__ import annotations

import os
import re
from pathlib import PurePosixPath

from .findings import Ctx, make_snippet, new_finding

_IDENT = re.compile(r"[A-Za-z_]\w*")
_NO_MODULE = re.compile(r"No module named '([^']+)'")
_CANNOT_IMPORT = re.compile(r"cannot import name '([^']+)' from '([^']+)'")


def norm_path(p: str | None) -> str:
    return os.path.normpath(p).replace("\\", "/") if p else ""


def enclosing_function(ctx: Ctx, rel: str | None, line: int | None) -> str | None:
    best = None
    for start, end, name in ctx.func_ranges.get(rel or "", []):
        if line and start <= line <= end and (best is None or start >= best[0]):
            best = (start, name)
    return best[1] if best else None


def blame_frame(frames: list[dict], ctx: Ctx):
    """Innermost frame in project (non-test) code, else innermost frame in a test file."""
    test_frame = None
    for fr in reversed(frames):
        p = norm_path(fr.get("path"))
        if p in ctx.project_files:
            if p in ctx.test_files or PurePosixPath(p).name == "conftest.py":
                test_frame = test_frame or fr
            else:
                return fr, "project"
    return (test_frame, "test") if test_frame else (None, None)


def _statement(frames: list[dict], test_file: str) -> str | None:
    """The source line of the innermost frame inside the test file (what the test was doing)."""
    for fr in reversed(frames):
        if norm_path(fr.get("path")) == test_file:
            for ln in fr.get("lines", []):
                if not ln.startswith("E") and ln.strip() and not set(ln.strip()) <= set("^~"):
                    return ln.strip()[:200]
    return None


def _code_under_test(statement: str | None, ctx: Ctx):
    """Heuristic: the first project function whose name appears in the failing statement."""
    for ident in _IDENT.findall(statement or ""):
        if ident in ctx.functions:
            return ident, *ctx.functions[ident]
    return None


def _first_line(text: str | None, limit: int = 200) -> str:
    return ((text or "").strip().splitlines() or [""])[0][:limit]


def missing_module(exc_type: str | None, message: str | None, ctx: Ctx) -> str | None:
    """Top-level name of an import failure caused by a package that is NOT part of the project."""
    if exc_type not in ("ModuleNotFoundError", "ImportError"):
        return None
    m = _NO_MODULE.search(message or "")
    if not m:
        return None
    top = m.group(1).split(".")[0]
    return None if top in ctx.module_names else top


def from_test_failure(failure: dict, ctx: Ctx) -> dict:
    crash, exc = failure["crash"], failure["exc"]
    frames = crash.get("frames", [])
    nodeid = failure["nodeid"]
    test_file = nodeid.split("::")[0]
    etype = exc.get("type") or "Error"
    msg_line = _first_line(exc.get("message") or crash.get("message"))
    frame, where = blame_frame(frames, ctx)
    statement = _statement(frames, test_file)
    is_assertion = etype == "AssertionError"
    phase_note = "" if failure["phase"] == "call" else f" during test {failure['phase']}"

    file = line = function = under_test = None
    if where == "project":
        file, line = norm_path(frame["path"]), frame["lineno"]
        function = enclosing_function(ctx, file, line)
    else:
        under_test = _code_under_test(statement, ctx)
        if under_test:
            function, file, line = under_test
        elif frame:
            file, line = norm_path(frame["path"]), frame["lineno"]

    if is_assertion:
        category, confidence = "test_failure", "Medium"
        title = f"Test failure: {nodeid.split('::')[-1]}"
        description = f"{nodeid} failed: {msg_line or 'assertion failed'}"
        root = ("The test's assertion does not hold"
                + (f" ({msg_line})" if msg_line else "")
                + ". Either the implementation returns a wrong result for this input or the test's expectation is wrong; "
                  "AutoPatch cannot tell which from the failure alone.")
        if under_test:
            root += f" The failing statement calls `{under_test[0]}` ({under_test[1]}:{under_test[2]}), the most likely code under test (heuristic: its name appears in the statement)."
    elif where == "project":
        category, confidence = "runtime_exception", "High"
        title = f"Unhandled {etype} in project code"
        description = f"{etype}: {msg_line} (raised at {file}:{line}{' in ' + function if function else ''}; reproduced by {nodeid})"
        root = (f"An unhandled {etype} is raised by project code at {file}:{line}"
                f"{' (function ' + function + ')' if function else ''} while running the test. "
                "The traceback shows the call chain from the test to the failing line.")
    else:
        category, confidence = "runtime_exception", "Low"
        title = f"{etype} raised in test code"
        description = f"{etype}: {msg_line} ({nodeid}{phase_note})"
        root = (f"The exception was raised in test or fixture code, not inside project code{phase_note}. "
                "It may be a problem with the test itself, a fixture, or the environment.")

    trigger = f"Running `pytest {nodeid}`." + (f" The failing statement is `{statement}`." if statement else "")
    return new_finding(
        category=category, severity="High", confidence=confidence, confirmation="runtime",
        title=title, description=description, root_cause=root, trigger=trigger,
        detection_method="pytest execution (runtime)",
        evidence={
            "summary": f"pytest observed {etype} in {nodeid}{phase_note}.",
            "nodeid": nodeid, "exception": {"type": etype, "message": (exc.get("message") or "")[:500]},
            "traceback": frames, "traceback_text": crash.get("text"), "captured": failure.get("captured") or [],
            "statement": statement,
            "snippet": make_snippet(ctx.original_dir, file, line),
            "code_under_test": ({"function": under_test[0], "file": under_test[1], "line": under_test[2]} if under_test else None),
        },
        file=file, line=line, function=function, rule=etype,
    )


def from_collect_error(ce: dict, ctx: Ctx) -> dict:
    crash, exc = ce["crash"], ce["exc"]
    etype = exc.get("type") or "Error"
    msg_line = _first_line(exc.get("message") or crash.get("message"))
    frames = crash.get("frames", [])
    frame, _ = blame_frame(frames, ctx)
    file = norm_path(frame["path"]) if frame else (ce["nodeid"] or None)
    line = frame["lineno"] if frame else None
    return new_finding(
        category="collection_error", severity="High", confidence="High", confirmation="runtime",
        title="Test module could not be collected",
        description=f"{ce['nodeid'] or 'collection'}: {etype}: {msg_line}",
        root_cause=(f"Importing the test module failed with {etype}, so none of its tests ran. "
                    "Typical causes: a broken import in project code, a missing symbol, a circular import, or an error at import time."),
        trigger=f"Importing {ce['nodeid'] or 'the test module'} (pytest collection).",
        detection_method="pytest collection (runtime)",
        evidence={"summary": f"pytest collection failed with {etype}: {msg_line}", "traceback": frames,
                  "traceback_text": crash.get("text"), "snippet": make_snippet(ctx.original_dir, file, line)},
        file=file, line=line, rule=etype,
    )


def from_hang(nodeid: str | None, timeout_s: float, ctx: Ctx) -> dict:
    file = nodeid.split("::")[0] if nodeid else None
    return new_finding(
        category="test_timeout", severity="High", confidence="Medium", confirmation="runtime",
        title="Test did not finish before the timeout",
        description=(f"{nodeid} was still running after {timeout_s:g}s and the test run was terminated." if nodeid
                     else f"pytest did not finish within {timeout_s:g}s (it may have hung during collection or start-up)."),
        root_cause=("Possible causes: an infinite loop, a blocking call (network, input, lock), or a genuinely slow operation. "
                    "AutoPatch cannot tell which from the timeout alone; a slow machine can also cause it."),
        trigger=f"Running `pytest {nodeid}`." if nodeid else "Running the test suite.",
        detection_method="pytest execution with a hard timeout (runtime)",
        evidence={"summary": f"The process was killed after {timeout_s:g}s.", "nodeid": nodeid},
        file=file, line=None, rule="timeout",
    )


def from_missing_package(pkg: str, places: list[str], ctx: Ctx) -> dict:
    return new_finding(
        category="environment", severity="Medium", confidence="High", confirmation="runtime",
        title=f"Package '{pkg}' is not installed",
        description=f"Importing '{pkg}' failed while running {', '.join(places[:3])}.",
        root_cause=("The package is imported but is not installed in the Python environment AutoPatch runs in. "
                    "This is an environment problem, not necessarily a bug in the project, but it stops tests or scripts from running "
                    "and limits what AutoPatch can verify."),
        trigger="Importing the package.",
        detection_method="ImportError observed while executing the project (runtime)",
        evidence={"summary": f"ModuleNotFoundError for '{pkg}'. Install it (pip install {pkg}) and re-run the analysis."},
        file=None, line=None, rule="ModuleNotFoundError",
    )


def from_entry_exception(entry: str, tb: dict, ctx: Ctx, stdin_note: str) -> dict:
    frames = tb["frames"]
    blame = next((f for f in reversed(frames) if f["rel"] in ctx.project_files), None)
    file, line = (blame["rel"], blame["line"]) if blame else (entry, None)
    function = enclosing_function(ctx, file, line)
    etype, msg = tb["type"], _first_line(tb["message"])
    return new_finding(
        category="runtime_exception", severity="High", confidence="Medium", confirmation="runtime",
        title=f"Unhandled {etype} when running {entry}",
        description=f"{etype}: {msg} (raised at {file}:{line})" if line else f"{etype}: {msg}",
        root_cause=(f"Running the script raised {etype}. It was started {stdin_note}, so the failure may depend on "
                    "inputs or arguments the script normally receives; confidence is therefore Medium."),
        trigger=f"Running `python {entry}` {stdin_note}.",
        detection_method="Entry-point execution (runtime)",
        evidence={"summary": f"{etype} observed when running {entry}.", "exception": {"type": etype, "message": tb["message"][:500]},
                  "traceback": [{"path": f["rel"] or f["file"], "lineno": f["line"], "lines": [f["code"]] if f["code"] else []} for f in frames],
                  "traceback_text": tb["text"], "snippet": make_snippet(ctx.original_dir, file, line)},
        file=file, line=line, function=function, rule=etype,
    )
