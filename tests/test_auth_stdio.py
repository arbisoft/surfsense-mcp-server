"""Unit tests for ``surfsense_mcp.auth.stdio``.

The password cache is module-level state guarded by a ``Lock``. Smoke coverage
through ``test_tools.py`` exercises it end-to-end, but the cache TTL, the
``TOKEN_TTL`` parser, and the resolution-order branches deserve direct unit
tests so a regression here can't hide behind a successful integration test.
"""

from __future__ import annotations

import httpx
import pytest

from surfsense_mcp.auth import stdio


@pytest.fixture(autouse=True)
def _clean_cache_and_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no env JWT, no password creds, no cached token."""
    monkeypatch.setenv("SURFSENSE_BASE_URL", "https://surfsense.test")
    monkeypatch.delenv("SURFSENSE_JWT", raising=False)
    monkeypatch.delenv("SURFSENSE_EMAIL", raising=False)
    monkeypatch.delenv("SURFSENSE_PASSWORD", raising=False)
    monkeypatch.delenv("TOKEN_TTL", raising=False)
    stdio.invalidate_cache()


def _install_login_handler(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Patch ``httpx.AsyncClient`` to route through ``handler`` and record requests."""
    recorded: list[httpx.Request] = []
    original_init = httpx.AsyncClient.__init__

    def wrapped(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return handler(request)

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(wrapped)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return recorded


# ---------------------------------------------------------------------------
# resolve_jwt — primary paste path
# ---------------------------------------------------------------------------


async def test_resolve_jwt_returns_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_JWT", "paste-jwt-abc")
    assert await stdio.resolve_jwt() == "paste-jwt-abc"


async def test_resolve_jwt_env_takes_precedence_over_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env JWT is the primary path; password creds are a fallback only."""
    monkeypatch.setenv("SURFSENSE_JWT", "paste-jwt-abc")
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")
    recorded = _install_login_handler(monkeypatch, lambda req: httpx.Response(500))

    assert await stdio.resolve_jwt() == "paste-jwt-abc"
    # No HTTP call should have happened.
    assert recorded == []


async def test_resolve_jwt_raises_when_nothing_configured() -> None:
    with pytest.raises(RuntimeError, match="No SurfSense credential available"):
        await stdio.resolve_jwt()


# ---------------------------------------------------------------------------
# resolve_jwt — password fallback + cache
# ---------------------------------------------------------------------------


async def test_resolve_jwt_password_fallback_logs_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/auth/jwt/login"
        # fastapi-users expects form-encoded credentials.
        assert req.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
        body = req.content.decode()
        assert "username=user%40example.com" in body
        assert "password=hunter2" in body
        return httpx.Response(200, json={"access_token": "fresh-token", "token_type": "bearer"})

    _install_login_handler(monkeypatch, handler)
    assert await stdio.resolve_jwt() == "fresh-token"


async def test_resolve_jwt_uses_cached_token_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call within the TTL window must not hit the login endpoint."""
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "fresh-token", "token_type": "bearer"})

    recorded = _install_login_handler(monkeypatch, handler)
    first = await stdio.resolve_jwt()
    second = await stdio.resolve_jwt()

    assert first == second == "fresh-token"
    assert len(recorded) == 1, "Second resolve should hit the cache, not /auth/jwt/login"


async def test_invalidate_cache_forces_relogin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")

    counter = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(200, json={"access_token": f"token-v{counter['n']}", "token_type": "bearer"})

    _install_login_handler(monkeypatch, handler)
    assert await stdio.resolve_jwt() == "token-v1"

    stdio.invalidate_cache()
    assert await stdio.resolve_jwt() == "token-v2"


async def test_resolve_jwt_raises_on_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "wrong")
    _install_login_handler(monkeypatch, lambda req: httpx.Response(400, json={"detail": "Bad credentials"}))

    with pytest.raises(RuntimeError, match="password login failed: 400"):
        await stdio.resolve_jwt()


async def test_resolve_jwt_raises_when_login_returns_no_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")
    _install_login_handler(monkeypatch, lambda req: httpx.Response(200, json={"token_type": "bearer"}))

    with pytest.raises(RuntimeError, match="returned no access_token"):
        await stdio.resolve_jwt()


# ---------------------------------------------------------------------------
# is_password_in_use — retry-gate predicate
# ---------------------------------------------------------------------------


def test_is_password_in_use_false_when_env_jwt_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_JWT", "paste-jwt")
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")
    # Env JWT takes precedence — no point retrying since we have no way to
    # mint a fresh paste token automatically.
    assert stdio.is_password_in_use() is False


def test_is_password_in_use_true_with_only_password_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    monkeypatch.setenv("SURFSENSE_PASSWORD", "hunter2")
    assert stdio.is_password_in_use() is True


def test_is_password_in_use_false_with_neither() -> None:
    assert stdio.is_password_in_use() is False


def test_is_password_in_use_false_with_partial_password_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Email without password (or vice versa) doesn't count — both required."""
    monkeypatch.setenv("SURFSENSE_EMAIL", "user@example.com")
    assert stdio.is_password_in_use() is False


# ---------------------------------------------------------------------------
# _token_ttl_seconds — TOKEN_TTL parsing
# ---------------------------------------------------------------------------


def test_token_ttl_default_when_unset() -> None:
    assert stdio._token_ttl_seconds() == stdio._DEFAULT_TOKEN_TTL_SECONDS


def test_token_ttl_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_TTL", "120")
    assert stdio._token_ttl_seconds() == 120


def test_token_ttl_clamps_below_60(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TTL under a minute would cause near-constant re-login churn —
    clamp to the floor instead of trusting tiny values."""
    monkeypatch.setenv("TOKEN_TTL", "5")
    assert stdio._token_ttl_seconds() == 60


def test_token_ttl_falls_back_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_TTL", "not-a-number")
    assert stdio._token_ttl_seconds() == stdio._DEFAULT_TOKEN_TTL_SECONDS
