"""Tests for the documents storage layer (database.py).

Documents are per-session text files. We cover the filename guard (the name is
user-supplied and shown/used as a tab and download name), the CRUD helpers and
their ordering/lookup semantics, and the cascade that drops a chat's documents
when the chat is deleted.
"""

from __future__ import annotations

import unittest

from src import database


class TestSafeFilename(unittest.TestCase):
    def test_strips_directory_components(self):
        self.assertEqual(database.safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(database.safe_filename("a/b/c.txt"), "c.txt")
        self.assertEqual(database.safe_filename(r"a\b\c.txt"), "c.txt")

    def test_dots_only_falls_back(self):
        self.assertEqual(database.safe_filename(".."), "document.txt")
        self.assertEqual(database.safe_filename("   "), "document.txt")
        self.assertEqual(database.safe_filename(""), "document.txt")

    def test_drops_control_chars(self):
        self.assertEqual(database.safe_filename("a\x01b.txt"), "ab.txt")

    def test_keeps_plain_name(self):
        self.assertEqual(database.safe_filename("notes.md"), "notes.md")

    def test_bounds_length(self):
        name = "x" * 500 + ".md"
        self.assertLessEqual(len(database.safe_filename(name)), 200)


class TestDocumentCrud(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]

    def test_create_and_list_ordering(self):
        a = database.create_document(self.sid, "a.md", "AA")
        b = database.create_document(self.sid, "b.md", "BB")
        docs = database.list_documents(self.sid)
        self.assertEqual([d["id"] for d in docs], [a["id"], b["id"]])
        self.assertEqual(docs[0]["content"], "AA")
        self.assertEqual(docs[0]["source"], "upload")

    def test_create_sanitises_filename(self):
        doc = database.create_document(self.sid, "../secret.txt", "x")
        self.assertEqual(doc["filename"], "secret.txt")

    def test_get_document(self):
        doc = database.create_document(self.sid, "a.md", "x")
        self.assertEqual(database.get_document(doc["id"])["content"], "x")
        self.assertIsNone(database.get_document("nope"))

    def test_get_by_filename_case_insensitive(self):
        database.create_document(self.sid, "Notes.MD", "hello")
        found = database.get_document_by_filename(self.sid, "notes.md")
        self.assertIsNotNone(found)
        self.assertEqual(found["content"], "hello")

    def test_get_by_filename_newest_wins(self):
        database.create_document(self.sid, "dup.md", "old")
        newer = database.create_document(self.sid, "dup.md", "new")
        found = database.get_document_by_filename(self.sid, "dup.md")
        self.assertEqual(found["id"], newer["id"])
        self.assertEqual(found["content"], "new")

    def test_update_document(self):
        doc = database.create_document(self.sid, "a.md", "x")
        self.assertTrue(database.update_document(doc["id"], "y"))
        self.assertEqual(database.get_document(doc["id"])["content"], "y")
        self.assertFalse(database.update_document("nope", "z"))

    def test_rename_document(self):
        doc = database.create_document(self.sid, "a.md", "x")
        self.assertTrue(database.rename_document(doc["id"], "../b.md"))
        self.assertEqual(database.get_document(doc["id"])["filename"], "b.md")

    def test_delete_document(self):
        doc = database.create_document(self.sid, "a.md", "x")
        self.assertTrue(database.delete_document(doc["id"]))
        self.assertIsNone(database.get_document(doc["id"]))
        self.assertFalse(database.delete_document(doc["id"]))

    def test_cascade_on_session_delete(self):
        database.create_document(self.sid, "a.md", "x")
        database.create_document(self.sid, "b.md", "y")
        database.delete_session(self.sid)
        self.assertEqual(database.list_documents(self.sid), [])

    def test_documents_are_session_scoped(self):
        other = database.create_session(title="o")["id"]
        database.create_document(self.sid, "a.md", "mine")
        self.assertEqual(database.list_documents(other), [])


if __name__ == "__main__":
    unittest.main()
