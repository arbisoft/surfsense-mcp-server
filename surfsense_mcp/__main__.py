"""Main entry point for the SurfSense MCP Server."""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from enum import Enum

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from surfsense_mcp.server import get_header_mcp, get_stdio_mcp


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }
        return json.dumps(log_entry)


def configure_json_logging() -> None:
    """Replace FastMCP's Rich handlers with a JSON formatter on the fastmcp logger."""
    fastmcp_logger = logging.getLogger("fastmcp")

    for handler in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    fastmcp_logger.addHandler(handler)
    fastmcp_logger.setLevel(logging.INFO)
    fastmcp_logger.propagate = False


configure_json_logging()

logger = logging.getLogger("fastmcp.surfsense_mcp")


class ServerMode(Enum):
    STDIO = "stdio"
    HTTP = "http"


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def resolve_cors_origins() -> list[str]:
    """Parse MCP_ALLOWED_ORIGINS; fall back to ['*'] outside production."""
    raw = os.getenv("MCP_ALLOWED_ORIGINS", "").strip()
    env = os.getenv("MCP_ENV", "development").strip().lower()

    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if origins:
            if env == "production" and "*" in origins:
                logger.warning(
                    "MCP_ALLOWED_ORIGINS contains '*' in production — "
                    "tighten to explicit origins before public exposure"
                )
            return origins

    if env == "production":
        logger.warning(
            "MCP_ALLOWED_ORIGINS is unset in production — defaulting to '*'. "
            "Set MCP_ALLOWED_ORIGINS to restrict CORS."
        )
    return ["*"]


def main() -> None:
    """Run the MCP server."""
    server_mode = ServerMode.STDIO
    if len(sys.argv) > 1:
        server_mode = ServerMode(sys.argv[1])

    if not os.getenv("SURFSENSE_BASE_URL"):
        raise ValueError("SURFSENSE_BASE_URL is not set")

    if server_mode == ServerMode.STDIO:
        has_jwt = bool(os.getenv("SURFSENSE_JWT"))
        has_password_creds = bool(os.getenv("SURFSENSE_EMAIL")) and bool(os.getenv("SURFSENSE_PASSWORD"))
        if not has_jwt and not has_password_creds:
            raise ValueError(
                "stdio mode requires SURFSENSE_JWT, or both SURFSENSE_EMAIL "
                "and SURFSENSE_PASSWORD for the password-login fallback."
            )
        get_stdio_mcp().run()
        return

    if server_mode == ServerMode.HTTP:
        header_mcp = get_header_mcp()
        cors = [
            Middleware(
                CORSMiddleware,
                allow_origins=resolve_cors_origins(),
                allow_credentials=False,
                allow_methods=["*"],
                allow_headers=[
                    "mcp-protocol-version",
                    "mcp-session-id",
                    "Authorization",
                    "Content-Type",
                ],
                expose_headers=["mcp-session-id"],
            )
        ]
        header_app = header_mcp.http_app(middleware=cors, stateless_http=True)

        app = Starlette(
            routes=[
                Route("/healthz", healthz, methods=["GET"]),
                Mount("/", app=header_app),
            ],
            lifespan=header_app.lifespan,
        )

        for uv_logger_name in ("uvicorn", "uvicorn.error"):
            uv_logger = logging.getLogger(uv_logger_name)
            for h in uv_logger.handlers[:]:
                uv_logger.removeHandler(h)
            uv_handler = logging.StreamHandler(sys.stderr)
            uv_handler.setFormatter(JSONFormatter())
            uv_logger.addHandler(uv_handler)

        logger.info("Starting HTTP server on :8211")
        uvicorn.run(app, host="0.0.0.0", port=8211, log_level="info", access_log=False)
        return


if __name__ == "__main__":
    main()
