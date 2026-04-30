"""Tests for ``surfsense_mcp.auth.storage``.

Two layers under test:

- :func:`parse_storage_url` — pure-Python URL parsing. No I/O, no GLIDE.
- :func:`build_oauth_storage` — env-var dispatch + Fernet wrap. The Valkey
  GLIDE client is imported lazily so the unset-env and missing-key-material
  branches don't require it.
"""

from __future__ import annotations

import importlib

import pytest
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from surfsense_mcp.auth import storage as storage_module
from surfsense_mcp.auth.storage import (
    ValkeyConfig,
    build_oauth_storage,
    parse_storage_url,
)

# ---------------------------------------------------------------------------
# parse_storage_url
# ---------------------------------------------------------------------------


def test_parse_redis_full_url():
    config = parse_storage_url("redis://valkey:6379/11")
    assert config == ValkeyConfig(host="valkey", port=6379, db=11, username=None, password=None)


def test_parse_valkey_scheme_equivalent_to_redis():
    config = parse_storage_url("valkey://localhost:6379/3")
    assert config.host == "localhost"
    assert config.port == 6379
    assert config.db == 3


def test_parse_defaults_port_and_db_when_omitted():
    config = parse_storage_url("redis://valkey")
    assert config.port == 6379
    assert config.db == 0


def test_parse_extracts_username_and_password():
    config = parse_storage_url("redis://user:secret@valkey:6379/2")
    assert config.username == "user"
    assert config.password == "secret"


def test_parse_rejects_non_redis_scheme():
    with pytest.raises(ValueError, match="redis:// or valkey://"):
        parse_storage_url("https://valkey:6379/11")


def test_parse_rejects_missing_hostname():
    with pytest.raises(ValueError, match="missing a hostname"):
        parse_storage_url("redis:///11")


def test_parse_rejects_non_numeric_db():
    with pytest.raises(ValueError, match="numeric DB index"):
        parse_storage_url("redis://valkey:6379/notanint")


# ---------------------------------------------------------------------------
# build_oauth_storage
# ---------------------------------------------------------------------------


def test_returns_none_when_url_unset(monkeypatch):
    """Unset env → caller falls through to FastMCP's encrypted file store."""
    monkeypatch.delenv("MCP_OAUTH_STORAGE_URL", raising=False)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret")

    assert build_oauth_storage() is None


def test_returns_none_when_url_blank(monkeypatch):
    """Whitespace-only env is treated as unset."""
    monkeypatch.setenv("MCP_OAUTH_STORAGE_URL", "   ")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret")

    assert build_oauth_storage() is None


def test_raises_when_no_key_material_set(monkeypatch):
    """Without either MCP_JWT_SIGNING_KEY or OIDC_CLIENT_SECRET the
    Fernet key cannot be derived.
    """
    monkeypatch.setenv("MCP_OAUTH_STORAGE_URL", "redis://valkey:6379/11")
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)

    with pytest.raises(
        ValueError,
        match="neither MCP_JWT_SIGNING_KEY nor OIDC_CLIENT_SECRET is set",
    ):
        build_oauth_storage()


def test_builds_fernet_wrapped_valkey_store_from_client_secret(monkeypatch):
    """Confidential-client path: only OIDC_CLIENT_SECRET set.

    Skips when the Valkey GLIDE client isn't installed (uv install without
    the [valkey] extra). This branch is exercised in CI / dev images.
    """
    pytest.importorskip("glide")
    valkey_module = importlib.import_module("key_value.aio.stores.valkey")
    valkey_store_cls = valkey_module.ValkeyStore

    monkeypatch.setenv("MCP_OAUTH_STORAGE_URL", "redis://valkey:6379/11")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)

    store = build_oauth_storage()

    assert isinstance(store, FernetEncryptionWrapper)
    assert isinstance(store.key_value, valkey_store_cls)


def test_builds_fernet_wrapped_valkey_store_from_jwt_signing_key(monkeypatch):
    """Public-client path: only MCP_JWT_SIGNING_KEY set."""
    pytest.importorskip("glide")
    valkey_module = importlib.import_module("key_value.aio.stores.valkey")
    valkey_store_cls = valkey_module.ValkeyStore

    monkeypatch.setenv("MCP_OAUTH_STORAGE_URL", "redis://valkey:6379/11")
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "test-signing-key")

    store = build_oauth_storage()

    assert isinstance(store, FernetEncryptionWrapper)
    assert isinstance(store.key_value, valkey_store_cls)


def test_client_secret_takes_precedence_over_jwt_signing_key(monkeypatch):
    """When both are set, OIDC_CLIENT_SECRET wins (confidential Cognito client
    is the typical prod setup; the signing key is the sandbox/public fallback).
    Spy on derive_jwt_key to record the material fed into HKDF.
    """
    pytest.importorskip("glide")

    monkeypatch.setenv("MCP_OAUTH_STORAGE_URL", "redis://valkey:6379/11")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "preferred-client-secret")
    monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "should-be-ignored")

    real_derive = storage_module.derive_jwt_key
    captured: list[str] = []

    def spy(*, high_entropy_material: str, salt: str) -> bytes:
        captured.append(high_entropy_material)
        return real_derive(high_entropy_material=high_entropy_material, salt=salt)

    monkeypatch.setattr(storage_module, "derive_jwt_key", spy)

    build_oauth_storage()

    assert captured == ["preferred-client-secret"]
