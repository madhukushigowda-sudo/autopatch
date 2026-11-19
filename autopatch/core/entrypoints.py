"""Run a project's script entry points (restricted local prototype; see sandbox.py).

An entry point is run once, with no arguments and a closed stdin, under a hard timeout.
Only an *unhandled exception traceback* becomes a finding. Everything else is recorded as
information, because a script that waits for input, needs arguments or runs a server
for a long time is not necessarily buggy:

    ok | exception | timeout | needs_input | needs_arguments | exit_nonzero | not_run
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePosixPath

from .sandbox import build_env, run_process

_FRAME = re.compile(r'^\s*File "(?P<file>.+)", line (?P<line>\d+)(?:, in (?P<func>.+))?$')
_SKIP_NAMES = {"setup.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py"}
_SCRIPT_NAMES = {"main.py", "__main__.py"}  # run top-level code even without an `if __name__` guard


def pick_entry_points(index: dict, limit: int) -> list[str]:
    """Scripts that are reasonable to run: have a __main__ guard, or are main.py/__main__.py."""
    chosen = []
    by_path = {f["path"]: f for f in index["files"]}
    for rel in index["entry_points"]:
        f = by_path.get(rel)
        name = PurePosixPath(rel).name
        if f is None or f["is_test"] or f["error"] or name in _SKIP_NAMES:
            continue
        if f["has_main"] or name in _SCRIPT_NAMES:
            chosen.append(rel)
    return chosen[: max(0, limit)]


def parse_traceback(stderr: str, workdir: Path) -> dict | None:
    """Parse the LAST Python traceback in ``stderr`` (the exception that finally escaped)."""
    lines = stderr.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last):"):
            start = i
    if start is None:
        # A SyntaxError in an imported module can print `File "...", line N` without a header.
        frames_at = [i for i, line in enumerate(lines) if _FRAME.match(line)]
        if not frames_at:
            return None
        start = frames_at[0] - 1
    base = os.path.abspath(workdir)
    frames, exc_at = [], None
    i = start + 1
    while i < len(lines):
        m = _FRAME.match(lines[i])
        if m:
            code = ""
            if i + 1 < len(lines) and lines[i + 1].startswith("    ") and not _FRAME.match(lines[i + 1]):
                code = lines[i + 1].strip()
            file = m.group("file")
            absolute = os.path.abspath(file if os.path.isabs(file) else os.path.join(base, file))
            rel = None
            try:
                rel = Path(absolute).relative_to(base).as_posix()
            except ValueError:
                pass
            frames.append({"file": file, "rel": rel, "line": int(m.group("line")), "func": m.group("func"), "code": code})
        elif lines[i] and not lines[i].startswith((" ", "\t")):
            exc_at = i
            break
        i += 1
    if exc_at is None:
        return None
    head = lines[exc_at]
    etype, _, msg = head.partition(":")
    if not re.fullmatch(r"[A-Za-z_][\w.]*", etype.strip()):
        return None
    message = (msg.strip() + "\n" + "\n".join(lines[exc_at + 1:])).strip()
    return {"type": etype.strip().rsplit(".", 1)[-1], "message": message[:1000], "frames": frames,
            "text": "\n".join(lines[start:])[-6000:]}


def classify(proc, tb: dict | None) -> str:
    if proc.error:
        return "not_run"
    if proc.timed_out:
        return "timeout"
    if proc.returncode == 0:
        return "ok"
    if tb is not None:
        if tb["type"] == "EOFError" and "EOF when reading a line" in tb["message"]:
            return "needs_input"
        return "exception"
    if proc.returncode == 2 and "usage:" in (proc.stderr or "").lower():
        return "needs_arguments"
    return "exit_nonzero"


def run_entry_point(workdir: Path, rel: str, run_dir: Path, timeout: float, max_output: int) -> dict:
    workdir = Path(workdir)
    extra_path = os.pathsep.join(
        [str(workdir)] + ([str(workdir / "src")] if (workdir / "src").is_dir() else [])
    )
    env = build_env(Path(run_dir) / "tmp", {"PYTHONPATH": extra_path})
    proc = run_process([sys.executable, "-X", "utf8", rel], cwd=workdir, timeout=timeout, env=env, max_output=max_output)
    tb = parse_traceback(proc.stderr, workdir) if proc.stderr else None
    status = classify(proc, tb)
    return {
        "file": rel, "status": status, "exit_code": proc.returncode, "duration_s": proc.duration,
        "timed_out": proc.timed_out, "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:],
        "output_truncated": proc.stdout_truncated or proc.stderr_truncated,
        "traceback": tb if status == "exception" else None,
        "note": proc.error,
    }
