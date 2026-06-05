"""Tests for turning a model reply into session documents (routes/chat.py).

Two layers: the pure block detector (_extract_file_blocks) that mirrors the
frontend's artifact rules, and _write_back_documents which applies them to the
database (an edit updates the matching file, a new file is created, an unlabelled
block edits the single doc or seeds one from scratch). Also keeps the
_collapse_code_blocks coverage, which still strips stale file copies from history.
"""

from __future__ import annotations

import unittest

from src import database
from src.routes.chat import (
    _collapse_code_blocks,
    _derive_filename,
    _extract_file_blocks,
    _slug_from_content,
    _write_back_documents,
)


class TestExtractFileBlocks(unittest.TestCase):
    def test_named_block(self):
        reply = "Here:\n```notes.md\n# T\na\nb\nc\nd\ne\n```\ndone"
        blocks = _extract_file_blocks(reply)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][0], "notes.md")
        self.assertIn("# T", blocks[0][1])

    def test_language_only_block(self):
        reply = "```json\n{\n\"a\": 1,\n\"b\": 2,\n\"c\": 3,\n\"d\": 4\n}\n```"
        blocks = _extract_file_blocks(reply)
        self.assertEqual(len(blocks), 1)
        self.assertIsNone(blocks[0][0])
        self.assertEqual(blocks[0][2], "json")

    def test_short_block_is_ignored(self):
        # Under MIN lines: stays inline, not a document.
        reply = "```py\nx = 1\n```"
        self.assertEqual(_extract_file_blocks(reply), [])

    def test_four_backtick_wrapper_with_inner_fences(self):
        reply = "````markdown\n# T\n```\ncode\n```\nmore text\nlast\n````"
        blocks = _extract_file_blocks(reply)
        self.assertEqual(len(blocks), 1)
        # The inner triple fences are part of the one document, not separate blocks.
        self.assertIn("code", blocks[0][1])


class TestWriteBack(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]

    def _body(self, lines=8):
        return "\n".join(f"line {i}" for i in range(lines))

    def test_edit_updates_matching_document(self):
        database.create_document(self.sid, "notes.md", "old content")
        reply = f"Updated:\n```notes.md\n{self._body()}\n```"
        self.assertTrue(_write_back_documents(self.sid, reply))
        doc = database.get_document_by_filename(self.sid, "notes.md")
        self.assertIn("line 0", doc["content"])

    def test_new_named_file_is_created(self):
        reply = f"```new.md\n{self._body()}\n```"
        self.assertTrue(_write_back_documents(self.sid, reply))
        doc = database.get_document_by_filename(self.sid, "new.md")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["source"], "assistant")

    def test_unlabelled_updates_single_document(self):
        d = database.create_document(self.sid, "only.md", "old")
        reply = f"```markdown\n{self._body()}\n```"
        self.assertTrue(_write_back_documents(self.sid, reply))
        self.assertIn("line 0", database.get_document(d["id"])["content"])

    def test_unlabelled_from_scratch_creates_derived_name(self):
        reply = f"```markdown\n{self._body()}\n```"
        self.assertTrue(_write_back_documents(self.sid, reply))
        docs = database.list_documents(self.sid)
        self.assertEqual(len(docs), 1)
        self.assertTrue(docs[0]["filename"].endswith(".md"))

    def test_unlabelled_from_scratch_uses_title(self):
        # A markdown H1 becomes the filename, so the file is referable later.
        reply = "```markdown\n# Acme Project\n\nintro\nmore\nlines\nhere\nyes\n```"
        self.assertTrue(_write_back_documents(self.sid, reply))
        docs = database.list_documents(self.sid)
        self.assertEqual(docs[0]["filename"], "acme-project.md")

    def test_unlabelled_with_several_docs_is_skipped(self):
        database.create_document(self.sid, "a.md", "x")
        database.create_document(self.sid, "b.md", "y")
        reply = f"```markdown\n{self._body()}\n```"
        # Ambiguous which to edit - leave them all alone.
        self.assertFalse(_write_back_documents(self.sid, reply))

    def test_no_blocks_returns_false(self):
        self.assertFalse(_write_back_documents(self.sid, "just a plain reply"))


class TestDeriveFilename(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]

    def test_slug_from_h1(self):
        self.assertEqual(_slug_from_content("# Acme Project\n\nbody"), "acme-project")

    def test_slug_skips_codey_first_line(self):
        self.assertEqual(_slug_from_content('{"a": 1}\nmore'), "")

    def test_derive_uses_title(self):
        self.assertEqual(_derive_filename(self.sid, "md", "# My Notes\n"), "my-notes.md")

    def test_derive_falls_back_to_document(self):
        self.assertEqual(_derive_filename(self.sid, "txt", "x = 1; y = 2;"), "document.txt")

    def test_derive_dedupes(self):
        database.create_document(self.sid, "my-notes.md", "x")
        self.assertEqual(_derive_filename(self.sid, "md", "# My Notes\n"), "my-notes-2.md")


class TestCollapseCodeBlocks(unittest.TestCase):
    def test_collapses_large_block(self):
        text = "Here it is:\n```markdown\na\nb\nc\nd\ne\n```\nEnjoy!"
        out = _collapse_code_blocks(text)
        self.assertIn("Here it is:", out)
        self.assertIn("Enjoy!", out)
        self.assertNotIn("```", out)
        self.assertIn("omitted", out)

    def test_keeps_small_block(self):
        text = "Run:\n```\nls\n```\ndone"
        self.assertEqual(_collapse_code_blocks(text), text)

    def test_handles_four_backtick_wrapper_with_inner_fences(self):
        text = "File:\n````markdown\n# T\n```\ncode\n```\nmore\n````\nbye"
        out = _collapse_code_blocks(text)
        self.assertIn("File:", out)
        self.assertIn("bye", out)
        self.assertEqual(out.count("omitted"), 1)
        self.assertNotIn("code", out)


if __name__ == "__main__":
    unittest.main()
