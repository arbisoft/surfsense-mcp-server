"""Per-request auth headers for SurfSense backend calls.

Two transports, two strategies:

- **stdio** (single-user, on a developer's laptop) → ``Authorization: Bearer
  <surfsense-jwt>`` from ``SURFSENSE_JWT`` or the password fallback.
- **http** (multi-user, behind FastMCP's ``AWSCognitoProvider``) →
  ``X-Auth-Request-User`` derived from the validated Cognito token's
  ``username`` claim. The Cognito Bearer is *not* forwarded.

The dispatcher in :func:`build_auth_headers` picks based on whether a FastMCP
HTTP request scope is active. Generic Cognito-side wiring (claim extraction,
``AccessToken`` access) lives in the shared ``moneta_mcp_auth`` lib; the
SurfSense-specific stdio JWT/password fallback stays in
:mod:`surfsense_mcp.auth.stdio`.
"""

from __future__ import annotations

from moneta_mcp_auth import extract_header_from_token, request_token

from surfsense_mcp.auth import stdio as _stdio

__all__ = [
    "auth_came_from_password",
    "build_auth_headers",
    "invalidate_password_token",
]


async def build_auth_headers() -> dict[str, str]:
    """Return the auth header(s) to attach to the next SurfSense backend call."""
    token = request_token()
    if token is not None:
        return extract_header_from_token(token)
    return {"Authorization": f"Bearer {await _stdio.resolve_jwt()}"}


def auth_came_from_password() -> bool:
    """Gate the 401-retry-once path.

    HTTP mode: never retry — a 401 means the Cognito token validated fine but
    the backend rejected the user (real provisioning failure), and we want to
    surface that. Stdio with a paste JWT: never retry — the user pastes a new
    one. Stdio with password fallback: retry — the cached token may have
    expired server-side before our local TTL.
    """
    if request_token() is not None:
        return False
    return _stdio.is_password_in_use()


def invalidate_password_token() -> None:
    """Drop the stdio password-cache so the next call forces a fresh login."""
    _stdio.invalidate_cache()
