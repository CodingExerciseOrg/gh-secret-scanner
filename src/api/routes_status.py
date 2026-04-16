"""
src/api/routes_status.py — GET /api/status
"""

import sys
from pathlib import Path
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import APIRouter, Cookie
from pydantic import BaseModel

import identity

router = APIRouter(prefix="/api/status", tags=["status"])


class StatusOut(BaseModel):
    message:            str
    seconds_until_poll: int
    findings_count:     int


def make_router(registry, get_status_fn):

    @router.get("", response_model=StatusOut)
    def get_status(
        identity_cookie: str | None = Cookie(default=None, alias=identity.COOKIE_NAME),
    ):
        ident = identity.read_cookie_value(identity_cookie)
        if not ident:
            return StatusOut(message="No config saved yet.", seconds_until_poll=0, findings_count=0)
        return get_status_fn(ident)

    return router
