"""Static analysis (Ruff) and security scanning (Bandit), run as child processes.

Both tools only READ source files; they never import or execute project code.
Each runner returns a ToolResult dict:
    {"tool", "status": ok|unavailable|error|timeout, "version", "count", "error", "duration_s", "raw": [...]}
``raw`` items are normalised dicts: {tool, code, message, file, line, column, ...}.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from .sandbox import build_env, run_process

log = logging.getLogger(__name__)

# Rules chosen to find likely BUGS rather than style problems (all verified against Ruff 0.16).
RUFF_SELECT = (
    "E9,F5,F63,F7,F811,F821,F822,F823,F841,E711,E712,E721,E722,"
    "B002,B006,B012,B014,B015,B018,B023,B025,B033,PLE,RET503"
)
RUFF_FALLBACK_SELECT = "F,E9"  # used if this Ruff version rejects one of the selectors above
BANDIT_SKIP = "B101"  # assert_used: reported separately by tests/AST, too noisy in this setting
MAX_RAW = 1000


# ---------------------------------------------------------------- discovery
@functools.lru_cache(maxsize=None)
def _find(tool: str):
    """Return the argv prefix to launch ``tool`` or None. Prefers ``python -m tool`` (same venv)."""
    if importlib.util.find_spec(tool) is not None:
        return (sys.executable, "-m", tool)
    exe = shutil.which(tool)
    return (exe,) if exe else None


def _version(argv: tuple, scratch: Path, workdir: Path) -> str | None:
    res = run_process([*argv, "--version"], cwd=workdir, timeout=20, env=build_env(scratch))
    first = (res.stdout or "").strip().splitlines()
    return first[0][:80] if first else None


def _rel(filename: str, workdir: Path) -> str | None:
    """Project-relative POSIX path, or None if the tool reported a file outside the project copy."""
    abs_path = Path(os.path.abspath(os.path.join(workdir, filename.replace("\\", "/"))))
    try:
        return abs_path.relative_to(Path(os.path.abspath(workdir))).as_posix()
    except ValueError:
        return None


def _result(tool: str, **kw) -> dict:
    base = {"tool": tool, "status": "ok", "version": None, "count": 0, "error": None, "duration_s": 0.0, "raw": []}
    base.update(kw)
    return base


# ---------------------------------------------------------------- Ruff
def parse_ruff_json(text: str, workdir: Path) -> list[dict]:
    data = json.loads(text or "[]")
    if not isinstance(data, list):
        raise ValueError("unexpected Ruff output")
    items = []
    for d in data:
        rel = _rel(str(d.get("filename", "")), workdir)
        if rel is None:
            continue
        loc = d.get("location") or {}
        end = d.get("end_location") or {}
        items.append(
            {
                "tool": "ruff", "code": d.get("code"), "message": str(d.get("message", ""))[:400],
                "file": rel, "line": loc.get("row"), "column": loc.get("column"), "end_line": end.get("row"),
                "url": d.get("url"),
            }
        )
    return items


def run_ruff(workdir: Path, scratch: Path, timeout: float) -> dict:
    argv = _find("ruff")
    if argv is None:
        return _result("ruff", status="unavailable", error="Ruff is not installed (pip install ruff).")
    env = build_env(scratch)
    version = _version(argv, scratch, workdir)
    last = None
    for select in (RUFF_SELECT, RUFF_FALLBACK_SELECT):
        cmd = [*argv, "check", "--isolated", "--no-cache", "--output-format", "json", "--select", select, "."]
        res = run_process(cmd, cwd=workdir, timeout=timeout, env=env)
        last = res
        if res.timed_out:
            return _result("ruff", status="timeout", version=version, error=f"Ruff exceeded {timeout:g}s.", duration_s=res.duration)
        if res.error:
            return _result("ruff", status="error", version=version, error=res.error)
        if res.returncode in (0, 1):
            try:
                raw = parse_ruff_json(res.stdout, workdir)
            except (ValueError, TypeError) as exc:
                return _result("ruff", status="error", version=version, error=f"Could not parse Ruff output ({exc}).")
            return _result("ruff", version=version, count=len(raw), raw=raw[:MAX_RAW], duration_s=res.duration,
                           error=None if select == RUFF_SELECT else "Used a reduced rule set (this Ruff rejected some rule selectors).")
        log.warning("Ruff exited with %s using selection %r: %s", res.returncode, select, res.stderr[:200])
    return _result("ruff", status="error", version=version, error=(last.stderr or "Ruff failed")[:300] if last else "Ruff failed")


# ---------------------------------------------------------------- Bandit
def parse_bandit_json(text: str, workdir: Path) -> tuple[list[dict], list[str]]:
    data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise ValueError("unexpected Bandit output")
    items = []
    for d in data.get("results", []):
        rel = _rel(str(d.get("filename", "")), workdir)
        if rel is None:
            continue
        cwe = d.get("issue_cwe") or {}
        items.append(
            {
                "tool": "bandit", "code": d.get("test_id"), "test_name": d.get("test_name"),
                "message": str(d.get("issue_text", ""))[:400],
                "file": rel, "line": d.get("line_number"), "line_range": d.get("line_range"),
                "bandit_severity": str(d.get("issue_severity", "LOW")).upper(),
                "bandit_confidence": str(d.get("issue_confidence", "LOW")).upper(),
                "cwe": cwe.get("id") if isinstance(cwe, dict) else None,
                "code_excerpt": str(d.get("code", ""))[:600],
                "url": d.get("more_info"),
            }
        )
    errors = [f"{e.get('filename', '?')}: {e.get('reason', '')}"[:200] for e in data.get("errors", [])][:20]
    return items, errors


def run_bandit(workdir: Path, scratch: Path, timeout: float) -> dict:
    argv = _find("bandit")
    if argv is None:
        return _result("bandit", status="unavailable", error="Bandit is not installed (pip install bandit); security scan skipped.")
    env = build_env(scratch)
    version = _version(argv, scratch, workdir)
    cmd = [*argv, "-r", ".", "-f", "json", "-q", "--skip", BANDIT_SKIP]
    res = run_process(cmd, cwd=workdir, timeout=timeout, env=env)
    if res.timed_out:
        return _result("bandit", status="timeout", version=version, error=f"Bandit exceeded {timeout:g}s.", duration_s=res.duration)
    if res.error:
        return _result("bandit", status="error", version=version, error=res.error)
    if res.returncode not in (0, 1):
        return _result("bandit", status="error", version=version, error=(res.stderr or "Bandit failed")[:300])
    try:
        raw, errors = parse_bandit_json(res.stdout, workdir)
    except (ValueError, TypeError) as exc:
        return _result("bandit", status="error", version=version, error=f"Could not parse Bandit output ({exc}).")
    return _result("bandit", version=version, count=len(raw), raw=raw[:MAX_RAW], duration_s=res.duration,
                   error=("Some files could not be scanned: " + "; ".join(errors[:3])) if errors else None)
