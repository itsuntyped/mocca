"""Builds a GBNF grammar that forces a model to emit a well-formed tool call.

This is the heart of the "grammar-guided" path. Many local GGUF models were not
trained for native function calling and, left unconstrained, produce tool calls
in inconsistent, unparseable shapes. By constraining generation with a GBNF
grammar, llama.cpp will only ever sample tokens that fit our schema, so the
output is *guaranteed* parseable - the model literally cannot malform it.

The grammar restricts the whole response to exactly one of two JSON shapes:

    {"tool": "<one of the enabled tool names>", "arguments": { ... }}
    {"answer": "<the final reply to the user>"}

So each turn the model must either call a tool or answer. The loop (see
``tool_loop.py``) executes the tool and asks again, or streams the answer.

The grammar only constrains *shape*, not *which* tool is sensible - that's what
the tool manifest in the prompt is for. We keep a generic JSON grammar for the
arguments object rather than a per-tool one: it's far simpler, and a wrong
argument is caught and reported by the tool itself.
"""

from __future__ import annotations

# A standard JSON sub-grammar (object/array/string/number) reused for the
# arguments object and the answer string. Kept as a constant so the only part we
# generate per-request is the tool-name alternation.
_JSON_RULES = r"""
object ::= "{" ws ( pair ( ws "," ws pair )* )? ws "}"
pair   ::= string ws ":" ws value
array  ::= "[" ws ( value ( ws "," ws value )* )? ws "]"
value  ::= string | number | object | array | "true" | "false" | "null"
string ::= "\"" char* "\""
char   ::= [^"\\] | "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex    ::= [0-9a-fA-F]
number ::= "-"? int frac? exp?
int    ::= "0" | [1-9] [0-9]*
frac   ::= "." [0-9]+
exp    ::= ("e" | "E") ("+" | "-")? [0-9]+
ws     ::= [ \t\n]*
"""


def _quote(name: str) -> str:
    """Render a tool name as a GBNF string literal (e.g. calculator -> "\"calculator\"")."""
    # Tool names are snake_case identifiers, so the only character that needs
    # escaping in a GBNF double-quoted literal is the double quote itself - which
    # a valid tool name never contains. Escape defensively anyway.
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"\\"{escaped}\\""'


def build_tool_grammar(tool_names: list[str]) -> str:
    """Return GBNF constraining output to a tool call (from ``tool_names``) or an answer.

    Raises ValueError if ``tool_names`` is empty - with no tools there is nothing
    to constrain, and the caller should generate normally instead.
    """
    if not tool_names:
        raise ValueError("Cannot build a tool grammar with no tools.")

    # Alternation over the allowed names, e.g.  "calculator" | "web_search"
    name_alt = " | ".join(_quote(n) for n in tool_names)

    head = (
        'root     ::= toolcall | answer\n'
        'toolcall ::= "{" ws "\\"tool\\"" ws ":" ws toolname ws "," ws '
        '"\\"arguments\\"" ws ":" ws object ws "}"\n'
        'answer   ::= "{" ws "\\"answer\\"" ws ":" ws string ws "}"\n'
        f'toolname ::= {name_alt}\n'
    )
    return head + _JSON_RULES
