"""Secret detection before any source is sent to an external AI service.

Pure text scanning. The matched secret value is NEVER returned or logged: findings carry the location and the kind of
secret only (no preview, not even a prefix). If anything is found the caller must refuse to send the text.

A line may carry '# autopatch: allow-secret' to say "this is a harmless test value"; there is no global bypass.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_BYTES = 1_000_000

_PATTERNS = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[0-9a-zA-Z]{16,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("Database URL with credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@\"'<>]+:[^\s:/@\"'<>]+@[^\s\"'<>]+", re.I)),
]
_ASSIGN = re.compile(r"""(?ix)\b([\w.\-]*(?:password|passwd|secret|token|api[_\-]?key|private[_\-]?key|access[_\-]?key|auth)[\w.\-]*)\s*[:=]\s*(['"])([^'"\n]{8,})\2""")
_PLACEHOLDER = re.compile(r"(?i)^(your|<|\$\{|\{\{|changeme|example|placeholder|xxx+|\*+|todo|none|null|test|dummy|sample|password|secret|token)")
_ALLOW = re.compile(r"#\s*autopatch:\s*allow-secret", re.I)
_SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock"}


@dataclass
class SecretFinding:
    file: str
    line: int
    kind: str

    def to_dict(self) -> dict:
        return asdict(self)


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def scan_text(text: str, file: str = "") -> list[SecretFinding]:
    out: list[SecretFinding] = []
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > 5000:
            line = line[:5000]
        if _ALLOW.search(line):
            continue
        hit = False
        for kind, rx in _PATTERNS:
            m = rx.search(line)
            if m:
                out.append(SecretFinding(file, no, kind))
                hit = True
                break
        if hit:
            continue
        m = _ASSIGN.search(line)
        if m:
            value = m.group(3)
            if not _PLACEHOLDER.match(value) and " " not in value.strip() and _entropy(value) >= 3.0:
                out.append(SecretFinding(file, no, "Hard-coded credential"))
    return out


def scan_files(root: Path, files: list[str]) -> list[SecretFinding]:
    root = Path(root).resolve()
    found: list[SecretFinding] = []
    for rel in files:
        p = (root / rel)
        if Path(rel).name.lower() in _SKIP_NAMES or ".." in Path(rel).parts:
            continue
        name = Path(rel).name.lower()
        if name == ".env" or name.startswith(".env."):
            if name != ".env.example":
                found.append(SecretFinding(rel, 0, "Environment file"))
                continue
        try:
            if not p.resolve().is_relative_to(root) or p.is_symlink() or not p.is_file():
                continue
            with open(p, "rb") as fh:
                data = fh.read(MAX_BYTES)
        except OSError:
            continue
        found.extend(scan_text(data.decode("utf-8", errors="replace"), rel))
    return found
