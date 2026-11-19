"""Safe handling of uploaded files.

Nothing here imports or executes uploaded code. We only copy bytes into a
fresh directory after rejecting dangerous archives:

* path traversal ("../x", absolute paths, drive letters, backslash tricks)
* symbolic links and encrypted entries
* duplicate / case-colliding names (Windows treats A.py and a.py as one file)
* Windows-reserved names and illegal characters
* too many entries/files, oversized files, oversized total (ZIP bombs)
"""
from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


class UploadError(ValueError):
    """The upload is invalid or unsafe. The message is safe to show to the user."""


@dataclass(frozen=True)
class Limits:
    max_entries: int = 5000
    max_files: int = 500
    max_uncompressed_bytes: int = 50 * 1024 * 1024
    max_file_bytes: int = 2 * 1024 * 1024


def limits_from_config(cfg) -> Limits:
    return Limits(
        max_entries=int(cfg.MAX_ENTRIES),
        max_files=int(cfg.MAX_FILES),
        max_uncompressed_bytes=int(cfg.MAX_UNCOMPRESSED_MB * 1024 * 1024),
        max_file_bytes=int(cfg.MAX_FILE_MB * 1024 * 1024),
    )


# Only text/source files are kept; binaries (.pyc, .so, .exe, images...) are dropped.
ALLOWED_EXTENSIONS = {".py", ".txt", ".md", ".rst", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml", ".csv"}
IGNORED_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "node_modules", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode", "site-packages", "__macosx",
}
IGNORED_FILES = {".ds_store", "thumbs.db"}
_BAD_CHARS = set('<>:"|?*')
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_MAX_REPORTED_SKIPS = 200


@dataclass
class ExtractResult:
    files: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    skipped_total: int = 0
    stripped_root: str | None = None
    total_bytes: int = 0


def _entry_parts(name: str) -> list[str]:
    """Validate one ZIP entry name and return its path components."""
    if any(ord(c) < 32 for c in name):
        raise UploadError(f"Unsafe entry name rejected: {name!r}")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise UploadError(f"Absolute path in ZIP rejected: {name!r}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UploadError(f"Path traversal in ZIP rejected: {name!r}")
    for part in parts:
        if any(c in _BAD_CHARS for c in part):
            raise UploadError(f"Illegal characters in path rejected: {name!r}")
        if part.split(".")[0].lower() in _WINDOWS_RESERVED:
            raise UploadError(f"Reserved name in path rejected: {name!r}")
        if part.endswith((" ", ".")):
            raise UploadError(f"Invalid path component rejected: {name!r}")
    return parts


def _skip(result: ExtractResult, parts: list[str], reason: str) -> None:
    result.skipped_total += 1
    if len(result.skipped) < _MAX_REPORTED_SKIPS:
        result.skipped.append({"path": "/".join(parts), "reason": reason})


def extract_zip(zip_path, dest, limits: Limits) -> ExtractResult:
    """Extract an uploaded ZIP into ``dest`` (created if needed). Raises UploadError."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    result = ExtractResult()

    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UploadError("The uploaded file is not a valid ZIP archive.") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise UploadError(f"The archive has too many entries ({len(infos)}); the limit is {limits.max_entries}.")

        # 1) Validate every entry first. Attack indicators reject the whole upload.
        entries: list[tuple[zipfile.ZipInfo, list[str]]] = []
        for info in infos:
            parts = _entry_parts(info.filename)
            if info.flag_bits & 0x1:
                raise UploadError("Encrypted ZIP entries are not supported.")
            if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise UploadError(f"Symbolic links in ZIP are not allowed: {info.filename!r}")
            if info.is_dir() or not parts:
                continue
            entries.append((info, parts))

        # 2) Drop ignored folders/files, then strip one shared top-level folder.
        kept = []
        for info, parts in entries:
            if any(p.lower() in IGNORED_DIRS for p in parts[:-1]) or parts[-1].lower() in IGNORED_FILES:
                _skip(result, parts, "ignored folder/file")
            else:
                kept.append((info, parts))
        # (Not when that folder is itself a package: stripping it would break "import pkg.x".)
        if kept and all(len(p) >= 2 for _, p in kept):
            roots = {p[0] for _, p in kept}
            if len(roots) == 1:
                root_name = next(iter(roots))
                is_package = any(p == [root_name, "__init__.py"] for _, p in kept)
                if not is_package:
                    result.stripped_root = root_name
                    kept = [(i, p[1:]) for i, p in kept]

        # 3) Keep only allowed, reasonably sized text/source files.
        selected = []
        for info, parts in kept:
            if PurePosixPath(parts[-1]).suffix.lower() not in ALLOWED_EXTENSIONS:
                _skip(result, parts, "unsupported file type")
            elif info.file_size > limits.max_file_bytes:
                _skip(result, parts, f"file larger than {limits.max_file_bytes // 1024} KB limit")
            else:
                selected.append((info, parts))

        if len(selected) > limits.max_files:
            raise UploadError(f"Too many files ({len(selected)}); the limit is {limits.max_files}.")
        if sum(i.file_size for i, _ in selected) > limits.max_uncompressed_bytes:
            raise UploadError("The archive expands beyond the allowed total size.")

        seen: set[str] = set()
        for _, parts in selected:
            key = "/".join(parts).lower()
            if key in seen:
                raise UploadError(f"Duplicate or case-colliding entry rejected: {'/'.join(parts)!r}")
            seen.add(key)

        # 4) Copy bytes with running size checks (defence in depth against lying headers).
        total = 0
        try:
            for info, parts in selected:
                target = dest.joinpath(*parts).resolve()
                if dest_resolved not in target.parents:
                    raise UploadError("An entry would be written outside the workspace.")
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with archive.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if size > limits.max_file_bytes or total > limits.max_uncompressed_bytes:
                            raise UploadError("The archive expands beyond the allowed size (possible ZIP bomb).")
                        out.write(chunk)
                result.files.append("/".join(parts))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
            raise UploadError("The archive is corrupt or uses an unsupported feature.") from exc

    result.files.sort()
    result.total_bytes = total
    if not any(f.lower().endswith(".py") for f in result.files):
        raise UploadError("No Python files were found. AutoPatch supports Python projects only in this prototype.")
    return result


def write_python_files(items: list[tuple[str, bytes]], dest, limits: Limits) -> ExtractResult:
    """Store individually uploaded .py files (names already sanitised by the caller)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if len(items) > limits.max_files:
        raise UploadError(f"Too many files ({len(items)}); the limit is {limits.max_files}.")
    result = ExtractResult()
    seen: set[str] = set()
    total = 0
    for name, data in items:
        if not name.lower().endswith(".py"):
            raise UploadError(f"Only .py files can be uploaded individually: {name!r}")
        if name.lower() in seen:
            raise UploadError(f"Two uploaded files have the same name: {name!r}")
        seen.add(name.lower())
        if len(data) > limits.max_file_bytes:
            raise UploadError(f"{name!r} is larger than the {limits.max_file_bytes // 1024} KB per-file limit.")
        total += len(data)
        if total > limits.max_uncompressed_bytes:
            raise UploadError("The uploaded files exceed the allowed total size.")
        (dest / name).write_bytes(data)
        result.files.append(name)
    result.files.sort()
    result.total_bytes = total
    return result
