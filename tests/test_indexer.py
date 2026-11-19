from autopatch.core.indexer import index_project, module_name_for


def make(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_module_names():
    assert module_name_for("a/b/c.py") == "a.b.c"
    assert module_name_for("a/b/__init__.py") == "a.b"
    assert module_name_for("top.py") == "top"
    assert module_name_for("__init__.py") == ""


def test_full_index(tmp_path):
    root = make(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/core.py": "import os\nimport requests\nfrom . import helpers\nfrom .helpers import slug\n\n"
                       "class Calc:\n    def add(self, a, b):\n        return a + b\n\n"
                       "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
        "pkg/helpers.py": "from pkg import core\n\ndef slug(s):\n    \"\"\"doc\"\"\"\n    return s\n",
        "tests/test_core.py": "import unittest\nfrom pkg.core import Calc\n\n"
                              "def test_add():\n    assert Calc().add(1, 2) == 3\n\n"
                              "class TestMore:\n    def test_x(self):\n        pass\n    def helper(self):\n        pass\n\n"
                              "class T(unittest.TestCase):\n    def test_y(self):\n        pass\n",
        "tests/conftest.py": "",
        "broken.py": "def oops(:\n    pass\n",
        "requirements.txt": "requests==2.0\nflask>=3  # web\n-r other.txt\n",
    })
    m = index_project(root)
    assert m["totals"]["python_files"] == 6  # __init__, core, helpers, test_core, conftest, broken
    by ={f["path"]: f for f in m["files"]}

    # functions / classes
    core = by["pkg/core.py"]
    assert {f["qualname"] for f in core["functions"]} == {"Calc.add", "main"}
    assert core["classes"][0]["name"] == "Calc" and core["classes"][0]["methods"] == ["add"]
    assert core["has_main"] is True
    assert by["pkg/helpers.py"]["functions"][0]["has_docstring"] is True

    # imports classified
    kinds = {(r["module"], r["kind"]) for r in core["imports"]}
    assert ("os", "stdlib") in kinds
    assert ("requests", "third_party") in kinds
    assert (".", "internal") in kinds or (".helpers", "internal") in kinds

    # third-party summary + requirements
    tp = {t["name"]: t for t in m["third_party"]}
    assert tp["requests"]["declared_in_requirements"] is True
    assert m["declared_requirements"] == ["flask", "requests"]

    # graph: core <-> helpers is a cycle
    assert ["pkg.core", "pkg.helpers"] in m["graph"]["cycles"]
    assert any(e == {"source": "pkg.core", "target": "pkg.helpers"} for e in m["graph"]["edges"])

    # syntax errors are reported, not raised
    assert any(e["path"] == "broken.py" for e in m["syntax_errors"])

    # entry points and tests (static)
    assert "pkg/core.py" in m["entry_points"]
    ids = set(m["tests"]["ids"])
    assert ids == {
        "tests/test_core.py::test_add",
        "tests/test_core.py::TestMore::test_x",
        "tests/test_core.py::T::test_y",
    }
    assert m["tests"]["has_conftest"] is True
    assert m["tests"]["files"] == ["tests/test_core.py"]  # conftest is not a test file


def test_relative_imports_in_nested_packages(tmp_path):
    root = make(tmp_path, {
        "a/__init__.py": "",
        "a/b/__init__.py": "",
        "a/b/c.py": "from .. import top\nfrom ..top import f\nfrom . import d\n",
        "a/b/d.py": "",
        "a/top.py": "def f(): pass\n",
    })
    m = index_project(root)
    edges = {(e["source"], e["target"]) for e in m["graph"]["edges"]}
    assert ("a.b.c", "a.top") in edges
    assert ("a.b.c", "a.b.d") in edges
    assert not m["unresolved_imports"]


def test_src_layout_alias(tmp_path):
    root = make(tmp_path, {"src/shop/__init__.py": "", "src/shop/cart.py": "", "tests/test_cart.py": "import shop.cart\n"})
    m = index_project(root)
    assert {"source": "tests.test_cart", "target": "src.shop.cart"} in m["graph"]["edges"]


def test_undecodable_and_null_byte_files_do_not_crash(tmp_path):
    (tmp_path / "bad.py").write_bytes(b"x = '\xff\xfe'\n# coding: ascii\n")
    (tmp_path / "nul.py").write_bytes(b"x = 1\x00\n")
    (tmp_path / "ok.py").write_text("x = 1\n")
    m = index_project(tmp_path)
    errs = {e["path"] for e in m["syntax_errors"]}
    assert errs == {"bad.py", "nul.py"}


def test_deeply_nested_source_does_not_crash(tmp_path):
    (tmp_path / "deep.py").write_text("x = " + "(" * 5000 + "1" + ")" * 5000 + "\n")
    (tmp_path / "ok.py").write_text("y = 2\n")
    m = index_project(tmp_path)  # must not raise
    assert any(f["path"] == "ok.py" and not f["error"] for f in m["files"])


def test_indexer_does_not_execute_code(tmp_path):
    marker = tmp_path / "executed.txt"
    (tmp_path / "evil.py").write_text(f"open({str(marker)!r}, 'w').write('pwned')\n")
    index_project(tmp_path)
    assert not marker.exists()
