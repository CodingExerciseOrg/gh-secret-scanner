"""
src/main.py — FastAPI application entry point.

Run from the project root:
    python run.py
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

_SRC = Path(__file__).parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import identity
from identity_registry import IdentityRegistry
from api.routes_config   import make_router as config_router
from api.routes_repos    import make_router as repos_router
from api.routes_findings import make_router as findings_router
from api.routes_status   import make_router as status_router

# ------------------------------------------------------------------
# Shared state
# ------------------------------------------------------------------

CONFIG_DIR = Path(__file__).parent.parent / "config"

# Initialise identity module (loads or creates the signing secret)
identity.init(CONFIG_DIR)

# Per-identity status messages
_status_messages: dict[str, str] = {}


def _make_on_update(ident: str):
    def on_update(msg: str):
        _status_messages[ident] = msg
    return on_update


registry = IdentityRegistry(CONFIG_DIR, _make_on_update)


def _get_status(ident: str) -> dict:
    storage = registry.get_storage(ident)
    poller  = registry.get_poller(ident)
    return {
        "message":            _status_messages.get(ident, "Ready."),
        "seconds_until_poll": poller.seconds_until_next_poll(),
        "findings_count":     len(storage.load_findings()),
    }


def _on_config_saved(ident: str) -> None:
    registry.rebuild_scanner(ident)


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    registry.stop_all()


# ------------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------------

app = FastAPI(
    title="GitHub Actions Secret Scanner",
    description="Scans GitHub Actions workflow logs for leaked secrets.",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(config_router(registry, _on_config_saved))
app.include_router(repos_router(registry))
app.include_router(findings_router(registry))
app.include_router(status_router(registry, _get_status))

WEB_DIR = Path(__file__).parent.parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")
