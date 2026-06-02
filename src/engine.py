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
import logging
import threading
from typing import Any, AsyncIterator

from . import config
from .models import model_path

log = logging.getLogger("mocca.engine")


class EngineError(RuntimeError):
    """Raised for load/generation failures, with a UI-friendly message."""


def is_available() -> bool:
    """True if llama-cpp-python is importable (i.e. the engine can run)."""
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


# Sentinel pushed onto the queue to mark end-of-stream.
_DONE = object()


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


# Module-level singleton; the whole app shares one loaded model.
_worker = _Worker()


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
    opts: dict[str, Any] = {}
    if options:
        for key in ("temperature", "top_p"):
            if options.get(key) is not None:
                opts[key] = options[key]
        # max_tokens <= 0 means "no explicit limit" → omit it.
        mt = options.get("max_tokens")
        if mt and mt > 0:
            opts["max_tokens"] = mt

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
