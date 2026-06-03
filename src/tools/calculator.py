"""Calculator tool: evaluate a basic arithmetic expression safely.

We deliberately do NOT use ``eval`` - that would let a model run arbitrary
Python. Instead the expression is parsed into an AST and walked, allowing only
numbers and a whitelist of arithmetic operators. Anything else raises, so the
tool can never do more than maths.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from .base import Tool, ToolError

# The only binary operators we permit, mapped to their implementations.
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
# The only unary operators we permit (e.g. a leading minus sign).
_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval(node: ast.AST) -> float:
    """Recursively evaluate a whitelisted arithmetic AST node."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ToolError("Expression contains something other than basic arithmetic.")


def _run(args: dict[str, Any]) -> str:
    expr = str(args.get("expression", "")).strip()
    if not expr:
        raise ToolError("No expression given.")
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval(tree)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - report any parse/maths error simply
        raise ToolError(f"Could not evaluate '{expr}': {exc}") from exc
    # Render whole numbers without a trailing ".0" for tidiness.
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


TOOL = Tool(
    name="calculator",
    description=(
        "Evaluate a basic arithmetic expression (+, -, *, /, //, %, **, and "
        "parentheses). Use this for any exact calculation instead of guessing."
    ),
    category="math",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The arithmetic expression, e.g. '2 * (3 + 4) ** 2'.",
            },
        },
        "required": ["expression"],
    },
    run=_run,
)
