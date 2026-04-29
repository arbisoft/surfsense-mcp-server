"""Main entry point for the SurfSense MCP Server."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn
from moneta_mcp_auth import (
    ServerMode,
    build_http_app,
    configure_json_logging,
    require_http_env_vars,
    resolve_http_port,
    resolve_log_level,
    warn_if_storage_missing_in_production,
)

from surfsense_mcp.server import get_header_mcp, get_stdio_mcp

logger = logging.getLogger("fastmcp.surfsense_mcp")


def main() -> None:
    """Run the MCP server."""
    configure_json_logging()

    server_mode = ServerMode.STDIO
    if len(sys.argv) > 1:
        server_mode = ServerMode(sys.argv[1])

    if not os.getenv("SURFSENSE_BASE_URL"):
        raise ValueError("SURFSENSE_BASE_URL is not set")

    if server_mode is ServerMode.STDIO:
        has_jwt = bool(os.getenv("SURFSENSE_JWT"))
        has_password_creds = bool(os.getenv("SURFSENSE_EMAIL")) and bool(os.getenv("SURFSENSE_PASSWORD"))
        if not has_jwt and not has_password_creds:
            raise ValueError(
                "stdio mode requires SURFSENSE_JWT, or both SURFSENSE_EMAIL "
                "and SURFSENSE_PASSWORD for the password-login fallback."
            )
        get_stdio_mcp().run()
        return

    require_http_env_vars()
    warn_if_storage_missing_in_production()

    level = resolve_log_level()
    configure_json_logging(level=level, logger_names=("uvicorn", "uvicorn.error"))

    app = build_http_app(get_header_mcp())
    port = resolve_http_port()
    logger.info("Starting HTTP server on :%d", port)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level=logging.getLevelName(level).lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
