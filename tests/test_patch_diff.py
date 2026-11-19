"""Diff parsing, validation and safe in-memory application."""
import pytest

from autopatch.core.patch_diff import Limits, PatchError, parse_unified_diff, plan_patch

SRC = b"def add(a, b):\n    return a - b\n\ndef other():\n    return 1\n"
FILES = {"util.py": SRC, "extra.py": b"x = 1\n", "test_util.py": b"def test_x():\n    pass\n", "notes.txt": b"hi\n"}
ALLOWED = {"util.py", "extra.py", "test_util.py", "notes.txt"}
TESTS = {"test_util.py"}
GOOD = "--- a/util.py\n+++ b/util.py\n@@ -1,3 +1,3 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n \n"


def plan(diff, files=FILES, allowed=ALLOWED, **lim):
    return plan_patch(diff, files, allowed, TESTS, Limits(**lim))


def test_good_patch_applies_in_memory_and_yields_canonical_diff():
    p = plan(GOOD)
    assert p.changes[0].after == b"def add(a, b):\n    return a + b\n\ndef other():\n    return 1\n"
    assert (p.added, p.removed) == (1, 1) and "+    return a + b" in p.diff and len(p.sha256) == 64
    assert FILES["util.py"] == SRC  # input untouched


def test_wrong_line_numbers_are_tolerated_but_context_must_match():
    shifted = GOOD.replace("@@ -1,3 +1,3 @@", "@@ -40,3 +40,3 @@")
    assert plan(shifted).changes[0].after.count(b"a + b") == 1
    with pytest.raises(PatchError, match="context does not match"):
        plan(GOOD.replace(" def add(a, b):", " def add(x, y):"))


def test_wrong_header_counts_are_corrected_with_a_warning():
    p = plan(GOOD.replace("@@ -1,3 +1,3 @@", "@@ -1,9 +1,9 @@"))
    assert any("counts" in w for w in p.warnings)


def test_code_fences_and_git_preamble_are_accepted():
    wrapped = "```diff\ndiff --git a/util.py b/util.py\nindex 111..222 100644\n" + GOOD + "```"
    assert plan(wrapped).changes[0].path == "util.py"


@pytest.mark.parametrize("diff,msg", [
    ("", "did not include a diff"),
    ("hello world", "Unexpected text"),
    ("--- a/util.py\n+++ b/util.py\n", "no hunks"),
    ("--- a/util.py\n+++ b/util.py\n@@ nonsense @@\n x\n", "Malformed hunk header"),
    ("--- a/util.py\n+++ b/util.py\n@@ -1,1 +1,1 @@\n?bad line\n", "Malformed diff line"),
    ("--- a/util.py\n+++ b/util.py\n@@ -1,1 +1,1 @@\n def add(a, b):\n", "no changes"),
    ("@@ -1,1 +1,1 @@\n-x\n+y\n", "before any file header"),
    ("--- a/util.py\n+++ b/other.py\n@@ -1,1 +1,1 @@\n-x\n+y\n", "renames"),
    ("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+x = 1\n", "creates or deletes"),
    ("--- a/util.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x\n", "creates or deletes"),
    ("new file mode 100644\n" + GOOD, "creates, deletes, renames"),
    ("deleted file mode 100644\n" + GOOD, "creates, deletes, renames"),
    ("rename from a.py\nrename to b.py\n", "creates, deletes, renames"),
    ("Binary files a/x and b/x differ\n", "creates, deletes, renames"),
    (GOOD + GOOD, "more than once"),
])
def test_malformed_or_forbidden_diffs_are_rejected(diff, msg):
    with pytest.raises(PatchError, match=msg):
        plan(diff)


@pytest.mark.parametrize("path", ["../evil.py", "a/../../evil.py", "/etc/passwd", "C:/x.py", "..\\x.py", "sub\\x.py", "~/x.py", "a/b/../../../x.py"])
def test_path_traversal_and_unsafe_paths_are_rejected(path):
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    diff = diff.replace("a//", "/") if path.startswith("/") else diff
    with pytest.raises(PatchError, match="Unsafe path|unrelated|does not exist"):
        plan(diff, allowed=ALLOWED | {path})  # even if "allowed", traversal is refused first


def test_unrelated_test_nonpython_and_missing_files_are_rejected():
    def d(p):
        return f"--- a/{p}\n+++ b/{p}\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    with pytest.raises(PatchError, match="unrelated"):
        plan(d("extra.py"), allowed={"util.py"})
    with pytest.raises(PatchError, match="test file"):
        plan(d("test_util.py"))
    with pytest.raises(PatchError, match="not a Python file"):
        plan(d("notes.txt"))
    with pytest.raises(PatchError, match="does not exist"):
        plan(d("ghost.py"), allowed=ALLOWED | {"ghost.py"})


def test_size_limits():
    with pytest.raises(PatchError, match="limit is 1"):
        plan(GOOD + "--- a/extra.py\n+++ b/extra.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n", max_files=1)
    with pytest.raises(PatchError, match="changes 2 lines"):
        plan(GOOD, max_changed_lines=1)
    with pytest.raises(PatchError, match="larger than"):
        plan(GOOD, max_diff_bytes=10)


def test_result_must_be_valid_python():
    bad = "--- a/util.py\n+++ b/util.py\n@@ -1,3 +1,3 @@\n def add(a, b):\n-    return a - b\n+    return a +\n \n"
    with pytest.raises(PatchError, match="syntax error"):
        plan(bad)


def test_addition_only_hunk_without_context_is_refused():
    with pytest.raises(PatchError, match="no context"):
        plan("--- a/util.py\n+++ b/util.py\n@@ -1,0 +1,1 @@\n+import os\n")


def test_crlf_bom_and_missing_final_newline_are_preserved():
    crlf = b"def add(a, b):\r\n    return a - b\r\n"
    short = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
    out = plan(short, files={**FILES, "util.py": crlf}).changes[0].after
    assert out == b"def add(a, b):\r\n    return a + b\r\n"
    bom = b"\xef\xbb\xbf" + SRC
    assert plan(GOOD, files={**FILES, "util.py": bom}).changes[0].after.startswith(b"\xef\xbb\xbf")
    nonl = b"def add(a, b):\n    return a - b"
    d = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n\\ No newline at end of file\n+    return a + b\n\\ No newline at end of file\n"
    assert plan(d, files={**FILES, "util.py": nonl}).changes[0].after == b"def add(a, b):\n    return a + b"


def test_undecodable_and_stray_cr_files_are_refused():
    with pytest.raises(PatchError, match="UTF-8"):
        plan(GOOD, files={**FILES, "util.py": b"def add(a, b):\n    return a - b\n# \xff\xfe\n"})
    with pytest.raises(PatchError, match="stray carriage return"):
        plan(GOOD, files={**FILES, "util.py": b"def add(a, b):\r    return a - b\r"})


def test_multi_hunk_and_multi_file_patch():
    src = b"a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\ng = 7\nh = 8\ni = 9\nj = 10\n"
    d = ("--- a/util.py\n+++ b/util.py\n@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 20\n c = 3\n"
         "@@ -8,3 +8,3 @@\n h = 8\n-i = 9\n+i = 90\n j = 10\n"
         "--- a/extra.py\n+++ b/extra.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n")
    p = plan(d, files={**FILES, "util.py": src})
    assert [c.path for c in p.changes] == ["util.py", "extra.py"] and (p.added, p.removed) == (3, 3)


def test_parse_exposes_structure():
    fp = parse_unified_diff(GOOD)[0]
    assert fp.path == "util.py" and [t for t, _ in fp.hunks[0].lines].count("-") == 1


def test_mixed_line_endings_are_patched_and_untouched_lines_keep_theirs():
    mixed = b"def add(a, b):\r\n    return a - b\n\ndef other():\r\n    return 1\r\n"
    p = plan(GOOD, files={**FILES, "util.py": mixed})
    after = p.changes[0].after
    assert after.startswith(b"def add(a, b):\r\n") and after.endswith(b"def other():\r\n    return 1\r\n")  # untouched lines unchanged
    assert any("mixed line endings" in w for w in p.warnings)


def test_pure_crlf_file_with_crlf_diff_and_multiple_hunks():
    src = b"".join(f"v{i} = {i}\r\n".encode() for i in range(1, 11))
    d = ("--- a/util.py\r\n+++ b/util.py\r\n@@ -1,3 +1,3 @@\r\n v1 = 1\r\n-v2 = 2\r\n+v2 = 20\r\n v3 = 3\r\n"
         "@@ -8,3 +8,3 @@\r\n v8 = 8\r\n-v9 = 9\r\n+v9 = 90\r\n v10 = 10\r\n")
    after = plan(d, files={**FILES, "util.py": src}).changes[0].after
    assert after.count(b"\r\n") == 10 and after.count(b"\n") == 10 and b"v2 = 20\r\n" in after and b"v9 = 90\r\n" in after


def test_appending_after_a_last_line_without_newline_adds_the_missing_terminator():
    src = b"def add(a, b):\n    return a - b"
    d = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,3 @@\n def add(a, b):\n     return a - b\n+    # note\n"
    after = plan(d, files={**FILES, "util.py": src}).changes[0].after
    assert after == b"def add(a, b):\n    return a - b\n    # note\n"
