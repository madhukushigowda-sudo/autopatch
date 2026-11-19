"""AutoPatch stage check: one command, read-only with respect to your data.

    py -3.13 scripts\\check.py            run every check
    py -3.13 scripts\\check.py --only pytest -k gemini     targeted test run
    py -3.13 scripts\\check.py --write-checksums           (re)write CHECKSUMS.txt

Checks, in order: files-vs-CHECKSUMS.txt, secret/leak scan, ruff, full pytest in a
controlled environment, baseline test-id guard, "no data folder created" guard.

Safety: standard library only. It never reads .env, never touches MongoDB, never
reads your real data folder. Tests run with the config environment variables removed
and DATA_DIR pointing at a throw-away temporary folder.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "data", "node_modules", ".idea", ".vscode"}
CHECKSUM_FILE = "CHECKSUMS.txt"

# The 27 names config.py reads. They are removed from the environment for test runs.
CONFIG_ENV = [
    "ALLOW_EXECUTION", "DATA_DIR", "ENTRYPOINT_TIMEOUT_SECONDS", "GEMINI_API_KEY", "GEMINI_MAX_OUTPUT_TOKENS",
    "GEMINI_MODEL", "GEMINI_RETRIES", "GEMINI_TEMPERATURE", "GEMINI_THINKING_LEVEL", "GEMINI_TIMEOUT_SECONDS", "GEMINI_TRANSPORT", "GEMINI_REST_BASE_URL", "ALLOWED_HOSTS",
    "MAX_ENTRIES", "MAX_ENTRYPOINTS", "MAX_FILES", "MAX_FILE_MB", "MAX_FINDINGS", "MAX_OUTPUT_KB",
    "MAX_UNCOMPRESSED_MB", "MAX_UPLOAD_MB", "MONGODB_DB", "MONGODB_TIMEOUT_MS", "MONGODB_URI",
    "PATCH_MAX_CHANGED_LINES", "PATCH_MAX_CONTEXT_KB", "PATCH_MAX_DIFF_KB", "PATCH_MAX_FILES",
    "PYTEST_TIMEOUT_SECONDS", "SECRET_KEY", "STORAGE_BACKEND", "TOOL_TIMEOUT_SECONDS",
]

LEAK_PATTERNS = [
    ("Google API key shape", re.compile(r"AIza[0-9A-Za-z_\-]{20,}")),
    ("sk- style key", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("private key block", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("MongoDB URI with credentials", re.compile(r"mongodb(?:\+srv)?://[^\s\"'<>]+:[^\s\"'<>@]+@[^\s\"'<>]+")),
    ("API key assignment", re.compile(r"(?:API_KEY|SECRET_KEY)\s*=\s*['\"]?[A-Za-z0-9_\-]{12,}")),
]
# Known synthetic values used by existing tests or placeholders in .env.example.
# Each entry is (relative path, exact matched text). Anything else is reported.
LEAK_ALLOWLIST = {
    ("tests/test_store.py", "mongodb://user:secret@127.0.0.1:1/?x=1"),
    ("tests/test_sandbox.py", "mongodb://u:p@h/db"),
    ("tests/test_api_analysis.py", "mongodb+srv://user:pw-secret@cluster/db"),
}
TEXT_EXT = {".py", ".md", ".txt", ".html", ".css", ".js", ".json", ".ini", ".toml", ".cfg", ".yml", ".yaml", ".example", ".bat", ".ps1", ".sha256", ".gitignore", ""}

results: list[tuple[str, str, str]] = []  # (name, PASS/FAIL/SKIP, detail)


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""))


def project_files() -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.is_symlink():
                continue
            out.append(p)
    return out


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums() -> None:
    lines = [f"{sha256(p)}  {rel(p)}" for p in project_files() if p.name != CHECKSUM_FILE]
    (ROOT / CHECKSUM_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {CHECKSUM_FILE} with {len(lines)} entries")


def check_env_file() -> bool:
    # config.py would load this file during tests. Existence only; contents are never read.
    if (ROOT / ".env").exists():
        record("no .env in this folder", "FAIL", "a .env exists here and tests would load it. Move it away; use a fresh stage folder.")
        return False
    record("no .env in this folder", "PASS")
    return True


def check_checksums() -> None:
    f = ROOT / CHECKSUM_FILE
    if not f.exists():
        record("files match CHECKSUMS.txt", "FAIL", "CHECKSUMS.txt is missing (run with --write-checksums only if you made the change yourself)")
        return
    listed: dict[str, str] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, path = line.partition("  ")
        listed[path] = digest
    actual = {rel(p): p for p in project_files() if p.name != CHECKSUM_FILE}
    missing = sorted(set(listed) - set(actual))
    extra = sorted(set(actual) - set(listed))
    modified = sorted(k for k in set(listed) & set(actual) if sha256(actual[k]) != listed[k])
    if missing or extra or modified:
        detail = f"missing={missing} extra={extra} modified={modified}"
        record("files match CHECKSUMS.txt", "FAIL", detail)
    else:
        record("files match CHECKSUMS.txt", "PASS", f"{len(listed)} files")


def check_leaks() -> None:
    hits = []
    scanned = 0
    for p in project_files():
        if p.name == CHECKSUM_FILE or p.suffix.lower() not in TEXT_EXT or rel(p) == "scripts/check.py":
            continue  # check.py itself contains the detection patterns
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for label, rx in LEAK_PATTERNS:
            for m in rx.finditer(text):
                if (rel(p), m.group(0)) in LEAK_ALLOWLIST:
                    continue
                if rel(p) == ".env.example":
                    continue  # placeholders only; the file is documented as such
                if rel(p).startswith("tests/") and re.search(r"AIza-[a-z\-]*(secret|key|test)", m.group(0)):
                    continue  # existing synthetic test keys (must never leak) used by Gemini tests
                hits.append(f"{rel(p)}: {label}")
    if hits:
        record("secret and leak scan", "FAIL", "; ".join(sorted(set(hits))))
    else:
        record("secret and leak scan", "PASS", f"{scanned} text files")


def check_ruff() -> None:
    exe = shutil.which("ruff")
    cmd = [exe] if exe else [sys.executable, "-m", "ruff"]
    try:
        v = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        record("ruff", "SKIP", "ruff not installed")
        return
    if v.returncode != 0:
        record("ruff", "SKIP", "ruff not installed")
        return
    r = subprocess.run(
        cmd + ["check", "--isolated", "--no-cache", "--select", "E,F,W,B", "--ignore", "E501",
               "--per-file-ignores", "autopatch/core/patch_diff.py:E702",
               "--exclude", "tests/fixtures",  # the detection corpus contains deliberately buggy code
               str(ROOT / "autopatch"), str(ROOT / "tests"), str(ROOT / "scripts"), str(ROOT / "app.py")],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    if r.returncode == 0:
        record("ruff (E,F,W,B)", "PASS", "no findings; known E702 in patch_diff.py exempted")
    else:
        record("ruff (E,F,W,B)", "FAIL", (r.stdout + r.stderr).strip()[-1500:])


def clean_env(data_dir: str) -> dict[str, str]:
    env = dict(os.environ)
    for k in CONFIG_ENV:
        env.pop(k, None)
    env["DATA_DIR"] = data_dir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def parse_junit(path: Path) -> dict[str, str]:
    out = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        tid = f"{tc.get('classname')}::{tc.get('name')}"
        if tc.find("failure") is not None or tc.find("error") is not None:
            out[tid] = "failed"
        elif tc.find("skipped") is not None:
            out[tid] = "skipped"
        else:
            out[tid] = "passed"
    return out


def compare_baseline(baseline_text: str, outcomes: dict[str, str]) -> tuple[list[str], int]:
    """Check that no baseline test disappeared, failed or silently stopped running.

    Tests are grouped by function (the id up to the first "["). A function must still have at least as many
    cases as in the baseline, none may fail, and only functions marked passed-or-skipped may be skipped.
    Exact parameter text is not compared: pytest spells some long parametrized ids differently on Windows
    (seen on a real Windows run), and that must not look like a missing test.
    """
    base: dict[str, dict] = {}
    n = 0
    for line in baseline_text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tid, _, need = line.rpartition("\t")
        n += 1
        entry = base.setdefault(tid.split("[", 1)[0], {"count": 0, "skip_ok": False})
        entry["count"] += 1
        entry["skip_ok"] = entry["skip_ok"] or need == "passed-or-skipped"
    now: dict[str, list[str]] = {}
    for tid, outcome in outcomes.items():
        now.setdefault(tid.split("[", 1)[0], []).append(outcome)
    bad = []
    for func, info in sorted(base.items()):
        got = now.get(func, [])
        if not got:
            bad.append(f"{func}: missing")
            continue
        if len(got) < info["count"]:
            bad.append(f"{func}: {len(got)} case(s) now, {info['count']} in the baseline")
        if "failed" in got:
            bad.append(f"{func}: {got.count('failed')} failed")
        if "skipped" in got and not info["skip_ok"]:
            bad.append(f"{func}: skipped")
    return bad, n


def run_pytest(extra: list[str], full: bool) -> None:
    had_data = (ROOT / "data").exists()
    with tempfile.TemporaryDirectory(prefix="autopatch-check-") as tmp:
        xml = Path(tmp) / "junit.xml"
        env = clean_env(str(Path(tmp) / "data"))
        env["TMPDIR"] = env["TEMP"] = env["TMP"] = tmp
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--junitxml={xml}", "-rs"] + extra
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
        if not xml.exists():
            record("pytest", "FAIL", "no result file produced:\n" + tail)
            return
        outcomes = parse_junit(xml)
    counts = {k: sum(1 for v in outcomes.values() if v == k) for k in ("passed", "failed", "skipped")}
    summary = f"{len(outcomes)} tests: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped"
    record("pytest (clean environment, temporary data)", "PASS" if r.returncode == 0 and counts["failed"] == 0 else "FAIL",
           summary if r.returncode == 0 else summary + "\n" + tail)
    skipped = sorted(k for k, v in outcomes.items() if v == "skipped")
    if skipped:
        print("      skipped:", ", ".join(skipped))
    if full:
        bad, n = compare_baseline((ROOT / "tests" / "baseline_ids.txt").read_text(encoding="utf-8"), outcomes)
        record("baseline tests still present and passing", "FAIL" if bad else "PASS", "; ".join(bad) if bad else f"{n} baseline tests")
    if not had_data and (ROOT / "data").exists():
        record("no data folder created in project", "FAIL", "a data folder appeared; tests wrote outside the temporary DATA_DIR")
    else:
        record("no data folder created in project", "PASS")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["checksums", "leaks", "ruff", "pytest"], help="run a single check")
    ap.add_argument("--write-checksums", action="store_true", help="write CHECKSUMS.txt and exit")
    ap.add_argument("-k", dest="keyword", help="pytest -k expression (use with --only pytest)")
    a = ap.parse_args()
    if a.write_checksums:
        write_checksums()
        return 0
    print(f"AutoPatch check in {ROOT}\nPython {sys.version.split()[0]}\n")
    ok_env = check_env_file()
    if a.only in (None, "checksums"):
        check_checksums()
    if a.only in (None, "leaks"):
        check_leaks()
    if a.only in (None, "ruff"):
        check_ruff()
    if a.only in (None, "pytest") and ok_env:
        run_pytest(["-k", a.keyword] if a.keyword else [], full=a.only is None)
    failed = [r for r in results if r[1] == "FAIL"]
    print("\n" + ("RESULT: PASS" if not failed else f"RESULT: FAIL ({len(failed)} failed check(s))"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
