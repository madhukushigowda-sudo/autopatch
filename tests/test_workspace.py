import stat
import zipfile

import pytest

from autopatch.core.workspace import Limits, UploadError, extract_zip, write_python_files
from tests.helpers import GOOD_PROJECT, WRAPPED_PROJECT, make_zip, mark_all_entries_encrypted, write_zip

LIMITS = Limits()


def test_strips_wrapper_folder_that_is_not_a_package(tmp_path):
    z = write_zip(tmp_path / "p.zip", {**WRAPPED_PROJECT, "myrepo/data.bin": b"\x00\x01"})
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert res.stripped_root == "myrepo"
    assert res.files == ["app.py", "requirements.txt", "test_util.py", "util.py"]
    assert any(s["path"] == "data.bin" for s in res.skipped)  # binary dropped, reported
    assert (tmp_path / "out" / "util.py").read_text().startswith("import requests")


def test_keeps_top_folder_when_it_is_a_package(tmp_path):
    # Stripping would break "from proj.util import add" in the tests.
    z = write_zip(tmp_path / "p.zip", GOOD_PROJECT)
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert res.stripped_root is None
    assert "proj/app.py" in res.files and "proj/tests/test_util.py" in res.files
    assert "proj/data.bin" not in res.files


@pytest.mark.parametrize(
    "name",
    ["../evil.py", "a/../../evil.py", "/abs/evil.py", "C:/evil.py", "..\\evil.py", "a\\..\\..\\evil.py"],
)
def test_rejects_path_traversal_and_absolute(tmp_path, name):
    z = write_zip(tmp_path / "e.zip", {"ok.py": "x=1", name: "boom"})
    with pytest.raises(UploadError):
        extract_zip(z, tmp_path / "out", LIMITS)
    assert not (tmp_path / "evil.py").exists()


def test_rejects_symlink(tmp_path):
    info = zipfile.ZipInfo("link.py")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    z = write_zip(tmp_path / "s.zip", {"ok.py": "x=1"}, infos=[(info, "/etc/passwd")])
    with pytest.raises(UploadError, match="Symbolic"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_rejects_encrypted_flag(tmp_path):
    z = tmp_path / "enc.zip"
    z.write_bytes(mark_all_entries_encrypted(make_zip({"ok.py": "x=1"})))
    with pytest.raises(UploadError, match="Encrypted"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_rejects_not_a_zip(tmp_path):
    f = tmp_path / "fake.zip"
    f.write_bytes(b"this is not a zip")
    with pytest.raises(UploadError, match="not a valid ZIP"):
        extract_zip(f, tmp_path / "out", LIMITS)


def test_rejects_case_collision_and_duplicates(tmp_path):
    z = write_zip(tmp_path / "c.zip", {"A.py": "x=1", "a.py": "x=2"})
    with pytest.raises(UploadError, match="case-colliding"):
        extract_zip(z, tmp_path / "out", LIMITS)


@pytest.mark.parametrize("name", ["con.py", "dir/NUL.txt", "bad:name.py", "what?.py", "trailing."])
def test_rejects_windows_unsafe_names(tmp_path, name):
    z = write_zip(tmp_path / "w.zip", {"ok.py": "x=1", name: "x"})
    with pytest.raises(UploadError):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_too_many_files(tmp_path):
    z = write_zip(tmp_path / "m.zip", {f"f{i}.py": "x=1" for i in range(6)})
    with pytest.raises(UploadError, match="Too many files"):
        extract_zip(z, tmp_path / "out", Limits(max_files=5))


def test_total_size_limit_blocks_zip_bomb(tmp_path):
    big = "x = 1\n" * 100_000  # ~600 KB each, compresses to almost nothing
    z = write_zip(tmp_path / "b.zip", {f"b{i}.py": big for i in range(5)})
    assert z.stat().st_size < 100_000  # tiny on disk, ~3 MB once expanded
    with pytest.raises(UploadError, match="expands beyond"):
        extract_zip(z, tmp_path / "out", Limits(max_uncompressed_bytes=1_000_000, max_file_bytes=700_000))


def test_oversized_file_is_skipped_not_extracted(tmp_path):
    z = write_zip(tmp_path / "o.zip", {"ok.py": "x=1", "huge.py": "y = 2\n" * 100_000})
    res = extract_zip(z, tmp_path / "out", Limits(max_file_bytes=10_000))
    assert res.files == ["ok.py"]
    assert any("larger than" in s["reason"] for s in res.skipped)


def test_requires_python_files(tmp_path):
    z = write_zip(tmp_path / "n.zip", {"readme.md": "hi", "x.txt": "y"})
    with pytest.raises(UploadError, match="No Python files"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_ignored_dirs_are_dropped(tmp_path):
    z = write_zip(tmp_path / "i.zip", {"a.py": "x=1", "__pycache__/a.pyc": b"x", ".git/config": "x", "venv/lib/x.py": "x=1", "__MACOSX/._a.py": "x"})
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert res.files == ["a.py"]


def test_write_python_files_checks(tmp_path):
    res = write_python_files([("a.py", b"x=1"), ("b.py", b"y=2")], tmp_path / "o", LIMITS)
    assert res.files == ["a.py", "b.py"]
    with pytest.raises(UploadError):
        write_python_files([("a.py", b"x"), ("A.py", b"y")], tmp_path / "o2", LIMITS)
    with pytest.raises(UploadError):
        write_python_files([("a.txt", b"x")], tmp_path / "o3", LIMITS)
    with pytest.raises(UploadError):
        write_python_files([("a.py", b"x" * 20)], tmp_path / "o4", Limits(max_file_bytes=10))
