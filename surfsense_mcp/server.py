"""SurfSense MCP Server implementation."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from mcp.types import Icon
from moneta_mcp_auth import build_cognito_mcp, log_payloads_enabled

from surfsense_mcp.tools import register_tools

ICON = Icon(src="https://surfsense.net/favicon.ico", alt="SurfSense MCP Server")


def get_header_mcp() -> FastMCP:
    """HTTP mode — FastMCP is the sole auth layer (mPass is NOT in front).

    All Cognito wiring (provider construction, OAuth state storage, DCR shim,
    discovery endpoints, claim → ``X-Auth-Request-User`` extraction) is
    delegated to ``moneta_mcp_auth.build_cognito_mcp``. The SurfSense backend's
    ``ProxyAuthMiddleware`` reads the injected header and auto-provisions the
    user on the internal docker-network call.
    """
    return build_cognito_mcp(
        server_name="SurfSense MCP Server (http)",
        register_tools=register_tools,
        icons=[ICON],
        website_url="https://surfsense.net",
    )


def get_stdio_mcp() -> FastMCP:
    """Stdio mode — supports two upstream auth paths.

    It can use the ``SURFSENSE_JWT`` environment variable for upstream auth,
    or fall back to the email/password login flow provided by
    ``surfsense_mcp.auth.stdio``.
    """
    mcp = FastMCP("SurfSense MCP Server (stdio)", icons=[ICON])
    mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=log_payloads_enabled()))
    register_tools(mcp)
    return mcp
