"""Real-time SSE event bus and in-memory live state store for the Multi-Agent Clinic EMR Harness.

Provides instant cross-surface synchronization between:
- Patient Phone (/p, /p/qr/<patient_id>)
- Clinic Staff Console (/c — Nurse Rachel & Dr. Kenneth Lam)
- Separate Simulated Clinic EMR + Agent Harness Inspector (/emr)

Thread-safe across FastAPI sync endpoints (run in threadpool) and async SSE generators.
"""
from __future__ import annotations

import asyncio
import copy
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .db import new_id


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveHarnessBus:
    """Thread-safe pub/sub event bus + bounded per-patient live agent state store."""

    def __init__(self, max_events: int = 200) -> None:
        self._lock = threading.RLock()
        self._seq: int = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]], str | None]] = []
        self._patient_state: dict[str, dict[str, Any]] = {}

    def _ensure_patient_locked(self, patient_id: str) -> dict[str, Any]:
        st = self._patient_state.get(patient_id)
        if st is None:
            st = {
                "patient_id": patient_id,
                "phone_connected": False,
                "phone_connected_at": None,
                "workflow_stage": "idle",  # idle -> qr_scanned -> intake_submitted -> nurse_ready -> doctor_signed
                "engine_mode": "deterministic_harness",
                "active_agent": None,
                "memory": {
                    "emr_memory": {},
                    "episodic_memory": [],
                    "protocol_memory": [],
                },
                "context_window": None,
                "tool_calls": [],
                "rich_cards": [],
                "booked_slot": None,
                "flu_vax_deferred": False,
                "active_proposal_id": None,
                "last_emr_highlight": None,
            }
            self._patient_state[patient_id] = st
        return st

    def get_patient_state(self, patient_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._ensure_patient_locked(patient_id))

    def get_all_patient_states(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {pid: copy.deepcopy(st) for pid, st in self._patient_state.items()}

    def update_patient_state(self, patient_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            st = self._ensure_patient_locked(patient_id)
            for k, v in updates.items():
                st[k] = copy.deepcopy(v)
            return copy.deepcopy(st)

    def append_tool_call(self, patient_id: str, call_record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            st = self._ensure_patient_locked(patient_id)
            rec = {
                "id": call_record.get("id") or new_id("tc"),
                "timestamp": call_record.get("timestamp") or _utc_iso(),
                **copy.deepcopy(call_record),
            }
            st["tool_calls"].append(rec)
            if len(st["tool_calls"]) > 80:
                st["tool_calls"] = st["tool_calls"][-80:]
            if rec.get("emr_target"):
                st["last_emr_highlight"] = {
                    "target": rec["emr_target"],
                    "authority": rec.get("authority", "READ"),
                    "tool": rec.get("tool"),
                    "timestamp": rec["timestamp"],
                }
        self.publish("tool.call", patient_id=patient_id, data=rec)
        return rec

    def upsert_episodic_fact(self, patient_id: str, fact: dict[str, Any]) -> list[dict[str, Any]]:
        with self._lock:
            st = self._ensure_patient_locked(patient_id)
            items: list[dict[str, Any]] = st["memory"]["episodic_memory"]
            fact_copy = {**copy.deepcopy(fact), "updated_at": _utc_iso()}
            replaced = False
            for idx, existing in enumerate(items):
                if existing.get("key") == fact_copy.get("key"):
                    items[idx] = fact_copy
                    replaced = True
                    break
            if not replaced:
                items.append(fact_copy)
            snapshot = copy.deepcopy(st["memory"])
        self.publish("memory.updated", patient_id=patient_id, data={"fact": fact_copy, "memory": snapshot})
        return snapshot["episodic_memory"]

    def set_memory_layers(
        self,
        patient_id: str,
        *,
        emr_memory: dict[str, Any] | None = None,
        protocol_memory: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            st = self._ensure_patient_locked(patient_id)
            if emr_memory is not None:
                st["memory"]["emr_memory"] = copy.deepcopy(emr_memory)
            if protocol_memory is not None:
                st["memory"]["protocol_memory"] = copy.deepcopy(protocol_memory)
            snapshot = copy.deepcopy(st["memory"])
        self.publish("memory.updated", patient_id=patient_id, data={"memory": snapshot})
        return snapshot

    def set_context_window(self, patient_id: str, context_window: dict[str, Any]) -> None:
        ctx = {**copy.deepcopy(context_window), "assembled_at": _utc_iso()}
        with self._lock:
            st = self._ensure_patient_locked(patient_id)
            st["context_window"] = ctx
            if ctx.get("engine_mode"):
                st["engine_mode"] = ctx["engine_mode"]
        self.publish("context.assembled", patient_id=patient_id, data=ctx)

    def reset_patient(self, patient_id: str) -> None:
        with self._lock:
            self._patient_state.pop(patient_id, None)
            self._ensure_patient_locked(patient_id)
        self.publish("demo.reset", patient_id=patient_id, data={"patient_id": patient_id})

    def reset_all(self) -> None:
        with self._lock:
            self._patient_state.clear()
            self._events.clear()
        self.publish("demo.reset", patient_id=None, data={"scope": "all"})

    def recent_events(self, patient_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            evs = list(self._events)
        if patient_id:
            evs = [e for e in evs if e.get("patient_id") in (None, patient_id)]
        return evs[-limit:]

    def publish(self, event_type: str, *, patient_id: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            ev = {
                "seq": self._seq,
                "id": new_id("evt"),
                "type": event_type,
                "patient_id": patient_id,
                "timestamp": _utc_iso(),
                "data": copy.deepcopy(data or {}),
            }
            self._events.append(ev)
            subs = list(self._subscribers)

        for loop, queue, filter_pid in subs:
            if filter_pid and patient_id and filter_pid != patient_id:
                continue
            try:
                loop.call_soon_threadsafe(_put_nowait_drop_oldest, queue, ev)
            except RuntimeError:
                # Event loop already closed
                pass
        return ev

    def subscribe(self, loop: asyncio.AbstractEventLoop, patient_id: str | None = None) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
        with self._lock:
            self._subscribers.append((loop, q, patient_id))
        return q

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers = [sub for sub in self._subscribers if sub[1] is not queue]


def _put_nowait_drop_oldest(q: asyncio.Queue[dict[str, Any]], item: dict[str, Any]) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass
