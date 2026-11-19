"""Project service: turns an upload into an indexed, stored project.

Layout on disk (the uploaded originals are never modified after this step):
    DATA_DIR/projects/<project_id>/original/...   extracted source files
    DATA_DIR/tmp/                                 transient upload files (auto-cleaned)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

from ..utils import is_valid_id, utcnow_iso
from .indexer import index_project
from .workspace import UploadError, extract_zip, limits_from_config, write_python_files

ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")  # normal / empty archive


def projects_dir(cfg) -> Path:
    return Path(cfg.DATA_DIR) / "projects"


def project_root(cfg, project_id: str) -> Path:
    if not is_valid_id(project_id):
        raise UploadError("Invalid project id.")
    return projects_dir(cfg) / project_id


def original_dir(cfg, project_id: str) -> Path:
    return project_root(cfg, project_id) / "original"


def cleanup_tmp(cfg, max_age_seconds: int = 3600) -> int:
    """Delete stale temporary upload files. Returns how many were removed."""
    tmp = Path(cfg.DATA_DIR) / "tmp"
    removed = 0
    if not tmp.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    for item in tmp.iterdir():
        try:
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_project(store, cfg, uploads) -> dict:
    """Validate, extract, index and store an upload. Raises UploadError for bad input."""
    if not uploads:
        raise UploadError("No file was uploaded.")
    limits = limits_from_config(cfg)
    names = [(u.filename or "") for u in uploads]
    is_zip = [n.lower().endswith(".zip") for n in names]
    is_py = [n.lower().endswith(".py") for n in names]

    if any(is_zip) and len(uploads) != 1:
        raise UploadError("Upload either one ZIP file or one or more .py files, not a mix.")
    if not any(is_zip) and not all(is_py):
        raise UploadError("Only .zip or .py files are accepted.")

    project_id = uuid.uuid4().hex
    root = project_root(cfg, project_id)
    original = root / "original"
    tmp_dir = Path(cfg.DATA_DIR) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None

    try:
        if any(is_zip):
            fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".zip")
            os.close(fd)
            tmp_path = Path(tmp_name)
            uploads[0].save(tmp_path)
            with open(tmp_path, "rb") as fh:
                if fh.read(4) not in ZIP_MAGIC:  # do not trust the extension
                    raise UploadError("The uploaded file is not a valid ZIP archive.")
            source_sha = _sha256_file(tmp_path)
            extracted = extract_zip(tmp_path, original, limits)
            kind = "zip"
            display_name = Path(secure_filename(names[0]) or "upload.zip").stem
        else:
            items = []
            h = hashlib.sha256()
            for upload in uploads:
                safe = secure_filename(upload.filename or "")
                if not safe.lower().endswith(".py"):
                    raise UploadError(f"Unusable file name: {upload.filename!r}")
                data = upload.stream.read(limits.max_file_bytes + 1)
                items.append((safe, data))
            for safe, data in sorted(items):
                h.update(safe.encode("utf-8") + b"\0" + data + b"\0")
            source_sha = h.hexdigest()
            extracted = write_python_files(items, original, limits)
            kind = "files"
            display_name = Path(items[0][0]).stem if len(items) == 1 else f"{len(items)} Python files"

        index = index_project(original)
        warnings: list[str] = []
        if index["syntax_errors"]:
            warnings.append(f"{len(index['syntax_errors'])} file(s) could not be parsed (syntax or decoding errors).")
        if extracted.skipped_total:
            warnings.append(f"{extracted.skipped_total} file(s) were skipped (unsupported type, ignored folder, or too large).")
        if not index["tests"]["files"]:
            warnings.append("No test files were found. Verification needs tests; test generation is not built yet.")
        missing = [t["name"] for t in index["third_party"] if t["installed_here"] is False]
        if missing:
            warnings.append("Imports not installed in this environment (tests may fail to import): " + ", ".join(missing[:10]))

        totals = index["totals"]
        doc = {
            "_id": project_id,
            "name": display_name,
            "kind": kind,
            "status": "indexed",
            "created_at": utcnow_iso(),
            "source_sha256": source_sha,
            "stripped_root": extracted.stripped_root,
            "python_file_count": totals["python_files"],
            "file_count": totals["all_files"],
            "loc": totals["loc"],
            "function_count": totals["functions"],
            "class_count": totals["classes"],
            "test_file_count": len(index["tests"]["files"]),
            "static_test_count": index["tests"]["static_test_count"],
            "syntax_error_count": len(index["syntax_errors"]),
            "warnings": warnings,
            "skipped": extracted.skipped,
            "skipped_total": extracted.skipped_total,
            "map": index,
        }
        store.insert("projects", doc)
        return doc
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def delete_project(store, cfg, project_id: str) -> bool:
    root = project_root(cfg, project_id)
    existed = store.delete("projects", project_id)
    shutil.rmtree(root, ignore_errors=True)
    # Stored analysis results and AI patch history belong to the project and go with it.
    for coll in ("runs", "findings", "patches"):
        for doc in store.find(coll, {"project_id": project_id}):
            store.delete(coll, doc["_id"])
    return existed


def read_project_file(store, cfg, project_id: str, rel_path: str) -> dict | None:
    """Return a file's text if (and only if) it was extracted for this project."""
    doc = store.get("projects", project_id)
    if not doc or rel_path not in set(doc["map"]["all_files"]):
        return None
    path = original_dir(cfg, project_id) / rel_path
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": rel_path, "content": text, "lines": text.count("\n") + 1}
