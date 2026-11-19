"""Strict parsing, validation and safe application of AI-proposed unified diffs.

The model's diff is treated as untrusted text. Nothing here runs a command or calls ``patch``/``git``:
the diff is parsed, checked against rules, applied in memory with exact context matching, and the *result*
is re-rendered as a canonical diff (what the developer reviews, hashes and stores).

Rejected: malformed diffs, new/deleted/renamed files, paths outside the project (absolute, ``..``,
drive letters, backslashes), files the model was not given, test files, non-Python files, oversized
patches, context that does not match the real file, undecodable or mixed-line-ending files, and any
result that is not valid Python.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


class PatchError(ValueError):
    """The diff is invalid or unsafe. The message is safe to show to the user."""


@dataclass
class Hunk:
    old_start: int
    lines: list[tuple[str, str]]  # (' ' | '-' | '+', text without newline)
    no_eol_old: bool = False
    no_eol_new: bool = False
    header_counts: tuple[int | None, int | None] = (None, None)


@dataclass
class FilePatch:
    path: str
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class Limits:
    max_files: int = 3
    max_changed_lines: int = 200
    max_diff_bytes: int = 64 * 1024


@dataclass
class FileChange:
    path: str
    before: bytes
    after: bytes
    before_sha256: str
    after_sha256: str
    added: int
    removed: int


@dataclass
class PatchPlan:
    changes: list[FileChange]
    diff: str  # canonical unified diff of the real before -> after change
    sha256: str
    added: int
    removed: int
    warnings: list[str]


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FORBIDDEN = ("new file mode", "deleted file mode", "rename from", "rename to", "copy from", "copy to",
              "similarity index", "old mode", "new mode", "binary files", "git binary patch")
_WIN_BAD = set('<>:"|?*')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- paths
def clean_path(raw: str) -> str:
    """Normalise a path from a diff header and reject anything that is not a plain relative project path."""
    p = raw.split("\t")[0].strip().strip('"')
    if p in ("/dev/null", "dev/null"):
        raise PatchError("The patch creates or deletes a file. Only edits to existing files are allowed.")
    if p.startswith(("a/", "b/")):
        p = p[2:]
    if not p or "\x00" in p:
        raise PatchError("The patch contains an empty or invalid file path.")
    if "\\" in p or p.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", p):
        raise PatchError(f"Unsafe path in patch: {p[:80]!r} (absolute, drive or backslash paths are not allowed).")
    parts = PurePosixPath(p).parts
    if ".." in parts:
        raise PatchError(f"Unsafe path in patch: {p[:80]!r} (path traversal).")
    parts = tuple(x for x in parts if x != ".")
    if not parts or any(set(x) & _WIN_BAD for x in parts):
        raise PatchError(f"Unsafe path in patch: {p[:80]!r}.")
    return "/".join(parts)


# ---------------------------------------------------------------- parsing
def strip_fences(text: str) -> str:
    t = text.strip("\n")
    m = re.match(r"^```[a-zA-Z]*\n(.*?)\n?```\s*$", t, re.S)
    return m.group(1) if m else t


def parse_unified_diff(text: str) -> list[FilePatch]:
    if not isinstance(text, str) or not text.strip():
        raise PatchError("The AI response did not include a diff.")
    lines = strip_fences(text.replace("\r\n", "\n")).split("\n")
    files: list[FilePatch] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        low = line.lower()
        if low.startswith(_FORBIDDEN):
            raise PatchError("The patch creates, deletes, renames or changes modes of files, which is not allowed.")
        if line.startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ "):
            old_p, new_p = clean_path(line[4:]), clean_path(lines[i + 1][4:])
            if old_p != new_p:
                raise PatchError("The patch renames a file, which is not allowed.")
            fp = FilePatch(path=new_p)
            i += 2
            while i < n and lines[i].startswith("@@"):
                m = _HUNK_RE.match(lines[i])
                if not m:
                    raise PatchError(f"Malformed hunk header: {lines[i][:60]!r}")
                hunk = Hunk(old_start=int(m.group(1)), lines=[],
                            header_counts=(int(m.group(2)) if m.group(2) is not None else 1, int(m.group(4)) if m.group(4) is not None else 1))
                i += 1
                while i < n:
                    b = lines[i]
                    if b.startswith("@@") or (b.startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ ")) \
                            or b.lower().startswith(("diff --git", "index ")):
                        break
                    if b.startswith("\\"):
                        if not hunk.lines:
                            raise PatchError("Malformed diff: stray 'No newline' marker.")
                        tag = hunk.lines[-1][0]
                        if tag in ("-", " "):
                            hunk.no_eol_old = True
                        if tag in ("+", " "):
                            hunk.no_eol_new = True
                    elif b == "":
                        # A blank line inside a hunk is a context line whose single space got stripped,
                        # but a trailing blank at the very end of the diff is just the final newline.
                        if all(x == "" for x in lines[i:]):
                            break
                        hunk.lines.append((" ", ""))
                    elif b[0] in " +-":
                        hunk.lines.append((b[0], b[1:]))
                    else:
                        raise PatchError(f"Malformed diff line (expected ' ', '+' or '-'): {b[:60]!r}")
                    i += 1
                if not any(t in "+-" for t, _ in hunk.lines):
                    raise PatchError("A hunk in the patch contains no changes.")
                fp.hunks.append(hunk)
            if not fp.hunks:
                raise PatchError(f"The patch for {fp.path} has no hunks.")
            if any(f.path == fp.path for f in files):
                raise PatchError(f"The patch lists {fp.path} more than once.")
            files.append(fp)
            continue
        if line.startswith(("diff --git", "index ", "--- ", "+++ ")) or line.strip() == "":
            i += 1
            continue
        if line.startswith("@@"):
            raise PatchError("Malformed diff: hunk found before any file header.")
        raise PatchError(f"Unexpected text in the diff: {line[:60]!r}")
    if not files:
        raise PatchError("The diff contains no file changes.")
    return files


# ---------------------------------------------------------------- applying
def _decode(path: str, data: bytes) -> tuple[list[str], list[str], bool]:
    """-> (lines without terminators, each line's own terminator, has_bom).

    Line endings are tracked per line, so a file with CRLF, LF or a mix is patched without rewriting the
    endings of lines the patch does not touch. Only undecodable files and stray CR characters are refused.
    """
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data[3:].decode("utf-8") if bom else data.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchError(f"{path} is not valid UTF-8, so it cannot be patched safely.") from None
    if "\r" in text.replace("\r\n", ""):
        raise PatchError(f"{path} contains a stray carriage return (old-Mac line endings); refusing to patch it.")
    lines, ends = [], []
    for raw in text.split("\n"):
        if raw.endswith("\r"):
            lines.append(raw[:-1])
            ends.append("\r\n")
        else:
            lines.append(raw)
            ends.append("\n")
    # text.split leaves one extra element after a trailing newline; that element is not a line
    if text.endswith("\n") or text == "":
        lines.pop()
        ends.pop()
    else:
        ends[-1] = ""  # last line has no terminator
    return lines, ends, bom


def _find(lines: list[str], old: list[str], expected: int, cursor: int, warnings: list[str]) -> int:
    def candidates(norm):
        out = []
        m = len(old)
        for k in range(cursor, len(lines) - m + 1):
            if all(norm(lines[k + j]) == norm(old[j]) for j in range(m)):
                out.append(k)
        return out
    for norm, note in ((lambda s: s, None), (lambda s: s.rstrip(), "context matched ignoring trailing whitespace")):
        found = candidates(norm)
        if found:
            if note:
                warnings.append(note)
            return min(found, key=lambda k: abs(k - expected))
    raise PatchError("The patch's context does not match the current file contents (it may be based on different code).")


def apply_file_patch(fp: FilePatch, data: bytes, warnings: list[str]) -> bytes:
    lines, ends, bom = _decode(fp.path, data)
    crlf = sum(1 for e in ends if e == "\r\n")
    dominant = "\r\n" if crlf > sum(1 for e in ends if e == "\n") else "\n"
    if crlf and any(e == "\n" for e in ends):
        warnings.append("the file has mixed line endings; untouched lines keep theirs, new lines use " + ("CRLF" if dominant == "\r\n" else "LF"))
    final_nl = not ends or ends[-1] != ""
    cursor, shift = 0, 0
    for h in fp.hunks:
        old = [t for tag, t in h.lines if tag in " -"]
        if not any(tag == " " for tag, _ in h.lines) and not any(tag == "-" for tag, _ in h.lines):
            raise PatchError("A hunk only adds lines and has no context to anchor it; refusing to guess where it goes.")
        new_count = sum(1 for tag, _ in h.lines if tag in " +")
        if (h.header_counts[0], h.header_counts[1]) != (len(old), new_count):
            warnings.append("hunk header line counts were corrected")
        pos = _find(lines, old, h.old_start - 1 + shift, cursor, warnings)
        new_lines, new_ends, k = [], [], pos
        for tag, text in h.lines:
            if tag == " ":      # unchanged line: keep exactly what the file had (including its own ending)
                new_lines.append(lines[k]); new_ends.append(ends[k]); k += 1
            elif tag == "-":
                k += 1
            else:               # added line
                new_lines.append(text); new_ends.append(dominant)
        lines[pos:pos + len(old)] = new_lines
        ends[pos:pos + len(old)] = new_ends
        shift += len(new_lines) - len(old)
        cursor = pos + len(new_lines)
        if pos + len(new_lines) >= len(lines):  # hunk touches the end of the file
            if h.no_eol_new:
                final_nl = False
            elif h.lines[-1][0] == "+" or h.no_eol_old:  # lines were appended, so the file now ends with a newline
                final_nl = True
    for i in range(len(ends) - 1):  # every line except the last needs a terminator
        ends[i] = ends[i] or dominant
    if lines:
        ends[-1] = (ends[-1] or dominant) if final_nl else ""
    out = "".join(t + e for t, e in zip(lines, ends, strict=True)).encode("utf-8")
    return (b"\xef\xbb\xbf" + out) if bom else out


def canonical_diff(path: str, before: bytes, after: bytes) -> tuple[str, int, int]:
    def view(b: bytes) -> list[str]:
        return b.decode("utf-8-sig").replace("\r\n", "\n").splitlines()
    a, b = view(before), view(after)
    diff = list(difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", n=3, lineterm=""))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    return "\n".join(diff) + "\n", added, removed


def plan_patch(diff_text: str, current_files: dict[str, bytes], allowed: set[str], test_files: set[str], limits: Limits) -> PatchPlan:
    """Parse + validate + apply in memory. Raises PatchError; never touches the disk."""
    if len(diff_text.encode("utf-8", "replace")) > limits.max_diff_bytes:
        raise PatchError(f"The patch is larger than the {limits.max_diff_bytes // 1024} KB limit.")
    patches = parse_unified_diff(diff_text)
    if len(patches) > limits.max_files:
        raise PatchError(f"The patch changes {len(patches)} files; the limit is {limits.max_files}.")
    warnings: list[str] = []
    changes: list[FileChange] = []
    chunks: list[str] = []
    for fp in patches:
        if fp.path in test_files:
            raise PatchError(f"The patch edits a test file ({fp.path}). Fixes must change the code under test, not the tests.")
        if not fp.path.endswith(".py"):
            raise PatchError(f"The patch edits {fp.path}, which is not a Python file.")
        if fp.path not in allowed:
            raise PatchError(f"The patch edits {fp.path}, which is unrelated to this finding (not among the files provided to the model).")
        if fp.path not in current_files:
            raise PatchError(f"The patch edits {fp.path}, which does not exist in the project.")
        before = current_files[fp.path]
        after = apply_file_patch(fp, before, warnings)
        if after == before:
            raise PatchError(f"The patch makes no change to {fp.path}.")
        try:
            ast.parse(after.decode("utf-8-sig"), filename=fp.path)
        except SyntaxError as exc:
            raise PatchError(f"Applying the patch would leave {fp.path} with a syntax error (line {exc.lineno}).") from None
        diff, added, removed = canonical_diff(fp.path, before, after)
        changes.append(FileChange(fp.path, before, after, sha256_bytes(before), sha256_bytes(after), added, removed))
        chunks.append(diff)
    added, removed = sum(c.added for c in changes), sum(c.removed for c in changes)
    if added + removed > limits.max_changed_lines:
        raise PatchError(f"The patch changes {added + removed} lines; the limit is {limits.max_changed_lines}.")
    full = "".join(chunks)
    return PatchPlan(changes, full, sha256_bytes(full.encode("utf-8")), added, removed, sorted(set(warnings)))
