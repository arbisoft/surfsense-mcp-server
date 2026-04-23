# SurfSense MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes a SurfSense knowledge base to any MCP-compatible client (Claude Desktop, Cursor, VS Code, Windsurf, …).

The v1 surface is **read-only** — it lets external AI tools list search spaces, search documents by title, fetch documents, and read past research threads. Semantic search and the `quick_research` / `deep_research` / `summarize` / `compare` / `extract_facts` tools are deliberately deferred because they require new backend endpoints in `surfsense_backend`.

## Tools

| Tool | Description |
|---|---|
| `list_search_spaces` | List the search spaces the authenticated user can access. |
| `search_documents` | Keyword search on document titles within a search space (ILIKE, not semantic). |
| `get_document` | Fetch a document by ID, including content and metadata. |
| `get_recent_documents` | List recently added documents in a search space (newest first, sorted by `created_at`). |
| `list_research_threads` | List chat/research threads in a search space (active + archived). |
| `get_research_thread` | Fetch a thread with its full message history. |

## Authentication

SurfSense currently issues only short-lived JWTs — there is no long-lived API-key concept. This server therefore accepts a pre-obtained JWT and forwards it as `Authorization: Bearer <jwt>` on every upstream call. When the token expires, paste a fresh one.

### Get a JWT

**Option A — browser localStorage (easiest)**

1. Log in to your SurfSense instance.
2. Open DevTools → Console and run:
   ```js
   localStorage.getItem('surfsense_bearer_token')
   ```
3. Copy the printed value.

**Option B — Network tab**

1. Open DevTools → Network, filter by your SurfSense backend hostname.
2. Click any API request and copy the `Authorization: Bearer …` header value (strip the `Bearer ` prefix).

**Option C — curl**

```bash
curl -X POST https://<surfsense-host>/api/v1/auth/jwt/login \
  -d 'username=<email>&password=<password>'
# Returns {"access_token":"<jwt>","token_type":"bearer"}
```

> The token is short-lived (typically 1 day). Re-paste when tools start returning 401.

## Install

This package is **not published to PyPI** — install it directly from the source tree.

```bash
cd surfsense-mcp-server
uv venv && uv pip install -e ".[dev]"
```

Or with pip:

```bash
cd surfsense-mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Run — stdio (local, recommended)

```bash
SURFSENSE_BASE_URL=http://localhost:8000 \
SURFSENSE_JWT=<paste-jwt-here> \
python -m surfsense_mcp stdio
```

Replace `http://localhost:8000` with your SurfSense backend URL (the default port is `8000`; your instance may differ — check `UVICORN_PORT` in the backend's environment).

## Run — HTTP (remote)

```bash
SURFSENSE_BASE_URL=http://localhost:8000 \
python -m surfsense_mcp http   # binds 0.0.0.0:8211
```

Clients supply the JWT via the `Authorization: Bearer …` request header. The server validates it by calling `GET {SURFSENSE_BASE_URL}/users/me` on the upstream SurfSense API.

## MCP Client Config

### Claude Desktop / Cursor (stdio — local install)

Locate `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add:

```json
{
  "mcpServers": {
    "surfsense": {
      "command": "/absolute/path/to/surfsense-mcp-server/.venv/bin/python",
      "args": ["-m", "surfsense_mcp", "stdio"],
      "env": {
        "SURFSENSE_BASE_URL": "http://localhost:8000",
        "SURFSENSE_JWT": "<paste-jwt-here>"
      }
    }
  }
}
```

Replace `/absolute/path/to/surfsense-mcp-server/.venv/bin/python` with the real path — e.g. `/Users/you/code/surfsense-mcp-server/.venv/bin/python`.

> **Why not `uvx`?** The package is not on PyPI so `uvx surfsense-mcp-server` won't resolve. Use the venv Python path shown above.

### Remote HTTP (via mcp-remote)

```json
{
  "mcpServers": {
    "surfsense": {
      "command": "npx",
      "args": ["mcp-remote@latest", "http://localhost:8211/mcp"],
      "headers": {
        "Authorization": "Bearer <jwt>"
      }
    }
  }
}
```

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `SURFSENSE_BASE_URL` | yes (all modes) | Base URL of the SurfSense backend — no trailing slash (e.g. `http://localhost:8000`). |
| `SURFSENSE_JWT` | yes (stdio only) | JWT forwarded as `Authorization: Bearer`. In HTTP mode the token is taken from the request header. |
| `MCP_ENV` | no | `production` | `development` (default). Controls warning-level CORS behaviour. |
| `MCP_ALLOWED_ORIGINS` | no | Comma-separated CORS origins for HTTP mode. If unset in production, falls back to `*` with a warning. |

## Production deployment (foss-server-bundle-devstack)

When deployed as part of the Moneta `foss-server-bundle-devstack` the MCP server runs as a sidecar container on its own subdomain:

```
https://<prefix>research-mcp.<platform-domain>/mcp
```

End users still authenticate with a **SurfSense JWT** — the MCP endpoint is intentionally *not* wrapped in `mpass-auth` because MCP clients (Claude Desktop, Cursor, mcp-remote) cannot follow interactive OIDC redirects mid-stream. The server's `SurfSenseHeaderAuthProvider` validates every request by calling `GET /users/me` on the internal `surfsense-backend:8000`.

### Minting a JWT via mPass

After a browser login to the SurfSense app (which goes through oauth2-proxy → Cognito), the SurfSense fork exposes a bridge route that exchanges the mPass-authenticated session for a long-lived SurfSense JWT:

```bash
# The _oauth2_proxy cookie set on .<platform-domain> is sent automatically by
# the browser. The fork's /auth/jwt/proxy-login route reads X-Auth-Request-Email
# (injected by Traefik after ForwardAuth) and returns a SurfSense JWT.
curl -s https://<prefix>research.<platform-domain>/auth/jwt/proxy-login \
     -b "_oauth2_proxy=$COOKIE" \
     | jq -r '.access_token'
```

In practice, easiest is Option A from [Get a JWT](#get-a-jwt) — copy the bearer token from `localStorage` after signing in.

### MCP client config (Claude Desktop / Cursor via mcp-remote)

```json
{
  "mcpServers": {
    "surfsense": {
      "command": "npx",
      "args": [
        "mcp-remote@latest",
        "https://<prefix>research-mcp.<platform-domain>/mcp"
      ],
      "headers": {
        "Authorization": "Bearer <paste-surfsense-jwt-here>"
      }
    }
  }
}
```

The JWT is short-lived (default ~1 day). When calls start returning `401`, re-mint and update the config. There is no automatic refresh in HTTP mode — that is the tradeoff for staying out of the browser-redirect flow. A future MCP OAuth 2.1 bridge can eliminate the paste step (see `plans/mcp-oauth-bridge.md` for the design).

### Healthcheck

- Container-level: `GET /healthz` returns `{"status":"ok"}` unauthenticated. Docker and Traefik uptime probes use this route rather than `/mcp` so they never need to forge bearer headers.
- Upstream dependency health (SurfSense backend reachable, JWT validation working) is surfaced only by real tool calls — `/healthz` is deliberately process-liveness only.

## Development

```bash
cd surfsense-mcp-server

# Install with dev extras
uv pip install -e ".[dev]"

# Lint & format
ruff check surfsense_mcp/
ruff format surfsense_mcp/

# Tests (no live backend required — httpx is mocked)
pytest

# Upgrade fastmcp (v3 required)
uv sync --extra dev --upgrade-package fastmcp
```

Tests use an in-memory `httpx.MockTransport` fixture — no running SurfSense instance required. The seven tests cover URL construction, query-string parameters, the `Authorization` header, and 401 error propagation.

## Future Work

Tools deferred to a later iteration because they require backend changes in `surfsense_backend`:

- `semantic_search` — needs a new HTTP route exposing `DocumentHybridSearchRetriever` (currently agent-internal at `app/retriever/documents_hybrid_search.py`).
- `summarize_documents`, `compare_documents`, `extract_facts` — currently only available via the streaming chat agent (`app/agents/new_chat/tools/report.py`).
- `quick_research` / `deep_research` — would need SSE-stream consumption against the existing `POST /api/v1/new_chat` endpoint.
- OAuth / mPass integration — would replace the JWT-paste UX with a proper SSO flow via Cognito + oauth2-proxy.
