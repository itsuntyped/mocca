"""In-depth tests for the track_shipment tool.

The carrier resolution is the heart of this tool, and most of it is offline: the
custom-carrier table (matched by prefix, S10 country suffix, or an explicit
carrier hint) and the Intelcom JSON parsing. We test those directly, plus the
various _run output shapes (live-status carrier, per-number deep link, landing
page, no-public-page), all with the HTTP call faked. The tracking-numbers
package path is tested only when the package is installed.
"""

from __future__ import annotations

import unittest

import httpx

from src.tools import shipping
from src.tools.base import ToolError
from helpers import FakeResponse, patch_httpx


class TestMatchCustom(unittest.TestCase):
    def test_intelcom_by_prefix(self):
        entry = shipping._match_custom("INTLCMI306283305", "")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["name"], "Intelcom")

    def test_aliexpress_by_hint(self):
        entry = shipping._match_custom("ANYNUMBER123", "aliexpress")
        self.assertEqual(entry["name"], "AliExpress (Cainiao)")

    def test_royal_mail_by_s10_gb(self):
        entry = shipping._match_custom("LX123456789GB", "")
        self.assertEqual(entry["name"], "Royal Mail")

    def test_china_post_by_s10_cn(self):
        entry = shipping._match_custom("LX123456789CN", "")
        self.assertEqual(entry["name"], "China Post / EMS")

    def test_deutsche_post_by_s10_de(self):
        entry = shipping._match_custom("LX123456789DE", "")
        self.assertEqual(entry["name"], "Deutsche Post / DHL Germany")

    def test_hint_does_not_over_match_dhl(self):
        # A plain "dhl" hint must not grab the "dhl germany" entry - plain DHL
        # numbers should fall through to the tracking-numbers package.
        self.assertIsNone(shipping._match_custom("1234567890", "dhl"))

    def test_no_match(self):
        self.assertIsNone(shipping._match_custom("1234", ""))


class TestIntelcomHelpers(unittest.TestCase):
    def test_en_from_dict(self):
        self.assertEqual(shipping._en({"en": "Delivered", "fr": "Livre"}), "Delivered")

    def test_en_from_non_dict(self):
        self.assertEqual(shipping._en("plain"), "")

    def test_format_eta_range(self):
        eta = {"from": "2026-06-04T00:00:00", "to": "2026-06-06T00:00:00"}
        self.assertEqual(shipping._format_eta(eta), "2026-06-04 to 2026-06-06")

    def test_format_eta_single(self):
        eta = {"from": "2026-06-04T00:00:00", "to": "2026-06-04T00:00:00"}
        self.assertEqual(shipping._format_eta(eta), "2026-06-04")

    def test_format_eta_empty(self):
        self.assertEqual(shipping._format_eta({}), "")

    def test_event_time_from_iso(self):
        event = {"package_location": {"address": {"event_local_time": "2026-05-21T10:19:33-04:00"}}}
        self.assertEqual(shipping._intelcom_event_time(event), "2026-05-21 10:19")

    def test_event_place(self):
        event = {"package_location": {"address": {"city": "Laval", "state_province": "QC", "country_code": "CA"}}}
        self.assertEqual(shipping._intelcom_event_place(event), "Laval, QC, CA")


class TestResolveCustom(unittest.TestCase):
    def test_per_number_url(self):
        name, url, entry = shipping._resolve("LX123456789GB", "")
        self.assertEqual(name, "Royal Mail")
        self.assertIn("LX123456789GB", url)
        self.assertIsNotNone(entry)

    def test_landing_page_url_has_no_number(self):
        name, url, entry = shipping._resolve("LX123456789CN", "")
        self.assertEqual(name, "China Post / EMS")
        self.assertNotIn("LX123456789CN", url)


class TestShipmentRun(unittest.IsolatedAsyncioTestCase):
    async def test_empty_number_rejected(self):
        with self.assertRaises(ToolError):
            await shipping._run({"tracking_number": "  "})

    async def test_intelcom_live_status(self):
        payload = {
            "data": {
                "result": {
                    "last_status": {"label": "Out for delivery", "isDelivered": False},
                    "client_code": "AMZ",
                    "public_eta": {"from": "2026-06-05", "to": "2026-06-05"},
                    "status_list": [{"label": "Shipped"}],
                }
            }
        }
        with patch_httpx(shipping, lambda m, u, k: FakeResponse(json_data=payload)):
            out = await shipping._run({"tracking_number": "INTLABC123456"})
        self.assertIn("Carrier: Intelcom", out)
        self.assertIn("Out for delivery", out)
        self.assertIn("Estimated delivery: 2026-06-05", out)

    async def test_intelcom_status_unavailable(self):
        # API failure -> the official URL still comes back with a graceful note.
        with patch_httpx(shipping, lambda m, u, k: FakeResponse(ok=False)):
            out = await shipping._run({"tracking_number": "INTLABC123456"})
        self.assertIn("Carrier: Intelcom", out)
        self.assertIn("Live status could not be retrieved", out)

    async def test_landing_page_carrier(self):
        # China Post has no per-number deep link: tell the user to enter the number.
        out = await shipping._run({"tracking_number": "LX123456789CN"})
        self.assertIn("China Post / EMS", out)
        self.assertIn("no direct tracking link", out)

    async def test_per_number_deep_link(self):
        out = await shipping._run({"tracking_number": "ANY123", "carrier": "aliexpress"})
        self.assertIn("AliExpress (Cainiao)", out)
        self.assertIn("Official tracking page:", out)

    async def test_no_public_page_carrier(self):
        out = await shipping._run({"tracking_number": "ANY123", "carrier": "shopee"})
        self.assertIn("Shopee (SPX)", out)
        self.assertIn("No public tracking URL", out)

    async def test_unknown_carrier_rejected(self):
        # A number nothing recognises (and no package match) is a friendly error.
        # Skip if the tracking-numbers package isn't installed (its import path).
        try:
            import tracking_numbers  # noqa: F401
        except ImportError:
            self.skipTest("tracking-numbers package not installed")
        with self.assertRaises(ToolError):
            await shipping._run({"tracking_number": "0000"})


class TestShipmentMetadata(unittest.TestCase):
    def test_is_network_tool(self):
        self.assertEqual(shipping.TOOL.category, "shipping")
        self.assertFalse(shipping.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
