"""Finding model: static vs runtime labelling, rules, grouping, ordering."""
import pytest

from autopatch.core import findings as F
from autopatch.core import runtime_findings as RF
from autopatch.core.analysis import group_duplicates
from autopatch.core.findings import Ctx


@pytest.fixture()
def ctx(tmp_path):
    (tmp_path / "util.py").write_text("def add(a, b):\n    return a - b\n")
    return Ctx(original_dir=tmp_path, project_files={"util.py", "test_util.py"}, test_files={"test_util.py"},
               imported_files={"util.py"}, entry_files=set(), module_names={"util", "test_util"}, python_version="3.13",
               functions={"add": ("util.py", 1)}, func_ranges={"util.py": [(1, 2, "add")]})


def test_every_rule_in_the_knowledge_base_is_well_formed():
    for code, rule in F.RULES.items():
        assert rule["category"] in F.CATEGORY_LABELS, code
        assert rule["severity"] in F.SEVERITIES and rule["confidence"] in F.CONFIDENCES, code
        assert all(rule[k].strip() for k in ("title", "root_cause", "trigger")), code


def test_rule_lookup_uses_longest_prefix_and_has_a_default():
    assert F.lookup_rule("F501")["title"] == F.lookup_rule("F5")["title"]
    assert F.lookup_rule("PLE0101")["category"] == "likely_runtime_error"
    assert F.lookup_rule("NOPE999")["severity"] == "Low" and F.lookup_rule(None)["severity"] == "Low"


def test_new_finding_rejects_invalid_labels():
    base = dict(category="security", severity="High", confidence="High", confirmation="static", title="t",
                description="d", root_cause="r", trigger="g", detection_method="m", evidence={})
    for bad in ({"severity": "Severe"}, {"confidence": "Certain"}, {"confirmation": "maybe"}, {"category": "other"}):
        with pytest.raises(AssertionError):
            F.new_finding(**{**base, **bad})


def test_all_static_builders_say_static_and_never_runtime(ctx):
    items = [
        F.from_syntax_error({"msg": "invalid syntax", "line": 1}, "util.py", ctx),
        F.from_ruff({"code": "F821", "message": "Undefined name", "file": "util.py", "line": 2}, ctx),
        F.from_pattern({"code": "AP002", "message": "div by zero", "file": "util.py", "line": 2, "function": "add"}, ctx),
        F.from_bandit({"code": "B307", "test_name": "eval", "message": "eval", "file": "util.py", "line": 2,
                       "bandit_severity": "HIGH", "bandit_confidence": "HIGH", "cwe": 78}, ctx),
    ]
    for f in items:
        assert f["confirmation"] == "static" and f["evidence"]["type"] == "static", f["title"]
        assert "not confirmed" in f["confirmation_label"].lower()
        assert "static" in f["detection_method"].lower() or "parse" in f["detection_method"].lower()


def test_runtime_builders_say_runtime(ctx):
    crash = {"text": "t", "message": "x", "path": "util.py", "lineno": 2,
             "frames": [{"path": "test_util.py", "lineno": 3, "lines": ["    assert add(1, 2) == 3"]}]}
    fail = {"nodeid": "test_util.py::test_add", "outcome": "failed", "phase": "call", "crash": crash,
            "exc": {"type": "AssertionError", "message": "assert -1 == 3"}, "captured": []}
    items = [RF.from_test_failure(fail, ctx), RF.from_collect_error({"nodeid": "test_util.py", "crash": crash,
             "exc": {"type": "ImportError", "message": "x"}}, ctx), RF.from_hang("test_util.py::test_x", 5, ctx),
             RF.from_missing_package("requests", ["test_util.py"], ctx)]
    for f in items:
        assert f["confirmation"] == "runtime" and f["evidence"]["type"] == "runtime"


def test_assertion_failure_is_honest_about_what_it_can_know(ctx):
    crash = {"text": "t", "message": "assert -1 == 3", "path": "test_util.py", "lineno": 3,
             "frames": [{"path": "test_util.py", "lineno": 3, "lines": ["    assert add(1, 2) == 3", "E   assert -1 == 3"]}]}
    f = RF.from_test_failure({"nodeid": "test_util.py::test_add", "outcome": "failed", "phase": "call", "crash": crash,
                              "exc": {"type": "AssertionError", "message": "assert -1 == 3"}, "captured": []}, ctx)
    assert f["category"] == "test_failure" and f["confidence"] == "Medium"
    assert "expectation is wrong" in f["root_cause"]  # the test itself might be the thing that is wrong
    assert (f["file"], f["line"], f["function"]) == ("util.py", 1, "add")
    assert "heuristic" in f["root_cause"]
    assert f["evidence"]["code_under_test"]["function"] == "add"


def test_snippet_cannot_read_outside_the_project(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "secret.py").write_text("SECRET = 1\n")
    assert F.make_snippet(tmp_path / "proj", "../secret.py", 1) is None
    assert F.make_snippet(tmp_path / "proj", "missing.py", 1) is None
    (tmp_path / "proj" / "a.py").write_text("x = 1\n")
    assert F.make_snippet(tmp_path / "proj", "a.py", 99) is None
    assert F.make_snippet(tmp_path / "proj", "a.py", 1)["lines"] == ["x = 1"]


def test_bandit_high_severity_and_confidence_is_critical_but_still_static(ctx):
    item = {"code": "B602", "test_name": "subprocess_popen_with_shell_equals_true", "message": "shell=True", "file": "util.py",
            "line": 1, "bandit_severity": "HIGH", "bandit_confidence": "HIGH", "cwe": 78}
    f = F.from_bandit(item, ctx)
    assert (f["severity"], f["confirmation"]) == ("Critical", "static")
    assert "does not prove" in f["trigger"]  # pattern match, not a proven exploit
    assert F.from_bandit({**item, "bandit_confidence": "MEDIUM"}, ctx)["severity"] == "High"


def test_finalize_orders_by_severity_assigns_unique_ids_and_cross_links(ctx):
    static = F.from_ruff({"code": "F821", "message": "Undefined", "file": "util.py", "line": 2}, ctx)
    runtime = F.new_finding(category="runtime_exception", severity="High", confidence="High", confirmation="runtime",
                            title="r", description="d", root_cause="r", trigger="t", detection_method="pytest execution (runtime)",
                            evidence={}, file="util.py", line=2)
    low = F.from_ruff({"code": "F841", "message": "unused", "file": "util.py", "line": 9}, ctx)
    out = F.finalize([low, static, runtime], "abcdef123456", "p" * 32)
    assert [f["severity"] for f in out] == ["High", "High", "Low"]
    assert len({f["_id"] for f in out}) == 3 and len({f["finding_id"] for f in out}) == 3
    assert out[0]["finding_id"].startswith("abcdef-F")
    pair = [f for f in out if f["file"] == "util.py" and f["line"] == 2]
    assert pair[0]["related"] == [pair[1]["finding_id"]] and pair[1]["related"] == [pair[0]["finding_id"]]
    assert out[2]["related"] == []


def test_group_duplicates_merges_same_site_exceptions_only(ctx):
    def exc_finding(nodeid, line=2):
        return F.new_finding(category="runtime_exception", severity="High", confidence="High", confirmation="runtime",
                             title="Unhandled ZeroDivisionError", description="d", root_cause="r", trigger="t",
                             detection_method="pytest execution (runtime)", evidence={"nodeid": nodeid},
                             file="calc.py", line=line, rule="ZeroDivisionError")
    out = group_duplicates([exc_finding("t::a"), exc_finding("t::b"), exc_finding("t::c", line=9)])
    assert len(out) == 2
    assert out[0]["tests"] == ["t::a", "t::b"] and out[0]["occurrences"] == 2 and "2 tests fail the same way" in out[0]["description"]
    assert out[1]["occurrences"] == 1


def test_summarize_counts(ctx):
    items = F.finalize([F.from_ruff({"code": "F821", "message": "u", "file": "util.py", "line": 2}, ctx),
                        F.from_ruff({"code": "F841", "message": "u", "file": "util.py", "line": 1}, ctx)], "r" * 32, "p" * 32)
    s = F.summarize(items)
    assert s["total"] == 2 and s["by_severity"]["High"] == 1 and s["by_severity"]["Low"] == 1
    assert s["by_confirmation"] == {"runtime": 0, "static": 2}
