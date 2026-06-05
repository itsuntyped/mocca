"""Tests for the document API endpoints (routes/documents.py).

We call the route handlers directly (they are plain async functions) rather than
spinning up a server - the storage is already covered elsewhere, so here we check
the HTTP-shaped behaviour: 404s, the list omitting content, the text-only guard,
and the size cap.
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException

from src import database
from src.routes import documents as route


def _run(coro):
    return asyncio.run(coro)


class TestDocumentRoutes(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.sid = database.create_session(title="t")["id"]

    def _create(self, filename="a.md", content="hello", source="upload"):
        return _run(route.create_document(
            self.sid, route.CreateDocumentRequest(filename=filename, content=content, source=source)
        ))

    def test_create_and_get(self):
        doc = self._create()
        self.assertEqual(doc["filename"], "a.md")
        self.assertEqual(doc["content"], "hello")
        full = _run(route.get_document(self.sid, doc["id"]))
        self.assertEqual(full["content"], "hello")

    def test_create_unknown_session_404(self):
        with self.assertRaises(HTTPException) as cm:
            _run(route.create_document("nope", route.CreateDocumentRequest(filename="a.md")))
        self.assertEqual(cm.exception.status_code, 404)

    def test_list_omits_content(self):
        self._create()
        resp = _run(route.list_documents(self.sid))
        self.assertEqual(len(resp["documents"]), 1)
        self.assertNotIn("content", resp["documents"][0])
        self.assertIn("filename", resp["documents"][0])

    def test_get_wrong_session_404(self):
        doc = self._create()
        other = database.create_session(title="o")["id"]
        with self.assertRaises(HTTPException) as cm:
            _run(route.get_document(other, doc["id"]))
        self.assertEqual(cm.exception.status_code, 404)

    def test_patch_updates(self):
        doc = self._create()
        _run(route.update_document(doc["id"], route.UpdateDocumentRequest(content="changed")))
        self.assertEqual(database.get_document(doc["id"])["content"], "changed")

    def test_patch_unknown_404(self):
        with self.assertRaises(HTTPException) as cm:
            _run(route.update_document("nope", route.UpdateDocumentRequest(content="x")))
        self.assertEqual(cm.exception.status_code, 404)

    def test_delete(self):
        doc = self._create()
        _run(route.delete_document(doc["id"]))
        self.assertIsNone(database.get_document(doc["id"]))
        with self.assertRaises(HTTPException) as cm:
            _run(route.delete_document(doc["id"]))
        self.assertEqual(cm.exception.status_code, 404)

    def test_binary_content_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            self._create(content="bad\x00bytes")
        self.assertEqual(cm.exception.status_code, 400)

    def test_oversize_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            self._create(content="x" * (route._MAX_CONTENT + 1))
        self.assertEqual(cm.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
