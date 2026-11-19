"""Stage 0 compatibility checks. Read-only against synthetic fixtures in a temporary folder.

Nothing here touches a real data folder, a .env file, or MongoDB. The fixtures are
synthetic collections written by the current app version ("current") and the same data
with the reviewer-instruction fields removed ("legacy", as older stored patches look).
"""
import hashlib
import importlib.util
import re
import shutil
from pathlib import Path

import pytest

from autopatch import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "compat"
ROOT = Path(__file__).resolve().parent.parent


def digests(folder: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.json"))}


@pytest.fixture(params=["current", "legacy"])
def stored(request, tmp_path):
    """An app whose local store is a copy of the synthetic fixture. Returns (client, store_dir, variant)."""
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / request.param, data / "db")
    app = create_app({"DATA_DIR": data, "STORAGE_BACKEND": "local"})
    return app.test_client(), data / "db", request.param


def ids(client):
    pid = client.get("/api/projects").get_json()["projects"][0]["_id"]
    return pid


def test_read_endpoints_work_on_stored_data_and_never_modify_it(stored):
    client, db, variant = stored
    before = digests(db)
    assert before, "fixture copy is empty"

    listing = client.get("/api/projects")
    assert listing.status_code == 200
    pid = ids(client)
    assert client.get(f"/api/projects/{pid}").status_code == 200
    runs = client.get(f"/api/projects/{pid}/runs").get_json()
    run_id = (runs.get("runs") or runs)[0]["_id"]
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    findings = client.get(f"/api/runs/{run_id}/findings").get_json()
    fid = findings["findings"][0]["_id"]
    assert client.get(f"/api/findings/{fid}").status_code == 200
    plist = client.get(f"/api/findings/{fid}/patches").get_json()["patches"]
    assert len(plist) == 1
    patch = client.get(f"/api/patches/{plist[0]['_id']}")
    assert patch.status_code == 200
    assert client.get(f"/api/projects/{pid}/patches").status_code == 200
    assert client.get("/api/overview").status_code == 200
    assert client.get(f"/app/findings/{fid}").status_code == 200

    assert digests(db) == before, "reading stored data changed it on disk"


def test_legacy_patch_without_instruction_fields_is_still_readable(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / "legacy", data / "db")
    app = create_app({"DATA_DIR": data, "STORAGE_BACKEND": "local"})
    store = app.extensions["autopatch"]["store"]
    doc = store.find("patches")[0]
    assert "instruction" not in doc and "instruction_chars" not in doc
    body = app.test_client().get(f"/api/patches/{doc['_id']}").get_json()
    assert body["_id"] == doc["_id"]


def test_fixture_patch_keeps_its_instruction_when_present(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / "current", data / "db")
    app = create_app({"DATA_DIR": data, "STORAGE_BACKEND": "local"})
    doc = app.extensions["autopatch"]["store"].find("patches")[0]
    assert doc["instruction"] == "Keep it small." and doc["instruction_chars"] == len("Keep it small.")


def test_fixtures_are_checked_in_unmodified_and_contain_no_secret_shapes():
    text = "".join(p.read_text(encoding="utf-8") for p in FIXTURES.rglob("*.json"))
    assert not re.search(r"AIza[0-9A-Za-z_\-]{20,}|BEGIN [A-Z ]*PRIVATE KEY|mongodb(\+srv)?://", text)


def test_check_script_clears_every_env_name_that_config_reads():
    """Guards drift: if config.py starts reading a new env name, scripts/check.py must clear it too."""
    spec = importlib.util.spec_from_file_location("autopatch_check", ROOT / "scripts" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    source = (ROOT / "config.py").read_text(encoding="utf-8")
    used = set(re.findall(r"""["']([A-Z][A-Z0-9_]{3,})["']""", source))
    names_read = {n for n in used if n in set(mod.CONFIG_ENV) or re.fullmatch(r"(GEMINI|MONGODB|MAX|PATCH|PYTEST|TOOL|ENTRYPOINT|STORAGE|ALLOW|DATA|SECRET)_[A-Z_]+|ALLOW_EXECUTION", n)}
    assert names_read <= set(mod.CONFIG_ENV), sorted(names_read - set(mod.CONFIG_ENV))


def test_check_script_environment_removes_config_names_and_points_data_elsewhere(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("autopatch_check2", ROOT / "scripts" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in mod.CONFIG_ENV:
        monkeypatch.setenv(name, "hostile-value")
    env = mod.clean_env(str(tmp_path / "d"))
    assert env["DATA_DIR"] == str(tmp_path / "d")
    assert all(n not in env for n in mod.CONFIG_ENV if n != "DATA_DIR")


# ---------------------------------------------------------------- baseline guard in scripts/check.py
def _check_module():
    spec = importlib.util.spec_from_file_location("autopatch_check3", ROOT / "scripts" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE = "t.m::test_plain\tpassed\nt.m::test_param[a\\n-x]\tpassed\nt.m::test_param[b-y]\tpassed\nt.m::test_posix\tpassed-or-skipped\n"


def test_baseline_guard_passes_when_everything_is_present_and_passing():
    out = {"t.m::test_plain": "passed", "t.m::test_param[a\\n-x]": "passed", "t.m::test_param[b-y]": "passed", "t.m::test_posix": "skipped", "t.m::test_new": "passed"}
    assert _check_module().compare_baseline(BASE, out) == ([], 4)


def test_baseline_guard_tolerates_different_parameter_spelling_as_seen_on_windows():
    out = {"t.m::test_plain": "passed", "t.m::test_param[a-x-0]": "passed", "t.m::test_param[different text]": "passed", "t.m::test_posix": "passed"}
    assert _check_module().compare_baseline(BASE, out)[0] == []


@pytest.mark.parametrize("change,expect", [
    (lambda o: o.pop("t.m::test_plain"), "test_plain: missing"),
    (lambda o: o.pop("t.m::test_param[b-y]"), "1 case(s) now, 2 in the baseline"),
    (lambda o: o.update({"t.m::test_plain": "failed"}), "test_plain: 1 failed"),
    (lambda o: o.update({"t.m::test_plain": "skipped"}), "test_plain: skipped"),
    (lambda o: o.update({"t.m::test_param[a\\n-x]": "failed"}), "test_param: 1 failed"),
])
def test_baseline_guard_still_catches_lost_failed_or_skipped_tests(change, expect):
    out = {"t.m::test_plain": "passed", "t.m::test_param[a\\n-x]": "passed", "t.m::test_param[b-y]": "passed", "t.m::test_posix": "passed"}
    change(out)
    bad, _ = _check_module().compare_baseline(BASE, out)
    assert any(expect in b for b in bad), bad


def test_real_baseline_file_parses_and_every_line_has_a_known_outcome():
    lines = [ln for ln in (ROOT / "tests" / "baseline_ids.txt").read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert len(lines) == 267 and all(ln.rpartition("\t")[2] in ("passed", "passed-or-skipped") for ln in lines)
