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
from .routes import chat, folders, models, sessions, system
from .routes import tools as tools_routes
from .tools import registry as tools
from .version import __version__

log = logging.getLogger("mocca.server")


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

# Serve CSS/JS and the single HTML page.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the single-page web UI."""
    return templates.TemplateResponse(request, "index.html")


# Register the API routers (each defines its own /api/... paths).
app.include_router(system.router)
app.include_router(models.router)
app.include_router(folders.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(tools_routes.router)
