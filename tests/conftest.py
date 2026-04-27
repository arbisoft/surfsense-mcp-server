"""Shared pytest fixtures for the SurfSense MCP Server tests."""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx
import pytest

FAKE_BASE_URL = "https://surfsense.test"
FAKE_JWT = "test-jwt-token"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env vars the client layer requires and reset auth caches."""
    monkeypatch.setenv("SURFSENSE_BASE_URL", FAKE_BASE_URL)
    monkeypatch.setenv("SURFSENSE_JWT", FAKE_JWT)
    # Make sure no leftover email/password creds from a prior test leak in.
    monkeypatch.delenv("SURFSENSE_EMAIL", raising=False)
    monkeypatch.delenv("SURFSENSE_PASSWORD", raising=False)
    monkeypatch.delenv("TOKEN_TTL", raising=False)
    # Reset the password-token cache so tests are independent.
    from surfsense_mcp import auth as _auth

    _auth.invalidate_password_token()


@pytest.fixture
def mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]:
    """Patch httpx.AsyncClient so tool calls hit an in-memory handler.

    Returns a setup fn that takes a handler and returns a recorded-requests
    list. Every request the tool makes is appended to that list so tests can
    assert on URL, query string, and headers.
    """
    recorded: list[httpx.Request] = []

    def setup(handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
        def wrapped(request: httpx.Request) -> httpx.Response:
            recorded.append(request)
            return handler(request)

        original_init = httpx.AsyncClient.__init__

        def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(wrapped)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
        return recorded

    return setup


def json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


# Make fixtures importable from test modules.
__all__ = ["FAKE_BASE_URL", "FAKE_JWT", "json_response", "mock_transport"]

# Guard against a stray SURFSENSE_JWT in the developer's shell.
if "SURFSENSE_JWT" in os.environ and os.environ.get("SURFSENSE_JWT") != FAKE_JWT:
    # pytest monkeypatch fixture above will overwrite it per-test, but this
    # gives a clearer signal if someone imports the module outside pytest.
    pass
