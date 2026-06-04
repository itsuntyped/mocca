"""In-depth tests for the calculator tool.

Covers the arithmetic the tool promises (the whitelisted operators, precedence,
parentheses, unary signs), the tidy integer formatting, and - just as important
for a tool a model drives - that anything outside basic arithmetic is rejected
with a ToolError rather than evaluated.
"""

from __future__ import annotations

import unittest

from src.tools import calculator
from src.tools.base import ToolError


class TestCalculator(unittest.TestCase):
    def calc(self, expression: str) -> str:
        return calculator._run({"expression": expression})

    # Happy paths: each permitted operator and combination.
    def test_addition(self):
        self.assertEqual(self.calc("2 + 2"), "4")

    def test_subtraction(self):
        self.assertEqual(self.calc("10 - 3"), "7")

    def test_multiplication(self):
        self.assertEqual(self.calc("6 * 7"), "42")

    def test_precedence(self):
        self.assertEqual(self.calc("2 + 3 * 4"), "14")

    def test_parentheses(self):
        self.assertEqual(self.calc("(2 + 3) * 4"), "20")

    def test_power(self):
        self.assertEqual(self.calc("2 ** 10"), "1024")

    def test_floor_division(self):
        self.assertEqual(self.calc("17 // 5"), "3")

    def test_modulo(self):
        self.assertEqual(self.calc("17 % 5"), "2")

    def test_unary_minus(self):
        self.assertEqual(self.calc("-5 + 2"), "-3")

    def test_nested_expression(self):
        self.assertEqual(self.calc("2 * (3 + 4) ** 2"), "98")

    # Formatting: whole-number floats render without a trailing ".0", real
    # fractions keep their decimals.
    def test_whole_float_renders_as_int(self):
        self.assertEqual(self.calc("10 / 2"), "5")

    def test_true_fraction_keeps_decimals(self):
        self.assertEqual(self.calc("7 / 2"), "3.5")

    def test_float_literals(self):
        self.assertEqual(self.calc("0.1 + 0.2"), str(0.1 + 0.2))

    # Errors: empty input and anything that isn't plain arithmetic.
    def test_empty_expression(self):
        with self.assertRaises(ToolError):
            self.calc("")

    def test_whitespace_only(self):
        with self.assertRaises(ToolError):
            self.calc("   ")

    def test_name_rejected(self):
        with self.assertRaises(ToolError):
            self.calc("a + 1")

    def test_function_call_rejected(self):
        # The whole point of the AST whitelist: no arbitrary Python execution.
        with self.assertRaises(ToolError):
            self.calc("__import__('os').system('echo hi')")

    def test_attribute_access_rejected(self):
        with self.assertRaises(ToolError):
            self.calc("(1).__class__")

    def test_division_by_zero(self):
        with self.assertRaises(ToolError):
            self.calc("1 / 0")

    def test_syntax_error(self):
        with self.assertRaises(ToolError):
            self.calc("2 +* 3")

    # The tool metadata stays consistent (the registry/engine relies on it).
    def test_tool_metadata(self):
        self.assertEqual(calculator.TOOL.name, "calculator")
        self.assertEqual(calculator.TOOL.category, "math")
        self.assertTrue(calculator.TOOL.is_local)


if __name__ == "__main__":
    unittest.main()
