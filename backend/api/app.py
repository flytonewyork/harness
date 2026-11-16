"""FastAPI service: the doctor's follow-up list.

Read endpoints serve the list as it stood at `as_of` (default: the end of the data). Two write surfaces, kept
apart: /api/agent/* (open an item, raise its urgency; nothing else) and /api/doctor/* (close an item, or
confirm, reject or close a case fact). Each checks the caller's role (X-Role header: agent or doctor; real
login comes with EC's environment). A patient's `case` is the `case-projection/1` view. An item carries that
same projection beside its own reasoning trace. The built React app is served from frontend/dist when present.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .store import AuthorityError, Store

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class OpenItem(BaseModel):
    patient_id: str
    owner_id: str
    family: str
    reason: str = Field(max_length=200)
    evidence: list[str]
    urgency: int = Field(ge=1, le=4)
    due: str | None = None
    reasoning: list[dict] | None = None


class Raise(BaseModel):
    urgency: int = Field(ge=1, le=4)
    reason: str = ""


class Close(BaseModel):
    resolution: str
    note: str = ""


class FactAct(BaseModel):
    record_id: str
    quote: str
    note: str = ""


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="tmrw follow-up harness", version="0.1")

    def when(as_of: str | None) -> datetime:
        return datetime.fromisoformat(as_of) if as_of else store.default_as_of()

    def role(x_role: str | None, need: str):
        if x_role != need:
            raise HTTPException(403, f"{need} only")

    @app.get("/api/health")
    def health():
        return {"ok": True, "items": len(store.items)}

    @app.get("/api/source")
    def source():
        return {**store.source, "clock": store.clock(), "staff": list(store.staff.values())}

    @app.get("/api/items")
    def items(as_of: str | None = None, status: str = Query("open", pattern="^(open|all)$"), owner: str | None = None,
              patient: str | None = None):
        t = when(as_of)
        rows = store.list_items(t, status, owner, patient)
        return {"as_of": t.isoformat(timespec="minutes"), "count": len(rows), "items": rows}

    @app.get("/api/items/{iid}")
    def item(iid: str, as_of: str | None = None):
        try:
            return store.item(iid, when(as_of))
        except KeyError:
            raise HTTPException(404, "no such item at that time")

    @app.get("/api/documents/{did}")
    def document(did: str):
        if did not in store.documents:
            raise HTTPException(404, "no such document")
        return store.document(did)

    @app.get("/api/feeds")
    def feeds(as_of: str | None = None):
        t = when(as_of)
        down = store.feed_status(t)
        return {"as_of": t.isoformat(timespec="minutes"), "down": down, "all_clear_allowed": not down}

    @app.get("/api/alarms")
    def alarms():
        """Every feed alarm in the data, with when the feed came back (for the year view)."""
        return store.feeds

    @app.get("/api/patients/{pid}")
    def patient(pid: str, as_of: str | None = None):
        if pid not in store.patients:
            raise HTTPException(404, "no such patient")
        return store.patient(pid, when(as_of))

    @app.get("/api/evaluation")
    def evaluation():
        if not store.source.get("simulated"):
            raise HTTPException(404, "only simulated runs carry an answer key")
        return store.evaluation

    # ------------------------------------------------------------------ the agent: open or raise, nothing else
    @app.post("/api/agent/items", status_code=201)
    def agent_open(body: OpenItem, as_of: str | None = None, x_role: str | None = Header(None)):
        role(x_role, "agent")
        try:
            iid = store.agent_open(body.patient_id, body.owner_id, body.family, body.reason, body.evidence, body.urgency,
                                   body.due, body.reasoning, when(as_of))
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e))
        return {"id": iid}

    @app.post("/api/agent/items/{iid}/raise")
    def agent_raise(iid: str, body: Raise, as_of: str | None = None, x_role: str | None = Header(None)):
        role(x_role, "agent")
        if iid not in store.items:
            raise HTTPException(404, "no such item")
        try:
            return store.agent_raise(iid, body.urgency, body.reason, when(as_of))
        except AuthorityError as e:
            raise HTTPException(403, str(e))

    # ------------------------------------------------------------------ the doctor: only a doctor closes
    @app.post("/api/doctor/items/{iid}/close")
    def doctor_close(iid: str, body: Close, as_of: str | None = None, x_role: str | None = Header(None),
                     x_doctor: str | None = Header(None)):
        role(x_role, "doctor")
        if iid not in store.items:
            raise HTTPException(404, "no such item")
        try:
            return store.doctor_close(iid, body.resolution, x_doctor or "doctor", body.note, when(as_of))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/doctor/patients/{pid}/facts/{fact_id}/{action}")
    def doctor_fact(pid: str, fact_id: str, action: str, body: FactAct, as_of: str | None = None,
                    x_role: str | None = Header(None), x_doctor: str | None = Header(None)):
        role(x_role, "doctor")
        if action not in ("confirm", "reject", "close"):
            raise HTTPException(404, "no such action")
        if pid not in store.patients:
            raise HTTPException(404, "no such patient")
        try:
            return store.doctor_fact(pid, fact_id, action, body.record_id, body.quote, x_doctor or "doctor",
                                     body.note, when(as_of))
        except KeyError:
            raise HTTPException(404, "no such fact")
        except AuthorityError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/audit")
    def audit():
        return store.audit

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
