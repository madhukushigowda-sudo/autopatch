"""Read-only insight endpoints: computed from stored records only, no fabricated values."""
from tests.projects import EXCEPTION, PASSING


def test_empty_store_has_no_health_score(client):
    d = client.get("/api/dashboard").get_json()
    assert d["repositories"] == 0 and d["bugs_detected"] == 0 and d["files_scanned"] == 0
    assert d["health"]["score"] is None and d["health"]["formula"]
    assert d["recent_activity"] == []
    assert client.get("/api/issues").get_json()["issues"] == []
    assert client.get("/api/history").get_json()["events"] == []


def test_dashboard_issues_history_after_analysis(client, analyze):
    run, findings, pid = analyze(EXCEPTION)
    d = client.get("/api/dashboard").get_json()
    assert d["repositories"] == 1 and d["repositories_analyzed"] == 1
    assert d["bugs_detected"] == len(findings) > 0
    assert d["files_scanned"] >= 1
    assert 0 <= d["health"]["score"] < 100
    assert d["patches"]["total"] == 0
    iss = client.get("/api/issues").get_json()
    assert iss["total"] == len(findings)
    assert {i["finding_id"] for i in iss["issues"]} == {f["finding_id"] for f in findings}
    assert all(i["project_name"] and i["severity"] for i in iss["issues"])
    sev = iss["issues"][0]["severity"]
    assert all(i["severity"] == sev for i in client.get(f"/api/issues?severity={sev}").get_json()["issues"])
    assert client.get("/api/issues?q=zzz-no-match").get_json()["issues"] == []
    events = client.get("/api/history").get_json()["events"]
    assert {e["event"] for e in events} >= {"scan_started", "scan_completed"}
    v = client.get("/api/verification").get_json()
    assert v["runs"][0]["project_id"] == pid and v["runs"][0]["pytest"] is not None


def test_report_json_and_markdown(client, analyze):
    _, _, pid = analyze(PASSING)
    r = client.get(f"/api/reports/{pid}").get_json()
    assert r["analysis"]["health"]["score"] == 100 and r["patches"] == []
    md = client.get(f"/api/reports/{pid}?format=md")
    assert md.status_code == 200 and md.mimetype == "text/markdown"
    assert "attachment" in md.headers["Content-Disposition"]
    text = md.get_data(as_text=True)
    assert "# AutoPatch report" in text and "Limitations" in text and "not a hardened sandbox" in text
    assert client.get("/api/reports/not-a-real-id").status_code == 404
    assert client.get("/api/issues?project_id=../x").status_code == 404


def test_health_penalty_math():
    from autopatch.core.insights import health_for_run
    run = {"findings_summary": {"by_severity": {"Critical": 1, "High": 1, "Medium": 1, "Low": 1}}, "summary": {"pytest": {"summary": {"failed": 10}}}}
    h = health_for_run(run)
    assert h["finding_penalty"] == 32 and h["test_penalty"] == 25 and h["score"] == 43
