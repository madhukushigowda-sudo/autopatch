"""Stage 3: AI patch generation, review decisions, safe apply, verification, history and rollback.
Gemini is always replaced by a fake: no network and no paid calls."""
import hashlib
import json
from pathlib import Path

import pytest

from autopatch import create_app
from autopatch.core import analysis as analysis_svc
from autopatch.core import patches as svc
from autopatch.core.analysis import tree_digest
from autopatch.core.gemini_client import AIError
from autopatch.core.projects import original_dir
from tests import projects as P
from tests.helpers import needs_ruff

FIX_ADD = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
BREAK_ADD = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a * 2\n"
SWAP_ADD = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a + b\n+    return b + a\n"
FIX_STATIC = "--- a/lib.py\n+++ b/lib.py\n@@ -4,2 +4,2 @@\n def never_called():\n-    return missing_name + 1\n+    return 1\n"
FIX_ENTRY = "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def divide(a, b):\n-    return a / b\n+    return a / b if b else 0\n"
KEY = "AIza-test-key-must-never-leak"


def proposal(diff, **over):
    data = {"explanation": "Addition was wrong.", "why_it_fixes": "Uses +.", "affected_files": [], "diff": diff,
            "side_effects": ["None expected"], "suggested_tests": ["assert add(2, 3) == 5"], "risk": "low", "confidence": "medium"}
    data.update(over)
    return json.dumps(data)


class FakeLLM:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def generate(self, system, prompt):
        self.calls.append({"system": system, "prompt": prompt})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def env(app, client, ctx):
    store, cfg = ctx
    cfg.GEMINI_API_KEY = KEY
    ext = app.extensions["autopatch"]

    class E:
        pass
    e = E()
    e.app, e.client, e.store, e.cfg, e.ext = app, client, store, cfg, ext
    e.set_llm = lambda *r: ext.__setitem__("llm", FakeLLM(*r)) or ext["llm"]
    return e


def setup_project(env, analyze, files=P.FAILING, category="test_failure", opts=None):
    run, findings, pid = analyze(files, opts or {"run_entrypoints": False})
    f = next(x for x in findings if x["category"] == category)
    return f, pid


def gen(env, fid, expect=201):
    r = env.client.post(f"/api/findings/{fid}/patches", json={"confirm_send": True})
    assert r.status_code == expect, r.get_json()
    return r.get_json()


def acts(env, pid_, what, expect=200, **body):
    r = env.client.post(f"/api/patches/{pid_}/{what}", json=body)
    assert r.status_code == expect, r.get_json()
    return r.get_json()


def walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)
    elif isinstance(obj, str):
        yield obj


# ------------------------------------------------------------------ generation
def test_ai_status_never_exposes_the_key(env):
    st = env.client.get("/api/ai/status").get_json()
    assert st["configured"] is True and KEY not in json.dumps(st) and "model" in st


def test_scanning_never_calls_gemini(env, analyze):
    llm = env.set_llm(proposal(FIX_ADD))
    analyze(P.FAILING)
    assert llm.calls == []


def test_generate_requires_confirmation_and_local_json_requests(env, analyze):
    f, _ = setup_project(env, analyze)
    llm = env.set_llm(proposal(FIX_ADD))
    url = f"/api/findings/{f['_id']}/patches"
    assert env.client.post(url, json={}).status_code == 400
    assert env.client.post(url, json={"confirm_send": "yes"}).status_code == 400
    assert env.client.post(url, json={"confirm_send": True}, environ_overrides={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403
    assert env.client.post(url, json={"confirm_send": True}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert env.client.post(url, json={"confirm_send": True}, base_url="http://evil.example").status_code == 403
    assert env.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert llm.calls == []
    assert env.client.post("/api/findings/nope/patches", json={"confirm_send": True}).status_code == 404
    assert env.client.post(f"/api/findings/{'0' * 32}/patches", json={"confirm_send": True}).status_code == 404


def test_generate_builds_a_reviewable_proposal_without_applying_it(env, analyze):
    f, pid = setup_project(env, analyze)
    llm = env.set_llm(proposal(FIX_ADD))
    before = tree_digest(original_dir(env.cfg, pid))
    p = gen(env, f["_id"])
    assert p["status"] == "proposed" and p["patch_no"] == 1 and p["decision"] is None and p["applied"] is None
    assert p["files"][0]["path"] == "util.py" and (p["added"], p["removed"]) == (1, 1)
    assert "-    return a - b" in p["diff"] and "+    return a + b" in p["diff"] and len(p["patch_hash"]) == 64
    assert p["explanation"] and p["suggested_tests"] and p["side_effects"]
    assert p["risk"]["level"] in ("low", "medium", "high") and "calibrated" in p["confidence"]["note"]
    assert "return a - b" in llm.calls[0]["prompt"] and "test_add" in llm.calls[0]["prompt"]
    assert tree_digest(original_dir(env.cfg, pid)) == before                    # original untouched
    assert not svc.working_dir(env.cfg, pid).exists()                           # nothing applied
    assert KEY not in json.dumps(p) and KEY not in llm.calls[0]["prompt"]


@pytest.mark.parametrize("diff,msg", [
    ("this is not a diff", "Unexpected text"),
    ("--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(x, y):\n-    return x - y\n+    return x + y\n", "context does not match"),
    ("--- a/../../evil.py\n+++ b/../../evil.py\n@@ -1,1 +1,1 @@\n-x\n+y\n", "Unsafe path"),
    ("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+x = 1\n", "creates or deletes"),
    ("--- a/test_util.py\n+++ b/test_util.py\n@@ -1,1 +1,1 @@\n-from util import add\n+import os\n", "test file"),
    ("--- a/other.py\n+++ b/other.py\n@@ -1,1 +1,1 @@\n-x\n+y\n", "unrelated"),
    ("--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a +\n", "syntax error"),
])
def test_invalid_proposals_are_rejected_recorded_and_never_applied(env, analyze, diff, msg):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(diff))
    r = env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == 422 and msg in r.get_json()["error"]
    hist = env.client.get(f"/api/findings/{f['_id']}/patches").get_json()["patches"]
    assert len(hist) == 1 and hist[0]["status"] == "invalid" and msg in hist[0]["error"]
    assert not svc.working_dir(env.cfg, pid).exists()
    assert env.client.post(f"/api/patches/{hist[0]['_id']}/accept", json={}).status_code == 409


@pytest.mark.parametrize("raw", ["", "not json", json.dumps({"explanation": "x"}), json.dumps({"diff": FIX_ADD}), "[]"])
def test_malformed_ai_responses_are_422_and_recorded(env, analyze, raw):
    f, _ = setup_project(env, analyze)
    env.set_llm(raw)
    assert env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True}).status_code == 422
    assert env.client.get(f"/api/findings/{f['_id']}/patches").get_json()["patches"][0]["status"] == "invalid"


@pytest.mark.parametrize("kind,status", [("not_configured", 503), ("sdk_missing", 503), ("invalid_key", 502), ("quota", 429),
                                         ("network", 502), ("timeout", 504), ("blocked", 502), ("model", 502)])
def test_gemini_failures_become_clear_api_errors_and_leave_no_patch(env, analyze, kind, status):
    f, _ = setup_project(env, analyze)
    env.set_llm(AIError(kind))
    r = env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == status and r.get_json()["error"] and KEY not in json.dumps(r.get_json())
    assert env.client.get(f"/api/findings/{f['_id']}/patches").get_json()["patches"] == []


def test_default_client_without_a_key_reports_how_to_fix_it(env, analyze):
    f, _ = setup_project(env, analyze)
    env.cfg.GEMINI_API_KEY = ""
    r = env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.get_json()["error"]


# ------------------------------------------------------------------ accept / verify
def test_accept_applies_to_the_working_copy_only_and_verifies(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    orig = original_dir(env.cfg, pid)
    before = tree_digest(orig)
    p = gen(env, f["_id"])
    done = acts(env, p["_id"], "accept")
    work = svc.working_dir(env.cfg, pid)
    assert done["status"] == "applied" and done["decision"]["action"] == "accept"
    assert (work / "util.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert (orig / "util.py").read_text() == "def add(a, b):\n    return a - b\n"      # original never modified
    assert tree_digest(orig) == before and done["original_unchanged"] is True
    v = done["verification"]
    assert v["verdict"] == "target_resolved" and v["label"].startswith("Verified against the available test suite")
    assert "2 passed, 0 failed" in v["label"] and "baseline: 1 passed, 1 failed" in v["label"]
    assert v["baseline"]["summary"]["failed"] == 1 and v["after"]["summary"]["failed"] == 0
    assert v["comparison"]["newly_passing"] == ["test_util.py::test_add"] and v["comparison"]["newly_failing"] == []
    assert "do not prove" in v["caveat"] and "fixed" not in v["label"].lower() and "correct" not in v["label"].lower()
    ap = done["applied"]
    assert ap["changed_files"][0]["path"] == "util.py" and ap["patch_hash"] == p["patch_hash"] and ap["pre_digest"] != ap["post_digest"]
    assert (svc.snapshot_dir(env.cfg, pid, p["_id"]) / "files" / "util.py").read_text() == "def add(a, b):\n    return a - b\n"
    assert [e["event"] for e in done["events"]][:2] == ["proposed", "applying"] or "accepted" in [e["event"] for e in done["events"]]


def test_regression_is_detected_preserved_and_reverted_exactly(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(BREAK_ADD))
    p = gen(env, f["_id"])
    done = acts(env, p["_id"], "accept")
    v = done["verification"]
    assert v["verdict"] == "regression" and done["status"] == "reverted_regression"
    assert "test_util.py::test_other" in v["comparison"]["newly_failing"] and v["label"].startswith("Regression detected")
    assert v["after_failures"] and v["pytest_output_tail"]                           # evidence preserved
    work = svc.working_dir(env.cfg, pid)
    assert (work / "util.py").read_text() == "def add(a, b):\n    return a - b\n"      # back to the pre-patch state
    assert done["rollback"]["verified_exact"] is True and done["rollback"]["digest"] == done["applied"]["pre_digest"]


@needs_ruff
def test_static_finding_without_a_covering_test_is_called_insufficiently_verified(env, analyze):
    f, pid = setup_project(env, analyze, P.STATIC_ONLY, "likely_runtime_error")
    env.set_llm(proposal(FIX_STATIC))
    done = acts(env, gen(env, f["_id"])["_id"], "accept")
    v = done["verification"]
    assert done["status"] == "applied" and v["verdict"] == "no_relevant_test"
    assert "insufficiently verified" in v["label"] and v["comparison"]["relevant_tests"] == []


def test_project_without_tests_is_unverified(env, analyze):
    f, pid = setup_project(env, analyze, P.ENTRY_CRASH, "runtime_exception", {"run_entrypoints": True})
    env.set_llm(proposal(FIX_ENTRY))
    done = acts(env, gen(env, f["_id"])["_id"], "accept")
    assert done["verification"]["verdict"] == "no_tests" and "unverified" in done["verification"]["label"]


def test_accept_is_blocked_when_tests_may_not_run_or_analysis_is_active(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    analysis_svc._active["x"] = pid
    try:
        assert env.client.post(f"/api/patches/{p['_id']}/accept", json={}).status_code == 409
    finally:
        analysis_svc._active.pop("x", None)
    env.cfg.ALLOW_EXECUTION = False
    assert env.client.post(f"/api/patches/{p['_id']}/accept", json={}).status_code == 403
    env.cfg.ALLOW_EXECUTION = True
    assert env.client.post(f"/api/patches/{p['_id']}/accept", json={}, environ_overrides={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403
    assert not svc.working_dir(env.cfg, pid).exists()


# ------------------------------------------------------------------ reject, multiple candidates, history
def test_reject_records_the_decision_and_changes_nothing(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    r = acts(env, p["_id"], "reject", reason="Not what I want")
    assert r["status"] == "rejected" and r["decision"]["action"] == "reject" and r["decision"]["reason"] == "Not what I want"
    assert not svc.working_dir(env.cfg, pid).exists()
    assert env.client.post(f"/api/patches/{p['_id']}/accept", json={}).status_code == 409
    assert env.client.post(f"/api/patches/{p['_id']}/reject", json={}).status_code == 409
    assert env.client.post(f"/api/patches/{p['_id']}/rollback", json={}).status_code == 409


def test_multiple_candidates_keep_their_history(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(BREAK_ADD), proposal(FIX_ADD))
    a, b = gen(env, f["_id"]), gen(env, f["_id"])
    assert (a["patch_no"], b["patch_no"]) == (1, 2) and a["patch_hash"] != b["patch_hash"]
    acts(env, a["_id"], "reject", reason="looks wrong")
    acts(env, b["_id"], "accept")
    hist = env.client.get(f"/api/findings/{f['_id']}/patches").get_json()["patches"]
    assert [(h["patch_no"], h["status"]) for h in hist] == [(1, "rejected"), (2, "applied")]
    proj = env.client.get(f"/api/projects/{pid}/patches").get_json()["patches"]
    assert {h["_id"] for h in proj} == {a["_id"], b["_id"]} and all(h["finding_title"] for h in proj)


def test_a_second_candidate_for_the_same_code_is_stale_after_the_first_is_applied(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD), proposal(FIX_ADD))
    a, b = gen(env, f["_id"]), gen(env, f["_id"])
    acts(env, a["_id"], "accept")
    r = acts(env, b["_id"], "accept", expect=409)
    assert "changed since this patch was generated" in r["error"]
    assert env.client.get(f"/api/patches/{b['_id']}").get_json()["status"] == "proposed"


def test_history_survives_a_restart(env, analyze, tmp_path):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    done = acts(env, p["_id"], "accept")
    app2 = create_app({"DATA_DIR": env.cfg.DATA_DIR, "STORAGE_BACKEND": "local"})
    again = app2.test_client().get(f"/api/patches/{p['_id']}").get_json()
    for key in ("status", "diff", "patch_hash", "explanation", "decision", "verification", "applied", "events", "files"):
        assert again[key] == done[key], key
    assert again["finding_ref"] == f["finding_id"] and again["decision"]["at"]
    assert app2.test_client().get(f"/api/projects/{pid}/patches").get_json()["patches"][0]["status"] == "applied"


# ------------------------------------------------------------------ rollback
def test_rollback_restores_the_exact_pre_patch_state(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    work = svc.ensure_working(env.cfg, pid)
    pre = tree_digest(work)
    (work / "util.py").write_bytes(b"def add(a, b):\r\n    return a - b\r\n".replace(b"\r\n", b"\n"))
    done = acts(env, p["_id"], "accept")
    assert tree_digest(work) != pre
    rb = acts(env, p["_id"], "rollback")
    assert rb["status"] == "rolled_back" and rb["rollback"]["verified_exact"] is True and rb["rollback"]["digest"] == done["applied"]["pre_digest"]
    assert tree_digest(work) == done["applied"]["pre_digest"] == pre
    assert (work / "util.py").read_text() == "def add(a, b):\n    return a - b\n"
    assert rb["rollback"]["original_unchanged"] is True
    assert env.client.post(f"/api/patches/{p['_id']}/rollback", json={}).status_code == 409      # not twice
    assert [e["event"] for e in rb["events"]][-1] == "rolled_back"


def test_rollback_must_go_in_reverse_order(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD), proposal(SWAP_ADD))
    a = gen(env, f["_id"])
    acts(env, a["_id"], "accept")
    b = gen(env, f["_id"])                       # generated against the patched working copy
    acts(env, b["_id"], "accept")
    work = svc.working_dir(env.cfg, pid)
    assert (work / "util.py").read_text().endswith("return b + a\n")
    err = acts(env, a["_id"], "rollback", expect=409)
    assert "more recently applied" in err["error"]
    acts(env, b["_id"], "rollback")
    acts(env, a["_id"], "rollback")
    assert tree_digest(work) == tree_digest(original_dir(env.cfg, pid))


def test_rollback_refuses_to_overwrite_later_manual_edits(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    acts(env, p["_id"], "accept")
    work = svc.working_dir(env.cfg, pid)
    (work / "util.py").write_text("# someone edited this\n")
    err = acts(env, p["_id"], "rollback", expect=409)
    assert "modified after the patch" in err["error"] and (work / "util.py").read_text() == "# someone edited this\n"


def test_rollback_without_snapshot_is_refused(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    acts(env, p["_id"], "accept")
    import shutil
    shutil.rmtree(svc.snapshot_dir(env.cfg, pid, p["_id"]))
    assert "snapshot" in acts(env, p["_id"], "rollback", expect=409)["error"]


def test_crash_during_apply_is_recovered_from_the_snapshot(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    acts(env, p["_id"], "accept")
    acts(env, p["_id"], "rollback")
    work = svc.working_dir(env.cfg, pid)
    # simulate: snapshot exists, file half-written, status stuck on "applying"
    (work / "util.py").write_text("def add(a, b):\n    return broken\n")
    env.store.update("patches", p["_id"], {"status": "applying"})
    assert svc.recover_interrupted_patches(env.store, env.cfg) == 1
    assert (work / "util.py").read_text() == "def add(a, b):\n    return a - b\n"
    assert env.store.get("patches", p["_id"])["status"] == "proposed"


# ------------------------------------------------------------------ misc API behaviour
def test_unknown_patch_ids_and_actions(env):
    for url in ("/api/patches/nope", "/api/patches/" + "0" * 32):
        assert env.client.get(url).status_code == 404
    for what in ("accept", "reject", "rollback"):
        assert env.client.post(f"/api/patches/{'0' * 32}/{what}", json={}).status_code == 404
        assert env.client.post(f"/api/patches/bad-id/{what}", json={}).status_code == 404
    assert env.client.get("/api/projects/nope/patches").status_code == 404
    assert env.client.get("/api/findings/nope/patches").status_code == 404


def test_deleting_a_project_removes_its_patches_and_working_files(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    acts(env, p["_id"], "accept")
    assert env.client.delete(f"/api/projects/{pid}").status_code == 200
    assert env.client.get(f"/api/patches/{p['_id']}").status_code == 404
    assert env.store.find("patches", {"project_id": pid}) == [] and not svc.working_dir(env.cfg, pid).exists()


def test_the_api_key_never_appears_in_any_response(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    acts(env, p["_id"], "accept")
    seen = [env.client.get(u).get_json() for u in ("/api/ai/status", f"/api/patches/{p['_id']}", f"/api/projects/{pid}/patches",
                                                  f"/api/findings/{f['_id']}/patches", f"/api/findings/{f['_id']}", "/api/health", "/api/overview")]
    assert not any(KEY in s for obj in seen for s in walk_strings(obj))
    assert hashlib.sha256(KEY.encode()).hexdigest() not in json.dumps(seen)


def test_stage3_does_not_break_original_integrity_checks(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    acts(env, p["_id"], "accept")
    run = analysis_svc.start_analysis(env.store, env.cfg, pid, {"run_entrypoints": False}, background=False)
    assert run["integrity"]["original_unchanged"] is True
    assert Path(original_dir(env.cfg, pid) / "util.py").read_text().endswith("a - b\n")


def test_failed_working_copy_creation_leaves_nothing_behind(env, analyze, monkeypatch):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])

    def boom(src, dst, **kw):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "half.py").write_text("partial")
        raise PermissionError(13, "denied", "util.py")
    monkeypatch.setattr(svc.shutil, "copytree", boom)
    r = env.client.post(f"/api/patches/{p['_id']}/accept", json={})
    assert r.status_code == 500 and "PermissionError" in r.get_json()["error"] and "working copy" in r.get_json()["error"]
    wd = svc.working_dir(env.cfg, pid)
    assert not wd.exists() and not wd.with_name("working.tmp").exists()
    monkeypatch.undo()
    assert acts(env, p["_id"], "accept")["status"] == "applied"           # and a later attempt works


def test_rejected_proposals_record_file_diagnostics(env, analyze):
    f, _ = setup_project(env, analyze)
    env.set_llm(proposal("not a diff"))
    env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    listing = env.client.get(f"/api/findings/{f['_id']}/patches").get_json()["patches"]
    doc = env.client.get(f"/api/patches/{listing[0]['_id']}").get_json()
    info = doc["file_diagnostics"]["util.py"]
    assert info["crlf"] == 0 and info["lf_only"] == 2 and info["bytes"] > 0 and len(info["sha256"]) == 64


def test_crlf_project_files_get_valid_patches_end_to_end(env, client, ctx):
    import io
    crlf = "def divide(a, b):\r\n    return a / b\r\n\r\nprint(divide(1, 0))\r\n"
    r = client.post("/api/projects", data={"files": (io.BytesIO(crlf.encode()), "main.py")}, content_type="multipart/form-data")
    pid = r.get_json()["_id"]
    run = analysis_svc.start_analysis(env.store, env.cfg, pid, {"run_entrypoints": True}, background=False)
    f = next(x for x in env.store.find("findings", {"run_id": run["_id"]}) if x["category"] == "runtime_exception")
    env.set_llm(proposal(FIX_ENTRY.replace("calc.py", "main.py")))
    p = gen(env, f["_id"])
    done = acts(env, p["_id"], "accept")
    assert done["status"] == "applied"
    assert (svc.working_dir(env.cfg, pid) / "main.py").read_bytes() == b"def divide(a, b):\r\n    return a / b if b else 0\r\n\r\nprint(divide(1, 0))\r\n"
    assert (original_dir(env.cfg, pid) / "main.py").read_bytes() == crlf.encode()


# ------------------------------------------------------------------ reviewer instruction
def gen_with(env, fid, instruction, expect=201):
    r = env.client.post(f"/api/findings/{fid}/patches", json={"confirm_send": True, "instruction": instruction})
    assert r.status_code == expect, r.get_json()
    return r.get_json()


def test_instruction_reaches_the_prompt_and_is_stored(env, analyze):
    f, _ = setup_project(env, analyze)
    llm = env.set_llm(proposal(FIX_ADD))
    p = gen_with(env, f["_id"], "  Keep the change to one line.\r\n ")
    assert "Keep the change to one line." in llm.calls[0]["prompt"] and "Reviewer instruction" in llm.calls[0]["prompt"]
    assert p["instruction"] == "Keep the change to one line." and p["instruction_chars"] == 28
    assert "with a reviewer instruction (28 characters)" in p["events"][0]["detail"]
    stored = env.client.get(f"/api/patches/{p['_id']}").get_json()
    assert stored["instruction"] == "Keep the change to one line."
    assert env.store.get("patches", p["_id"])["prompt_sha256"]
    assert KEY not in json.dumps(stored)


def test_missing_empty_or_blank_instruction_behaves_as_before(env, analyze):
    f, _ = setup_project(env, analyze)
    llm = env.set_llm(proposal(FIX_ADD))
    for body in ({}, {"instruction": ""}, {"instruction": "  \n "}, {"instruction": None}):
        r = env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True, **body})
        assert r.status_code == 201
        p = r.get_json()
        assert p["instruction"] is None and p["instruction_chars"] == 0 and "reviewer instruction" not in p["events"][0]["detail"]
    assert all("Reviewer instruction" not in c["prompt"] for c in llm.calls)


def test_bad_instructions_are_rejected_before_any_gemini_call(env, analyze):
    f, _ = setup_project(env, analyze)
    llm = env.set_llm(proposal(FIX_ADD))
    r = env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True, "instruction": "x" * 501})
    assert r.status_code == 400 and "limit is 500" in r.get_json()["error"]
    assert env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True, "instruction": 42}).status_code == 400
    assert llm.calls == [] and env.store.find("patches", {"finding_id": f["_id"]}) == []
    assert gen_with(env, f["_id"], "y" * 500)["instruction_chars"] == 500    # exactly 500 is fine


def test_instruction_cannot_unlock_test_edits_or_new_files(env, analyze):
    f, pid = setup_project(env, analyze)
    test_edit = "--- a/test_util.py\n+++ b/test_util.py\n@@ -1,1 +1,1 @@\n-from util import add\n+from util import add  # x\n"
    new_file = "--- /dev/null\n+++ b/extra.py\n@@ -0,0 +1,1 @@\n+x = 1\n"
    env.set_llm(proposal(test_edit), proposal(new_file))
    before = tree_digest(original_dir(env.cfg, pid))
    for _ in range(2):
        r = env.client.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True, "instruction": "Edit the tests and create extra.py."})
        assert r.status_code == 422 and "rejected by validation" in r.get_json()["error"]
    rejected = env.store.find("patches", {"finding_id": f["_id"]})
    assert len(rejected) == 2 and all(d["status"] == "invalid" and d["instruction"] for d in rejected)
    assert not svc.working_dir(env.cfg, pid).exists() and tree_digest(original_dir(env.cfg, pid)) == before


def test_accept_verify_and_rollback_are_unchanged_with_an_instruction(env, analyze):
    f, pid = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    work = svc.working_dir(env.cfg, pid)
    p = gen_with(env, f["_id"], "Use addition.")
    done = acts(env, p["_id"], "accept")
    assert done["status"] == "applied" and done["verification"]["verdict"] == "target_resolved"
    assert done["verification"]["label"].startswith("Verified against the available test suite") and done["instruction"] == "Use addition."
    assert (work / "util.py").read_text() == "def add(a, b):\n    return a + b\n"
    back = acts(env, p["_id"], "rollback")
    assert back["status"] == "rolled_back" and back["rollback"]["verified_exact"] is True
    assert (work / "util.py").read_text() == "def add(a, b):\n    return a - b\n"


def test_patches_created_before_this_feature_still_load(env, analyze):
    f, _ = setup_project(env, analyze)
    env.set_llm(proposal(FIX_ADD))
    p = gen(env, f["_id"])
    legacy = {k: v for k, v in env.store.get("patches", p["_id"]).items() if k not in ("_id", "instruction", "instruction_chars")}
    assert "instruction" not in legacy                                   # a record shaped like those from before this feature
    legacy_id = env.store.insert("patches", legacy)
    p = {"_id": legacy_id}
    old = env.client.get(f"/api/patches/{p['_id']}")
    assert old.status_code == 200 and old.get_json().get("instruction") is None
    assert env.client.get(f"/api/findings/{f['_id']}/patches").status_code == 200
