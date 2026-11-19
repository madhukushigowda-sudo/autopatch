"""Read-only summaries for the dashboard, issue list, history, verification and reports.

Everything here is computed from records the app already stores (projects, runs, findings, patches). Nothing is
written, estimated or invented. Where a number has a definition that could be misread (health score, "repairs"), the
definition is returned with it so the UI can show it.
"""
from __future__ import annotations

from . import findings as F

PENALTY = {"Critical": 20, "High": 8, "Medium": 3, "Low": 1}
TEST_PENALTY_EACH, TEST_PENALTY_CAP = 5, 25
HEALTH_FORMULA = ("Starts at 100. Subtracts 20 per Critical, 8 per High, 3 per Medium and 1 per Low finding in the latest completed analysis, "
                  "then 5 per failing, erroring or uncollectable test (at most 25). Never below 0. It is a heuristic summary, not a measurement of quality.")


def latest_runs(store) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for r in store.find("runs", sort=("created_at", -1)):
        if r.get("status") == "completed" and r.get("project_id") not in latest:
            latest[r["project_id"]] = r
    return latest


def health_for_run(run: dict) -> dict:
    fs = (run.get("findings_summary") or {}).get("by_severity") or {}
    pt = ((run.get("summary") or {}).get("pytest") or {}).get("summary") or {}
    bad_tests = (pt.get("failed") or 0) + (pt.get("errors") or 0) + (pt.get("collection_errors") or 0)
    pen = sum(PENALTY[s] * (fs.get(s) or 0) for s in PENALTY)
    tpen = min(TEST_PENALTY_CAP, TEST_PENALTY_EACH * bad_tests)
    return {"score": max(0, 100 - pen - tpen), "finding_penalty": pen, "test_penalty": tpen}


def patch_counts(patches: list[dict]) -> dict:
    out = {"total": len(patches), "proposed": 0, "applied": 0, "verified_applied": 0, "reverted_regression": 0, "rolled_back": 0, "rejected": 0, "invalid": 0}
    for p in patches:
        st = p.get("status")
        if st in out:
            out[st] += 1
        if st == "applied" and ((p.get("verification") or {}).get("verdict") == "target_resolved"):
            out["verified_applied"] += 1
    return out


def history(store, limit: int = 60) -> list[dict]:
    projects = {p["_id"]: p for p in store.find("projects")}
    items: list[dict] = []
    for r in store.find("runs", sort=("created_at", -1), limit=200):
        name = r.get("project_name") or (projects.get(r["project_id"]) or {}).get("name") or ""
        items.append({"at": r.get("created_at"), "kind": "scan", "event": "scan_started", "detail": "Analysis started", "project_id": r["project_id"], "project_name": name, "run_id": r["_id"]})
        if r.get("finished_at"):
            ev = "scan_completed" if r.get("status") == "completed" else "scan_failed"
            total = (r.get("findings_summary") or {}).get("total")
            detail = (f"{total} finding(s)" if total is not None else "Analysis finished") if ev == "scan_completed" else (r.get("error") or "Analysis failed")
            items.append({"at": r["finished_at"], "kind": "scan", "event": ev, "detail": detail, "project_id": r["project_id"], "project_name": name, "run_id": r["_id"]})
    for p in store.find("patches"):
        name = (projects.get(p.get("project_id")) or {}).get("name") or ""
        for e in p.get("events") or []:
            items.append({"at": e.get("at"), "kind": "patch", "event": e.get("event"), "detail": e.get("detail") or "", "project_id": p.get("project_id"), "project_name": name,
                          "finding_id": p.get("finding_id"), "finding_ref": p.get("finding_ref"), "patch_id": p["_id"], "patch_no": p.get("patch_no")})
    items = [i for i in items if i["at"]]
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]


def dashboard(store) -> dict:
    projects = store.find("projects")
    latest = latest_runs(store)
    sev = dict.fromkeys(F.SEVERITIES, 0)
    conf = dict.fromkeys(F.CONFIRMATIONS, 0)
    detected = files = 0
    per_project = []
    for pid, run in latest.items():
        fs = run.get("findings_summary") or {}
        detected += fs.get("total", 0)
        for k, v in (fs.get("by_severity") or {}).items():
            sev[k] = sev.get(k, 0) + v
        for k, v in (fs.get("by_confirmation") or {}).items():
            conf[k] = conf.get(k, 0) + v
        proj = next((p for p in projects if p["_id"] == pid), None)
        files += (proj or {}).get("python_file_count", 0)
        h = health_for_run(run)
        per_project.append({"project_id": pid, "name": run.get("project_name") or (proj or {}).get("name"), "score": h["score"], "findings": fs.get("total", 0)})
    patches = store.find("patches")
    scores = [p["score"] for p in per_project]
    return {
        "repositories": len(projects), "repositories_analyzed": len(latest), "files_scanned": files,
        "bugs_detected": detected, "by_severity": sev, "by_confirmation": conf,
        "patches": patch_counts(patches),
        "health": {"score": round(sum(scores) / len(scores)) if scores else None, "formula": HEALTH_FORMULA, "projects": sorted(per_project, key=lambda x: x["score"])},
        "recent_activity": history(store, 10),
        "basis": "Counts use the most recent completed analysis of each repository. Files scanned counts Python source files in those repositories.",
    }


def issues(store, *, project_id: str | None = None, severity: list[str] | None = None, category: list[str] | None = None,
           confirmation: str = "", q: str = "", limit: int = 500) -> dict:
    latest = latest_runs(store)
    run_ids = {r["_id"] for pid, r in latest.items() if not project_id or pid == project_id}
    patches_by_finding: dict[str, list[dict]] = {}
    for p in store.find("patches"):
        patches_by_finding.setdefault(p.get("finding_id"), []).append(p)
    docs = []
    for rid in run_ids:
        docs.extend(store.find("findings", {"run_id": rid}))
    facets = {"severity": {s: sum(1 for d in docs if d["severity"] == s) for s in F.SEVERITIES},
              "confirmation": {c: sum(1 for d in docs if d["confirmation"] == c) for c in F.CONFIRMATIONS},
              "category": {c: {"label": F.CATEGORY_LABELS.get(c, c), "count": sum(1 for d in docs if d["category"] == c)} for c in sorted({d["category"] for d in docs})}}
    if severity:
        docs = [d for d in docs if d["severity"] in severity]
    if category:
        docs = [d for d in docs if d["category"] in category]
    if confirmation:
        docs = [d for d in docs if d["confirmation"] == confirmation]
    if q:
        needle = q.lower()
        docs = [d for d in docs if needle in " ".join(str(d.get(k) or "") for k in ("title", "file", "description", "rule", "function", "finding_id", "project_name")).lower()]
    order = {s: i for i, s in enumerate(F.SEVERITIES)}
    docs.sort(key=lambda d: (order.get(d["severity"], 9), d.get("project_name") or "", d.get("finding_id") or ""))
    out = []
    for d in docs[:limit]:
        ps = sorted(patches_by_finding.get(d["_id"], []), key=lambda p: p.get("created_at") or "")
        row = {k: d.get(k) for k in ("_id", "finding_id", "run_id", "project_id", "project_name", "title", "category", "category_label", "severity", "confidence",
                                     "confirmation", "confirmation_label", "file", "line", "function", "rule", "description", "root_cause", "detection_method", "occurrences")}
        row["patch_count"] = len(ps)
        row["latest_patch"] = {"_id": ps[-1]["_id"], "patch_no": ps[-1].get("patch_no"), "status": ps[-1].get("status")} if ps else None
        out.append(row)
    return {"issues": out, "total": len(docs), "facets": facets}


def verification(store, limit: int = 40) -> dict:
    projects = {p["_id"]: p for p in store.find("projects")}
    runs = []
    for r in store.find("runs", sort=("created_at", -1), limit=limit):
        s = r.get("summary") or {}
        pt = (s.get("pytest") or {})
        runs.append({"_id": r["_id"], "project_id": r["project_id"], "project_name": r.get("project_name") or (projects.get(r["project_id"]) or {}).get("name"),
                     "status": r.get("status"), "created_at": r.get("created_at"), "finished_at": r.get("finished_at"),
                     "pytest_status": pt.get("status"), "pytest": pt.get("summary"), "duration_s": pt.get("duration_s"),
                     "tools": {k: (s.get(k) or {}).get("status") for k in ("ruff", "bandit")},
                     "findings_total": (r.get("findings_summary") or {}).get("total"), "error": r.get("error")})
    pdocs = []
    for p in sorted(store.find("patches"), key=lambda d: d.get("created_at") or "", reverse=True)[:limit]:
        v = p.get("verification") or {}
        pdocs.append({"_id": p["_id"], "patch_no": p.get("patch_no"), "finding_id": p.get("finding_id"), "finding_ref": p.get("finding_ref"), "finding_title": p.get("finding_title"),
                      "project_id": p.get("project_id"), "project_name": (projects.get(p.get("project_id")) or {}).get("name"), "status": p.get("status"),
                      "created_at": p.get("created_at"), "verdict": v.get("verdict"), "label": v.get("label"),
                      "baseline": (v.get("baseline") or {}).get("summary"), "after": (v.get("after") or {}).get("summary"),
                      "newly_failing": (v.get("comparison") or {}).get("newly_failing") or [], "newly_passing": (v.get("comparison") or {}).get("newly_passing") or []})
    return {"runs": runs, "patches": pdocs, "counts": patch_counts(store.find("patches"))}


def report(store, project_id: str) -> dict | None:
    proj = store.get("projects", project_id)
    if not proj:
        return None
    runs = [r for r in store.find("runs", {"project_id": project_id}, sort=("created_at", -1)) if r.get("status") == "completed"]
    run = runs[0] if runs else None
    patches = sorted(store.find("patches", {"project_id": project_id}), key=lambda d: d.get("created_at") or "")
    findings = []
    if run:
        findings = sorted(store.find("findings", {"run_id": run["_id"]}), key=lambda d: (F.SEVERITIES.index(d["severity"]) if d["severity"] in F.SEVERITIES else 9, d.get("finding_id") or ""))
    s = (run or {}).get("summary") or {}
    return {
        "project": {k: proj.get(k) for k in ("_id", "name", "created_at", "python_file_count", "loc", "test_file_count", "source_sha256")},
        "analysis": None if not run else {
            "run_id": run["_id"], "finished_at": run.get("finished_at"), "pytest": (s.get("pytest") or {}).get("summary"), "pytest_status": (s.get("pytest") or {}).get("status"),
            "tools": {k: {"status": (s.get(k) or {}).get("status"), "count": (s.get(k) or {}).get("count")} for k in ("ruff", "bandit")},
            "findings": run.get("findings_summary"), "health": health_for_run(run), "integrity_original_unchanged": (run.get("integrity") or {}).get("original_unchanged")},
        "health_formula": HEALTH_FORMULA,
        "top_findings": [{k: d.get(k) for k in ("finding_id", "title", "severity", "confidence", "confirmation_label", "file", "line", "category_label")} for d in findings[:25]],
        "patches": [{"patch_no": p.get("patch_no"), "finding_ref": p.get("finding_ref"), "finding_title": p.get("finding_title"), "status": p.get("status"), "created_at": p.get("created_at"),
                     "added": p.get("added"), "removed": p.get("removed"), "files": [f.get("path") for f in (p.get("files") or [])], "patch_hash": p.get("patch_hash"),
                     "verification_label": (p.get("verification") or {}).get("label"), "verdict": (p.get("verification") or {}).get("verdict"),
                     "baseline": ((p.get("verification") or {}).get("baseline") or {}).get("summary"), "after": ((p.get("verification") or {}).get("after") or {}).get("summary")} for p in patches],
        "patch_counts": patch_counts(patches),
        "limitations": ["Findings come from Ruff, Bandit, AST checks and the project's own tests; they are not proof that the code is free of other problems.",
                        "Confidence values are heuristic, not calibrated probabilities.",
                        "A patch is described as verified only against the tests that exist in the project, and only when those tests ran.",
                        "Test execution runs in a restricted subprocess, which is not a hardened sandbox."],
    }


def report_markdown(rep: dict) -> str:
    p, a = rep["project"], rep["analysis"]
    out = [f"# AutoPatch report: {p['name']}\n\n", f"- Uploaded: {p.get('created_at')}\n", f"- Python files: {p.get('python_file_count')}, lines of code: {p.get('loc')}, test files: {p.get('test_file_count')}\n",
           f"- Source SHA-256: {p.get('source_sha256')}\n\n"]
    out.append("## Analysis\n")
    if not a:
        out.append("No completed analysis exists for this repository yet.\n\n")
    else:
        pt = a.get("pytest") or {}
        f = a.get("findings") or {}
        out.append(f"- Finished: {a.get('finished_at')}\n- Tests: {pt.get('passed', 0)} passed, {pt.get('failed', 0)} failed, {pt.get('errors', 0)} errors, {pt.get('skipped', 0)} skipped (pytest status: {a.get('pytest_status')})\n")
        out.append(f"- Findings: {f.get('total', 0)} (" + ", ".join(f"{k}: {v}" for k, v in (f.get('by_severity') or {}).items() if v) + ")\n")
        out.append(f"- Health score (heuristic): {a['health']['score']} / 100. {rep['health_formula']}\n")
        if a.get("integrity_original_unchanged") is not None:
            out.append(f"- Original upload unchanged after the run: {'yes' if a['integrity_original_unchanged'] else 'NO - do not trust this run'}\n")
        out.append("\n")
    if rep["top_findings"]:
        out.append("## Findings (most important first)\n\n| ID | Severity | Confidence (heuristic) | Evidence | Location | Title |\n|---|---|---|---|---|---|\n")
        for t in rep["top_findings"]:
            loc = f"{t.get('file') or '-'}:{t.get('line') or ''}".rstrip(":")
            out.append(f"| {t['finding_id']} | {t['severity']} | {t['confidence']} | {t.get('confirmation_label')} | {loc} | {str(t['title']).replace('|', '/')} |\n")
        out.append("\n")
    out.append("## Repair proposals\n")
    if not rep["patches"]:
        out.append("No patches were proposed for this repository.\n\n")
    for pt_ in rep["patches"]:
        b, af = pt_.get("baseline") or {}, pt_.get("after") or {}
        out.append(f"- Patch #{pt_['patch_no']} for {pt_['finding_ref']} ({pt_['status']}): +{pt_.get('added')} / -{pt_.get('removed')} lines in {', '.join(pt_['files']) or 'n/a'}. ")
        if pt_.get("verification_label"):
            out.append(f"{pt_['verification_label']} Baseline {b.get('passed', '?')} passed / {b.get('failed', '?')} failed; after {af.get('passed', '?')} passed / {af.get('failed', '?')} failed. ")
        else:
            out.append("Unverified proposal: no verification is recorded. ")
        out.append(f"Patch SHA-256: {pt_.get('patch_hash')}\n")
    c = rep["patch_counts"]
    out.append(f"\nTotals: {c['total']} proposed, {c['applied']} applied ({c['verified_applied']} verified against the available tests), {c['reverted_regression']} auto-reverted, {c['rolled_back']} rolled back, {c['rejected']} rejected, {c['invalid']} rejected by validation.\n\n")
    out.append("## Limitations\n" + "".join(f"- {x}\n" for x in rep["limitations"]))
    return "".join(out)
