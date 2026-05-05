"""Tool registration for the SurfSense MCP Server."""

from fastmcp import FastMCP

from surfsense_mcp.tools.documents import register_document_tools
from surfsense_mcp.tools.logs import register_log_tools
from surfsense_mcp.tools.notes import register_note_tools
from surfsense_mcp.tools.reports import register_report_tools
from surfsense_mcp.tools.search_spaces import register_search_space_tools
from surfsense_mcp.tools.threads import register_thread_tools


def register_tools(mcp: FastMCP) -> None:
    """Register all tools with the MCP server."""
    register_search_space_tools(mcp)
    register_document_tools(mcp)
    register_thread_tools(mcp)
    register_report_tools(mcp)
    register_note_tools(mcp)
    register_log_tools(mcp)
