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

REQUIRED_HTTP_ENV_VARS = (
    "MCP_BASE_URL",
    "COGNITO_USER_POOL_ID",
    "COGNITO_AWS_REGION",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
)

DEFAULT_HTTP_PORT = 8211


def resolve_http_port() -> int:
    """Resolve the HTTP listener port from MCP_HTTP_PORT (default 8211).

    Garbage values fall back to the default rather than crashing the
    container at boot — operators who mistype the env var get a usable
    server they can then reconfigure.
    """
    raw = os.getenv("MCP_HTTP_PORT", "").strip()
    if not raw:
        return DEFAULT_HTTP_PORT
    try:
        port = int(raw)
    except ValueError:
        logger.warning("MCP_HTTP_PORT=%r is not an integer; using default %d", raw, DEFAULT_HTTP_PORT)
        return DEFAULT_HTTP_PORT
    if not (1 <= port <= 65535):
        logger.warning("MCP_HTTP_PORT=%d is out of range; using default %d", port, DEFAULT_HTTP_PORT)
        return DEFAULT_HTTP_PORT
    return port


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


_VALID_LOG_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def resolve_log_level() -> int:
    """Pick the runtime log level from env vars.

    Resolution order:

    1. ``MCP_LOG_LEVEL`` (explicit override; case-insensitive).
    2. ``MCP_ENV=production`` → ``INFO`` (quieter for hosted deploys).
    3. Anything else (including ``development``) → ``DEBUG``.

    A bogus ``MCP_LOG_LEVEL`` (e.g. ``verbose``) logs a warning via the
    module logger and falls through to the env-derived default rather
    than blocking startup.
    """
    raw = os.getenv("MCP_LOG_LEVEL", "").strip().upper()
    if raw:
        if raw in _VALID_LOG_LEVELS:
            return _VALID_LOG_LEVELS[raw]
        # The fastmcp logger doesn't have its handlers attached yet at the
        # point this is called, so the warning rides on the root stderr
        # logger — visible regardless. ``logging.warning`` honours the
        # root logger's lazy-init basicConfig.
        logging.warning(
            "MCP_LOG_LEVEL=%r is not a known level; expected one of %s. Falling back to MCP_ENV-derived default.",
            raw,
            ", ".join(_VALID_LOG_LEVELS),
        )

    env = os.getenv("MCP_ENV", "development").strip().lower()
    return logging.INFO if env == "production" else logging.DEBUG


def configure_json_logging(level: int | None = None) -> None:
    """Replace FastMCP's Rich handlers with a JSON formatter on the fastmcp logger.

    Called from :func:`main`, not at import time — importing this module
    must not mutate global logging handlers (would leak across tests and
    any in-process consumer that imports the package).

    ``level`` defaults to :func:`resolve_log_level` so callers normally
    don't pass it. Tests pass an explicit level to avoid touching env vars.
    """
    if level is None:
        level = resolve_log_level()

    fastmcp_logger = logging.getLogger("fastmcp")

    for handler in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    fastmcp_logger.addHandler(handler)
    fastmcp_logger.setLevel(level)
    fastmcp_logger.propagate = False


logger = logging.getLogger("fastmcp.surfsense_mcp")


class ServerMode(Enum):
    STDIO = "stdio"
    HTTP = "http"


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def resolve_cors_origins() -> list[str]:
    """Parse MCP_ALLOWED_ORIGINS; fall back to ['*'] when unset or invalid.

    In production this function still defaults to ['*'], but emits a warning
    so operators can tighten CORS by setting explicit origins.
    """
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
            "MCP_ALLOWED_ORIGINS is unset in production — defaulting to '*'. Set MCP_ALLOWED_ORIGINS to restrict CORS."
        )
    return ["*"]


def warn_if_storage_missing_in_production() -> None:
    """Warn when running in production without external OAuth-state storage.

    Without ``MCP_OAUTH_STORAGE_URL`` set, FastMCP keeps OAuth state in an
    encrypted file tree on the container's writable layer. That state is
    wiped on container recreation (``docker compose down && up``), forcing
    every MCP client to re-OAuth. Warn but don't block — someone running
    the image for evaluation shouldn't be forced to stand up Valkey first.
    """
    env = os.getenv("MCP_ENV", "development").strip().lower()
    if env != "production":
        return
    if os.getenv("MCP_OAUTH_STORAGE_URL", "").strip():
        return
    logger.warning(
        "MCP_OAUTH_STORAGE_URL is unset in production — OAuth state will live "
        "on the container filesystem and be lost on container recreation. "
        "Point this at a Valkey/Redis URL (e.g. redis://valkey:6379/11) for "
        "persistent, multi-replica-safe storage."
    )


def main() -> None:
    """Run the MCP server."""
    configure_json_logging()

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
        missing = [name for name in REQUIRED_HTTP_ENV_VARS if not os.getenv(name)]
        if missing:
            raise ValueError("http mode is missing required env vars: " + ", ".join(missing))
        warn_if_storage_missing_in_production()
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

        # AWSCognitoProvider publishes /.well-known/oauth-protected-resource and
        # /.well-known/oauth-authorization-server natively on the mounted app,
        # so we only layer a /healthz probe in front of it.
        app = Starlette(
            routes=[
                Route("/healthz", healthz, methods=["GET"]),
                Mount("/", app=header_app),
            ],
            lifespan=header_app.lifespan,
        )

        level = resolve_log_level()
        for uv_logger_name in ("uvicorn", "uvicorn.error"):
            uv_logger = logging.getLogger(uv_logger_name)
            for h in uv_logger.handlers[:]:
                uv_logger.removeHandler(h)
            uv_handler = logging.StreamHandler(sys.stderr)
            uv_handler.setFormatter(JSONFormatter())
            uv_logger.addHandler(uv_handler)
            uv_logger.setLevel(level)

        port = resolve_http_port()
        logger.info("Starting HTTP server on :%d", port)
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_level=logging.getLevelName(level).lower(),
            access_log=False,
        )
        return


if __name__ == "__main__":
    main()
