# CLAUDE.md — surfsense-mcp-server

## What this package is

A read-only [FastMCP v3](https://gofastmcp.com) server that wraps SurfSense's existing `GET` endpoints and exposes them as MCP tools. It is a sibling to `plane-mcp-server/` and follows the same structure.

No backend changes to SurfSense are required or allowed — all tools call existing `surfsense_backend` HTTP routes.

## Package layout

```
surfsense-mcp-server/
├── pyproject.toml
├── README.md
├── CLAUDE.md                              ← this file
├── .env.example
├── surfsense_mcp/
│   ├── __init__.py
│   ├── __main__.py                        # CLI entry: ServerMode enum (stdio|http), JSON logging
│   ├── server.py                          # get_stdio_mcp() / get_header_mcp() factories
│   ├── client.py                          # get_surfsense_client_context() → httpx.AsyncClient
│   ├── auth/
│   │   ├── __init__.py
│   │   └── surfsense_header_auth_provider.py  # TokenVerifier — validates JWT via GET /users/me
│   └── tools/
│       ├── __init__.py                    # register_tools(mcp) — calls all per-module register fns
│       ├── search_spaces.py               # list_search_spaces
│       ├── documents.py                   # search_documents, get_document, get_recent_documents
│       └── threads.py                     # list_research_threads, get_research_thread
└── tests/
    ├── conftest.py                        # mock_transport fixture, FAKE_JWT, json_response
    └── test_tools.py                      # 7 smoke tests — URL, query string, auth header, 401
```

## Dev commands

```bash
# Install
cd surfsense-mcp-server
uv venv && uv pip install -e ".[dev]"

# Tests (no live backend needed — httpx is mocked)
pytest

# Lint / format
ruff check surfsense_mcp/
ruff format surfsense_mcp/

# Upgrade fastmcp to latest v3
uv sync --extra dev --upgrade-package fastmcp

# Run stdio locally
SURFSENSE_BASE_URL=http://localhost:8000 SURFSENSE_JWT=<jwt> python -m surfsense_mcp stdio

# Run HTTP mode
SURFSENSE_BASE_URL=http://localhost:8000 python -m surfsense_mcp http   # binds :8211
```

## Architecture

### Transport modes

- **stdio** — used by Claude Desktop / Cursor / VS Code. `SURFSENSE_JWT` env var carries the token. `get_stdio_mcp()` builds a FastMCP instance without an auth provider; the JWT is read directly from env in `client.py`.
- **http** — for remote deploys. `get_header_mcp()` attaches `SurfSenseHeaderAuthProvider` which validates each request's `Authorization: Bearer` header against `GET /users/me`. CORS is configured via `http_app(middleware=[...])`. Port: `8211`.

### Auth model

SurfSense has no API-key concept — only short-lived JWTs from `fastapi-users`. The server does not refresh tokens. When a JWT expires, the user re-pastes a fresh one.

- **stdio**: JWT from `SURFSENSE_JWT` env var.
- **http**: JWT from `Authorization: Bearer` request header; validated by calling `GET {SURFSENSE_BASE_URL}/users/me`.

`get_surfsense_client_context()` in `client.py` handles both: it tries `get_access_token()` (FastMCP's request-scoped token) first, falls back to `SURFSENSE_JWT` env.

### Tool conventions

- Every tool is decorated with `@mcp.tool()` and registered via a `register_*` function called from `tools/__init__.py:register_tools()`.
- Tools return raw `dict` / `list` — no Pydantic re-modeling of SurfSense's response schemas.
- Tools `raise` on non-2xx responses so FastMCP surfaces errors to the MCP client.
- All tools open and close `httpx.AsyncClient` within the call using an `async with` block (client is not shared across calls).

### FastMCP version

Requires **fastmcp >= 3.0.0, < 4.0.0**. The v3 HTTP mode API differs from v2:

```python
# v3 — correct
header_app = header_mcp.http_app(middleware=cors, stateless_http=True)
app = Starlette(routes=[Mount("/", app=header_app)], lifespan=header_app.lifespan)

# v2 — do NOT use
app = header_mcp.http_app(middleware=cors)  # different signature
```

`lifespan` must be `header_app.lifespan` (not a lambda wrapping it).

## Key constraints

- **No backend changes** — all tools must call existing `GET` endpoints. Do not add routes to `surfsense_backend`.
- **Read-only** — no `POST`, `PUT`, `DELETE` tools.
- **No resources** — tools only; MCP resources are not exposed.
- **No Pydantic schemas** — return raw dicts from httpx responses; SurfSense's schemas are not imported here.

## Relevant SurfSense backend files

When adding tools, check these backend files to confirm route paths and query parameters:

| Backend file | What it defines |
|---|---|
| `surfsense_backend/app/routes/search_space_routes.py` | `/api/v1/searchspaces` — `owned_only`, `skip`, `limit` |
| `surfsense_backend/app/routes/documents_routes.py` | `/api/v1/documents`, `/api/v1/documents/search`, `/api/v1/documents/{id}` |
| `surfsense_backend/app/routes/threads_routes.py` | `/api/v1/threads`, `/api/v1/threads/{id}` |
| `surfsense_backend/app/routes/auth_routes.py` | `/users/me` (used by the auth provider) |

> `sort_column_map` in `documents_routes.py` only accepts `"created_at"`, `"title"`, `"document_type"` — `"updated_at"` is not a valid sort key.

## Test fixtures

`tests/conftest.py` provides:

- `mock_transport` — patches `httpx.AsyncClient.__init__` with a `MockTransport`. Returns a `setup(handler)` callable; calling it registers a response handler and returns a `recorded: list[httpx.Request]` for assertion.
- `_env` (autouse) — sets `SURFSENSE_BASE_URL` and `SURFSENSE_JWT` per test via `monkeypatch`.
- `json_response(payload, status_code=200)` — helper to build `httpx.Response` from a dict.

Tests use `Client(get_stdio_mcp())` (FastMCP in-process client) — no subprocess, no network.

## SurfSense backend port

The default SurfSense backend port is `8000` (`UVICORN_PORT`). Instances vary — confirm with the operator before hardcoding. In the Moneta devstack it may run on a different port (e.g. `8929`).
