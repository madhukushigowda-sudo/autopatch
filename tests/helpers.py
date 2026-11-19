"""Test helpers: build ZIPs in memory (including malicious ones), upload projects, wait for runs."""
import io
import time
import zipfile

import pytest

from autopatch.core import tools

needs_ruff = pytest.mark.skipif(tools._find("ruff") is None, reason="Ruff is not installed in this environment")
needs_bandit = pytest.mark.skipif(tools._find("bandit") is None, reason="Bandit is not installed in this environment")


def upload_files(client, files: dict, name="proj.zip") -> str:
    """Upload {path: text} as a ZIP through the real API and return the project id."""
    r = client.post("/api/projects", data={"files": (io.BytesIO(make_zip(files)), name)}, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["_id"]


def wait_for_run(client, run_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").get_json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(0.1)
    raise AssertionError("analysis did not finish in time")


def make_zip(entries: dict, *, infos=()):
    """entries: {name: str|bytes}. infos: extra (ZipInfo, data) pairs for crafted entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for info, data in infos:
            zf.writestr(info, data)
    return buf.getvalue()


def write_zip(path, entries, **kw):
    path.write_bytes(make_zip(entries, **kw))
    return path


def mark_all_entries_encrypted(zip_bytes: bytes) -> bytes:
    """zipfile clears the 'encrypted' flag when writing, so set it in the central directory by hand."""
    data = bytearray(zip_bytes)
    pos = data.find(b"PK\x01\x02")
    while pos != -1:
        data[pos + 8] |= 0x01  # general-purpose bit flag, bit 0 = encrypted
        pos = data.find(b"PK\x01\x02", pos + 4)
    return bytes(data)


# A zip whose single top-level folder is a PACKAGE (has __init__.py): must not be stripped.
GOOD_PROJECT = {
    "proj/app.py": "from . import util\n\ndef main():\n    return util.add(1, 2)\n\nif __name__ == '__main__':\n    main()\n",
    "proj/util.py": "def add(a, b):\n    return a + b\n",
    "proj/__init__.py": "",
    "proj/tests/test_util.py": "from proj.util import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "proj/requirements.txt": "requests==2.31\n",
    "proj/data.bin": b"\x00\x01\x02",
}

# A typical repository zip: one wrapper folder that is NOT a package -> wrapper is stripped.
WRAPPED_PROJECT = {
    "myrepo/app.py": "import util\n\ndef main():\n    return util.add(1, 2)\n",
    "myrepo/util.py": "import requests\n\ndef add(a, b):\n    return a + b\n",
    "myrepo/test_util.py": "from util import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "myrepo/requirements.txt": "requests==2.31\n",
}
