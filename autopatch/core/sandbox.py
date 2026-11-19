"""Restricted local process execution.

THIS IS NOT A HARDENED SECURITY SANDBOX. It reduces accidents; it does not stop
hostile code. What it does:

* runs code in a child process, never in the Flask process
* hard timeout, then kills the whole process tree
* captures stdout/stderr with a size cap (so output floods cannot fill memory)
* passes an allow-list environment: no API keys, database URIs or other secrets
* points HOME/TEMP at a per-run scratch folder that is deleted afterwards

What it does NOT do: it does not restrict file-system access, network access,
memory, CPU or process creation. Project code can read or write anything the
current user can. Only analyse projects you are willing to run on this machine.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Only these variables are copied from the server's environment (Windows needs most of them).
ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
)
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "URI", "DSN")


@dataclass
class ProcResult:
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration: float = 0.0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None  # the process could not be started at all


class _Capture:
    """Keeps the first ``cap`` bytes; keeps draining the pipe so the child never blocks."""

    def __init__(self, cap: int):
        self.cap, self.size, self.truncated = cap, 0, False
        self.chunks: list[bytes] = []

    def feed(self, data: bytes) -> None:
        room = self.cap - self.size
        if room > 0:
            part = data[:room]
            self.chunks.append(part)
            self.size += len(part)
            if len(part) < len(data):
                self.truncated = True
        else:
            self.truncated = True

    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")


def _drain(stream, capture: _Capture) -> None:
    try:
        reader = getattr(stream, "read1", stream.read)
        while True:
            data = reader(8192)
            if not data:
                break
            capture.feed(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def build_env(scratch_dir: Path, extra: dict | None = None) -> dict:
    """Allow-list environment for child processes. Never includes the server's secrets."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    for key in list(env):  # belt and braces: an allow-listed name that looks like a secret is dropped
        if any(h in key.upper() for h in _SECRET_HINTS):
            del env[key]
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch = str(scratch_dir)
    env.update(
        {
            "TEMP": scratch, "TMP": scratch, "TMPDIR": scratch, "HOME": scratch, "USERPROFILE": scratch,
            "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1", "PYTHONHASHSEED": "0", "NO_COLOR": "1",
        }
    )
    if extra:
        env.update(extra)
    return env


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it started (best effort)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)  # the child leads its own session/group
    except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_process(cmd: list[str], cwd, timeout: float, env: dict, max_output: int = 200_000) -> ProcResult:
    """Run ``cmd`` with a hard timeout. Never raises for child failures."""
    kwargs: dict = {"cwd": str(cwd), "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True

    started = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - fixed argv list, no shell
    except OSError as exc:
        from .diagnostics import blocked_start_message  # a Windows Application Control block gets a specific, honest message
        return ProcResult(error=blocked_start_message(exc), duration=0.0)

    out, err = _Capture(max_output), _Capture(max_output)
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True),
    ]
    for t in threads:
        t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        log.warning("Process exceeded %.1fs timeout; killing process tree (pid %s)", timeout, proc.pid)
        kill_process_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - extremely unlikely after SIGKILL
            pass
    else:
        # Normal exit: still clean up stragglers (e.g. background children a test left running).
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    for t in threads:
        t.join(timeout=3)
    return ProcResult(
        returncode=proc.returncode,
        stdout=out.text(), stderr=err.text(),
        timed_out=timed_out, duration=round(time.monotonic() - started, 3),
        stdout_truncated=out.truncated, stderr_truncated=err.truncated,
    )
