"""REST endpoints for projects."""
from __future__ import annotations

import sys

from flask import Blueprint, current_app, jsonify, request

from ..core import projects as svc
from ..core.workspace import limits_from_config
from ..utils import is_valid_id

bp = Blueprint("projects", __name__, url_prefix="/api")

VERSION = "0.3.0 (hackathon build)"


def _ctx():
    ext = current_app.extensions["autopatch"]
    return ext["store"], ext["cfg"]


def _summary(doc: dict) -> dict:
    """Everything except the (large) index map."""
    return {k: v for k, v in doc.items() if k != "map"}


@bp.get("/health")
def health():
    store, cfg = _ctx()
    limits = limits_from_config(cfg)
    return jsonify(
        status="ok",
        version=VERSION,
        python=sys.version.split()[0],
        storage=store.describe(),
        supported_language="python",
        limits={
            "max_upload_mb": cfg.MAX_UPLOAD_MB,
            "max_files": limits.max_files,
            "max_file_kb": limits.max_file_bytes // 1024,
            "max_uncompressed_mb": limits.max_uncompressed_bytes // (1024 * 1024),
        },
    )


@bp.post("/projects")
def upload_project():
    store, cfg = _ctx()
    doc = svc.create_project(store, cfg, request.files.getlist("files"))
    return jsonify(_summary(doc)), 201


@bp.get("/projects")
def list_projects():
    store, _ = _ctx()
    docs = store.find("projects", sort=("created_at", -1), limit=25)
    return jsonify(projects=[_summary(d) for d in docs])


def _get_or_404(project_id: str):
    store, _ = _ctx()
    if not is_valid_id(project_id):
        return None
    return store.get("projects", project_id)


@bp.get("/projects/<project_id>")
def get_project(project_id):
    doc = _get_or_404(project_id)
    if not doc:
        return jsonify(error="Project not found."), 404
    return jsonify(doc)


@bp.get("/projects/<project_id>/file")
def get_project_file(project_id):
    store, cfg = _ctx()
    if not is_valid_id(project_id):
        return jsonify(error="Project not found."), 404
    path = request.args.get("path", "")
    result = svc.read_project_file(store, cfg, project_id, path)
    if result is None:
        return jsonify(error="File not found in this project."), 404
    return jsonify(result)


@bp.delete("/projects/<project_id>")
def delete_project(project_id):
    store, cfg = _ctx()
    if not is_valid_id(project_id):
        return jsonify(error="Project not found."), 404
    if not svc.delete_project(store, cfg, project_id):
        return jsonify(error="Project not found."), 404
    return jsonify(deleted=project_id)
