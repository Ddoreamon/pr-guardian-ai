"""Unit tests for the diff parser and git input validation."""

import pytest

from pr_guardian.git_reader import GitReaderError, parse_unified_diff, validate_ref
from pr_guardian.models import ChangeType

SAMPLE = """\
diff --git a/src/a.py b/src/a.py
index 1..2 100644
--- a/src/a.py
+++ b/src/a.py
@@ -2,0 +3,2 @@ def f():
+    x = 1
+    y = 2
@@ -10 +12 @@ def g():
-    old
+    new
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+print("hi")
diff --git a/src/gone.py b/src/gone.py
deleted file mode 100644
--- a/src/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-bye
"""


def test_parses_added_and_modified_line_numbers():
    files = {f.file_path: f for f in parse_unified_diff(SAMPLE)}
    a = files["src/a.py"]
    assert a.change_type == ChangeType.MODIFIED
    assert a.added_lines == [3, 4, 12]  # new-file line numbers, not old ones
    assert a.deleted_lines == [10]


def test_detects_added_and_deleted_files():
    files = {f.file_path: f for f in parse_unified_diff(SAMPLE)}
    assert files["src/new.py"].change_type == ChangeType.ADDED
    assert files["src/new.py"].added_lines == [1]
    assert files["src/gone.py"].change_type == ChangeType.DELETED


def test_language_detection():
    files = {f.file_path: f for f in parse_unified_diff(SAMPLE)}
    assert files["src/a.py"].language == "python"


def test_empty_diff_returns_no_files():
    assert parse_unified_diff("") == []


@pytest.mark.parametrize("ref", ["main", "feature/x-1", "HEAD~1", "v1.0.0"])
def test_accepts_safe_refs(ref):
    assert validate_ref(ref) == ref


@pytest.mark.parametrize(
    "ref",
    ["--output=/tmp/x", "main; rm -rf /", "a b", "$(whoami)", "", "x" * 300, "main`id`"],
)
def test_rejects_unsafe_refs(ref):
    with pytest.raises(GitReaderError):
        validate_ref(ref)
