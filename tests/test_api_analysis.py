"""HTTP API for analyses: guards, polling, findings queries, overview, malicious uploads."""
import io

import pytest

from autopatch import create_app
from autopatch.core import analysis as svc
from tests import projects as P
from tests.helpers import make_zip, upload_files, wait_for_run

JSON = {"content_type": "application/json"}


def start(client, pid, body=None, **kw):
    return client.post(f"/api/projects/{pid}/runs", json=body if body is not None else {"run_entrypoints": False}, **kw)


def test_start_poll_and_read_results(client):
    pid = upload_files(client, P.EXCEPTION)
    r = start(client, pid)
    assert r.status_code == 202
    run = wait_for_run(client, r.get_json()["_id"])
    assert run["status"] == "completed" and run["summary"]["pytest"]["summary"]["failed"] == 2
    listing = client.get(f"/api/runs/{run['_id']}/findings").get_json()
    assert listing["total"] >= 1 and set(listing["facets"]) == {"severity", "category", "confirmation"}
    detail = client.get(f"/api/findings/{listing['findings'][0]['_id']}").get_json()
    assert detail["evidence"]["type"] in ("runtime", "static") and detail["root_cause"] and detail["trigger"]
    runs = client.get(f"/api/projects/{pid}/runs").get_json()["runs"]
    assert runs[0]["_id"] == run["_id"] and "summary" not in runs[0]
    assert client.get(f"/api/projects/{pid}").get_json()["last_run"]["id"] == run["_id"]


def test_finding_filters(client):
    pid = upload_files(client, P.EXCEPTION | {"x.py": "def f():\n    try:\n        return 1\n    except Exception:\n        pass\n"})
    run = wait_for_run(client, start(client, pid).get_json()["_id"])
    base = f"/api/runs/{run['_id']}/findings"
    total = client.get(base).get_json()["total"]
    high = client.get(base, query_string={"severity": "High"}).get_json()
    assert 0 < high["total"] <= total and all(f["severity"] == "High" for f in high["findings"])
    rt = client.get(base, query_string={"confirmation": "runtime"}).get_json()
    st = client.get(base, query_string={"confirmation": "static"}).get_json()
    assert rt["total"] + st["total"] == total and rt["total"] >= 1
    assert all(f["confirmation"] == "runtime" for f in rt["findings"])
    cat = client.get(base, query_string={"category": "runtime_exception"}).get_json()
    assert cat["total"] >= 1 and all(f["category"] == "runtime_exception" for f in cat["findings"])
    assert client.get(base, query_string={"q": "calc.py"}).get_json()["total"] >= 1
    assert client.get(base, query_string={"q": "zzz-no-match"}).get_json()["total"] == 0
    assert client.get(base, query_string={"severity": "Low", "confirmation": "runtime"}).get_json()["total"] == 0


def test_unknown_ids_are_404(client):
    for url in ("/api/runs/nope", "/api/runs/" + "0" * 32, "/api/runs/%2e%2e", "/api/runs/nope/findings", "/api/findings/nope",
                "/api/projects/nope/runs"):
        assert client.get(url).status_code == 404, url
    assert client.post("/api/projects/nope/runs", json={}).status_code == 404
    assert client.post("/api/projects/" + "0" * 32 + "/runs", json={}).status_code == 404


@pytest.mark.parametrize("kwargs,code", [
    ({"environ_overrides": {"REMOTE_ADDR": "10.1.2.3"}}, 403),
    ({"headers": {"Origin": "http://evil.example"}}, 403),
    ({"base_url": "http://evil.example"}, 403),
])
def test_execution_guard_blocks_remote_cross_site_and_foreign_host(client, kwargs, code):
    pid = upload_files(client, P.PASSING)
    r = client.post(f"/api/projects/{pid}/runs", json={}, **kwargs)
    assert r.status_code == code
    assert client.get(f"/api/projects/{pid}/runs").get_json()["runs"] == []  # nothing started


def test_non_json_start_is_rejected(client):
    pid = upload_files(client, P.PASSING)
    assert client.post(f"/api/projects/{pid}/runs", data="x=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert client.post(f"/api/projects/{pid}/runs").status_code == 415


def test_execution_can_be_disabled(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "ALLOW_EXECUTION": False})
    c = app.test_client()
    pid = upload_files(c, P.PASSING)
    r = c.post(f"/api/projects/{pid}/runs", json={})
    assert r.status_code == 403 and "disabled" in r.get_json()["error"]


def test_only_one_analysis_at_a_time(client, ctx):
    store, cfg = ctx
    pid = upload_files(client, P.PASSING)
    svc._active["fake-run"] = pid
    try:
        r = start(client, pid)
        assert r.status_code == 409
    finally:
        svc._active.pop("fake-run", None)


def test_overview_is_empty_then_reflects_stored_results_only(client):
    empty = client.get("/api/overview").get_json()
    assert empty["projects"] == 0 and empty["findings_total"] == 0 and empty["recent"] == []
    assert all(v == 0 for v in empty["by_severity"].values())
    pid = upload_files(client, P.EXCEPTION)
    after_upload = client.get("/api/overview").get_json()
    assert after_upload["projects"] == 1 and after_upload["findings_total"] == 0  # nothing invented before an analysis
    run = wait_for_run(client, start(client, pid).get_json()["_id"])
    ov = client.get("/api/overview").get_json()
    assert ov["findings_total"] == run["findings_summary"]["total"] > 0
    assert sum(ov["by_severity"].values()) == ov["findings_total"]
    assert ov["recent"][0]["_id"] == run["_id"]


def test_overview_counts_only_latest_run_per_project(client):
    pid = upload_files(client, P.EXCEPTION)
    wait_for_run(client, start(client, pid).get_json()["_id"])
    second = wait_for_run(client, start(client, pid).get_json()["_id"])
    ov = client.get("/api/overview").get_json()
    assert ov["analyses"] == 2 and ov["findings_total"] == second["findings_summary"]["total"]


def test_deleting_a_project_removes_its_runs_and_findings(client, ctx):
    store, _ = ctx
    pid = upload_files(client, P.EXCEPTION)
    run = wait_for_run(client, start(client, pid).get_json()["_id"])
    assert store.find("findings", {"run_id": run["_id"]})
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/runs/{run['_id']}").status_code == 404
    assert store.find("findings", {"run_id": run["_id"]}) == [] and store.find("runs", {"project_id": pid}) == []


def test_malicious_zips_never_execute_or_create_runs(client, ctx):
    store, cfg = ctx
    marker = cfg.DATA_DIR.parent / "pwned.txt"
    evil = {
        "../../pwned.py": f"open(r'{marker}', 'w').write('x')\n",
        "ok.py": "x = 1\n",
    }
    r = client.post("/api/projects", data={"files": (io.BytesIO(make_zip(evil)), "evil.zip")}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert store.find("projects") == [] and store.find("runs") == [] and not marker.exists()
    # path-traversal and absolute names inside a zip, and a "test" that would write outside if run
    for name in ("/etc/pwned.py", "..\\..\\pwned.py", "C:\\pwned.py"):
        r = client.post("/api/projects", data={"files": (io.BytesIO(make_zip({name: "x=1", "test_a.py": "def test_a():\n    pass\n"})), "e.zip")},
                        content_type="multipart/form-data")
        assert r.status_code == 400, name
    assert store.find("projects") == [] and store.find("runs") == []


def test_no_endpoint_accepts_code_for_direct_execution(client):
    for path in ("/api/run", "/api/execute", "/api/exec", "/api/analyze"):
        assert client.post(path, json={"code": "print(1)"}).status_code in (404, 405)


def test_interrupted_runs_are_marked_failed_on_startup(client, ctx):
    store, _ = ctx
    pid = upload_files(client, P.PASSING)
    store.insert("runs", {"_id": "a" * 32, "project_id": pid, "status": "running", "created_at": "2026-01-01T00:00:00Z", "stages": []})
    store.insert("runs", {"_id": "b" * 32, "project_id": pid, "status": "completed", "created_at": "2026-01-01T00:00:00Z", "stages": []})
    svc.recover_interrupted_runs(store)
    assert store.get("runs", "a" * 32)["status"] == "failed" and "interrupted" in store.get("runs", "a" * 32)["error"].lower()
    assert store.get("runs", "b" * 32)["status"] == "completed"


def test_run_documents_never_contain_secrets(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://user:pw-secret@cluster/db")
    pid = upload_files(client, P.ENV_PROBE)
    run = wait_for_run(client, start(client, pid).get_json()["_id"])
    blob = client.get(f"/api/runs/{run['_id']}").get_data(as_text=True) + client.get(f"/api/runs/{run['_id']}/findings").get_data(as_text=True)
    assert "sk-test-secret-value" not in blob and "pw-secret" not in blob
