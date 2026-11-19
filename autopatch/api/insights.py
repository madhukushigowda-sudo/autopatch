"""Read-only endpoints that summarise stored data for the dashboard, issue list, history, verification and reports."""
from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request

from ..core import insights as svc
from ..utils import is_valid_id

bp = Blueprint("insights", __name__, url_prefix="/api")


def _store():
    return current_app.extensions["autopatch"]["store"]


def _limit(default: int, maximum: int) -> int:
    try:
        return max(1, min(maximum, int(request.args.get("limit", default))))
    except ValueError:
        return default


@bp.get("/dashboard")
def dashboard():
    return jsonify(svc.dashboard(_store()))


@bp.get("/issues")
def issues():
    pid = request.args.get("project_id") or None
    if pid and not is_valid_id(pid):
        return jsonify(error="Project not found."), 404
    split = lambda name: [x for x in request.args.get(name, "").split(",") if x]  # noqa: E731
    return jsonify(svc.issues(_store(), project_id=pid, severity=split("severity"), category=split("category"),
                              confirmation=request.args.get("confirmation", ""), q=request.args.get("q", "").strip(), limit=_limit(300, 500)))


@bp.get("/history")
def history():
    return jsonify(events=svc.history(_store(), _limit(60, 200)))


@bp.get("/verification")
def verification():
    return jsonify(svc.verification(_store(), _limit(40, 100)))


@bp.get("/reports/<project_id>")
def report(project_id):
    rep = svc.report(_store(), project_id) if is_valid_id(project_id) else None
    if rep is None:
        return jsonify(error="Project not found."), 404
    if request.args.get("format") == "md":
        body = svc.report_markdown(rep)
        name = "".join(c if c.isalnum() or c in "-_" else "-" for c in rep["project"]["name"] or "project")[:60] or "project"
        return Response(body, mimetype="text/markdown", headers={"Content-Disposition": f'attachment; filename="autopatch-report-{name}.md"', "X-Content-Type-Options": "nosniff"})
    return jsonify(rep)
