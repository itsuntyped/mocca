"""In-depth tests for the web_search tool.

The HTTP call is faked (see tests/helpers.py), so we test everything around it:
input validation, the DuckDuckGo HTML parsing (title, unwrapped redirect URL,
snippet), entity decoding, the result cap, the no-results message, and how a
network failure surfaces as a ToolError. The pure helpers (_clean, _real_url)
are unit-tested directly too.
"""

from __future__ import annotations

import unittest

import httpx

from src.tools import web_search
from src.tools.base import ToolError
from helpers import FakeResponse, patch_httpx


def _result_html(*entries: tuple[str, str, str]) -> str:
    """Build a fake DuckDuckGo HTML page from (encoded_url, title, snippet) rows."""
    blocks = []
    for enc_url, title, snippet in entries:
        blocks.append(
            f'<a class="result__a" href="/l/?uddg={enc_url}">{title}</a>'
            f'<a class="result__snippet">{snippet}</a>'
        )
    return "<html><body>" + "".join(blocks) + "</body></html>"


class TestWebSearchHelpers(unittest.TestCase):
    def test_clean_strips_tags_and_entities(self):
        self.assertEqual(web_search._clean("A <b>bold</b> &amp; clear"), "A bold & clear")

    def test_real_url_unwraps_redirect(self):
        href = "/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3Dc&rut=xyz"
        self.assertEqual(web_search._real_url(href), "https://example.com/a?b=c")

    def test_real_url_passthrough_when_not_wrapped(self):
        self.assertEqual(web_search._real_url("https://plain.example"), "https://plain.example")


class TestWebSearchRun(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_rejected(self):
        with self.assertRaises(ToolError):
            await web_search._run({"query": "  "})

    async def test_parses_results(self):
        html = _result_html(
            ("https%3A%2F%2Fexample.com%2Fpage", "Example &amp; Co", "First <b>snippet</b>"),
            ("https%3A%2F%2Ffoo.test%2Fbar", "Foo Bar", "Second snippet"),
        )
        with patch_httpx(web_search, lambda m, u, k: FakeResponse(text=html)):
            out = await web_search._run({"query": "anything"})
        self.assertIn("1. Example & Co", out)
        self.assertIn("https://example.com/page", out)
        self.assertIn("First snippet", out)
        self.assertIn("2. Foo Bar", out)
        self.assertIn("https://foo.test/bar", out)

    async def test_max_results_cap(self):
        entries = [
            (f"https%3A%2F%2Fsite{i}.test", f"Title {i}", f"Snippet {i}")
            for i in range(8)
        ]
        html = _result_html(*entries)
        with patch_httpx(web_search, lambda m, u, k: FakeResponse(text=html)):
            out = await web_search._run({"query": "x", "max_results": 3})
        # Only three numbered results when capped at 3.
        self.assertIn("3. Title 2", out)
        self.assertNotIn("4. Title 3", out)

    async def test_no_results_message(self):
        with patch_httpx(web_search, lambda m, u, k: FakeResponse(text="<html></html>")):
            out = await web_search._run({"query": "nothing here"})
        self.assertIn("No results found", out)

    async def test_http_failure_becomes_toolerror(self):
        def boom(method, url, kwargs):
            raise httpx.HTTPError("connection refused")

        with patch_httpx(web_search, boom):
            with self.assertRaises(ToolError):
                await web_search._run({"query": "x"})

    async def test_non_2xx_becomes_toolerror(self):
        with patch_httpx(web_search, lambda m, u, k: FakeResponse(text="", ok=False)):
            with self.assertRaises(ToolError):
                await web_search._run({"query": "x"})


class TestWebSearchMetadata(unittest.TestCase):
    def test_is_network_tool(self):
        self.assertEqual(web_search.TOOL.category, "web")
        self.assertFalse(web_search.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
