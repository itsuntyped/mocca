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

    async def test_js_rendered_page_falls_back_to_head_metadata(self):
        # A Credly-style SPA: empty <body>, but the <head> names the page. The
        # old behaviour returned "(no readable text)" and the model guessed.
        html = (
            "<html><head><title>Foo - Credly</title>"
            '<meta property="og:title" content="Business Agility Foundations">'
            '<meta content="Issued by ICAgile" property="og:description">'
            "</head><body></body></html>"
        )
        resp = FakeResponse(text=html, headers={"content-type": "text/html"})
        with patch_httpx(fetch_url, lambda m, u, k: resp):
            out = await fetch_url._run({"url": "https://www.credly.com/badges/x"})
        self.assertIn("Business Agility Foundations", out)
        self.assertIn("Issued by ICAgile", out)
        self.assertNotIn("no readable text", out)

    async def test_metadata_leads_the_body_on_a_normal_page(self):
        html = (
            "<html><head><title>Article Title</title></head>"
            "<body><p>The article body text.</p></body></html>"
        )
        resp = FakeResponse(text=html, headers={"content-type": "text/html"})
        with patch_httpx(fetch_url, lambda m, u, k: resp):
            out = await fetch_url._run({"url": "https://example.com/post"})
        self.assertIn("Title: Article Title", out)
        self.assertIn("The article body text.", out)
        self.assertLess(out.index("Article Title"), out.index("article body"))

    async def test_http_failure_becomes_toolerror(self):
        def boom(method, url, kwargs):
            raise httpx.HTTPError("timeout")

        with patch_httpx(fetch_url, boom):
            with self.assertRaises(ToolError):
                await fetch_url._run({"url": "https://example.com"})


class TestExtractMetadata(unittest.TestCase):
    """Head <title>/Open Graph mining - what lets a JS-rendered page (Credly,
    LinkedIn, ...) still say what it is instead of reading back blank."""

    def test_title_and_description_are_extracted_and_unescaped(self):
        html = (
            '<head><title>T &amp; U</title>'
            '<meta property="og:description" content="Desc &amp; more"></head>'
        )
        out = fetch_url._extract_metadata(html)
        self.assertIn("Title: T & U", out)
        self.assertIn("Description: Desc & more", out)

    def test_attribute_order_and_quotes_are_handled(self):
        # content= before property=, single quotes - both must still parse.
        html = "<meta content='Hello there' property='og:title'>"
        self.assertIn("Title: Hello there", fetch_url._extract_metadata(html))

    def test_longest_value_wins_per_label(self):
        html = (
            "<title>Short - Site</title>"
            '<meta property="og:title" content="A much fuller title naming the page">'
        )
        out = fetch_url._extract_metadata(html)
        self.assertIn("Title: A much fuller title naming the page", out)
        self.assertNotIn("Short - Site", out)

    def test_empty_when_no_head_metadata(self):
        self.assertEqual(fetch_url._extract_metadata("<p>hi</p>"), "")


class TestFetchUrlMetadata(unittest.TestCase):
    def test_is_network_tool(self):
        self.assertEqual(fetch_url.TOOL.category, "web")
        self.assertFalse(fetch_url.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
