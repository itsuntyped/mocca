"""In-depth tests for the unit-conversion tool.

Exercises each dimension (length, mass, temperature), the offset-based
temperature scales, case-insensitivity, and the failure modes: mismatched
dimensions, unknown units, and a missing/non-numeric value.
"""

from __future__ import annotations

import unittest

from src.tools import unit_convert
from src.tools.base import ToolError


class TestUnitConvert(unittest.TestCase):
    def convert(self, value, frm, to) -> str:
        return unit_convert._run({"value": value, "from_unit": frm, "to_unit": to})

    # Length.
    def test_mm_to_m(self):
        self.assertEqual(self.convert(1000, "mm", "m"), "1000 mm = 1 m")

    def test_km_to_mi(self):
        # 1 km = 0.621371... mi; %g trims to 6 significant figures.
        self.assertEqual(self.convert(1, "km", "mi"), "1 km = 0.621371 mi")

    def test_ft_to_in(self):
        self.assertEqual(self.convert(1, "ft", "in"), "1 ft = 12 in")

    # Mass.
    def test_kg_to_g(self):
        self.assertEqual(self.convert(2, "kg", "g"), "2 kg = 2000 g")

    def test_lb_to_oz(self):
        self.assertEqual(self.convert(1, "lb", "oz"), "1 lb = 16 oz")

    # Temperature (offset scales, special-cased).
    def test_c_to_f(self):
        self.assertEqual(self.convert(100, "c", "f"), "100 c = 212 f")

    def test_f_to_c(self):
        self.assertEqual(self.convert(32, "f", "c"), "32 f = 0 c")

    def test_c_to_k(self):
        self.assertEqual(self.convert(0, "c", "k"), "0 c = 273.15 k")

    def test_k_to_c(self):
        self.assertEqual(self.convert(273.15, "k", "c"), "273.15 k = 0 c")

    # Case-insensitivity: the converter lowercases units internally.
    def test_uppercase_units(self):
        self.assertEqual(self.convert(1, "KM", "M"), "1 KM = 1000 M")

    # Same unit in and out is the identity.
    def test_identity(self):
        self.assertEqual(self.convert(5, "m", "m"), "5 m = 5 m")

    # Errors.
    def test_cross_dimension_rejected(self):
        with self.assertRaises(ToolError):
            self.convert(1, "m", "kg")

    def test_unknown_unit(self):
        with self.assertRaises(ToolError):
            self.convert(1, "m", "parsec")

    def test_missing_units(self):
        with self.assertRaises(ToolError):
            unit_convert._run({"value": 1, "from_unit": "", "to_unit": "m"})

    def test_non_numeric_value(self):
        with self.assertRaises(ToolError):
            unit_convert._run({"value": "lots", "from_unit": "m", "to_unit": "cm"})

    def test_missing_value(self):
        with self.assertRaises(ToolError):
            unit_convert._run({"from_unit": "m", "to_unit": "cm"})

    def test_tool_metadata(self):
        self.assertEqual(unit_convert.TOOL.name, "convert_units")
        self.assertEqual(unit_convert.TOOL.category, "math")
        self.assertTrue(unit_convert.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
