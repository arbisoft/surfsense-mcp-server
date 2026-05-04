"""HTTP-mode auth: dispatch identity-header vs Cognito-Bearer by URL scheme.

In HTTP mode FastMCP's ``AWSCognitoProvider`` validates the inbound Bearer
against the Cognito user pool's JWKS and exposes the filtered claims via
``get_access_token()``. There are two supported ways to relay that identity
to the SurfSense backend, and the right one depends on whether the request
goes through Traefik:

* **HTTPS base URL** (e.g. ``https://foss-research.local.moneta.dev``) —
  request traverses Traefik + mPass. Traefik strips ``X-Auth-Request-*``
  headers, so we forward the validated Cognito Bearer untouched and let
  oauth2-proxy validate it (against the same JWKS) and set
  ``X-Auth-Request-User`` itself before the request reaches SurfSense.
* **HTTP base URL** (e.g. ``http://surfsense-backend:8000``) — direct on
  the docker network with no Traefik in the path. We inject
  ``X-Auth-Request-User`` ourselves from the ``username`` claim; SurfSense's
  ``ProxyAuthMiddleware`` reads it and auto-provisions the user.

The dispatcher in :func:`auth_headers_for_token` reads ``SURFSENSE_BASE_URL``
inline rather than threading it through call signatures — keeps the public
shape of :mod:`surfsense_mcp.auth` (``build_auth_headers() -> dict``)
unchanged.
"""

from __future__ import annotations

import os

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token


def request_token() -> AccessToken | None:
    """Return the FastMCP-validated access token for the in-flight HTTP request.

    Returns ``None`` outside an HTTP request scope (i.e. in stdio mode), where
    ``get_access_token()`` raises ``RuntimeError`` because there's no auth
    context to read from. The dispatcher in ``surfsense_mcp.auth`` uses the
    ``None`` return as the signal to fall back to stdio JWT resolution.
    """
    try:
        return get_access_token()
    except RuntimeError:
        return None


def username_header(token: AccessToken) -> dict[str, str]:
    """Build the ``{X-Auth-Request-User: <username>}`` header from a validated token.

    Used on the trusted internal-DNS path (HTTP base URL). Raises
    ``RuntimeError`` with an actionable message if the ``username`` claim is
    missing or non-string. We fail fast rather than silently send a request
    with no identity, because a header-less request would be auto-provisioned
    as some other (anonymous-ish) user on the backend.
    """
    claims = token.claims or {}
    username = claims.get("username")
    if not isinstance(username, str) or not username:
        raise RuntimeError(
            "Cognito username claim missing on validated token — cannot identify "
            "the upstream SurfSense user. Check the Cognito user pool's user_id_claim "
            "and that AWSCognitoProvider is in use."
        )
    return {"X-Auth-Request-User": username}


def bearer_header(token: AccessToken) -> dict[str, str]:
    """Forward the validated Cognito Bearer to oauth2-proxy / mPass.

    Used on the public-URL path (HTTPS base URL). Traefik's
    ``strip-auth-headers`` middleware removes any ``X-Auth-Request-*`` we
    might inject, so identity has to ride the standard ``Authorization``
    header for oauth2-proxy to pick up. ``token.token`` is the raw JWT
    string FastMCP's ``AWSCognitoProvider`` already validated against the
    Cognito JWKS, so oauth2-proxy will validate the same JWT against the
    same JWKS and accept it.
    """
    raw = token.token
    if not raw:
        raise RuntimeError(
            "Validated AccessToken has no raw token string — cannot forward "
            "to oauth2-proxy. This means AWSCognitoProvider produced a token "
            "without preserving the original JWT, which should not happen."
        )
    return {"Authorization": f"Bearer {raw}"}


def auth_headers_for_token(token: AccessToken) -> dict[str, str]:
    """Pick the auth strategy based on whether the call goes through mPass.

    Reads ``SURFSENSE_BASE_URL`` inline. The check is intentionally simple
    (scheme prefix) — operators who need a different rule can override
    explicitly by editing this function rather than juggling another env var.
    """
    base_url = os.getenv("SURFSENSE_BASE_URL", "")
    if base_url.startswith("https://"):
        return bearer_header(token)
    return username_header(token)
