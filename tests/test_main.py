"""Tests for ``surfsense_mcp.__main__`` entry-point helpers.

The HTTP listener port is read from ``MCP_HTTP_PORT`` so the Dockerfile
env var (and any operator override) actually drives the runtime. These
tests lock in the parsing/validation behavior so a regression here
can't silently fall back to the old hard-coded port.
"""

from __future__ import annotations

import importlib
import logging
import sys

import pytest

from surfsense_mcp.__main__ import DEFAULT_HTTP_PORT, resolve_http_port


def test_resolve_http_port_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HTTP_PORT", raising=False)
    assert resolve_http_port() == DEFAULT_HTTP_PORT


def test_resolve_http_port_default_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HTTP_PORT", "   ")
    assert resolve_http_port() == DEFAULT_HTTP_PORT


def test_resolve_http_port_honors_valid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HTTP_PORT", "9000")
    assert resolve_http_port() == 9000


def test_resolve_http_port_falls_back_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HTTP_PORT", "not-a-number")
    assert resolve_http_port() == DEFAULT_HTTP_PORT


def test_resolve_http_port_falls_back_on_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ports outside 1-65535 would crash uvicorn at bind time — fall back
    to the default so a typo in compose env doesn't take down the container."""
    monkeypatch.setenv("MCP_HTTP_PORT", "70000")
    assert resolve_http_port() == DEFAULT_HTTP_PORT


def test_resolve_http_port_falls_back_on_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HTTP_PORT", "0")
    assert resolve_http_port() == DEFAULT_HTTP_PORT


def test_importing_main_does_not_mutate_fastmcp_logger() -> None:
    """Logging reconfiguration belongs to ``main()``, not module import.
    Importing the module must leave the ``fastmcp`` logger untouched so
    test runners and library consumers don't lose their own handlers."""
    fastmcp_logger = logging.getLogger("fastmcp")
    sentinel = logging.NullHandler()
    fastmcp_logger.addHandler(sentinel)
    original_propagate = fastmcp_logger.propagate
    fastmcp_logger.propagate = True
    try:
        # Force a fresh import so any module-level side effects re-run.
        sys.modules.pop("surfsense_mcp.__main__", None)
        importlib.import_module("surfsense_mcp.__main__")

        assert sentinel in fastmcp_logger.handlers, "module import stripped existing handlers"
        assert fastmcp_logger.propagate is True, "module import flipped propagate to False"
    finally:
        fastmcp_logger.removeHandler(sentinel)
        fastmcp_logger.propagate = original_propagate
