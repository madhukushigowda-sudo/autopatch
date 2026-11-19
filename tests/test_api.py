import io
import os

from autopatch import create_app

from tests.helpers import GOOD_PROJECT, WRAPPED_PROJECT, make_zip


def upload(client, name, data, field="files"):
    return client.post("/api/projects", data={field: (io.BytesIO(data), name)}, content_type="multipart/form-data")


def test_health(client):
    r = client.get("/api/health")
    body = r.get_json()
    assert r.status_code == 200 and body["supported_language"] == "python"
    assert body["storage"]["backend"] == "local"
    assert "secret" not in str(body).lower()


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"AutoPatch" in r.data


def test_upload_zip_end_to_end(client, app):
    r = upload(client, "my project.zip", make_zip(WRAPPED_PROJECT))
    assert r.status_code == 201, r.get_json()
    doc = r.get_json()
    pid = doc["_id"]
    assert doc["python_file_count"] == 3 and doc["test_file_count"] == 1 and doc["static_test_count"] == 1
    assert doc["stripped_root"] == "myrepo"
    assert "map" not in doc  # summary only

    full = client.get(f"/api/projects/{pid}").get_json()
    assert full["map"]["entry_points"] == ["app.py"]
    tp = {t["name"]: t for t in full["map"]["third_party"]}
    assert set(tp) == {"requests"} and tp["requests"]["declared_in_requirements"] is True
    assert {"source": "app", "target": "util"} in full["map"]["graph"]["edges"]  # internal import resolved

    listing = client.get("/api/projects").get_json()["projects"]
    assert [p["_id"] for p in listing] == [pid]

    f = client.get(f"/api/projects/{pid}/file", query_string={"path": "util.py"}).get_json()
    assert "def add" in f["content"]

    # original files really are on disk, under the project folder only
    root = app.extensions["autopatch"]["cfg"].DATA_DIR / "projects" / pid / "original"
    assert (root / "util.py").is_file()

    # delete removes both the record and the files
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert not root.exists()


def test_upload_single_py_files(client):
    r = client.post("/api/projects", data={"files": [(io.BytesIO(b"def f():\n    return 1\n"), "calc.py"),
                                                     (io.BytesIO(b"x = 2\n"), "other.py")]},
                    content_type="multipart/form-data")
    assert r.status_code == 201
    assert r.get_json()["python_file_count"] == 2


def test_rejections_return_clean_json_errors(client):
    cases = {
        "not python": upload(client, "notes.txt", b"hello"),
        "fake zip": upload(client, "a.zip", b"not really a zip"),
        "traversal": upload(client, "e.zip", make_zip({"../evil.py": "x=1", "ok.py": "y=1"})),
        "no python": upload(client, "n.zip", make_zip({"readme.md": "hi"})),
    }
    for label, r in cases.items():
        assert r.status_code == 400, label
        assert "error" in r.get_json(), label
        assert "Traceback" not in r.get_data(as_text=True), label
    assert client.post("/api/projects").status_code == 400  # nothing uploaded
    assert client.get("/api/projects").get_json()["projects"] == []  # nothing was stored


def test_package_root_zip_keeps_imports_resolvable(client):
    doc = upload(client, "pkg.zip", make_zip(GOOD_PROJECT)).get_json()
    full = client.get(f"/api/projects/{doc['_id']}").get_json()
    assert doc["stripped_root"] is None
    assert full["map"]["third_party"] == []  # "from proj.util import add" resolves to the project itself
    assert {"source": "proj.tests.test_util", "target": "proj.util"} in full["map"]["graph"]["edges"]
    assert full["map"]["declared_requirements"] == ["requests"]


def test_mixed_zip_and_py_rejected(client):
    r = client.post("/api/projects", data={"files": [(io.BytesIO(make_zip({"a.py": "x=1"})), "a.zip"),
                                                     (io.BytesIO(b"x=1"), "b.py")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_failed_upload_leaves_no_files(client, app):
    upload(client, "e.zip", make_zip({"../evil.py": "x=1", "ok.py": "y=1"}))
    cfg = app.extensions["autopatch"]["cfg"]
    projects = cfg.DATA_DIR / "projects"
    assert not projects.exists() or not any(projects.iterdir())
    tmp = cfg.DATA_DIR / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())  # temp upload cleaned


def test_request_size_limit(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "MAX_UPLOAD_MB": 0.001})
    r = upload(app.test_client(), "big.zip", make_zip({"a.py": os.urandom(5000)}))  # incompressible
    assert r.status_code == 413
    assert "limit" in r.get_json()["error"]


def test_file_endpoint_cannot_escape_project(client, app):
    pid = upload(client, "p.zip", make_zip(GOOD_PROJECT)).get_json()["_id"]
    secret = app.extensions["autopatch"]["cfg"].DATA_DIR / "secret.txt"
    secret.write_text("top secret")
    for bad in ["../../secret.txt", "../../../secret.txt", "/etc/passwd", "data.bin", "", "nope.py"]:
        r = client.get(f"/api/projects/{pid}/file", query_string={"path": bad})
        assert r.status_code == 404, bad
    for bad_id in ["..", "../..", "x" * 32, "ZZZ"]:
        assert client.get(f"/api/projects/{bad_id}").status_code == 404
        assert client.delete(f"/api/projects/{bad_id}").status_code == 404


def test_original_upload_content_is_unchanged_byte_for_byte(client, app):
    src = b"def f():\r\n    return 1\r\n"  # CRLF must survive untouched
    pid = upload(client, "crlf.py", src).get_json()["_id"]
    stored = (app.extensions["autopatch"]["cfg"].DATA_DIR / "projects" / pid / "original" / "crlf.py").read_bytes()
    assert stored == src


def test_uploaded_code_is_never_executed(client, tmp_path):
    marker = tmp_path / "pwned.txt"
    evil = f"import os\nopen({str(marker)!r}, 'w').write('x')\n".encode()
    assert upload(client, "evil.py", evil).status_code == 201
    assert not marker.exists()
