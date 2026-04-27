"""HTTP-mode auth: derive ``X-Auth-Request-User`` from the validated Cognito token.

In HTTP mode FastMCP's ``AWSCognitoProvider`` validates the inbound Bearer
against the Cognito user pool's JWKS and exposes the filtered claims via
``get_access_token()``. We never forward the Cognito Bearer to the SurfSense
backend — instead we extract the ``username`` claim and inject it as
``X-Auth-Request-User``, mirroring how oauth2-proxy fronts the web apps.
SurfSense's ``ProxyAuthMiddleware`` reads the header, synthesizes
``{username}@{SMB_NAME}.com`` if the user has no email yet, and
auto-provisions/loads the user. The MCP → backend call therefore goes direct
on the docker network with no second OIDC round-trip.

This module is intentionally tiny: no I/O, no module-level state, no caches.
"""

from __future__ import annotations

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

    Raises ``RuntimeError`` with an actionable message if the ``username``
    claim is missing or non-string. We fail fast rather than silently send a
    request with no identity, because a request with no header would be
    auto-provisioned as some other (anonymous-ish) user on the backend.
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
