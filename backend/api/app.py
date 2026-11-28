"""FastAPI service: the doctor's follow-up list.

Thin composition root that mounts the canonical follow-up router (`backend/api/router.py`)
and serves the built React app from `frontend/dist` when present.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .router import Close, FactAct, OpenItem, Raise, build_followup_router
from .store import AuthorityError, Store

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="tmrw follow-up harness", version="0.1")
    app.include_router(build_followup_router(lambda: store))

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = DIST / path
            return FileResponse(f if path and f.is_file() else DIST / "index.html")
    return app


def app_from_env() -> FastAPI:
    """uvicorn backend.api.app:app_from_env --factory, with HARNESS_RUN=<harness-run json> or
    HARNESS_LOOKBACK=<look-back out dir>[,<export dir>]."""
    from ..adapters.loopbench_run import load_run
    from ..adapters.lookback_output import load_lookback

    if os.environ.get("HARNESS_LOOKBACK"):
        parts = os.environ["HARNESS_LOOKBACK"].split(",")
        return create_app(Store(load_lookback(parts[0], parts[1] if len(parts) > 1 else None)))
    return create_app(Store(load_run(os.environ["HARNESS_RUN"])))


__all__ = ["AuthorityError", "Close", "DIST", "FactAct", "OpenItem", "Raise", "Store", "app_from_env", "create_app"]
