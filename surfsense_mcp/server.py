"""SurfSense MCP Server implementation."""

from __future__ import annotations

import os

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from mcp.types import Icon

from surfsense_mcp.tools import register_tools

ICON = Icon(src="https://surfsense.net/favicon.ico", alt="SurfSense MCP Server")

# Default — covers Claude Desktop, Cursor, and MCP Inspector on a developer
# laptop. Non-localhost MCP clients (e.g. the Askii AI app) must be added via
# MCP_ALLOWED_CLIENT_REDIRECT_URIS to register at /register (DCR shim).
_DEFAULT_ALLOWED_CLIENT_REDIRECT_URIS: tuple[str, ...] = (
    "http://localhost:*/*",
    "http://127.0.0.1:*/*",
)


def _allowed_client_redirect_uris() -> list[str]:
    """Parse MCP_ALLOWED_CLIENT_REDIRECT_URIS (comma-separated).

    Unset/empty → localhost defaults. Operators opt in to broader allow-lists
    by listing specific patterns — do not allow arbitrary redirect URIs, they
    let an attacker DCR-register a malicious client and exfiltrate user tokens
    once the user authorizes.
    """
    raw = os.getenv("MCP_ALLOWED_CLIENT_REDIRECT_URIS", "").strip()
    if not raw:
        return list(_DEFAULT_ALLOWED_CLIENT_REDIRECT_URIS)

    allowed_uris = [uri.strip() for uri in raw.split(",") if uri.strip()]
    if not allowed_uris:
        return list(_DEFAULT_ALLOWED_CLIENT_REDIRECT_URIS)

    return allowed_uris
def get_header_mcp() -> FastMCP:
    """HTTP mode — FastMCP is the sole auth layer (mPass is NOT in front).

    ``AWSCognitoProvider`` makes this service a full OAuth 2.0 authorization
    server (via OAuthProxy / OIDCProxy). It publishes RFC 7591 + RFC 8414 +
    RFC 9728 discovery, implements the ``/register`` DCR shim backed by the
    pre-registered Cognito app client, and proxies ``/authorize`` /
    ``/auth/callback`` / ``/token`` to Cognito. MCP clients (Claude Desktop,
    Cursor) discover all of this automatically and run the OAuth flow without
    any manual Bearer paste.

    The same Cognito access token the client obtains is then Bearer-validated
    via Cognito's JWKS on every request to ``/mcp``; tools read the validated
    token's claims via ``get_access_token()`` and inject ``X-Auth-Request-User``
    when calling the SurfSense backend on the internal docker network.
    """
    from fastmcp.server.auth.providers.aws import AWSCognitoProvider

    provider = AWSCognitoProvider(
        user_pool_id=os.environ["COGNITO_USER_POOL_ID"],
        aws_region=os.environ["COGNITO_AWS_REGION"],
        client_id=os.environ["OIDC_CLIENT_ID"],
        client_secret=os.environ["OIDC_CLIENT_SECRET"],
        base_url=os.environ["MCP_BASE_URL"],
        redirect_path="/auth/callback",
        required_scopes=["openid"],
        allowed_client_redirect_uris=_allowed_client_redirect_uris(),
        # Cognito User Pools don't honor RFC 8707 Resource Indicators the way
        # the spec requires — forwarding `resource` on /authorize without it
        # being echoed on /token causes Cognito to return invalid_grant on the
        # token exchange. MCP clients still send `resource` to FastMCP; we just
        # don't pass it through to the upstream IdP.
        forward_resource=False,
    )
    mcp = FastMCP(
        "SurfSense MCP Server (http)",
        icons=[ICON],
        website_url="https://surfsense.net",
        auth=provider,
    )
    mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=True))
    register_tools(mcp)
    return mcp


def get_stdio_mcp() -> FastMCP:
    """Stdio mode — supports two upstream auth paths.

    It can use the ``SURFSENSE_JWT`` environment variable for upstream auth,
    or fall back to the email/password login flow provided by
    ``surfsense_mcp.auth.stdio``.
    """
    mcp = FastMCP("SurfSense MCP Server (stdio)", icons=[ICON])
    mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=True))
    register_tools(mcp)
    return mcp
