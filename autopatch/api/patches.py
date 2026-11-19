"""AI patch endpoints (Stage 3).

Same-machine protections as the analysis endpoint: these routes can call a paid API with your key, change
the working copy and run tests, so they accept requests only from this computer, as JSON, without a
cross-site Origin. The API key is never returned.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request

from ..core import patches as svc
from ..core.gemini_client import GeminiClient, ai_status
from ..utils import is_valid_id
from .analysis import _LOCAL_HOSTS, _LOOPBACK

bp = Blueprint("patches", __name__, url_prefix="/api")


def _ctx():
    ext = current_app.extensions["autopatch"]
    return ext["store"], ext["cfg"], ext


def _llm(ext):
    return ext.get("llm") or GeminiClient(ext["cfg"])  # tests inject a fake under ext["llm"]


def _guard(execution: bool):
    cfg = _ctx()[1]
    if execution and not cfg.ALLOW_EXECUTION:
        return jsonify(error="Running tests is disabled on this server (ALLOW_EXECUTION=false)."), 403
    if request.remote_addr not in _LOOPBACK:
        return jsonify(error="This action is only available from the machine AutoPatch runs on."), 403
    host = (request.host or "").rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
    if host.lower() not in _LOCAL_HOSTS:
        return jsonify(error="This action is only available via localhost."), 403
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify(error="Cross-site requests are not allowed."), 403
    if not request.is_json:
        return jsonify(error="Send the request as JSON."), 415
    return None


def _public(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "prompt_sha256"}


def _run(fn, *args):
    try:
        return jsonify(_public(fn(*args))), 200
    except svc.PatchServiceError as exc:
        return jsonify(error=str(exc)), exc.status


@bp.get("/ai/status")
def status():
    return jsonify(ai_status(_ctx()[1]))


@bp.post("/findings/<finding_id>/patches")
def generate(finding_id):
    blocked = _guard(False)
    if blocked:
        return blocked
    store, cfg, ext = _ctx()
    if not is_valid_id(finding_id) or not store.get("findings", finding_id):
        return jsonify(error="Finding not found."), 404
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    if body.get("confirm_send") is not True:
        return jsonify(error="Confirm that the relevant source code may be sent to the Gemini API (confirm_send: true)."), 400
    try:
        doc = svc.generate_patch(store, cfg, _llm(ext), finding_id, body.get("instruction"))
    except svc.PatchServiceError as exc:
        return jsonify(error=str(exc)), exc.status
    return jsonify(_public(doc)), 201


@bp.get("/findings/<finding_id>/patches")
def list_for_finding(finding_id):
    store = _ctx()[0]
    if not is_valid_id(finding_id) or not store.get("findings", finding_id):
        return jsonify(error="Finding not found."), 404
    docs = sorted(store.find("patches", {"finding_id": finding_id}), key=lambda d: d["patch_no"])
    return jsonify(patches=[svc.public_summary(d) for d in docs])


@bp.get("/projects/<project_id>/patches")
def list_for_project(project_id):
    store = _ctx()[0]
    if not is_valid_id(project_id) or not store.get("projects", project_id):
        return jsonify(error="Project not found."), 404
    docs = sorted(store.find("patches", {"project_id": project_id}), key=lambda d: d["created_at"], reverse=True)
    return jsonify(patches=[svc.public_summary(d) for d in docs])


@bp.get("/patches/<patch_id>")
def get_patch(patch_id):
    store = _ctx()[0]
    doc = store.get("patches", patch_id) if is_valid_id(patch_id) else None
    if not doc:
        return jsonify(error="Patch not found."), 404
    return jsonify(_public(doc))


def _action(patch_id, execution, fn, *extra):
    blocked = _guard(execution)
    if blocked:
        return blocked
    store, cfg, _ = _ctx()
    if not is_valid_id(patch_id):
        return jsonify(error="Patch not found."), 404
    return _run(fn, store, cfg, patch_id, *extra)


@bp.post("/patches/<patch_id>/accept")
def accept(patch_id):
    return _action(patch_id, True, svc.accept_patch)


@bp.post("/patches/<patch_id>/reject")
def reject(patch_id):
    return _action(patch_id, False, svc.reject_patch, str((request.get_json(silent=True) or {}).get("reason") or ""))


@bp.post("/patches/<patch_id>/rollback")
def rollback(patch_id):
    return _action(patch_id, False, svc.rollback_patch)
