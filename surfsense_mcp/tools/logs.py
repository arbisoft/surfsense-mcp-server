"""Log tools for the SurfSense MCP Server."""

from typing import Any

from fastmcp import FastMCP

from surfsense_mcp.client import authed_request


def register_log_tools(mcp: FastMCP) -> None:
    """Register log tools."""

    @mcp.tool()
    async def get_logs(
        search_space_id: int | None = None,
        level: str | None = None,
        status: str | None = None,
        source: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Fetch audit/ETL logs.

        If `search_space_id` is provided, the caller must have LOGS_READ
        permission for that space. If omitted, logs are returned from every
        space the user is a member of.

        Args:
            search_space_id: Restrict to one search space.
            level: Filter by LogLevel (e.g. "INFO", "WARNING", "ERROR",
                "CRITICAL", "DEBUG").
            status: Filter by LogStatus.
            source: Substring match on log source (ILIKE).
            start_date: ISO-8601 timestamp lower bound (inclusive).
            end_date: ISO-8601 timestamp upper bound (inclusive).
            skip: Pagination offset.
            limit: Max rows to return.

        Returns:
            List of LogRead objects.
        """
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if search_space_id is not None:
            params["search_space_id"] = search_space_id
        if level is not None:
            params["level"] = level
        if status is not None:
            params["status"] = status
        if source is not None:
            params["source"] = source
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        response = await authed_request("GET", "/api/v1/logs", params=params)
        return response.json()
