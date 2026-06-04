"""In-depth tests for the youtube_transcript tool.

The pure helpers - video-id extraction across URL shapes, timestamp formatting,
and the segment-to-timestamped-lines grouping - are the bulk of the tool's logic
and are tested directly. The full _run is tested with the transcript fetch and
the metadata HTTP call faked, so we cover the assembled output (title/channel
header + timestamped body) and the input-validation errors without any network.
"""

from __future__ import annotations

import unittest
from unittest import mock

from src.tools import youtube
from src.tools.base import ToolError
from helpers import FakeResponse, patch_httpx


class TestVideoId(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(
            youtube._video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_short_url(self):
        self.assertEqual(youtube._video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_shorts_url(self):
        self.assertEqual(
            youtube._video_id("https://www.youtube.com/shorts/abcdefghijk"),
            "abcdefghijk",
        )

    def test_embed_url(self):
        self.assertEqual(
            youtube._video_id("https://www.youtube.com/embed/abcdefghijk"),
            "abcdefghijk",
        )

    def test_bare_id(self):
        self.assertEqual(youtube._video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_non_youtube_url(self):
        self.assertIsNone(youtube._video_id("https://example.com/watch?v=nope"))

    def test_garbage(self):
        self.assertIsNone(youtube._video_id("just some words"))


class TestFormatTimestamp(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(youtube._format_timestamp(5), "0:05")

    def test_minutes(self):
        self.assertEqual(youtube._format_timestamp(65), "1:05")

    def test_hours(self):
        self.assertEqual(youtube._format_timestamp(3661), "1:01:01")

    def test_exact_hour(self):
        self.assertEqual(youtube._format_timestamp(3600), "1:00:00")


class TestBuildTranscript(unittest.TestCase):
    def test_groups_by_stamp_interval(self):
        segments = [(0.0, "a"), (5.0, "b"), (25.0, "c")]
        out = youtube._build_transcript(segments)
        self.assertEqual(out, "[0:00] a b\n[0:25] c")

    def test_single_segment(self):
        self.assertEqual(youtube._build_transcript([(12.0, "hi")]), "[0:12] hi")

    def test_empty(self):
        self.assertEqual(youtube._build_transcript([]), "")


class TestYoutubeRun(unittest.IsolatedAsyncioTestCase):
    async def test_empty_url_rejected(self):
        with self.assertRaises(ToolError):
            await youtube._run({"url": ""})

    async def test_invalid_url_rejected(self):
        with self.assertRaises(ToolError):
            await youtube._run({"url": "https://example.com/not-a-video"})

    async def test_assembles_header_and_body(self):
        segments = [(0.0, "Hello"), (3.0, "world")]
        meta = FakeResponse(json_data={"title": "My Video", "author_name": "Some Channel"})
        with mock.patch.object(youtube, "_fetch_segments", return_value=segments):
            with patch_httpx(youtube, lambda m, u, k: meta):
                out = await youtube._run({"url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertIn("Title: My Video", out)
        self.assertIn("Channel: Some Channel", out)
        self.assertIn("[0:00] Hello world", out)

    async def test_no_transcript_text(self):
        with mock.patch.object(youtube, "_fetch_segments", return_value=[]):
            with patch_httpx(youtube, lambda m, u, k: FakeResponse(json_data={})):
                out = await youtube._run({"url": "https://youtu.be/dQw4w9WgXcQ"})
        self.assertIn("no transcript text", out)


class TestYoutubeMetadata(unittest.TestCase):
    def test_is_network_tool(self):
        self.assertEqual(youtube.TOOL.category, "youtube")
        self.assertFalse(youtube.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
