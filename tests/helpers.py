"""Shared test helpers: fake HTTP clients for the network tools.

The network tools (web_search, fetch_url, youtube, shipping) all reach the
internet through ``httpx.AsyncClient``. To test their parsing and error handling
*in depth* without a flaky, slow, real network call, we swap that client for a
fake that returns canned responses. This keeps the suite offline and
deterministic (see scripts/test.py) while still exercising the full ``_run`` path
- query/URL validation, the HTTP call shape, response parsing, and truncation.

Use :func:`patch_httpx` as a context manager around the module under test:

    with patch_httpx(web_search, handler):
        result = asyncio.run(web_search._run({"query": "hi"}))

``handler`` is called as ``handler(method, url, kwargs)`` and returns a
:class:`FakeResponse` (or raises to simulate a network failure).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable
from unittest import mock

import httpx


class FakeResponse:
    """A stand-in for ``httpx.Response`` covering only what the tools use."""

    def __init__(
        self,
        text: str = "",
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        ok: bool = True,
    ) -> None:
        self.text = text
        self._json = json_data
        self.headers = headers or {}
        self._ok = ok

    def raise_for_status(self) -> None:
        # Mirror httpx: a bad status raises an HTTPError the tools catch.
        if not self._ok:
            raise httpx.HTTPError("fake non-2xx response")

    def json(self) -> Any:
        # Mirror httpx/json: a non-JSON body raises ValueError, which the tools
        # treat as "no usable data" rather than crashing.
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json


# A handler decides what a faked request returns: (method, url, kwargs) -> response.
Handler = Callable[[str, str, dict[str, Any]], FakeResponse]


class _FakeAsyncClient:
    """Async-context-manager client whose get/post defer to a module-level handler."""

    # Set per-instance via the factory in patch_httpx so each patch is isolated.
    _handler: Handler

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Accept and ignore timeout/follow_redirects/etc. - we don't need them.
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return type(self)._handler("GET", url, kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return type(self)._handler("POST", url, kwargs)


@contextmanager
def patch_httpx(module: Any, handler: Handler):
    """Patch ``module.httpx.AsyncClient`` with a fake driven by ``handler``.

    Yields nothing; restores the real client on exit. ``module`` is the tool
    module (it does ``import httpx`` and calls ``httpx.AsyncClient(...)``), so we
    patch the ``AsyncClient`` attribute on the httpx the module references.
    """
    client_cls = type("BoundFakeAsyncClient", (_FakeAsyncClient,), {"_handler": staticmethod(handler)})
    with mock.patch.object(module.httpx, "AsyncClient", client_cls):
        yield
