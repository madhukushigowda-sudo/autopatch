"""REST endpoints for analysis runs and findings.

Starting a run EXECUTES the project's tests and scripts on this machine, so that single
endpoint is guarded: loopback clients only, a localhost Host header (blocks DNS rebinding),
no cross-site Origin (blocks a web page in your browser from triggering it), and a JSON
body. Reading results is not restricted.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request

from ..core import analysis as svc
from ..core.findings import CATEGORY_LABELS, CONFIRMATIONS, SEVERITIES
from ..utils import is_valid_id

bp = Blueprint("analysis", __name__, url_prefix="/api")

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
LIST_FIELDS = ("_id", "finding_id", "run_id", "project_id", "project_name", "title", "category", "category_label",
               "severity", "confidence", "confirmation", "confirmation_label", "file", "line", "function",
               "rule", "description", "detection_method", "occurrences", "created_at")


def _ctx():
    ext = current_app.extensions["autopatch"]
    return ext["store"], ext["cfg"]


def _guard_execution():
    """Return an error response if this request must not start code execution, else None."""
    _, cfg = _ctx()
    if not cfg.ALLOW_EXECUTION:
        return jsonify(error="Running analyses is disabled on this server (ALLOW_EXECUTION=false)."), 403
    if request.remote_addr not in _LOOPBACK:
        return jsonify(error="Analyses can only be started from the machine AutoPatch runs on."), 403
    host = (request.host or "").rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
    if host.lower() not in _LOCAL_HOSTS:
        return jsonify(error="Analyses can only be started via localhost."), 403
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify(error="Cross-site requests cannot start an analysis."), 403
    if not request.is_json:
        return jsonify(error="Send the request as JSON."), 415
    return None


@bp.post("/projects/<project_id>/runs")
def start_run(project_id):
    blocked = _guard_execution()
    if blocked:
        return blocked
    store, cfg = _ctx()
    try:
        run = svc.start_analysis(store, cfg, project_id, request.get_json(silent=True) or {})
    except svc.AnalysisError as exc:
        return jsonify(error=str(exc)), exc.status
    return jsonify(run), 202


@bp.get("/projects/<project_id>/runs")
def list_runs(project_id):
    store, _ = _ctx()
    if not is_valid_id(project_id):
        return jsonify(error="Project not found."), 404
    runs = store.find("runs", {"project_id": project_id}, sort=("created_at", -1), limit=20)
    return jsonify(runs=[{k: v for k, v in r.items() if k != "summary"} | {"pytest": ((r.get("summary") or {}).get("pytest") or {}).get("summary")} for r in runs])


@bp.get("/runs/<run_id>")
def get_run(run_id):
    store, _ = _ctx()
    run = store.get("runs", run_id) if is_valid_id(run_id) else None
    if not run:
        return jsonify(error="Run not found."), 404
    return jsonify(run)


@bp.get("/runs/<run_id>/findings")
def list_findings(run_id):
    store, _ = _ctx()
    if not is_valid_id(run_id) or not store.get("runs", run_id):
        return jsonify(error="Run not found."), 404
    docs = store.find("findings", {"run_id": run_id})  # already in importance order (assigned by finalize)
    docs.sort(key=lambda d: d["finding_id"])
    facets = {
        "severity": {s: sum(1 for d in docs if d["severity"] == s) for s in SEVERITIES},
        "category": {c: {"label": CATEGORY_LABELS[c], "count": n}
                     for c, n in sorted({d["category"]: 0 for d in docs}.items()) for n in [sum(1 for d in docs if d["category"] == c)]},
        "confirmation": {c: sum(1 for d in docs if d["confirmation"] == c) for c in CONFIRMATIONS},
    }
    sev = [s for s in request.args.get("severity", "").split(",") if s]
    cat = [c for c in request.args.get("category", "").split(",") if c]
    conf = request.args.get("confirmation", "")
    q = request.args.get("q", "").strip().lower()
    if sev:
        docs = [d for d in docs if d["severity"] in sev]
    if cat:
        docs = [d for d in docs if d["category"] in cat]
    if conf:
        docs = [d for d in docs if d["confirmation"] == conf]
    if q:
        docs = [d for d in docs if q in " ".join(str(d.get(k) or "") for k in ("title", "file", "description", "rule", "function", "finding_id")).lower()]
    return jsonify(findings=[{k: d.get(k) for k in LIST_FIELDS} for d in docs], facets=facets, total=len(docs))


@bp.get("/findings/<finding_id>")
def get_finding(finding_id):
    store, _ = _ctx()
    doc = store.get("findings", finding_id) if is_valid_id(finding_id) else None
    if not doc:
        return jsonify(error="Finding not found."), 404
    return jsonify(doc)


@bp.get("/overview")
def overview():
    store, _ = _ctx()
    return jsonify(svc.build_overview(store))
