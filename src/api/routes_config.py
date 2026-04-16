"""
src/api/routes_config.py — GET /api/config, POST /api/config
"""

import sys, time
from pathlib import Path
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

import identity
from scanner import Scanner, ScannerError
from github_client import GitHubClient, GitHubError
from auth import APP_TOKEN_TTL_SECONDS

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigIn(BaseModel):
    org:             str
    auth_method:     str = "token"
    token:           str = ""
    app_id:          str = ""
    installation_id: str = ""
    private_key:     str = ""
    scanner_mode:    str = "mock"
    scanner_path:    str = ""


class ConfigOut(BaseModel):
    org:             str
    auth_method:     str
    token_set:       bool
    scanner_mode:    str
    scanner_path:    str
    app_id:          str
    installation_id: str
    private_key_set: bool


def make_router(registry, on_config_saved):

    @router.get("", response_model=ConfigOut)
    def get_config(
        response: Response,
        identity_cookie: str | None = Cookie(default=None, alias=identity.COOKIE_NAME),
    ):
        ident = identity.read_cookie_value(identity_cookie)
        if not ident:
            # No identity yet — return empty defaults
            return _empty_out()
        cfg = registry.get_storage(ident).load_config()
        return _to_out(cfg)

    @router.post("", response_model=ConfigOut)
    def save_config(
        body: ConfigIn,
        response: Response,
        identity_cookie: str | None = Cookie(default=None, alias=identity.COOKIE_NAME),
    ):
        if not body.org.strip():
            raise HTTPException(status_code=422, detail="Organization name is required.")

        method = body.auth_method

        if method == "token":
            # Fall back to saved token if none submitted
            old_ident = identity.read_cookie_value(identity_cookie)
            saved_token = ""
            if old_ident:
                saved_token = registry.get_storage(old_ident).load_config().get("token", "")
            token = body.token.strip() or saved_token.strip()
            if not token:
                raise HTTPException(status_code=422, detail="An authentication token is required.")

            cfg = {
                "org":             body.org.strip(),
                "auth_method":     "token",
                "token":           token,
                "app_id":          "",
                "installation_id": "",
                "private_key":     "",
                "scanner_mode":    body.scanner_mode,
                "scanner_path":    body.scanner_path,
            }

        elif method == "app":
            old_ident = identity.read_cookie_value(identity_cookie)
            saved = {}
            if old_ident:
                saved = registry.get_storage(old_ident).load_config()

            app_id          = body.app_id.strip()          or saved.get("app_id", "")
            installation_id = body.installation_id.strip() or saved.get("installation_id", "")
            private_key     = body.private_key.strip()     or saved.get("private_key", "")

            if not app_id:
                raise HTTPException(status_code=422, detail="App ID is required.")
            if not installation_id:
                raise HTTPException(status_code=422, detail="Installation ID is required.")
            if not private_key:
                raise HTTPException(status_code=422, detail="Private key is required.")

            try:
                _client, cached_token = GitHubClient.from_app(app_id, installation_id, private_key)
            except GitHubError as e:
                raise HTTPException(status_code=422, detail=f"GitHub App auth failed: {e}")

            cfg = {
                "org":              body.org.strip(),
                "auth_method":      "app",
                "token":            cached_token,
                "token_expires_at": time.time() + APP_TOKEN_TTL_SECONDS,
                "app_id":           app_id,
                "installation_id":  installation_id,
                "private_key":      private_key,
                "scanner_mode":     body.scanner_mode,
                "scanner_path":     body.scanner_path,
            }

        else:
            raise HTTPException(status_code=422, detail=f"Unknown auth method: {method}")

        try:
            Scanner(mode=cfg["scanner_mode"], binary_path=cfg["scanner_path"] or None)
        except ScannerError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # Derive identity hash from the new credential
        ident = identity.credential_hash(cfg)
        registry.get_storage(ident).save_config(cfg)

        # Set signed identity cookie
        response.set_cookie(
            key=identity.COOKIE_NAME,
            value=identity.make_cookie_value(ident),
            max_age=identity.COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )

        on_config_saved(ident)
        return _to_out(cfg)

    return router


def _to_out(cfg: dict) -> ConfigOut:
    return ConfigOut(
        org=cfg.get("org", ""),
        auth_method=cfg.get("auth_method", "token"),
        token_set=bool(cfg.get("token", "").strip()),
        scanner_mode=cfg.get("scanner_mode", "mock"),
        scanner_path=cfg.get("scanner_path", ""),
        app_id=cfg.get("app_id", ""),
        installation_id=cfg.get("installation_id", ""),
        private_key_set=bool(cfg.get("private_key", "").strip()),
    )


def _empty_out() -> ConfigOut:
    return ConfigOut(
        org="", auth_method="token", token_set=False,
        scanner_mode="mock", scanner_path="",
        app_id="", installation_id="", private_key_set=False,
    )
