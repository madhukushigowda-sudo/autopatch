"""HTML pages: public landing page and the workspace (dashboard, repositories, scan center, bug intelligence, repair studio, verification, reports)."""
from flask import Blueprint, redirect, render_template

bp = Blueprint("pages", __name__)


@bp.get("/")
def landing():
    return render_template("landing.html")


@bp.get("/app")
def dashboard():
    return render_template("dashboard.html", active="dashboard")


@bp.get("/app/repositories")
def repositories():
    return render_template("repositories.html", active="repositories")


@bp.get("/app/repositories/<project_id>")
def repository(project_id):
    return render_template("repository.html", active="repositories", project_id=project_id)


@bp.get("/app/scan")
def scan():
    return render_template("scan.html", active="scan")


@bp.get("/app/issues")
def issues():
    return render_template("issues.html", active="issues")


@bp.get("/app/findings/<finding_id>")
def finding(finding_id):
    return render_template("finding.html", active="issues", finding_id=finding_id)


@bp.get("/app/studio")
def studio():
    return render_template("studio.html", active="studio")


@bp.get("/app/studio/<finding_id>")
def studio_finding(finding_id):
    return render_template("studio_finding.html", active="studio", finding_id=finding_id)


@bp.get("/app/verification")
def verification():
    return render_template("verification.html", active="verification")


@bp.get("/app/reports")
def reports():
    return render_template("reports.html", active="reports")


# Earlier addresses keep working.
@bp.get("/analyze")
@bp.get("/app/analyze")
def legacy_analyze():
    return redirect("/app/repositories", code=302)


@bp.get("/app/projects/<project_id>")
def legacy_project(project_id):
    return redirect(f"/app/repositories/{project_id}", code=302)
