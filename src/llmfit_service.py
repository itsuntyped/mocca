"""Manages the bundled ``llmfit`` REST server and talks to its API.

Mocca ships with [llmfit](https://github.com/AlexsJones/llmfit) as a real
dependency (it installs as a platform wheel that bundles the binary). Rather
than shelling out per request, we start llmfit's HTTP server once when Mocca
boots and query it over its REST API:

    llmfit serve --host 127.0.0.1 --port <auto>

Endpoints we use (llmfit API v1):
    GET /health             -> {"status": "ok", ...}
    GET /api/v1/system      -> {"system": {total_ram_gb, gpu_vram_gb, ...}}
    GET /api/v1/models/top  -> ranked models with fit_level for this machine

Lifecycle is owned by the FastAPI ``lifespan`` in server.py: :meth:`start` on
boot, :meth:`stop` on shutdown. If the binary can't be found or the server
fails to come up, we log a warning and mark the service unavailable - Mocca
keeps working, just without hardware hints. The detected system info is cached
for the process lifetime (hardware doesn't change).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("mocca.llmfit")


def _find_executable() -> str | None:
    """Locate the llmfit binary.

    Checks, in order: the PyInstaller bundle (when frozen), next to the current
    Python (the pip wheel drops it in Scripts/bin), then anything on PATH.
    """
    exe_name = "llmfit.exe" if os.name == "nt" else "llmfit"
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / exe_name)  # bundled with the app
    candidates.append(Path(sys.executable).parent / exe_name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("llmfit")


def _free_port() -> int:
    """Ask the OS for a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlmfitService:
    """Owns the llmfit server subprocess and exposes async API helpers."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._ready: bool = False
        self._system: dict[str, Any] | None = None  # cached /system payload

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self._port}" if self._port else None

    def is_ready(self) -> bool:
        """True once the server has answered a health check."""
        return self._ready

    async def start(self) -> None:
        """Launch the llmfit server and wait until it's healthy."""
        exe = _find_executable()
        if not exe:
            log.warning(
                "llmfit not found; hardware hints disabled. "
                "Install it with 'pip install llmfit'."
            )
            return

        self._port = _free_port()
        log.info("Starting llmfit server (%s) on 127.0.0.1:%d", exe, self._port)
        # On Windows, keep the child's console from flashing up in the packaged
        # (windowed) app.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            # Detach the child's stdio; it's a server we poll over HTTP.
            self._proc = subprocess.Popen(
                [exe, "serve", "--host", "127.0.0.1", "--port", str(self._port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            log.warning("Could not start llmfit: %s", exc)
            self._proc = None
            return

        await self._await_health()

    async def _await_health(self, timeout: float = 20.0) -> None:
        """Poll /health until the server responds or we give up."""
        attempts = int(timeout / 0.5)
        async with httpx.AsyncClient(timeout=2.0) as client:
            for _ in range(attempts):
                # Bail early if the process died (e.g. bad args / port clash).
                if self._proc is not None and self._proc.poll() is not None:
                    log.warning("llmfit exited early (code %s)", self._proc.returncode)
                    return
                try:
                    resp = await client.get(f"{self.base_url}/health")
                    if resp.status_code == 200:
                        self._ready = True
                        log.info("llmfit server ready at %s", self.base_url)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        log.warning("llmfit server did not become ready within %.0fs", timeout)

    async def stop(self) -> None:
        """Terminate the llmfit server (graceful, then forced)."""
        self._ready = False
        if self._proc is None or self._proc.poll() is not None:
            return
        log.info("Stopping llmfit server")
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("llmfit did not stop gracefully; killing it")
            self._proc.kill()
        self._proc = None

    async def get_system(self, refresh: bool = False) -> dict[str, Any] | None:
        """Return llmfit's raw ``system`` info dict, or None if unavailable.

        Cached after the first successful fetch unless ``refresh`` is True.
        """
        if not self._ready:
            return None
        if self._system is not None and not refresh:
            return self._system
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/v1/system")
                resp.raise_for_status()
                self._system = resp.json().get("system")
        except httpx.HTTPError as exc:
            log.warning("llmfit /system request failed: %s", exc)
            return None
        return self._system

    async def get_top_models(
        self, limit: int = 10, min_fit: str = "good", use_case: str | None = None
    ) -> list[dict[str, Any]]:
        """Return llmfit's top models for this machine (empty list on failure)."""
        if not self._ready:
            return []
        params: dict[str, Any] = {"limit": limit, "min_fit": min_fit}
        if use_case:
            params["use_case"] = use_case
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self.base_url}/api/v1/models/top", params=params)
                resp.raise_for_status()
                return resp.json().get("models", [])
        except httpx.HTTPError as exc:
            log.warning("llmfit /models/top request failed: %s", exc)
            return []


# Module-level singleton shared across the app.
service = LlmfitService()
