"""The Mocca web server: FastAPI app setup, lifespan, and route registration.

The HTTP endpoints live in the ``src/routes`` package, one module per area
(system, models, folders, sessions, chat); this file just creates the app,
manages startup/shutdown, serves the single HTML page, and includes the
routers. Streaming endpoints use Server-Sent Events (see ``src/sse.py``).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import database, engine
from .llmfit_service import service as llmfit
from .paths import STATIC_DIR, TEMPLATES_DIR
from .routes import chat, folders, memory, models, sessions, system
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot/shutdown tasks. (Logging/config are set up by run.py first.)

    On startup we prepare storage and launch the bundled llmfit server in the
    background; on shutdown we stop it cleanly.
    """
    database.init_db()
    tools.discover()  # Find and register all tools the AI can call.
    await llmfit.start()
    log.info("Mocca server started (engine available: %s)", engine.is_available())
    yield
    await llmfit.stop()
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
app.include_router(chat.router)
app.include_router(memory.router)
