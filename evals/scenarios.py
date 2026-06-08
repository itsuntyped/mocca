"""The curated eval scenarios, across the three model-driven behaviours.

Adding a case is just appending a Scenario here - the same "drop a file / add an
entry" ethos as the tools and routes. Each lists the user turn(s), the hard
checks that gate pass/fail (assert signals, never exact prose), and an optional
one-line judge rubric for the soft quality score.

The three areas:
  * memory  - background capture (the "im Martin" regression class) and recall.
  * tools   - tool selection / routing and the args the model passes.
  * answer  - whether the model relays a tool result sensibly (judge-led).

Scenarios are deliberately chosen to avoid live network where possible: the
shipping cases here resolve entirely from the offline custom-carrier table (a
landing-page carrier and a marketplace deep link), so they are deterministic. The
one web-search case asserts only that the tool was *routed to* (which happens
before execution), so it passes even if the live search errors.
"""

from __future__ import annotations

from .harness import (
    Scenario,
    answer_contains,
    answer_matches,
    answer_not_matches,
    called_tool,
    document_any_contains,
    document_contains,
    memory_category,
    memory_contains,
    memory_count_at_least,
    memory_lacks,
    no_memories,
    no_tools,
    tool_arg_contains,
)

# A small file fixture for the artifact scenarios. The "keepThisKey" sentinel
# value lets a check prove the model worked from THIS content (the open file),
# since that string appears nowhere else.
_OPEN_FILE = (
    "{\n"
    '  "appName": "Mocca",\n'
    '  "keepThisKey": "DO_NOT_REMOVE_42",\n'
    '  "theme": "dark",\n'
    '  "maxRequests": 10\n'
    "}"
)

SCENARIOS: list[Scenario] = [
    # --- Memory: capture -----------------------------------------------------
    Scenario(
        name="casual-name-capture",
        area="memory",
        messages=["hey im Martin"],
        checks=[memory_contains("Martin"), memory_category("identity")],
        judge="The reply should greet Martin warmly by name and not ask a question back coldly.",
    ),
    Scenario(
        name="explicit-name-capture",
        area="memory",
        messages=["my name is Priya, nice to meet you"],
        checks=[memory_contains("Priya"), memory_category("identity")],
    ),
    Scenario(
        name="skill-capture",
        area="memory",
        messages=["yo, ive worked with Rust for about five years now"],
        checks=[memory_count_at_least(1), memory_contains("Rust")],
    ),
    Scenario(
        name="location-capture",
        area="memory",
        messages=["just so you know, i live in Berlin"],
        checks=[memory_contains("Berlin"), memory_category("location")],
    ),
    # --- Memory: recall ------------------------------------------------------
    Scenario(
        name="recall-name",
        area="memory",
        messages=["what's my name again?"],
        seed_memories=[("The user's name is Martin.", "identity")],
        checks=[answer_contains("Martin")],
        judge="The reply should state the user's name is Martin, drawn from memory.",
    ),
    Scenario(
        name="no-fabrication-when-unknown",
        area="memory",
        messages=["what's my name?"],
        # No seeded memory. Rather than enumerate every way to say "I don't know"
        # (brittle), assert the actual property: the reply engages with the name
        # question but never asserts a specific (fabricated) name like
        # "your name is X" / "you are X" / "you're X".
        checks=[
            answer_contains("name"),
            answer_not_matches(r"\byou(?:r name(?:'?s| is)| are|'re| go by)\s+[A-Z][a-z]+"),
        ],
        judge="The reply must admit it does not know the user's name yet, and must NOT invent a name.",
    ),
    # --- Memory: must NOT capture one-off task/edit actions -------------------
    # Regression guard: editing a file or asking for a change is not a durable
    # fact about the user. These turns must leave the memory table empty (the bug
    # was capturing "the user wants a quick start section added to the README").
    Scenario(
        name="memory-skips-file-edit",
        area="memory",
        messages=["I changed the introduction. Now add a quick start section to README.md."],
        documents=[("README.md", _OPEN_FILE)],
        checks=[no_memories()],
    ),
    Scenario(
        name="memory-ignores-task-request",
        area="memory",
        messages=["I changed the introduction, can you add a quick start section to my readme?"],
        checks=[no_memories()],
    ),
    # --- Memory: prune facts a turn made obsolete ----------------------------
    # The mirror of capture: a preference that is replaced, or one the user asks
    # us to drop, must be forgotten - while compatible facts stay put.
    Scenario(
        name="prune-replaced-preference",
        area="memory",
        messages=["honestly im not into Elixir anymore, ive switched to React and prefer it now"],
        seed_memories=[("The user loves the Elixir programming language.", "preference")],
        checks=[memory_lacks("Elixir")],
        judge="The reply should acknowledge the switch to React without arguing.",
    ),
    Scenario(
        name="prune-explicit-forget",
        area="memory",
        messages=["please forget that I live in Berlin"],
        seed_memories=[("The user lives in Berlin.", "location")],
        checks=[memory_lacks("Berlin")],
    ),
    Scenario(
        name="prune-keeps-compatible-fact",
        area="memory",
        # Adding a new, compatible interest must NOT wipe the existing one.
        messages=["i also really enjoy bouldering these days"],
        seed_memories=[("The user likes hiking.", "preference")],
        checks=[memory_contains("hiking")],
    ),
    # --- Tools: routing ------------------------------------------------------
    Scenario(
        name="calculator-routing",
        area="tools",
        messages=["what is 23 * 47 + 19?"],
        checks=[called_tool("calculator"), answer_contains("1100")],
    ),
    Scenario(
        name="datetime-routing",
        area="tools",
        messages=["what's today's date?"],
        checks=[called_tool("current_datetime")],
    ),
    Scenario(
        name="unit-convert-routing",
        area="tools",
        messages=["how many feet are in 3 meters?"],
        # Either tool path is fine; what matters is the answer lands near 9.84 ft.
        checks=[answer_matches(r"9\.8")],
    ),
    Scenario(
        name="web-search-routing",
        area="tools",
        messages=["search the web for the latest news about the Perseverance rover"],
        # Asserts routing only: the tool_call is emitted before execution, so this
        # passes even if the live search fails. (This case does touch the network.)
        checks=[called_tool("web_search")],
    ),
    Scenario(
        name="greeting-no-tools",
        area="tools",
        messages=["hey there, how's your day going?"],
        checks=[no_tools()],
    ),
    # Documents: the model must READ an attached file (via the tool) to answer a
    # question about it, rather than guessing. The sentinel fact appears only in
    # the file, so a correct answer proves it was read. Resolves offline.
    Scenario(
        name="doc-qa-reads-tool",
        area="tools",
        documents=[(
            "facts.md",
            "# Facts\n\nThe capital of Freedonia is Klopstokia.\n"
            "Our mascot is a purple otter named Sprog.\n",
        )],
        messages=["According to the attached facts.md, what is the capital of Freedonia?"],
        checks=[
            called_tool("read_document"),
            tool_arg_contains("read_document", "facts.md"),
            answer_contains("Klopstokia"),
        ],
    ),
    # Guards the model-based router specifically: a question that clearly needs a
    # live lookup but contains NO routing keyword ("search", "look up", a URL).
    # The old keyword router would offer no tools here; the model router should
    # still pick web. Asserts routing only (the tool_call precedes execution), so
    # it passes even when the live search errors. (Touches the network.)
    Scenario(
        name="keywordless-web-routing",
        area="tools",
        messages=["who is the current secretary-general of the united nations?"],
        checks=[called_tool("web_search")],
    ),
    Scenario(
        name="weather-routing",
        area="tools",
        messages=["what's the weather like in Tokyo right now?"],
        # Asserts routing only: the tool_call (with the location) is emitted before
        # the network lookup, so this passes even if the live fetch fails.
        checks=[called_tool("get_weather"), tool_arg_contains("get_weather", "Tokyo")],
    ),
    # --- Tools + answer: shipping (offline, deterministic resolution) ---------
    Scenario(
        name="shipping-landing-page",
        area="answer",
        messages=["can you track my China Post package, the number is LX123456789CN?"],
        checks=[
            called_tool("track_shipment"),
            tool_arg_contains("track_shipment", "LX123456789CN"),
        ],
        judge=(
            "China Post has no direct per-number tracking link. A good reply should "
            "give the official tracking page and tell the user to open it and enter "
            "their tracking number, rather than claiming a live status."
        ),
    ),
    Scenario(
        name="shipping-deep-link",
        area="answer",
        messages=["where's my aliexpress order? tracking number LP00123456789CN"],
        checks=[called_tool("track_shipment")],
        judge=(
            "A good reply should identify the carrier and hand over an official "
            "tracking link for the user to open, without inventing a delivery status."
        ),
    ),
    # --- Documents: editing an attached file ----------------------------------
    # The model must read the attached file, then return the COMPLETE updated file
    # so it is written back. The sentinel key in _OPEN_FILE proves the existing
    # content was preserved (not regenerated from scratch). document_contains
    # asserts the change actually landed in the stored document.
    Scenario(
        name="doc-edit-applies-and-preserves",
        area="artifact",
        documents=[("settings.json", _OPEN_FILE)],
        messages=['Add a field "version" set to "2.0" to the attached settings.json.'],
        checks=[
            called_tool("read_document"),         # read the file before editing
            answer_contains("version"),           # applied the requested change
            answer_contains("DO_NOT_REMOVE_42"),  # kept the existing content verbatim
            document_contains("settings.json", "version"),  # the edit was written back
        ],
        judge=(
            "The reply should return the whole settings.json with a version field "
            "added and every existing field (including keepThisKey) kept intact."
        ),
    ),
    # The tricky case the user flagged: a file the model GENERATED (so it named
    # itself, no upload) must still be editable later, even with another document
    # also attached. Turn 1 generates a readme; turn 2 edits it. The edit must read
    # the generated file and land an Installation section in some document (its
    # name is the model's choice, so assert filename-agnostically).
    Scenario(
        name="doc-edit-generated-file-later",
        area="artifact",
        documents=[("config.json", '{\n  "port": 8080,\n  "debug": false\n}\n')],
        messages=[
            "Write a short markdown README for a project called Acme. Keep it under 10 lines.",
            "Now add an Installation section to that readme.",
        ],
        checks=[
            called_tool("read_document"),               # read the generated file to edit it
            answer_contains("Installation"),            # applied the change
            document_any_contains("Installation"),      # and it was written back
        ],
        judge=(
            "The second reply should update the README the assistant wrote earlier "
            "with an Installation section, not touch config.json or start a new file."
        ),
    ),
    Scenario(
        name="doc-chitchat-no-regen",
        area="artifact",
        documents=[("settings.json", _OPEN_FILE)],
        messages=["thank you, that's perfect"],
        # A thank-you must NOT re-emit or rewrite the attached file.
        checks=[
            answer_not_matches(r"```"),               # no code block
            answer_not_matches("DO_NOT_REMOVE_42"),   # didn't echo the file contents
        ],
        judge=(
            "A brief, friendly acknowledgement is ideal; offering further help is "
            "also fine. The only real failure is repeating or regenerating the file."
        ),
    ),
]
