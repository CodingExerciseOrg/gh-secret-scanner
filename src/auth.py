"""
src/auth.py — Shared GitHub authentication helper.

Both the poller and API routes use this to get an authenticated
GitHubClient regardless of whether token or app auth is configured.
For app auth, if the cached token is missing or looks expired, a fresh
one is generated and persisted to storage.
"""

import time
from github_client import GitHubClient, GitHubError
from storage import Storage

APP_TOKEN_TTL_SECONDS = 50 * 60   # refresh before 60-min expiry


def make_client(storage: Storage) -> GitHubClient:
    """
    Return an authenticated GitHubClient using the current config.

    - Token auth : uses config["token"] directly.
    - App auth   : uses the cached installation token if still fresh,
                   otherwise generates a new one and persists it.
    """
    config = storage.load_config()
    method = config.get("auth_method", "token")
    org    = config.get("org", "").strip()

    if not org:
        raise GitHubError("No organization configured.")

    if method == "app":
        return _make_app_client(config, storage)
    else:
        token = config.get("token", "").strip()
        if not token:
            raise GitHubError("No authentication token configured.")
        return GitHubClient(token)


def _make_app_client(config: dict, storage: Storage) -> GitHubClient:
    app_id          = config.get("app_id", "").strip()
    installation_id = config.get("installation_id", "").strip()
    private_key     = config.get("private_key", "").strip()

    if not app_id or not installation_id or not private_key:
        raise GitHubError(
            "App auth requires App ID, Installation ID and a private key."
        )

    # Check whether the cached token is still usable.
    # We store the token expiry time alongside the token in config.
    token         = config.get("token", "").strip()
    token_expires = config.get("token_expires_at", 0)

    if token and time.time() < token_expires:
        # Cached token is still valid
        return GitHubClient(token)

    # Token is missing or expired — generate a fresh one
    client, new_token = GitHubClient.from_app(app_id, installation_id, private_key)

    # Persist the new token and its expiry time
    cfg = dict(config)
    cfg["token"]            = new_token
    cfg["token_expires_at"] = time.time() + APP_TOKEN_TTL_SECONDS
    storage.save_config(cfg)

    return client