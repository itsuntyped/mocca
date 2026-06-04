"""In-depth tests for the fetch_url tool.

The HTTP call is faked (see tests/helpers.py). We test the URL validation, the
HTML-to-text stripping (dropping script/style/nav chrome, collapsing
whitespace), the content-type branch (HTML stripped, non-HTML passed through),
truncation, the empty-page fallback, and network-failure handling.
"""

from __future__ import annotations

import unittest

import httpx

from src.tools import fetch_url
from src.tools.base import ToolError
from helpers import FakeResponse, patch_httpx


class TestStripHtml(unittest.TestCase):
    def test_drops_script_and_style(self):
        html = "<p>Keep this</p><script>evil()</script><style>.x{}</style>"
        out = fetch_url._strip_html(html)
        self.assertIn("Keep this", out)
        self.assertNotIn("evil", out)
        self.assertNotIn(".x{}", out)

    def test_drops_nav_chrome(self):
        html = "<nav>Menu Home About</nav><p>Article body</p><footer>Footer</footer>"
        out = fetch_url._strip_html(html)
        self.assertIn("Article body", out)
        self.assertNotIn("Menu", out)
        self.assertNotIn("Footer", out)

    def test_strips_comments(self):
        out = fetch_url._strip_html("<!-- hidden -->Visible")
        self.assertIn("Visible", out)
        self.assertNotIn("hidden", out)

    def test_collapses_blank_lines(self):
        out = fetch_url._strip_html("A<div></div><div></div><div></div>B")
        self.assertNotIn("\n\n\n", out)


class TestFetchUrlRun(unittest.IsolatedAsyncioTestCase):
    async def test_empty_url_rejected(self):
        with self.assertRaises(ToolError):
            await fetch_url._run({"url": ""})

    async def test_non_http_scheme_rejected(self):
        with self.assertRaises(ToolError):
            await fetch_url._run({"url": "ftp://example.com/file"})

    async def test_html_is_stripped(self):
        resp = FakeResponse(
            text="<html><body><h1>Title</h1><p>Hello there</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        with patch_httpx(fetch_url, lambda m, u, k: resp):
            out = await fetch_url._run({"url": "https://example.com"})
        self.assertIn("Title", out)
        self.assertIn("Hello there", out)
        self.assertNotIn("<h1>", out)

    async def test_non_html_passthrough(self):
        resp = FakeResponse(
            text='{"key": "value"}',
            headers={"content-type": "application/json"},
        )
        with patch_httpx(fetch_url, lambda m, u, k: resp):
            out = await fetch_url._run({"url": "https://example.com/data.json"})
        self.assertEqual(out, '{"key": "value"}')

    async def test_truncation(self):
        body = "<p>" + ("word " * 5000) + "</p>"
        resp = FakeResponse(text=body, headers={"content-type": "text/html"})
        with patch_httpx(fetch_url, lambda m, u, k: resp):
            out = await fetch_url._run({"url": "https://example.com"})
        self.assertIn("[truncated at", out)

    async def test_empty_page_fallback(self):
        resp = FakeResponse(text="<html><body></body></html>",
                            headers={"content-type": "text/html"})
        with patch_httpx(fetch_url, lambda m, u, k: resp):
            out = await fetch_url._run({"url": "https://example.com"})
        self.assertIn("no readable text", out)

    async def test_http_failure_becomes_toolerror(self):
        def boom(method, url, kwargs):
            raise httpx.HTTPError("timeout")

        with patch_httpx(fetch_url, boom):
            with self.assertRaises(ToolError):
                await fetch_url._run({"url": "https://example.com"})


class TestFetchUrlMetadata(unittest.TestCase):
    def test_is_network_tool(self):
        self.assertEqual(fetch_url.TOOL.category, "web")
        self.assertFalse(fetch_url.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
