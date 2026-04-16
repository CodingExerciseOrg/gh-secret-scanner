"""
src/identity.py — Credential-based identity for per-user data isolation.

No login screen, no passwords, no user accounts.

How it works:
  - When the user saves config, a short hash is derived from their
    credential (token or app_id+installation_id).
  - That hash becomes the name of their data folder:
        config/users/<hash>/
  - The hash is stored in a signed cookie so subsequent requests
    automatically resolve to the correct data folder.
  - The cookie is signed with a server-side secret so it cannot be
    forged or tampered with. The hash itself reveals nothing about the
    credential.

Cookie format (managed by itsdangerous.URLSafeSerializer):
    identity=<hash>.<hmac_signature>
"""

import hashlib
import os
import secrets
from pathlib import Path

from itsdangerous import URLSafeSerializer, BadSignature

COOKIE_NAME    = "identity"
COOKIE_MAX_AGE = 90 * 24 * 60 * 60   # 90 days

# ------------------------------------------------------------------
# Server secret — generated once, persisted to config/secret.key
# ------------------------------------------------------------------

def _load_or_create_secret(config_dir: Path) -> str:
    secret_file = config_dir / "secret.key"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    config_dir.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret, encoding="utf-8")
    return secret


# Module-level serializer — initialised in init()
_serializer: URLSafeSerializer | None = None


def init(config_dir: Path) -> None:
    """Call once at startup with the config directory."""
    global _serializer
    secret = _load_or_create_secret(config_dir)
    _serializer = URLSafeSerializer(secret, salt="identity")


# ------------------------------------------------------------------
# Hash derivation
# ------------------------------------------------------------------

def credential_hash(config: dict) -> str:
    """
    Derive a short, stable, opaque identifier from the user's credential
    and organization. Including the org means the same credential used
    against different organizations produces different data folders,
    allowing one user to manage multiple organizations independently.
 
    For token auth  : hash of org + token.
    For app auth    : hash of org + app_id + installation_id + private_key.
 
    Returns the first 16 hex characters of the SHA-256 digest.
    """
    org    = config.get("org", "").strip().lower()
    method = config.get("auth_method", "token")
    if method == "app":
        raw = (
            org
            + "|" + config.get("app_id", "")
            + "|" + config.get("installation_id", "")
            + "|" + config.get("private_key", "")
        )
    else:
        raw = org + "|" + config.get("token", "")
 
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------------
# Cookie helpers
# ------------------------------------------------------------------

def make_cookie_value(identity_hash: str) -> str:
    """Return a signed cookie value for the given hash."""
    assert _serializer, "identity.init() not called"
    return _serializer.dumps(identity_hash)


def read_cookie_value(cookie: str) -> str | None:
    """
    Validate and unsign the cookie value.
    Returns the identity hash on success, None if invalid or tampered.
    """
    if not cookie or not _serializer:
        return None
    try:
        return _serializer.loads(cookie)
    except BadSignature:
        return None
