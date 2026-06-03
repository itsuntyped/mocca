"""User-editable settings, persisted to ``data/config.json``.

Settings are intentionally simple: a flat JSON document loaded into a
dataclass. ``load()`` fills in defaults for any missing keys so upgrading
Mocca never breaks an old config file. ``save()`` writes it back atomically.

The web UI reads/writes these via the ``/api/settings`` endpoints.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from typing import Any

# Tool categories enabled by default. Local-only categories are on; the "web"
# category is intentionally omitted so Mocca stays local unless the user opts in
# (see CLAUDE.md goal #2). These must match the categories the tools declare.
_DEFAULT_TOOL_CATEGORIES = ["math", "time", "files"]

from .paths import CONFIG_FILE, ensure_dirs

log = logging.getLogger("mocca.config")


@dataclass
class Settings:
    """All persisted, user-facing configuration.

    Mocca is a standalone app: it runs GGUF models in-process via
    llama-cpp-python, so there is no external server address to configure.
    The UI is dark-theme only by design, so there is no theme toggle either.
    """

    # Filename of the model used by default for new chats (a .gguf in
    # data/models/). Empty == "ask the user to pick / download one".
    default_model: str = ""

    # Optional system prompt prepended to every conversation.
    system_prompt: str = "You are Mocca, a helpful local AI assistant."

    # --- Sampling controls passed to the engine per request ----------------
    temperature: float = 0.7
    top_p: float = 0.9
    # Max tokens to generate per reply (<= 0 means "until the context fills").
    max_tokens: int = 1024

    # --- Engine (llama.cpp) load-time controls -----------------------------
    # Context window in tokens. Larger = more memory.
    n_ctx: int = 4096
    # Layers to offload to GPU. 0 = pure CPU (safe default everywhere);
    # raise it if you have a supported GPU build of llama-cpp-python.
    n_gpu_layers: int = 0
    # CPU threads for generation. 0 = let llama.cpp pick automatically.
    n_threads: int = 0

    # Verbosity of the application log. One of: DEBUG, INFO, WARNING, ERROR.
    log_level: str = "INFO"

    # --- Tools -------------------------------------------------------------
    # Which tool categories the AI may use. The "web" category (network access)
    # is off by default; the user enables it explicitly in Settings.
    enabled_tool_categories: list[str] = field(
        default_factory=lambda: list(_DEFAULT_TOOL_CATEGORIES)
    )

    # Whether the chat UI shows the assistant's tool calls (the collapsible
    # "Used tool" blocks). Off by default to keep the conversation clean; tool
    # calls are still executed and stored either way, just hidden until enabled.
    show_tool_calls: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# A module-level singleton so the rest of the app shares one Settings object.
_settings: Settings | None = None


def _known_keys() -> set[str]:
    return {f.name for f in fields(Settings)}


def _default_gpu_layers() -> int:
    """First-run default for n_gpu_layers.

    On a CUDA (GPU) engine build, offload the whole model by default (99 caps to
    the model's real layer count in llama.cpp); on a CPU build stay at 0. This is
    why the CUDA download runs on the GPU out of the box. Only applied when the
    config file doesn't exist yet, so it never overrides a user's choice. Lazily
    imports engine to avoid a circular import; engine's own import is cheap (it
    doesn't load llama_cpp here).
    """
    try:
        from . import engine
        return 99 if engine.is_cuda_build() else 0
    except Exception:  # noqa: BLE001 - detection must never block config creation
        return 0


def load() -> Settings:
    """Load settings from disk (creating the file with defaults on first run)."""
    global _settings
    if not CONFIG_FILE.exists():
        log.info("No config file found; writing defaults to %s", CONFIG_FILE)
        _settings = Settings(n_gpu_layers=_default_gpu_layers())
        save(_settings)
        return _settings

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt config shouldn't brick the app; fall back to defaults and
        # log loudly so the user knows what happened.
        log.error("Could not read config (%s); using defaults", exc)
        _settings = Settings()
        return _settings

    # Drop unknown keys (forward-compat) and let the dataclass supply defaults
    # for anything missing (backward-compat).
    known = _known_keys()
    filtered = {k: v for k, v in raw.items() if k in known}
    _settings = Settings(**filtered)
    log.debug("Loaded settings: %s", _settings)
    return _settings


def get() -> Settings:
    """Return the current settings, loading them on first access."""
    return _settings if _settings is not None else load()


def save(settings: Settings) -> None:
    """Persist settings to disk atomically (write to temp, then replace)."""
    global _settings
    ensure_dirs()
    _settings = settings
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    log.info("Settings saved to %s", CONFIG_FILE)


def update(patch: dict[str, Any]) -> Settings:
    """Apply a partial update to the current settings and persist it.

    Only recognised keys are applied; anything else is ignored.
    """
    current = get()
    known = _known_keys()
    data = current.to_dict()
    for key, value in patch.items():
        if key in known:
            data[key] = value
    new = Settings(**data)
    save(new)
    return new
