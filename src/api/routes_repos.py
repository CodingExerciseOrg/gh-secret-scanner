"""
src/api/routes_repos.py — GET /api/repos
"""

import sys
from pathlib import Path
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

import identity
from auth import make_client
from github_client import GitHubError

router = APIRouter(prefix="/api/repos", tags=["repos"])


class RepoOut(BaseModel):
    name:           str
    visibility:     str
    language:       str | None
    default_branch: str
    updated_at:     str


def make_router(registry):

    @router.get("", response_model=list[RepoOut])
    def list_repos(
        identity_cookie: str | None = Cookie(default=None, alias=identity.COOKIE_NAME),
    ):
        ident = identity.read_cookie_value(identity_cookie)
        if not ident:
            raise HTTPException(status_code=400, detail="No config saved yet.")

        storage = registry.get_storage(ident)
        cfg     = storage.load_config()
        org     = cfg.get("org", "").strip()

        if not org:
            raise HTTPException(status_code=400, detail="No org configured.")

        try:
            client = make_client(storage)
            repos  = client.list_repos(org)
        except GitHubError as e:
            raise HTTPException(status_code=502, detail=str(e))

        return [
            RepoOut(
                name=r.get("name", ""),
                visibility=r.get("visibility", ""),
                language=r.get("language"),
                default_branch=r.get("default_branch", ""),
                updated_at=(r.get("updated_at") or "")[:10],
            )
            for r in repos
        ]

    return router
