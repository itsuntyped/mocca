"""Tests for the "file open in the editor" prompt block (chat continuity).

When the user has an artifact open in the side panel and asks for a change, the
chat route folds the file's current contents into the system prompt via
``_open_file_block`` so the model edits the real, possibly hand-edited state.
This guards that helper's shape: the delimiters the model keys on, the optional
name, and that the content is passed through verbatim (it can itself contain
fenced code, so we must NOT wrap it in backticks).
"""

from __future__ import annotations

import unittest

from src.routes.chat import _collapse_code_blocks, _open_file_block


class TestOpenFileBlock(unittest.TestCase):
    def test_includes_content_verbatim(self):
        content = "# Title\n\n```\nsome code\n```\n"
        block = _open_file_block("", content)
        # Content sits directly between plain-text delimiters - NOT wrapped in a
        # fence of our own (it can contain its own fences, as here).
        self.assertIn("--- BEGIN CURRENT FILE ---\n" + content + "\n--- END CURRENT FILE ---", block)

    def test_names_the_file_when_known(self):
        block = _open_file_block("README.md", "hello")
        self.assertIn("named README.md", block)

    def test_omits_name_when_unknown(self):
        block = _open_file_block("   ", "hello")
        self.assertNotIn("named", block)

    def test_instructs_full_file_reply(self):
        # The model must return the whole updated file, not just a diff.
        block = _open_file_block("a.py", "x = 1")
        self.assertIn("COMPLETE updated file", block)

    def test_instructs_no_file_on_chitchat(self):
        # Small talk / thanks must NOT re-emit the file (the "keeps generating" bug).
        block = _open_file_block("a.py", "x = 1")
        self.assertIn("do NOT output the file", block)


class TestCollapseCodeBlocks(unittest.TestCase):
    def test_collapses_large_block(self):
        # A big fenced block (the stale file copy) is replaced by a placeholder.
        text = "Here it is:\n```markdown\na\nb\nc\nd\ne\n```\nEnjoy!"
        out = _collapse_code_blocks(text)
        self.assertIn("Here it is:", out)
        self.assertIn("Enjoy!", out)
        self.assertNotIn("```", out)
        self.assertIn("omitted", out)

    def test_keeps_small_block(self):
        # A short inline snippet is left as-is.
        text = "Run:\n```\nls\n```\ndone"
        out = _collapse_code_blocks(text)
        self.assertEqual(out, text)

    def test_handles_four_backtick_wrapper_with_inner_fences(self):
        # The real failure case: a four-backtick wrapper whose body has its own
        # triple-backtick fences must collapse as ONE block, not split.
        text = "File:\n````markdown\n# T\n```\ncode\n```\nmore\n````\nbye"
        out = _collapse_code_blocks(text)
        self.assertIn("File:", out)
        self.assertIn("bye", out)
        self.assertEqual(out.count("omitted"), 1)
        self.assertNotIn("code", out)


if __name__ == "__main__":
    unittest.main()
