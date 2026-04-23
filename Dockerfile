# Use Python 3.11 as base image
# TODO(prod): pin by digest once a release cadence is established
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (curl is used by the HEALTHCHECK)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files and application code
COPY pyproject.toml ./
COPY uv.lock* ./
COPY surfsense_mcp/ ./surfsense_mcp/

# Install the package and dependencies using uv
RUN uv pip install --system --no-cache .

# Create a non-root user and hand over /app
RUN groupadd --system --gid 10001 mcp \
    && useradd --system --uid 10001 --gid mcp --home-dir /app --no-create-home mcp \
    && chown -R mcp:mcp /app
USER mcp

# Expose port for HTTP transports (SSE, streamable-http, http)
EXPOSE 8211

# Set environment variables with defaults
ENV FASTMCP_PORT=8211

# Default to streamable-http transport, but allow override via command
# Users can override by passing different transport as CMD
ENTRYPOINT ["python", "-m", "surfsense_mcp"]
CMD ["http"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
    CMD curl -fsS http://127.0.0.1:8211/healthz || exit 1
