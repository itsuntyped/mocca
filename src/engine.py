"""The local inference engine - runs GGUF models in-process via llama.cpp.

This is what makes Mocca standalone: there is no separate model server. We load
a model with ``llama-cpp-python`` and generate replies directly inside this
process. Design notes:

  * **Lazy import.** ``llama_cpp`` is imported only when first needed, so the
    rest of the app (UI, downloading models, browsing settings) works even if
    the engine isn't installed yet. ``is_available()`` reports its presence so
    the UI can guide the user.

  * **One model in memory.** Loading a model is expensive, so we keep a single
    ``Llama`` instance cached and only reload when the model file or the
    load-time settings (context size, GPU layers, threads) change.

  * **Threaded generation.** llama.cpp generation is blocking CPU/GPU work. We
    run it on a worker thread and hand tokens back to the async event loop via a
    queue, so streaming never blocks the web server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from . import config
from .models import model_path
from .tool_grammar import build_tool_grammar

log = logging.getLogger("mocca.engine")


def _prepare_cuda_dll_path() -> None:
    """Make pip-installed CUDA runtime DLLs discoverable by the CUDA build of
    llama-cpp-python (Windows only).

    The prebuilt CUDA wheel's ``ggml-cuda.dll`` needs the CUDA runtime
    (``cudart``/``cublas``) at load time. When those come from the
    ``nvidia-*-cu12`` pip wheels - rather than a system CUDA Toolkit - their bin
    folders aren't on the search path, so the import fails with a cryptic
    "could not find module ... or one of its dependencies". We add those bin
    folders to both the DLL directory list and PATH (llama-cpp-python's loader
    relies on PATH) before ``llama_cpp`` is imported.

    No-op off Windows or when the nvidia packages aren't installed (e.g. a CPU
    build), so it's safe to always call.
    """
    if os.name != "nt":
        return
    try:
        import nvidia  # Namespace package from the nvidia-*-cu12 wheels.
    except ImportError:
        return
    for root in nvidia.__path__:
        for bin_dir in Path(root).glob("*/bin"):
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


# Run once at import, before any lazy ``import llama_cpp`` below, so the CUDA
# build can find its runtime DLLs.
_prepare_cuda_dll_path()


class EngineError(RuntimeError):
    """Raised for load/generation failures, with a UI-friendly message."""


@dataclass
class ToolCall:
    """A single tool invocation the model decided to make."""

    name: str
    arguments: dict[str, Any]
    # A stable id so the matching tool result can be tied back to this call in
    # the message history (mirrors the OpenAI tool_call_id convention).
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])


@dataclass
class Decision:
    """One step of the tool loop: either call tools, or answer.

    ``tool_calls`` non-empty means the model wants to run those tools first.
    Empty means it's ready to answer; ``content`` holds the answer the decision
    step produced (the loop may re-generate it streamed for a nicer UX).
    """

    tool_calls: list[ToolCall]
    content: str = ""


def is_available() -> bool:
    """True if llama-cpp-python is importable (i.e. the engine can run)."""
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


# Sentinel pushed onto the queue to mark end-of-stream.
_DONE = object()


def _native_tools_supported(llm: Any) -> bool:
    """Best-effort guess: does this loaded model support native function calling?

    Detection is genuinely hard across arbitrary GGUFs, so we use two cheap
    heuristics and fall back to the grammar path (which works on any model) when
    unsure:

      * the configured ``chat_format`` is a known function-calling format, or
      * the model's embedded chat template mentions tools/tool calls.

    A false negative just means we use the (still reliable) grammar path; a false
    positive is corrected by the grammar fallback if native calling misbehaves.
    """
    fmt = (getattr(llm, "chat_format", "") or "").lower()
    if "function" in fmt or "functionary" in fmt:
        return True
    metadata = getattr(llm, "metadata", None) or {}
    template = str(metadata.get("tokenizer.chat_template", "")).lower()
    return "tool" in template


def _tool_manifest(tool_schemas: list[dict[str, Any]]) -> str:
    """A plain-text description of the available tools for the grammar path.

    The grammar constrains the *shape* of the output but tells the model nothing
    about what each tool does. This manifest, injected as a system message, is
    how the model learns which tool to pick and what arguments it takes.
    """
    lines = [
        "You can use tools. To call one, respond with JSON: "
        '{"tool": "<name>", "arguments": {...}}. '
        'To answer the user instead, respond with JSON: {"answer": "<your reply>"}. '
        "Available tools:",
    ]
    for schema in tool_schemas:
        fn = schema["function"]
        params = json.dumps(fn.get("parameters", {}).get("properties", {}))
        lines.append(f"- {fn['name']}: {fn['description']} arguments={params}")
    return "\n".join(lines)


# A reusable JSON decoder for scanning tool calls out of free text.
_JSON_DECODER = json.JSONDecoder()


def _extract_tool_call_from_text(text: str, valid_names: set[str]) -> ToolCall | None:
    """Find a tool call embedded in a model's text output, or None.

    This is the workhorse for real local models. Many of them (Llama 3.2 among
    them) emit a perfectly good tool call as plain text - e.g.
    ``{"name": "fetch_url", "parameters": {"url": "..."}}`` - sometimes after some
    reasoning, behind a ``<|python_tag|>`` marker, or inside a code fence.
    llama-cpp-python's generic template path does not turn these into structured
    ``tool_calls``, so we parse them ourselves.

    We scan for the first JSON object whose ``name`` is a *known* tool (the
    valid-names check stops an ordinary answer that merely contains JSON from
    being mistaken for a call), accepting either ``arguments`` or ``parameters``.
    """
    # Strip markers/fences that commonly wrap the JSON so the scan can reach it.
    cleaned = text.replace("<|python_tag|>", "").replace("```json", "").replace("```", "")
    for i, ch in enumerate(cleaned):
        if ch != "{":
            continue
        try:
            obj, _ = _JSON_DECODER.raw_decode(cleaned[i:])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        if not isinstance(name, str) or name not in valid_names:
            continue
        # Accept either key; models name the arguments object differently.
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return ToolCall(name=name, arguments=args if isinstance(args, dict) else {})
    return None


def _parse_grammar_output(text: str, valid_names: set[str]) -> Decision:
    """Turn grammar-path output into a Decision.

    The grammar is meant to constrain output to ``{"tool":...}`` or
    ``{"answer":...}``, but not every build enforces a chat-completion grammar, so
    we also fall back to extracting a native-style tool call from free text before
    giving up and treating the text as a plain answer.
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool" in data:
            args = data.get("arguments")
            return Decision(tool_calls=[ToolCall(
                name=str(data["tool"]),
                arguments=args if isinstance(args, dict) else {},
            )])
        if isinstance(data, dict) and "answer" in data:
            return Decision(tool_calls=[], content=str(data["answer"]))
    except json.JSONDecodeError:
        pass
    extracted = _extract_tool_call_from_text(text, valid_names)
    if extracted:
        return Decision(tool_calls=[extracted])
    return Decision(tool_calls=[], content=text)


def _flatten_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite OpenAI tool messages into plain text any chat template can render.

    Native function-calling models understand assistant ``tool_calls`` and
    ``role: "tool"`` messages directly. Models without that support (the grammar
    path) would choke on those shapes, so we fold them into ordinary
    assistant/user turns: the tool *call* becomes a short assistant note, and the
    tool *result* becomes a user message. This keeps the tool loop speaking one
    standard format while every model still gets readable context.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            desc = "; ".join(
                f"{c['function']['name']}({c['function']['arguments']})"
                for c in m["tool_calls"]
            )
            text = (m.get("content") or "").strip()
            note = f"(calling tools: {desc})"
            out.append({"role": "assistant", "content": f"{text}\n{note}" if text else note})
        elif role == "tool":
            name = m.get("name", "tool")
            out.append({"role": "user", "content": f"Result from {name}:\n{m.get('content', '')}"})
        else:
            # Plain turn: keep role + content, drop any stray tool keys.
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def _parse_native_calls(message: dict[str, Any], valid_names: set[str]) -> Decision:
    """Turn a native llama.cpp chat message into a Decision.

    Prefers structured ``tool_calls`` when present, but falls back to scanning the
    message content - the common case for templates llama-cpp-python renders but
    doesn't parse, where the model's tool call lands in ``content`` as text.
    """
    calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                args = json.loads(raw or "{}")
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw or {}
        calls.append(ToolCall(
            name=name,
            arguments=args if isinstance(args, dict) else {},
            id=tc.get("id") or ("call_" + uuid.uuid4().hex[:12]),
        ))
    if calls:
        return Decision(tool_calls=calls, content=message.get("content") or "")
    # No structured calls: look for one embedded in the text the model produced.
    content = message.get("content") or ""
    extracted = _extract_tool_call_from_text(content, valid_names)
    if extracted:
        return Decision(tool_calls=[extracted])
    return Decision(tool_calls=[], content=content)


class _Worker:
    """Wraps a cached llama.cpp model and serialises access to it."""

    def __init__(self) -> None:
        self._llm: Any | None = None
        self._signature: tuple | None = None  # (model_name, n_ctx, n_gpu_layers, n_threads)
        # Generation isn't thread-safe; one chat at a time per loaded model.
        self._lock = threading.Lock()

    def _load_if_needed(self, model_name: str) -> None:
        """(Re)load the model if the file or load-time settings changed."""
        s = config.get()
        sig = (model_name, s.n_ctx, s.n_gpu_layers, s.n_threads)
        if self._llm is not None and self._signature == sig:
            return

        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise EngineError(
                "llama-cpp-python is not installed. Install it with "
                "'pip install llama-cpp-python' to run models."
            ) from exc

        path = model_path(model_name)
        if not path.exists():
            raise EngineError(f"Model '{model_name}' is not downloaded.")

        log.info("Loading model %s (n_ctx=%d, n_gpu_layers=%d, n_threads=%d)",
                 model_name, s.n_ctx, s.n_gpu_layers, s.n_threads)
        self._llm = Llama(
            model_path=str(path),
            n_ctx=s.n_ctx,
            n_gpu_layers=s.n_gpu_layers,
            n_threads=s.n_threads or None,  # None lets llama.cpp auto-pick.
            verbose=False,
        )
        self._signature = sig
        log.info("Model %s loaded", model_name)

    def generate(self, model_name: str, messages: list[dict[str, str]],
                 options: dict[str, Any], loop: asyncio.AbstractEventLoop,
                 queue: asyncio.Queue) -> None:
        """Run a streaming completion on this thread, pushing chunks to ``queue``.

        Always pushes the ``_DONE`` sentinel last, even on error (the error is
        pushed as an ``EngineError`` instance for the consumer to raise).
        """
        def emit(item: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        try:
            with self._lock:
                self._load_if_needed(model_name)
                # Models without native tool support can't render tool messages;
                # fold them into plain turns first (no-op for tool-free history).
                if not _native_tools_supported(self._llm):
                    messages = _flatten_tool_messages(messages)
                stream = self._llm.create_chat_completion(
                    messages=messages, stream=True, **options
                )
                for chunk in stream:
                    delta = chunk["choices"][0]["delta"].get("content")
                    if delta:
                        emit(delta)
        except EngineError as exc:
            emit(exc)
        except Exception as exc:  # noqa: BLE001 - surface any llama.cpp failure
            log.exception("Generation failed")
            emit(EngineError(f"Generation failed: {exc}"))
        finally:
            emit(_DONE)

    def decide(self, model_name: str, messages: list[dict[str, Any]],
               tool_schemas: list[dict[str, Any]],
               options: dict[str, Any]) -> Decision:
        """One non-streaming step: let the model call a tool or answer.

        Runs on a worker thread (via :func:`decide` below). Picks the native
        function-calling path when the model supports it, else the grammar path.
        Blocking work, so it holds the per-model lock just like generation.
        """
        with self._lock:
            self._load_if_needed(model_name)
            try:
                if _native_tools_supported(self._llm):
                    return self._decide_native(messages, tool_schemas, options)
                return self._decide_grammar(messages, tool_schemas, options)
            except EngineError:
                raise
            except Exception as exc:  # noqa: BLE001 - surface any llama.cpp failure
                log.exception("Tool decision failed")
                raise EngineError(f"Tool step failed: {exc}") from exc

    def _decide_native(self, messages: list[dict[str, Any]],
                       tool_schemas: list[dict[str, Any]],
                       options: dict[str, Any]) -> Decision:
        """Native path: hand the tool schemas to llama.cpp and read its choice."""
        log.debug("Tool decision via native function calling (%d tools)", len(tool_schemas))
        resp = self._llm.create_chat_completion(
            messages=messages, tools=tool_schemas, tool_choice="auto",
            stream=False, **options,
        )
        valid_names = {s["function"]["name"] for s in tool_schemas}
        return _parse_native_calls(resp["choices"][0]["message"], valid_names)

    def _decide_grammar(self, messages: list[dict[str, Any]],
                        tool_schemas: list[dict[str, Any]],
                        options: dict[str, Any]) -> Decision:
        """Grammar path: constrain output to a tool call or an answer (any model)."""
        from llama_cpp import LlamaGrammar

        names = [s["function"]["name"] for s in tool_schemas]
        grammar = LlamaGrammar.from_string(build_tool_grammar(names), verbose=False)
        # Prepend the manifest as a system message so the model knows the tools,
        # and flatten any prior tool calls/results this template can't render.
        manifest = {"role": "system", "content": _tool_manifest(tool_schemas)}
        flat = _flatten_tool_messages(messages)
        log.debug("Tool decision via grammar (%d tools)", len(tool_schemas))
        resp = self._llm.create_chat_completion(
            messages=[manifest, *flat], grammar=grammar, stream=False, **options,
        )
        content = resp["choices"][0]["message"].get("content") or ""
        return _parse_grammar_output(content, set(names))


# Module-level singleton; the whole app shares one loaded model.
_worker = _Worker()


def supports_tools(model_name: str) -> bool:
    """Whether the given model is loaded-and-detected as native tool-capable.

    Note this only reflects the *native* path; the grammar fallback means tools
    work regardless. Exposed mainly for diagnostics/UI hints. Loading the model
    if needed would be expensive, so this only reports for the currently loaded
    one and returns False otherwise.
    """
    s = config.get()
    sig = (model_name, s.n_ctx, s.n_gpu_layers, s.n_threads)
    if _worker._llm is None or _worker._signature != sig:
        return False
    return _native_tools_supported(_worker._llm)


# The tool-decision step is a classification ("which tool, or answer?"), not a
# creative one. We run it near-deterministically so a high chat temperature can't
# make the model pick irrelevant tools or wander; the final answer still streams
# at the user's temperature. A short token cap is plenty for a tool call.
_DECISION_TEMPERATURE = 0.0
_DECISION_MAX_TOKENS = 512


async def decide(
    model_name: str,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
) -> Decision:
    """Async wrapper around one tool-decision step (runs off the event loop).

    Returns a :class:`Decision`: tool calls to run, or an answer to stream.
    """
    opts = _sampling_options(options)
    # Override sampling for the decision: low temperature, bounded length. The
    # user's settings still govern the streamed answer (see chat()).
    opts["temperature"] = _DECISION_TEMPERATURE
    user_max = opts.get("max_tokens")
    opts["max_tokens"] = min(user_max, _DECISION_MAX_TOKENS) if user_max else _DECISION_MAX_TOKENS
    return await asyncio.to_thread(
        _worker.decide, model_name, messages, tool_schemas, opts
    )


def _sampling_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Translate Mocca's settings into the kwargs llama.cpp expects.

    Shared by streaming generation and the tool-decision step so both honour the
    same temperature/top_p/max_tokens rules.
    """
    opts: dict[str, Any] = {}
    if options:
        for key in ("temperature", "top_p"):
            if options.get(key) is not None:
                opts[key] = options[key]
        # max_tokens <= 0 means "no explicit limit" → omit it.
        mt = options.get("max_tokens")
        if mt and mt > 0:
            opts["max_tokens"] = mt
    return opts


async def chat(
    model_name: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Stream a chat completion, yielding text chunks as they're generated.

    ``messages`` is OpenAI-style ``[{"role", "content"}, ...]``. ``options``
    accepts ``temperature``, ``top_p``, and ``max_tokens``.
    """
    opts = _sampling_options(options)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    threading.Thread(
        target=_worker.generate,
        args=(model_name, messages, opts, loop, queue),
        daemon=True,
    ).start()

    while True:
        item = await queue.get()
        if item is _DONE:
            break
        if isinstance(item, EngineError):
            raise item
        yield item
