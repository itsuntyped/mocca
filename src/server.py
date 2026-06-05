"""The Mocca web server: FastAPI app setup, lifespan, and route registration.

The HTTP endpoints live in the ``src/routes`` package, one module per area
(system, models, folders, sessions, chat); this file just creates the app,
manages startup/shutdown, serves the single HTML page, and includes the
routers. Streaming endpoints use Server-Sent Events (see ``src/sse.py``).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import catalog, config, database, engine
from .paths import STATIC_DIR, TEMPLATES_DIR
from .routes import chat, documents, folders, memory, models, sessions, system
from .tools import registry as tools
from .version import __version__

log = logging.getLogger("mocca.server")


class RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that asks the browser to revalidate every asset.

    Mocca's frontend is a no-build set of ES modules: ``index.html`` loads
    ``main.js``, which ``import``s the others by relative path. With normal
    browser caching, an edited module can keep serving the stale version (and a
    version query string on ``main.js`` wouldn't help - the relative imports
    carry no query, and during development we rarely bump the version anyway).

    Setting ``Cache-Control: no-cache`` makes the browser revalidate each file
    on every load using the ETag/Last-Modified that StaticFiles already sends:
    changed files are re-fetched (so edits and upgrades show up immediately),
    unchanged files get a cheap ``304 Not Modified``. For a local, single-user
    app the per-load revalidation cost is negligible, and correctness wins.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# How often the idle-unload watcher checks (seconds). Coarse on purpose: this is
# a memory-reclaim convenience, not something that needs to be precise.
_IDLE_CHECK_SECONDS = 60


async def _idle_unload_watcher() -> None:
    """Unload the model after it has been idle past the configured threshold.

    Frees the multi-GB model (and any grown KV cache) when a chat is left
    unattended; the next message reloads it. Runs the actual unload in a thread
    because it briefly takes the engine lock. Gated on the user's
    ``unload_idle_minutes`` setting (0 = never).
    """
    while True:
        await asyncio.sleep(_IDLE_CHECK_SECONDS)
        minutes = config.get().unload_idle_minutes
        if minutes and minutes > 0 and (engine.idle_seconds() or 0) >= minutes * 60:
            await asyncio.to_thread(engine.unload_if_idle, minutes * 60)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot/shutdown tasks. (Logging/config are set up by run.py first.)"""
    database.init_db()
    tools.discover()  # Find and register all tools the AI can call.
    # Warm the model catalog from Hugging Face in the background so the Models
    # window opens instantly. Non-blocking and non-fatal: get_catalog swallows
    # network errors, so if we're offline it just doesn't populate (the UI shows
    # an offline message and offers Refresh). Never delays or breaks startup.
    prefetch = asyncio.create_task(catalog.get_catalog())
    # Reclaim model memory when a chat is left idle (see _idle_unload_watcher).
    idle_watcher = asyncio.create_task(_idle_unload_watcher())
    log.info("Mocca server started (engine available: %s)", engine.is_available())
    yield
    for task in (prefetch, idle_watcher):
        if not task.done():
            task.cancel()
    log.info("Mocca server stopping")


app = FastAPI(title="Mocca", version=__version__, docs_url="/api/docs", lifespan=lifespan)

# Serve CSS/JS and the single HTML page. The static mount revalidates assets on
# every load (see RevalidatingStaticFiles) so updated modules are never stale.
app.mount("/static", RevalidatingStaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the single-page web UI.

    Sent with ``no-cache`` for the same reason as the static assets: the page
    references ``main.js`` by a fixed path, so a stale cached page could keep
    pulling in old module URLs. Revalidating keeps the entry point fresh too.
    """
    response = templates.TemplateResponse(request, "index.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


# Register the API routers (each defines its own /api/... paths).
app.include_router(system.router)
app.include_router(models.router)
app.include_router(folders.router)
app.include_router(sessions.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(memory.router)
