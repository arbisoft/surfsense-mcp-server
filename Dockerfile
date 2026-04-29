# Production Dockerfile for surfsense-mcp.
#
# Build context: this directory (`surfsense-mcp-server/`). No flags needed:
#
#   docker build -t surfsense-mcp:dev .
#
# Multi-stage:
#   builder — produces a wheel from the local source. The wheel's METADATA
#             only carries [project.dependencies] (the [tool.uv.sources]
#             override is dev-only, never serialized into the wheel).
#   runtime — `pip install` (NOT `uv pip install`) the wheel. pip honors
#             only the wheel's Requires-Dist, so `moneta-mcp-auth` resolves
#             from PyPI per the pinned version range. No sibling checkout
#             required.
#
# For cross-package iteration (rebuilding with a local moneta-mcp-auth
# checkout), use `Dockerfile.dev` with `docker buildx --build-context`.

# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.11

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder
WORKDIR /src

RUN pip install --no-cache-dir --upgrade pip build

# Cache-friendly: dep-bearing files first, source last.
COPY pyproject.toml README.md /src/
COPY surfsense_mcp /src/surfsense_mcp/

RUN python -m build --wheel --outdir /dist

# Sanity: the built wheel must NOT carry path-style requirements. If it
# does, downstream `pip install` would try to resolve a missing sibling
# and fail. (Catches future regressions where a tool starts leaking
# [tool.uv.sources] into METADATA.)
RUN WHEEL=$(ls /dist/*.whl | head -1) && \
    python -m zipfile -e "$WHEEL" /tmp/whl && \
    if grep -RqE "uv\.sources|file://|moneta-mcp-auth @ " /tmp/whl/*.dist-info/METADATA; then \
      echo "Built wheel leaks workspace metadata; refusing to ship." >&2; \
      exit 1; \
    fi

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Install the wheel; pip resolves moneta-mcp-auth from PyPI per the pin.
COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# Drop privileges.
RUN groupadd --system --gid 10001 mcp \
 && useradd  --system --uid 10001 --gid mcp --home-dir /app --no-create-home mcp \
 && chown -R mcp:mcp /app
USER mcp

EXPOSE 8211

# Listener port. Read by moneta_mcp_auth.runtime.resolve_http_port(); the
# HEALTHCHECK below resolves it via the shell at runtime so changes here
# stay consistent without rebuilding.
ENV MCP_HTTP_PORT=8211

ENTRYPOINT ["python", "-m", "surfsense_mcp"]
CMD ["http"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
    CMD curl -fsS "http://127.0.0.1:${MCP_HTTP_PORT:-8211}/healthz" || exit 1
