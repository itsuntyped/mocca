"""In-depth tests for the read-only file tools.

The single most important property here is the sandbox: these tools must only
ever see inside ``data/files/`` and must reject any path that tries to escape it
(``..`` traversal, an absolute path). We also cover listing (empty and
populated), reading, the missing-file error, and the large-file truncation.

The test runner points MOCCA_DATA_DIR at a throwaway temp dir, so FILES_DIR here
is a sandbox we can freely create and delete files in (see scripts/test.py).
"""

from __future__ import annotations

import unittest

from src.paths import FILES_DIR, ensure_dirs
from src.tools import files
from src.tools.base import ToolError


class TestFileTools(unittest.TestCase):
    def setUp(self):
        # Start each test from a clean, existing files folder.
        ensure_dirs()
        self._clear_files_dir()

    def tearDown(self):
        self._clear_files_dir()

    def _clear_files_dir(self):
        root = FILES_DIR.resolve()
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()

    def _write(self, rel: str, text: str):
        path = FILES_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # Listing.
    def test_list_empty(self):
        out = files._list_run({})
        self.assertIn("No files available", out)

    def test_list_sorted_relative(self):
        self._write("b.txt", "b")
        self._write("a.txt", "a")
        self._write("sub/c.txt", "c")
        out = files._list_run({})
        self.assertIn("Available files:", out)
        listed = out.splitlines()[1:]
        # Forward slashes even on Windows, and sorted.
        self.assertEqual(listed, ["a.txt", "b.txt", "sub/c.txt"])

    # Reading.
    def test_read_file(self):
        self._write("notes.txt", "hello world")
        self.assertEqual(files._read_run({"path": "notes.txt"}), "hello world")

    def test_read_nested_file(self):
        self._write("sub/deep.txt", "nested")
        self.assertEqual(files._read_run({"path": "sub/deep.txt"}), "nested")

    def test_read_missing_file(self):
        with self.assertRaises(ToolError):
            files._read_run({"path": "nope.txt"})

    def test_read_empty_path(self):
        with self.assertRaises(ToolError):
            files._read_run({"path": ""})

    def test_truncation(self):
        big = "x" * (files._MAX_CHARS + 500)
        self._write("big.txt", big)
        out = files._read_run({"path": "big.txt"})
        self.assertIn("[truncated at", out)
        self.assertLess(len(out), len(big))

    # Sandbox: traversal and absolute paths must be rejected.
    def test_parent_traversal_rejected(self):
        with self.assertRaises(ToolError):
            files._read_run({"path": "../mocca.db"})

    def test_deep_traversal_rejected(self):
        with self.assertRaises(ToolError):
            files._read_run({"path": "../../etc/passwd"})

    def test_absolute_path_rejected(self):
        # An absolute path resolves outside the files folder and is rejected.
        with self.assertRaises(ToolError):
            files._read_run({"path": "C:/Windows/win.ini"})

    def test_tool_metadata(self):
        names = {t.name for t in files.TOOLS}
        self.assertEqual(names, {"list_files", "read_file"})
        for t in files.TOOLS:
            self.assertEqual(t.category, "files")
            self.assertTrue(t.is_local)


if __name__ == "__main__":
    unittest.main()
