"""Windows (and general) environment diagnostics.

Finds out WHICH component is blocked or missing and tells the person what to ask their administrator for.
It never tries to get around a block: no policy changes, no copying DLLs elsewhere, no unblocking, no elevation.
Every import is tried in a separate Python process, so a blocked DLL cannot take the checker down with it.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows error numbers that mean "policy refused to load or run this file"
POLICY_WINERRORS = {4551: "Application Control (WDAC / Smart App Control)", 1260: "Group Policy / AppLocker software restriction"}
_POLICY_TEXT = re.compile(r"application control|blocked this file|blocked by group policy|applocker|software restriction|"
                          r"windows defender application control|smart app control|winerror 4551|winerror 1260|"
                          r"the system cannot execute the specified program|has been blocked by your organi[sz]ation", re.I)
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\r\n\"'<>|?*]*?\.(?:pyd|dll|exe)", re.I)
_DLL_LOAD = re.compile(r"DLL load failed while importing ([\w.]+)", re.I)

# (label, importable module, what it is used for, is it required?)
COMPONENTS = [
    ("Flask", "flask", "web server", True),
    ("Werkzeug", "werkzeug", "web server", True),
    ("Jinja2", "jinja2", "page templates", True),
    ("pytest", "pytest", "running a project's tests", True),
    ("Ruff", "ruff", "static analysis (or the ruff executable)", False),
    ("Bandit", "bandit", "security scan (or the bandit executable)", False),
    ("pymongo", "pymongo", "MongoDB storage (only if STORAGE_BACKEND=mongo)", False),
    ("Pydantic Core", "pydantic_core", "used by the optional google-genai SDK only", False),
    ("google-genai SDK", "google.genai", "optional: AutoPatch has its own HTTPS client", False),
]

GUIDANCE_BLOCKED = (
    "Windows policy blocked this file. AutoPatch does not try to get around that. Ask your administrator to allow the file shown "
    "(by path or publisher), or use an alternative that is not blocked. For Gemini, GEMINI_TRANSPORT=rest uses a built-in client "
    "that needs neither google-genai nor Pydantic Core."
)


def classify_failure(text: str = "", winerror: int | None = None) -> dict:
    """Decide from an error message / WinError whether a file was blocked by policy, and find the path if the message names one."""
    text = text or ""
    policy = winerror in POLICY_WINERRORS or bool(_POLICY_TEXT.search(text))
    m = _PATH_RE.search(text)
    dll = _DLL_LOAD.search(text)
    return {
        "blocked": policy,
        "winerror": winerror if winerror in POLICY_WINERRORS else (4551 if "4551" in text and policy else None),
        "mechanism": POLICY_WINERRORS.get(winerror) if winerror in POLICY_WINERRORS else ("Windows Application Control" if policy else None),
        "file": m.group(0) if m else None,
        "module": dll.group(1) if dll else None,
    }


def blocked_start_message(exc: OSError) -> str:
    """User-facing text for a process that Windows refused to start. Names the type of block, never any path or argument."""
    info = classify_failure(str(exc), getattr(exc, "winerror", None))
    if info["blocked"]:
        return (f"Could not start the process: Windows blocked it ({info['mechanism'] or 'policy'}, WinError {info['winerror'] or 'n/a'}). "
                "AutoPatch does not bypass this. Ask your administrator to allow the tool, then run scripts\\windows_check.py for details.")
    return f"Could not start process ({type(exc).__name__})"


def _clean_env() -> dict:
    keep = ("SYSTEMROOT", "PATH", "PATHEXT", "COMSPEC", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "VIRTUAL_ENV")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def _run(argv: list[str], timeout: float = 30.0) -> tuple[int | None, str, int | None]:
    """(returncode, combined output, winerror). returncode None = could not start."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=_clean_env(), cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), None
    except subprocess.TimeoutExpired:
        return None, "timed out", None
    except OSError as exc:
        return None, str(exc), getattr(exc, "winerror", None)


def _short(text: str, limit: int = 300) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-3:])[:limit]


def check_import(module: str, python: str | None = None) -> dict:
    """Import ``module`` in a fresh interpreter (-I: ignores the current directory and user site)."""
    code, out, winerr = _run([python or sys.executable, "-I", "-c", f"import {module}"])
    if code == 0:
        return {"status": "OK", "detail": "", "file": None}
    info = classify_failure(out, winerr)
    if info["blocked"] or (code is not None and _DLL_LOAD.search(out) and "blocked" in out.lower()):
        return {"status": "BLOCKED", "detail": _short(out), "file": info["file"], "winerror": info["winerror"]}
    if re.search(r"ModuleNotFoundError|No module named", out):
        return {"status": "MISSING", "detail": f"not installed in this Python ({sys.executable})", "file": None}
    if code is None:
        return {"status": "ERROR", "detail": _short(out), "file": None}
    return {"status": "ERROR", "detail": _short(out), "file": info["file"]}


def check_launch(argv: list[str]) -> dict:
    code, out, winerr = _run(argv)
    if code == 0:
        return {"status": "OK", "detail": _short(out, 80), "file": None}
    info = classify_failure(out, winerr)
    if info["blocked"]:
        return {"status": "BLOCKED", "detail": _short(out), "file": info["file"], "winerror": info["winerror"]}
    gone = code is None and (winerr in (2, 3) or bool(re.search(r"no such file|cannot find|not found|errno 2", out, re.I)))
    return {"status": "MISSING" if gone else "ERROR", "detail": _short(out) or "could not start", "file": None}


def check_data_dir(path: Path) -> dict:
    try:
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".autopatch_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        if not existed:
            path.rmdir()  # leave nothing behind
        return {"status": "OK", "detail": str(path), "file": None}
    except OSError as exc:
        return {"status": "ERROR", "detail": f"cannot write here ({type(exc).__name__}); set DATA_DIR in .env to a folder you can write to", "file": None}


def check_bind(host: str = "127.0.0.1") -> dict:
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
        return {"status": "OK", "detail": "", "file": None}
    except OSError as exc:
        return {"status": "MISSING" if host == "::1" else "ERROR", "detail": f"cannot listen on {host} ({type(exc).__name__})", "file": None}


def run_all(root: Path, *, network: bool = False) -> dict:
    from .gemini_rest import RestGeminiClient  # noqa: F401 (import check of our own client)
    from .keycheck import check_key
    results = []
    for label, module, purpose, required in COMPONENTS:
        r = check_import(module)
        if module in ("ruff", "bandit") and r["status"] != "OK":
            exe = check_launch([module, "--version"])  # an installed executable also works
            if exe["status"] == "OK":
                r = {"status": "OK", "detail": f"as the {module} executable", "file": None}
            elif exe["status"] == "BLOCKED":
                r = exe
        results.append({"name": label, "module": module, "purpose": purpose, "required": required, **r})
    env_file = Path(root) / ".env"
    key_text = ""
    if env_file.is_file():
        for ln in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.strip().startswith("GEMINI_API_KEY"):
                key_text = ln.partition("=")[2].strip()
    key = check_key(key_text if key_text else os.environ.get("GEMINI_API_KEY", ""))
    others = {
        "Python": {"status": "OK", "detail": sys.version.split()[0] + " " + sys.executable, "file": None},
        "Writable data folder": check_data_dir(_data_dir(root)),
        "Listen on 127.0.0.1": check_bind("127.0.0.1"),
        "Listen on ::1 (IPv6)": check_bind("::1"),
        ".env file": {"status": "OK" if env_file.is_file() else "MISSING",
                      "detail": "" if env_file.is_file() else "copy .env.example to .env (optional unless you use Gemini)", "file": None},
        "Gemini key shape": {"status": "OK" if key["ok"] else "ERROR", "detail": key["message"] or key["note"] or "", "file": None},
    }
    if network:
        others["Gemini reachable"] = _gemini_ping(root)
    return {"components": results, "environment": others}


def _data_dir(root: Path) -> Path:
    try:
        from config import Config
        return Path(Config().DATA_DIR)
    except Exception:  # a bad .env must not stop the diagnostics
        return Path(root) / "data"


def _gemini_ping(root: Path) -> dict:
    from config import Config
    from .gemini_client import AIError
    from .gemini_rest import RestGeminiClient
    from .keycheck import apply_to_config
    cfg = Config()
    if not apply_to_config(cfg)["ok"]:
        return {"status": "ERROR", "detail": cfg.GEMINI_KEY_MESSAGE, "file": None}
    try:
        RestGeminiClient(cfg).generate("Connectivity check. Reply only with JSON.", 'Reply with {"ok": true}')
        return {"status": "OK", "detail": "one tiny request; no project code sent", "file": None}
    except AIError as exc:
        return {"status": "ERROR", "detail": f"{exc.kind}: {exc}"[:300], "file": None}


def verdict(report: dict) -> int:
    """Exit code: 1 if something REQUIRED is blocked/missing, else 0."""
    for c in report["components"]:
        if c["required"] and c["status"] != "OK":
            return 1
    return 0


def format_report(report: dict) -> str:
    lines = ["AutoPatch environment check", "=" * 27, ""]
    blocked = []
    for c in report["components"]:
        tag = {"OK": "[ OK      ]", "BLOCKED": "[ BLOCKED ]", "MISSING": "[ MISSING ]", "ERROR": "[ ERROR   ]"}[c["status"]]
        need = "required" if c["required"] else "optional"
        lines.append(f"{tag} {c['name']:<18} {need:<9} {c['purpose']}")
        if c["status"] != "OK" and c["detail"]:
            lines.append(f"             {c['detail']}")
        if c["status"] == "BLOCKED":
            blocked.append(c)
            if c.get("file"):
                lines.append(f"             blocked file: {c['file']}")
    lines += ["", "Environment", "-" * 11]
    for name, r in report["environment"].items():
        tag = {"OK": "[ OK      ]", "MISSING": "[ MISSING ]", "ERROR": "[ ERROR   ]", "BLOCKED": "[ BLOCKED ]"}[r["status"]]
        lines.append(f"{tag} {name:<22} {r['detail']}")
    if blocked:
        lines += ["", "What to do about BLOCKED items", "-" * 30, GUIDANCE_BLOCKED, "",
                  "Send your administrator the file path(s) shown above. AutoPatch cannot and will not change Windows policy."]
    req_bad = [c for c in report["components"] if c["required"] and c["status"] != "OK"]
    lines += ["", "RESULT: " + ("a required component is not usable (see above)" if req_bad else "all required components work")]
    return "\n".join(lines)
