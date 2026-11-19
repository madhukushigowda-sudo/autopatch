"""Every dashboard number is recomputed here from the raw stored records, by hand-written code that does not call
autopatch.core.insights, and compared with what /api/dashboard returns. Guards against the UI showing invented or drifting numbers."""
from autopatch.core import analysis as analysis_svc
from tests import projects as P
from tests.helpers import upload_files
from tests.test_gemini_rest import GOOD_DIFF, proposal

SEVS = ("Critical", "High", "Medium", "Low")
PEN = {"Critical": 20, "High": 8, "Medium": 3, "Low": 1}


class FakeLLM:
    transport = "fake"

    def generate(self, system, prompt):
        return proposal(GOOD_DIFF)


def _independent(store, py_counts):
    projects = store.find("projects")
    runs = store.find("runs")
    findings = store.find("findings")
    patches = store.find("patches")
    latest = {}
    for pr in projects:
        done = sorted((r for r in runs if r["project_id"] == pr["_id"] and r["status"] == "completed"), key=lambda r: r["created_at"])
        if done:
            latest[pr["_id"]] = done[-1]
    sev = dict.fromkeys(SEVS, 0)
    total = 0
    scores = []
    for run in latest.values():
        mine = [f for f in findings if f["run_id"] == run["_id"]]
        total += len(mine)
        for f in mine:
            sev[f["severity"]] += 1
        counts = {s: sum(1 for f in mine if f["severity"] == s) for s in SEVS}
        pt = (((run.get("summary") or {}).get("pytest") or {}).get("summary")) or {}
        bad = (pt.get("failed") or 0) + (pt.get("errors") or 0) + (pt.get("collection_errors") or 0)
        scores.append(max(0, 100 - sum(PEN[s] * counts[s] for s in SEVS) - min(25, 5 * bad)))
    pc = {"total": len(patches)}
    for st in ("proposed", "applied", "rolled_back", "rejected", "invalid", "reverted_regression"):
        pc[st] = sum(1 for p in patches if p["status"] == st)
    pc["verified_applied"] = sum(1 for p in patches if p["status"] == "applied" and (p.get("verification") or {}).get("verdict") == "target_resolved")
    return {
        "repositories": len(projects), "repositories_analyzed": len(latest), "bugs_detected": total, "by_severity": sev, "patches": pc,
        "files_scanned": sum(py_counts[pid] for pid in latest), "score": round(sum(scores) / len(scores)) if scores else None,
        "per_project": sorted(scores),
    }


def test_dashboard_matches_an_independent_recomputation(app, client, ctx):
    store, cfg = ctx
    app.extensions["autopatch"]["llm"] = FakeLLM()
    py_counts = {}
    ids = {}
    for name, files in (("failing", P.FAILING), ("exception", P.EXCEPTION), ("passing", P.PASSING), ("never_scanned", P.STATIC_ONLY)):
        pid = upload_files(client, files, name=f"{name}.zip")
        ids[name], py_counts[pid] = pid, sum(1 for k in files if k.endswith(".py"))
    # an OLD static-only scan, then the real scan: only the newest completed run may count
    old = analysis_svc.start_analysis(store, cfg, ids["failing"], {"run_tests": False, "run_entrypoints": False}, background=False)
    store.update("runs", old["_id"], {"created_at": "2020-01-01T00:00:00+00:00"})
    for name in ("failing", "exception", "passing"):
        analysis_svc.start_analysis(store, cfg, ids[name], {"run_entrypoints": False}, background=False)
    # one verified repair
    f = next(x for x in store.find("findings") if x["category"] == "test_failure" and x["project_id"] == ids["failing"]
             and store.get("runs", x["run_id"])["created_at"] > "2021")
    r = client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert client.post(f"/api/patches/{r.get_json()['_id']}/accept", json={}).status_code == 200
    # and one rejected proposal
    r2 = client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    if r2.status_code == 201:
        client.post(f"/api/patches/{r2.get_json()['_id']}/reject", json={})

    want = _independent(store, py_counts)
    got = client.get("/api/dashboard").get_json()
    assert got["repositories"] == want["repositories"] == 4 and got["repositories_analyzed"] == want["repositories_analyzed"] == 3
    assert got["bugs_detected"] == want["bugs_detected"] > 0
    assert {k: got["by_severity"].get(k, 0) for k in SEVS} == want["by_severity"]
    assert got["files_scanned"] == want["files_scanned"]
    assert got["health"]["score"] == want["score"]
    assert sorted(p["score"] for p in got["health"]["projects"]) == want["per_project"]
    for k, v in want["patches"].items():
        assert got["patches"][k] == v, (k, got["patches"], want["patches"])
    assert want["patches"]["verified_applied"] >= 1
    # the issue list agrees with the same raw findings
    iss = client.get("/api/issues?limit=1000").get_json()
    assert iss["total"] == want["bugs_detected"]
