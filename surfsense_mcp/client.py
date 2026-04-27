"""HTTP transport for SurfSense backend calls.

This module owns ``httpx.AsyncClient`` wiring, JSON / multipart / SSE
helpers, and the 401-retry-once orchestration. It does *not* own auth — the
per-mode credential resolution lives in :mod:`surfsense_mcp.auth` (see
``auth/stdio.py`` and ``auth/http.py``). Tools should only need to call
:func:`authed_request`, :func:`authed_multipart_post`, or
:func:`stream_authed_post`.
"""

from __future__ import annotations

import os
from typing import NamedTuple

import httpx
from fastmcp.utilities.logging import get_logger

from surfsense_mcp import auth as _auth

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0


class SurfSenseClientContext(NamedTuple):
    """An authenticated httpx client bound to the user's SurfSense backend."""

    client: httpx.AsyncClient
    base_url: str


def _base_url() -> str:
    base_url = os.getenv("SURFSENSE_BASE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("SURFSENSE_BASE_URL is not configured")
    return base_url


async def get_surfsense_client_context() -> SurfSenseClientContext:
    """Return an httpx client configured with base URL + per-mode auth headers.

    Caller is responsible for closing the client (use as an async context
    manager). For 401 handling on stdio password mode, prefer
    :func:`authed_request` rather than driving the client directly.
    """
    base_url = _base_url()
    auth_headers = await _auth.build_auth_headers()
    client = httpx.AsyncClient(
        base_url=base_url,
        headers={**auth_headers, "Content-Type": "application/json"},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    return SurfSenseClientContext(client=client, base_url=base_url)


async def authed_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: object | None = None,
) -> httpx.Response:
    """JSON request with 401-retry-once when stdio password auth is in use.

    Every tool that hits a simple JSON endpoint should go through this helper
    so password-cached tokens get refreshed transparently. HTTP mode and
    stdio-with-paste-JWT both skip the retry — see
    :func:`surfsense_mcp.auth.auth_came_from_password`.
    """

    async def _do() -> httpx.Response:
        ctx = await get_surfsense_client_context()
        async with ctx.client as client:
            return await client.request(method, path, params=params, json=json)

    response = await _do()
    if response.status_code == 401 and _auth.auth_came_from_password():
        logger.info("401 with password auth — invalidating cached token and retrying")
        _auth.invalidate_password_token()
        response = await _do()
    response.raise_for_status()
    return response


async def authed_multipart_post(
    path: str,
    *,
    files: dict[str, tuple[str, bytes, str]],
    data: dict[str, str] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    """Multipart POST with 401-retry-once. Used by document uploads.

    The shared client context's JSON Content-Type would break multipart, so
    this helper drives its own AsyncClient.
    """
    request_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS

    async def _do() -> httpx.Response:
        auth_headers = await _auth.build_auth_headers()
        async with httpx.AsyncClient(
            base_url=_base_url(),
            timeout=request_timeout,
            headers=auth_headers,
        ) as client:
            return await client.post(path, files=files, data=data)

    response = await _do()
    if response.status_code == 401 and _auth.auth_came_from_password():
        logger.info("401 with password auth on multipart — invalidating and retrying")
        _auth.invalidate_password_token()
        response = await _do()
    response.raise_for_status()
    return response


class _StreamContext:
    """Async context manager that yields an httpx streaming response and
    retries once on 401 if stdio password auth is in use.

    Use as::

        async with stream_authed_post(path, json=payload) as response:
            async for line in response.aiter_lines():
                ...
    """

    def __init__(self, path: str, json: object) -> None:
        self._path = path
        self._json = json
        self._client: httpx.AsyncClient | None = None
        self._response: httpx.Response | None = None

    async def __aenter__(self) -> httpx.Response:
        self._response = await self._open()
        if self._response.status_code == 401 and _auth.auth_came_from_password():
            logger.info("401 with password auth on SSE — invalidating and retrying")
            await self._close_active()
            _auth.invalidate_password_token()
            self._response = await self._open()
        self._response.raise_for_status()
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._close_active()

    async def _open(self) -> httpx.Response:
        auth_headers = await _auth.build_auth_headers()
        self._client = httpx.AsyncClient(
            base_url=_base_url(),
            timeout=httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, read=None),
            headers={
                **auth_headers,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        request = self._client.build_request("POST", self._path, json=self._json)
        return await self._client.send(request, stream=True)

    async def _close_active(self) -> None:
        if self._response is not None:
            try:
                await self._response.aclose()
            except Exception:
                logger.exception("Error closing streaming response")
            self._response = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception("Error closing streaming client")
            self._client = None


def stream_authed_post(path: str, *, json: object) -> _StreamContext:
    """Open an SSE stream with auth + 401-retry. Caller uses ``async with``."""
    return _StreamContext(path, json)
