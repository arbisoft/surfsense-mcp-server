"""HTTP-mode OAuth state storage selection.

FastMCP's :class:`AWSCognitoProvider` (via ``OAuthProxy``) keeps six
collections of OAuth state — DCR client registrations, in-flight authorize
transactions, authorization codes, upstream Cognito access/refresh tokens,
JTI mappings, and refresh-token metadata. By default these live in an
encrypted file tree inside the container, which means

- ``docker compose down && up`` wipes refresh tokens (every MCP client
  re-OAuths on next call), and
- horizontal scaling is impossible (state is per-container).

Setting ``MCP_OAUTH_STORAGE_URL`` swaps the file store for a Valkey/Redis
backend, Fernet-wrapped with a key derived from ``OIDC_CLIENT_SECRET``
(confidential clients — preferred when present) or ``MCP_JWT_SIGNING_KEY``
(public/PKCE clients) so the on-disk RDB never holds plaintext tokens.
Unset → fall through to FastMCP's default file store (back-compat for
users running the image outside the devstack).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.utilities.logging import get_logger
from key_value.aio.protocols.key_value import AsyncKeyValue
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

logger = get_logger(__name__)

# Same salt FastMCP uses for the file-store encryption key
# (fastmcp/server/auth/oauth_proxy/proxy.py:458). Sharing the salt means
# rotating the chosen entropy source invalidates state in either backend
# consistently — no surprise where one store keeps decrypting while the
# other doesn't.
_STORAGE_KEY_SALT = "fastmcp-storage-encryption-key"

_DEFAULT_VALKEY_PORT = 6379
_DEFAULT_VALKEY_DB = 0


@dataclass(frozen=True)
class ValkeyConfig:
    """Parsed connection params for a Valkey/Redis URL."""

    host: str
    port: int
    db: int
    username: str | None
    password: str | None


def parse_storage_url(raw: str) -> ValkeyConfig:
    """Parse ``redis://`` / ``valkey://`` URL into connection params.

    Pure-Python; no I/O, no glide imports — separated from
    :func:`build_oauth_storage` so tests can verify URL handling without
    requiring the Valkey GLIDE client to be installed.
    """
    parsed = urlparse(raw)
    if parsed.scheme not in {"redis", "valkey"}:
        raise ValueError(f"MCP_OAUTH_STORAGE_URL scheme must be redis:// or valkey://, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("MCP_OAUTH_STORAGE_URL is missing a hostname")

    db = _DEFAULT_VALKEY_DB
    if parsed.path and parsed.path != "/":
        try:
            db = int(parsed.path.lstrip("/"))
        except ValueError as exc:
            raise ValueError(f"MCP_OAUTH_STORAGE_URL path must be a numeric DB index, got {parsed.path!r}") from exc

    return ValkeyConfig(
        host=parsed.hostname,
        port=parsed.port or _DEFAULT_VALKEY_PORT,
        db=db,
        username=parsed.username or None,
        password=parsed.password or None,
    )


def build_oauth_storage() -> AsyncKeyValue | None:
    """Return the OAuth-state store for ``AWSCognitoProvider``.

    Returns ``None`` when ``MCP_OAUTH_STORAGE_URL`` is unset, signalling
    FastMCP to keep its default encrypted file store. When set to a
    ``redis://`` or ``valkey://`` URL, returns a :class:`ValkeyStore`
    wrapped in :class:`FernetEncryptionWrapper`.

    Raises ``ValueError`` when the URL is malformed or neither
    ``MCP_JWT_SIGNING_KEY`` nor ``OIDC_CLIENT_SECRET`` is set (without
    one of them the Fernet key cannot be derived deterministically).
    """
    raw = os.getenv("MCP_OAUTH_STORAGE_URL", "").strip()
    if not raw:
        return None

    config = parse_storage_url(raw)

    # Prefer the upstream client secret when present (confidential Cognito
    # client — the typical prod setup). Fall back to MCP_JWT_SIGNING_KEY
    # for public/PKCE clients (sandbox / no-secret deployments).
    key_material = os.getenv("OIDC_CLIENT_SECRET", "").strip() or os.getenv("MCP_JWT_SIGNING_KEY", "").strip()
    if not key_material:
        raise ValueError(
            "MCP_OAUTH_STORAGE_URL is set but neither MCP_JWT_SIGNING_KEY "
            "nor OIDC_CLIENT_SECRET is set; the Fernet encryption key "
            "cannot be derived without one of them."
        )

    # Imported lazily so unit tests for ``parse_storage_url`` and the
    # missing-key-material guard don't require the Valkey GLIDE client.
    from key_value.aio.stores.valkey import ValkeyStore

    valkey_store = ValkeyStore(
        host=config.host,
        port=config.port,
        db=config.db,
        username=config.username,
        password=config.password,
    )
    encryption_key = derive_jwt_key(
        high_entropy_material=key_material,
        salt=_STORAGE_KEY_SALT,
    )
    logger.info(
        "OAuth state stored in valkey://%s:%d/%d (Fernet-encrypted at rest)",
        config.host,
        config.port,
        config.db,
    )
    return FernetEncryptionWrapper(
        key_value=valkey_store,
        fernet=Fernet(key=encryption_key),
        raise_on_decryption_error=False,
    )
