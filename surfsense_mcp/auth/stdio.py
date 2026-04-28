"""Stdio-mode auth: SurfSense JWT (env paste or password fallback).

The stdio transport runs as a subprocess of an MCP client (Claude Desktop,
Cursor, …) on the user's laptop. SurfSense's fastapi-users issues short-lived
JWTs, so this module owns:

- ``SURFSENSE_JWT`` — the primary path: user pastes a fresh token when one
  expires. The server never refreshes it.
- ``SURFSENSE_EMAIL`` + ``SURFSENSE_PASSWORD`` — optional fallback for CI /
  long-running sessions. We exchange these for a JWT via ``POST /auth/jwt/login``,
  cache it for ``TOKEN_TTL`` seconds (default 3300 = 55 min, comfortably
  inside fastapi-users' typical 60-min expiry), and let the client retry
  once on 401 to recover when the cache outlives the server-side token.

The cache is module-level state guarded by a ``Lock`` so that concurrent
tool calls don't double-issue logins.
"""

from __future__ import annotations

import asyncio
import os
import time
from threading import Lock

import httpx
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_TOKEN_TTL_SECONDS = 3300

_cached_password_token: str | None = None
_cached_password_token_expires_at: float = 0.0
_password_token_lock = Lock()
_password_login_lock = asyncio.Lock()


def _base_url() -> str:
    base_url = os.getenv("SURFSENSE_BASE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("SURFSENSE_BASE_URL is not configured")
    return base_url


def _token_ttl_seconds() -> int:
    raw = os.getenv("TOKEN_TTL")
    if not raw:
        return _DEFAULT_TOKEN_TTL_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return _DEFAULT_TOKEN_TTL_SECONDS


def _has_password_creds() -> bool:
    return bool(os.getenv("SURFSENSE_EMAIL")) and bool(os.getenv("SURFSENSE_PASSWORD"))


async def _login_with_password() -> str:
    """Exchange SURFSENSE_EMAIL + SURFSENSE_PASSWORD for a JWT via fastapi-users."""
    email = os.getenv("SURFSENSE_EMAIL", "")
    password = os.getenv("SURFSENSE_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("Password-login fallback requires SURFSENSE_EMAIL and SURFSENSE_PASSWORD.")

    url = f"{_base_url()}/auth/jwt/login"
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url,
            data={"username": email, "password": password},
        )
    if response.status_code != 200:
        raise RuntimeError(f"SurfSense password login failed: {response.status_code} {response.text[:200]}")
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError("SurfSense password login returned no access_token")
    logger.info("Authenticated with SurfSense via password (TTL %ds)", _token_ttl_seconds())
    return token


def _get_cached_token() -> str | None:
    with _password_token_lock:
        if _cached_password_token and time.time() < _cached_password_token_expires_at:
            return _cached_password_token
    return None


def _store_cached_token(token: str) -> None:
    global _cached_password_token, _cached_password_token_expires_at
    with _password_token_lock:
        _cached_password_token = token
        _cached_password_token_expires_at = time.time() + _token_ttl_seconds()


def invalidate_cache() -> None:
    """Drop the cached password token so the next call forces a fresh login."""
    global _cached_password_token, _cached_password_token_expires_at
    with _password_token_lock:
        _cached_password_token = None
        _cached_password_token_expires_at = 0.0


def is_password_in_use() -> bool:
    """True iff stdio is currently relying on password-fallback (no env JWT,
    both email + password configured). Used by the dispatcher to gate the
    401-retry-once path: env-JWT and HTTP modes don't benefit from a retry,
    only the password cache does.
    """
    if os.getenv("SURFSENSE_JWT"):
        return False
    return _has_password_creds()


async def resolve_jwt() -> str:
    """Return a SurfSense JWT for stdio mode.

    Resolution order:
      1. ``SURFSENSE_JWT`` env var (primary, paste-based path)
      2. Password fallback — cached token if fresh, otherwise log in and cache.

    Raises ``RuntimeError`` with an actionable message if neither is configured.
    """
    env_token = os.getenv("SURFSENSE_JWT", "")
    if env_token:
        return env_token

    if _has_password_creds():
        cached = _get_cached_token()
        if cached:
            return cached
        # Serialize concurrent logins so an empty cache + N parallel tool
        # calls produce one POST /auth/jwt/login, not N. Re-check inside the
        # lock — by the time we acquire it, an earlier caller may have
        # already populated the cache.
        async with _password_login_lock:
            cached = _get_cached_token()
            if cached:
                return cached
            token = await _login_with_password()
            _store_cached_token(token)
            return token

    raise RuntimeError(
        "No SurfSense credential available. Set SURFSENSE_JWT, "
        "or SURFSENSE_EMAIL + SURFSENSE_PASSWORD for stdio fallback, "
        "or connect via HTTP so AWSCognitoProvider can validate the Cognito Bearer."
    )
