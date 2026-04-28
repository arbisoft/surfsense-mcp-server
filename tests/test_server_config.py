"""Tests for env-driven config in :mod:`surfsense_mcp.server`.

The structured-logging middleware can include full tool payloads when
turned on. Tool inputs/outputs include chat prompts, document content,
and base64 uploads up to 500 MB, so payload logging is **off by
default** and only opt-in via ``MCP_LOG_PAYLOADS``. These tests pin
that default and the truthy-value parsing.
"""

from __future__ import annotations

import pytest

from surfsense_mcp.server import _log_payloads_enabled


def test_log_payloads_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_LOG_PAYLOADS", raising=False)
    assert _log_payloads_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On"])
def test_log_payloads_recognized_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MCP_LOG_PAYLOADS", value)
    assert _log_payloads_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "maybe"])
def test_log_payloads_falsy_or_unrecognized_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Anything that isn't an explicit truthy keyword stays off — this is a
    security-sensitive default; a typo must not silently enable logging."""
    monkeypatch.setenv("MCP_LOG_PAYLOADS", value)
    assert _log_payloads_enabled() is False
