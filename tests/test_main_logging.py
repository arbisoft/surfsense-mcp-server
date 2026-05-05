"""Tests for ``surfsense_mcp.__main__.resolve_log_level``.

The helper is the single point of truth for which log level the MCP server
runs at. The matrix is small but worth pinning so an accidental tweak to
the env-var precedence (override beats env-derived default) doesn't slip in
silently.
"""

from __future__ import annotations

import logging

import pytest

from surfsense_mcp.__main__ import resolve_log_level


@pytest.fixture(autouse=True)
def _clear_log_env(monkeypatch):
    """Tests always start from a clean slate — no leaked env from the shell."""
    monkeypatch.delenv("MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("MCP_ENV", raising=False)


def test_default_is_debug_when_env_unset():
    """Empty env → development default → DEBUG."""
    assert resolve_log_level() == logging.DEBUG


def test_development_explicit_is_debug(monkeypatch):
    monkeypatch.setenv("MCP_ENV", "development")
    assert resolve_log_level() == logging.DEBUG


def test_production_is_info(monkeypatch):
    monkeypatch.setenv("MCP_ENV", "production")
    assert resolve_log_level() == logging.INFO


def test_explicit_override_wins_over_production(monkeypatch):
    """``MCP_LOG_LEVEL`` beats the ``MCP_ENV``-derived default."""
    monkeypatch.setenv("MCP_ENV", "production")
    monkeypatch.setenv("MCP_LOG_LEVEL", "DEBUG")
    assert resolve_log_level() == logging.DEBUG


def test_explicit_override_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("MCP_LOG_LEVEL", "warning")
    assert resolve_log_level() == logging.WARNING


@pytest.mark.parametrize(
    ("level_str", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ],
)
def test_each_known_level_resolves(level_str, expected, monkeypatch):
    monkeypatch.setenv("MCP_LOG_LEVEL", level_str)
    assert resolve_log_level() == expected


def test_bogus_level_falls_through_to_env_default(monkeypatch, caplog):
    """An unknown ``MCP_LOG_LEVEL`` warns and falls back to the env default."""
    monkeypatch.setenv("MCP_ENV", "production")
    monkeypatch.setenv("MCP_LOG_LEVEL", "verbose")
    with caplog.at_level(logging.WARNING):
        level = resolve_log_level()
    assert level == logging.INFO  # production default, not the bogus override
    # The helper uppercases the input before validating, so the warning
    # quotes "VERBOSE" rather than "verbose" — match either case.
    assert any(
        "MCP_LOG_LEVEL" in r.getMessage() and "VERBOSE" in r.getMessage().upper()
        for r in caplog.records
    )


def test_bogus_level_falls_through_to_dev_default_when_env_unset(monkeypatch):
    """Same fallback path when ``MCP_ENV`` is unset (development default)."""
    monkeypatch.setenv("MCP_LOG_LEVEL", "loud")
    assert resolve_log_level() == logging.DEBUG
