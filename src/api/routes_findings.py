"""
src/api/routes_findings.py — GET /api/findings, DELETE /api/findings
"""

import sys
from pathlib import Path
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import APIRouter, Cookie, HTTPException, Query
from pydantic import BaseModel

import identity

router = APIRouter(prefix="/api/findings", tags=["findings"])


class FindingOut(BaseModel):
    repo:         str
    run_id:       int | None
    run_name:     str | None
    step:         str | None
    line_number:  int | None
    secret_type:  str | None
    matched_text: str | None


def make_router(registry):

    @router.get("", response_model=list[FindingOut])
    def get_findings(
        identity_cookie: str | None = Cookie(default=None, alias=identity.COOKIE_NAME),
    ):
        ident = identity.read_cookie_value(identity_cookie)
        if not ident:
            return []
        return registry.get_storage(ident).load_findings()

    @router.delete("")
    def clear_findings(
        identity_cookie: str | None = Cookie(default=None, alias=identity.COOKIE_NAME),
        mode: str = Query("dismiss", pattern="^(dismiss|reset)$"),
    ):
        ident = identity.read_cookie_value(identity_cookie)
        if not ident:
            raise HTTPException(status_code=400, detail="No config saved yet.")
        storage = registry.get_storage(ident)
        storage.clear_findings()
        if mode == "reset":
            storage.clear_seen_runs()
            registry.get_poller(ident).trigger_now()
        return {"cleared": True, "mode": mode}

    return router
