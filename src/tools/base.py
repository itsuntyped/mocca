"""Defines what a Mocca tool is.

A tool is a small, self-contained capability the AI can invoke during a chat -
a calculator, a clock, a web search. Each tool lives in its own module under
``src/tools/`` and exposes a module-level ``TOOL`` (or ``TOOLS`` list) describing
itself - name, description, parameter schema - and how to run it.

Keeping the shape minimal and declarative means adding a new tool is just
dropping a file in this package: the registry discovers it automatically, the
same way the API routes and frontend modules are split by responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

# A tool's run function takes the parsed arguments (already shaped by the model
# against the tool's JSON schema) and returns a short text result the model reads
# back. It may be sync or async; the registry awaits coroutines.
RunFn = Callable[[dict[str, Any]], Union[str, Awaitable[str]]]


class ToolError(RuntimeError):
    """Raised when a tool fails, carrying a message safe to show the model/user."""


@dataclass(frozen=True)
class Tool:
    """A single capability the AI can call.

    Most fields map directly onto the OpenAI-style function schema we hand to the
    engine; the rest is Mocca-specific metadata for grouping and safety.
    """

    # Unique, snake_case identifier the model uses to call the tool.
    name: str
    # One sentence telling the model what the tool does and when to use it.
    description: str
    # Grouping key the UI toggles on/off (e.g. "math", "time", "files", "web").
    category: str
    # JSON Schema for the arguments object (the OpenAI "parameters" shape).
    parameters: dict[str, Any]
    # The function that actually does the work.
    run: RunFn
    # False for anything that reaches the network or outside world. Network tools
    # are opt-in so Mocca stays local-only by default (see CLAUDE.md goal #2).
    is_local: bool = True
    # If True, the UI should confirm with the user before running. Defaulted off
    # today (batch-1 tools are read-only), but wired in so a dangerous future
    # tool can demand approval without reworking the engine or routes.
    confirm: bool = False

    def schema(self) -> dict[str, Any]:
        """Return this tool as an OpenAI-format function-calling spec."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
