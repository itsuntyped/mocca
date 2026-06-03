"""Unit conversion tool: convert a value between common units.

Supports a curated set of everyday units grouped by dimension (length, mass,
temperature). Each linear dimension maps every unit to a base unit via a factor,
so conversion is simply "to base, then from base". Temperature is special-cased
because its scales have offsets, not just factors.
"""

from __future__ import annotations

from typing import Any, Callable

from .base import Tool, ToolError

# Linear units: the factor that turns ONE of this unit into the dimension's base.
_LINEAR: dict[str, dict[str, float]] = {
    "length": {  # base: metre
        "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
        "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344,
    },
    "mass": {  # base: gram
        "mg": 0.001, "g": 1.0, "kg": 1000.0,
        "oz": 28.349523125, "lb": 453.59237,
    },
}

# Temperature converters: into Celsius, then out of Celsius.
_TO_C: dict[str, Callable[[float], float]] = {
    "c": lambda v: v,
    "f": lambda v: (v - 32) * 5 / 9,
    "k": lambda v: v - 273.15,
}
_FROM_C: dict[str, Callable[[float], float]] = {
    "c": lambda v: v,
    "f": lambda v: v * 9 / 5 + 32,
    "k": lambda v: v + 273.15,
}


def _convert(value: float, frm: str, to: str) -> float:
    frm, to = frm.lower(), to.lower()
    if frm in _TO_C and to in _FROM_C:
        return _FROM_C[to](_TO_C[frm](value))
    # Find the single dimension that holds both units; mismatches fall through.
    for units in _LINEAR.values():
        if frm in units and to in units:
            return value * units[frm] / units[to]
    raise ToolError(
        f"Cannot convert from '{frm}' to '{to}' (unknown units or different dimensions)."
    )


def _run(args: dict[str, Any]) -> str:
    try:
        value = float(args["value"])
    except (KeyError, TypeError, ValueError):
        raise ToolError("Provide a numeric 'value'.")
    frm = str(args.get("from_unit", "")).strip()
    to = str(args.get("to_unit", "")).strip()
    if not frm or not to:
        raise ToolError("Provide both 'from_unit' and 'to_unit'.")
    result = _convert(value, frm, to)
    return f"{value:g} {frm} = {result:g} {to}"


TOOL = Tool(
    name="convert_units",
    description=(
        "Convert a number between units of length (mm, cm, m, km, in, ft, yd, "
        "mi), mass (mg, g, kg, oz, lb), or temperature (c, f, k)."
    ),
    category="math",
    parameters={
        "type": "object",
        "properties": {
            "value": {"type": "number", "description": "The amount to convert."},
            "from_unit": {"type": "string", "description": "Unit to convert from."},
            "to_unit": {"type": "string", "description": "Unit to convert to."},
        },
        "required": ["value", "from_unit", "to_unit"],
    },
    run=_run,
)
