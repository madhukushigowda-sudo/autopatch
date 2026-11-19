import threading

import pytest

from autopatch.store import LocalStore, create_store
from config import Config


def test_crud_and_persistence(tmp_path):
    s = LocalStore(tmp_path)
    a = s.insert("projects", {"name": "a", "created_at": "2026-01-01"})
    b = s.insert("projects", {"name": "b", "created_at": "2026-01-02"})
    assert s.get("projects", a)["name"] == "a"
    assert [d["name"] for d in s.find("projects", sort=("created_at", -1))] == ["b", "a"]
    assert [d["name"] for d in s.find("projects", {"name": "a"})] == ["a"]
    assert s.update("projects", a, {"name": "a2"}) and s.get("projects", a)["name"] == "a2"
    assert not s.update("projects", "missing", {"x": 1})
    # persisted to disk and reloaded by a new instance
    s2 = LocalStore(tmp_path)
    assert s2.get("projects", a)["name"] == "a2"
    assert s2.delete("projects", b) and not s2.delete("projects", b)
    assert len(s2.find("projects")) == 1


def test_returned_documents_are_copies(tmp_path):
    s = LocalStore(tmp_path)
    i = s.insert("projects", {"tags": ["x"]})
    doc = s.get("projects", i)
    doc["tags"].append("y")
    assert s.get("projects", i)["tags"] == ["x"]


def test_invalid_collection_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        LocalStore(tmp_path).insert("../evil", {})


def test_corrupt_file_is_preserved_not_overwritten(tmp_path):
    (tmp_path / "projects.json").write_text("{not json")
    s = LocalStore(tmp_path)
    assert s.find("projects") == []
    assert list(tmp_path.glob("projects.corrupt-*"))


def test_concurrent_inserts_are_all_kept(tmp_path):
    s = LocalStore(tmp_path)
    threads = [threading.Thread(target=lambda n=n: s.insert("runs", {"n": n})) for n in range(25)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(LocalStore(tmp_path).find("runs")) == 25


def test_factory_local_is_intentional(tmp_path):
    st = create_store(Config(DATA_DIR=tmp_path, STORAGE_BACKEND="local"))
    assert st.backend == "local" and st.fallback_reason is None


def test_factory_falls_back_without_uri(tmp_path):
    st = create_store(Config(DATA_DIR=tmp_path, STORAGE_BACKEND="auto", MONGODB_URI=""))
    assert st.backend == "local" and "MONGODB_URI" in st.fallback_reason


def test_factory_falls_back_when_mongo_unusable(tmp_path):
    # Either pymongo is missing, or the unreachable server times out quickly.
    st = create_store(Config(DATA_DIR=tmp_path, STORAGE_BACKEND="mongo",
                             MONGODB_URI="mongodb://user:secret@127.0.0.1:1/?x=1", MONGODB_TIMEOUT_MS=300))
    assert st.backend == "local"
    assert st.fallback_reason
    assert "secret" not in st.fallback_reason  # credentials never leak into the reason


def test_factory_rejects_bad_mode(tmp_path):
    with pytest.raises(RuntimeError):
        create_store(Config(DATA_DIR=tmp_path, STORAGE_BACKEND="bogus"))
