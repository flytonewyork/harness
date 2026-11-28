"""Canonical FastAPI router for the Follow-Up List API (`/api/*`).

Single Source of Truth mounted by the standalone Follow-Up API (`backend/api/app.py`)
and reusable by `backend/acute/app.py` (WO #73) to eliminate duplicated `/api/*` routes.

Asymmetric authority invariant:
- `/api/agent/*` exposes ONLY open_item and raise_urgency (never close, dismiss, downgrade, reassure).
- `/api/doctor/*` requires `X-Role: doctor` to close items or confirm/reject/close case facts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .store import AuthorityError, Store


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
    resolution: str = "acted"
    note: str = ""


class FactAct(BaseModel):
    record_id: str
    quote: str
    note: str = ""


def _require_role(x_role: str | None, need: str) -> None:
    if x_role != need:
        raise HTTPException(403, f"{need} only")


def _when(store: Store, as_of: str | None) -> datetime:
    return datetime.fromisoformat(as_of) if as_of else store.default_as_of()


def build_followup_router(
    get_store: Callable[[], Store],
    *,
    include_agent_writes: bool = True,
    include_doctor_facts: bool = True,
    require_simulated_for_eval: bool = True,
    default_doctor: str = "doctor",
) -> APIRouter:
    """Build the canonical Follow-Up List router backed by `get_store()`."""
    router = APIRouter()

    @router.get("/api/health")
    def health():
        store = get_store()
        return {"ok": True, "items": len(store.items)}

    @router.get("/api/source")
    def source():
        store = get_store()
        return {**store.source, "clock": store.clock(), "staff": list(store.staff.values())}

    @router.get("/api/items")
    def items(
        as_of: str | None = None,
        status: str = Query("open", pattern="^(open|all)$"),
        owner: str | None = None,
        patient: str | None = None,
    ):
        store = get_store()
        t = _when(store, as_of)
        rows = store.list_items(t, status, owner, patient)
        return {"as_of": t.isoformat(timespec="minutes"), "count": len(rows), "items": rows}

    @router.get("/api/items/{iid}")
    def item(iid: str, as_of: str | None = None):
        store = get_store()
        try:
            return store.item(iid, _when(store, as_of))
        except KeyError as exc:
            raise HTTPException(404, "no such item at that time") from exc

    @router.get("/api/documents/{did}")
    def document(did: str):
        store = get_store()
        if did not in store.documents:
            raise HTTPException(404, "no such document")
        return store.document(did)

    @router.get("/api/feeds")
    def feeds(as_of: str | None = None):
        store = get_store()
        t = _when(store, as_of)
        down = store.feed_status(t)
        return {"as_of": t.isoformat(timespec="minutes"), "down": down, "all_clear_allowed": not down}

    @router.get("/api/alarms")
    def alarms():
        """Every feed alarm in the data, with when the feed came back (for the year view)."""
        return get_store().feeds

    @router.get("/api/patients/{pid}")
    def patient(pid: str, as_of: str | None = None):
        store = get_store()
        if pid not in store.patients:
            raise HTTPException(404, "no such patient")
        return store.patient(pid, _when(store, as_of))

    @router.get("/api/evaluation")
    def evaluation():
        store = get_store()
        if require_simulated_for_eval and not store.source.get("simulated"):
            raise HTTPException(404, "only simulated runs carry an answer key")
        return store.evaluation

    if include_agent_writes:
        # ------------------------------------------------------------------ the agent: open or raise, nothing else
        @router.post("/api/agent/items", status_code=201)
        def agent_open(body: OpenItem, as_of: str | None = None, x_role: str | None = Header(None)):
            _require_role(x_role, "agent")
            store = get_store()
            try:
                iid = store.agent_open(
                    body.patient_id,
                    body.owner_id,
                    body.family,
                    body.reason,
                    body.evidence,
                    body.urgency,
                    body.due,
                    body.reasoning,
                    _when(store, as_of),
                )
            except (KeyError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"id": iid}

        @router.post("/api/agent/items/{iid}/raise")
        def agent_raise(iid: str, body: Raise, as_of: str | None = None, x_role: str | None = Header(None)):
            _require_role(x_role, "agent")
            store = get_store()
            if iid not in store.items:
                raise HTTPException(404, "no such item")
            try:
                return store.agent_raise(iid, body.urgency, body.reason, _when(store, as_of))
            except AuthorityError as exc:
                raise HTTPException(403, str(exc)) from exc

    # ------------------------------------------------------------------ the doctor: only a doctor closes
    @router.post("/api/doctor/items/{iid}/close")
    def doctor_close(
        iid: str,
        body: Close,
        as_of: str | None = None,
        x_role: str | None = Header(None),
        x_doctor: str | None = Header(None),
    ):
        _require_role(x_role, "doctor")
        store = get_store()
        if iid not in store.items:
            raise HTTPException(404, "no such item")
        try:
            return store.doctor_close(iid, body.resolution, x_doctor or default_doctor, body.note, _when(store, as_of))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    if include_doctor_facts:
        @router.post("/api/doctor/patients/{pid}/facts/{fact_id}/{action}")
        def doctor_fact(
            pid: str,
            fact_id: str,
            action: str,
            body: FactAct,
            as_of: str | None = None,
            x_role: str | None = Header(None),
            x_doctor: str | None = Header(None),
        ):
            _require_role(x_role, "doctor")
            if action not in ("confirm", "reject", "close"):
                raise HTTPException(404, "no such action")
            store = get_store()
            if pid not in store.patients:
                raise HTTPException(404, "no such patient")
            try:
                return store.doctor_fact(
                    pid,
                    fact_id,
                    action,
                    body.record_id,
                    body.quote,
                    x_doctor or default_doctor,
                    body.note,
                    _when(store, as_of),
                )
            except KeyError as exc:
                raise HTTPException(404, "no such fact") from exc
            except AuthorityError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @router.get("/api/audit")
    def audit():
        return get_store().audit

    return router
