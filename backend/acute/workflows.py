"""Durable PostgreSQL Transactional Outbox workflow engine (Temporal-swappable interface).

Survives process restarts and worker crashes because every scheduled timer, retry count, backoff
timestamp, and DLQ transition is durably persisted in PostgreSQL `outbox_events` under FORCED RLS.

Built-in clinical workflow schedulers & handlers:
- `booking.reminder`: sends bilingual non-clinical WhatsApp reminder before teleconsult slot
- `consult.followup_48h`: sends 48-hour post-consult check-in nudge via WhatsApp doorbell
- `proposal.sla_escalate`: escalates unreviewed AI proposals whose SLA deadline has elapsed
"""
from __future__ import annotations

import hashlib
import inspect
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .adapters import WhatsAppDoorbellAdapter
from .contracts import AdapterError
from .db import CaseLoop, Database, ExecutionTrace, OutboxEvent, Proposal, new_id, record_audit, utcnow
from .security import HKT

EVENT_BOOKING_REMINDER = "booking.reminder"
EVENT_FOLLOWUP_48H = "consult.followup_48h"
EVENT_PROPOSAL_SLA_ESCALATE = "proposal.sla_escalate"
EVENT_LOOP_CHECKPOINT_WAKE = "loop.checkpoint_wake"
EVENT_LOOP_NUDGE_RETRY = "loop.nudge_retry"
EVENT_ACTION_COMMIT_DISPATCH = "action.commit_dispatch"
EVENT_GRACE_WINDOW_EXPIRE = "loop.grace_window_expire"

HandlerFn = Callable[..., Any]


def _ensure_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_idempotency_key(key: str | None, *, event_type: str, tenant_id: str, aggregate_id: str) -> str:
    if not key:
        raw = f"{event_type}:{tenant_id}:{aggregate_id}:{uuid.uuid4().hex[:10]}"
    else:
        raw = key.strip()
    if len(raw) <= 96:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{raw[:70]}:{digest}"


def _invoke_handler(fn: HandlerFn, session: Session, event: OutboxEvent) -> Any:
    """Invoke handler supporting both `fn(session, event)` and `fn(event)` signatures."""
    sig = inspect.signature(fn)
    params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
    if has_varargs or len(params) >= 2:
        return fn(session, event)
    return fn(event)


@dataclass
class WorkflowBatchResult:
    processed: int = 0
    succeeded: int = 0
    retried: int = 0
    dlq: int = 0
    event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "retried": self.retried,
            "dlq": self.dlq,
            "event_ids": list(self.event_ids),
        }


class DurableWorkflowEngine:
    """Transactional Outbox workflow engine backed by PostgreSQL `outbox_events`."""

    def __init__(self, *, base_backoff_seconds: int = 30) -> None:
        self.base_backoff_seconds = max(0, int(base_backoff_seconds))
        self._handlers: dict[str, HandlerFn] = {}

    def register_handler(self, event_type: str, fn: HandlerFn) -> None:
        """Register a handler for `event_type` (accepts `(session, event)` or `(event)`)."""
        self._handlers[event_type] = fn

    def enqueue(
        self,
        session: Session,
        tenant_id: str,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        run_after: datetime | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> OutboxEvent:
        """Idempotently enqueue a durable outbox event inside the caller's PostgreSQL transaction."""
        key = _normalize_idempotency_key(
            idempotency_key,
            event_type=event_type,
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
        )
        due = _ensure_utc(run_after)
        stmt = (
            pg_insert(OutboxEvent)
            .values(
                id=new_id("evt"),
                tenant_id=tenant_id,
                event_type=event_type,
                aggregate_id=aggregate_id,
                payload_json=dict(payload),
                run_after=due,
                status="pending",
                attempts=0,
                max_attempts=max(1, int(max_attempts)),
                idempotency_key=key,
                created_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        session.execute(stmt)
        existing = session.execute(
            select(OutboxEvent).where(OutboxEvent.idempotency_key == key)
        ).scalar_one()
        return existing

    def cancel_pending(
        self,
        session_or_db: Session | Database,
        tenant_id: str,
        *,
        event_type: str | None = None,
        idempotency_prefix: str | None = None,
        case_loop_id: str | None = None,
        trace_id: str | None = None,
    ) -> int:
        """Cancel matching `pending` outbox events (e.g. inside the 10-second Undo window). Returns count cancelled."""

        def _do_cancel(sess: Session) -> int:
            q = select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.status == "pending",
            )
            if event_type is not None:
                q = q.where(OutboxEvent.event_type == event_type)
            rows = sess.execute(q).scalars().all()
            cancelled = 0
            now_dt = utcnow()
            for ev in rows:
                if idempotency_prefix and not str(ev.idempotency_key or "").startswith(idempotency_prefix):
                    continue
                payload = ev.payload_json or {}
                if case_loop_id and payload.get("case_loop_id") != case_loop_id and ev.aggregate_id != case_loop_id:
                    continue
                if trace_id and payload.get("trace_id") != trace_id and ev.aggregate_id != trace_id:
                    continue
                ev.status = "cancelled"
                ev.processed_at = now_dt
                ev.last_error = "cancelled_by_workflow"
                cancelled += 1
            return cancelled

        if isinstance(session_or_db, Database):
            with session_or_db.tenant_session(tenant_id) as sess:
                return _do_cancel(sess)
        return _do_cancel(session_or_db)

    def process_due(
        self,
        db: Database,
        tenant_id: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> WorkflowBatchResult:
        """Execute all due `pending` events for `tenant_id`, handling retries with backoff and DLQ."""
        now_dt = _ensure_utc(now)
        result = WorkflowBatchResult()

        with db.tenant_session(tenant_id) as session:
            due_ids = list(
                session.execute(
                    select(OutboxEvent.id)
                    .where(
                        OutboxEvent.tenant_id == tenant_id,
                        OutboxEvent.status == "pending",
                        OutboxEvent.run_after <= now_dt,
                    )
                    .order_by(OutboxEvent.run_after.asc(), OutboxEvent.created_at.asc())
                    .limit(limit)
                ).scalars()
            )

        for event_id in due_ids:
            with db.tenant_session(tenant_id) as session:
                ev = session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.id == event_id)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                if ev is None or ev.status != "pending" or _ensure_utc(ev.run_after) > now_dt:
                    continue

                result.processed += 1
                result.event_ids.append(ev.id)
                handler = self._handlers.get(ev.event_type)

                try:
                    if handler is None:
                        raise KeyError(f"No workflow handler registered for event_type='{ev.event_type}'")
                    with session.begin_nested():
                        _invoke_handler(handler, session, ev)
                    ev.attempts += 1
                    ev.status = "done"
                    ev.last_error = None
                    ev.processed_at = now_dt
                    result.succeeded += 1
                except Exception as exc:
                    ev.attempts += 1
                    ev.last_error = f"{type(exc).__name__}: {exc}"
                    non_retryable = isinstance(exc, AdapterError) and not exc.retryable
                    if non_retryable or ev.attempts >= ev.max_attempts:
                        ev.status = "dlq"
                        ev.processed_at = now_dt
                        result.dlq += 1
                    else:
                        delay_sec = self.base_backoff_seconds * (2 ** (ev.attempts - 1))
                        ev.run_after = now_dt + timedelta(seconds=delay_sec)
                        ev.status = "pending"
                        result.retried += 1

        return result

    # ------------------------------------------------------------------ Built-in Workflow Schedulers
    def schedule_booking_reminder(
        self,
        session: Session,
        tenant_id: str,
        *,
        encounter_id: str,
        patient_id: str,
        phone: str,
        doctor_name: str,
        slot_at: datetime,
        portal_url: str,
        remind_before_minutes: int = 30,
        lang: str = "bilingual",
        max_attempts: int = 3,
    ) -> OutboxEvent:
        slot_utc = _ensure_utc(slot_at)
        slot_hkt = slot_utc.astimezone(HKT)
        run_after = slot_utc - timedelta(minutes=remind_before_minutes)
        payload = {
            "encounter_id": encounter_id,
            "patient_id": patient_id,
            "phone": phone,
            "doctor_name": doctor_name,
            "slot_at": slot_utc.isoformat(),
            "slot_label": slot_hkt.strftime("%Y-%m-%d %H:%M HKT"),
            "portal_url": portal_url,
            "lang": lang,
        }
        return self.enqueue(
            session,
            tenant_id,
            EVENT_BOOKING_REMINDER,
            encounter_id,
            payload,
            run_after=run_after,
            idempotency_key=f"{EVENT_BOOKING_REMINDER}:{tenant_id}:{encounter_id}",
            max_attempts=max_attempts,
        )

    def schedule_followup_48h(
        self,
        session: Session,
        tenant_id: str,
        *,
        encounter_id: str,
        patient_id: str,
        phone: str,
        patient_first_name: str,
        completed_at: datetime,
        portal_url: str,
        delay_hours: int = 48,
        lang: str = "bilingual",
        max_attempts: int = 3,
    ) -> OutboxEvent:
        completed_utc = _ensure_utc(completed_at)
        run_after = completed_utc + timedelta(hours=delay_hours)
        payload = {
            "encounter_id": encounter_id,
            "patient_id": patient_id,
            "phone": phone,
            "patient_first_name": patient_first_name,
            "completed_at": completed_utc.isoformat(),
            "portal_url": portal_url,
            "lang": lang,
        }
        return self.enqueue(
            session,
            tenant_id,
            EVENT_FOLLOWUP_48H,
            encounter_id,
            payload,
            run_after=run_after,
            idempotency_key=f"{EVENT_FOLLOWUP_48H}:{tenant_id}:{encounter_id}",
            max_attempts=max_attempts,
        )

    def schedule_proposal_sla_escalation(
        self,
        session: Session,
        tenant_id: str,
        *,
        proposal_id: str,
        encounter_id: str,
        urgency: int,
        sla_due_at: datetime,
        max_attempts: int = 3,
    ) -> OutboxEvent:
        due_utc = _ensure_utc(sla_due_at)
        payload = {
            "proposal_id": proposal_id,
            "encounter_id": encounter_id,
            "urgency": int(urgency),
            "sla_due_at": due_utc.isoformat(),
        }
        return self.enqueue(
            session,
            tenant_id,
            EVENT_PROPOSAL_SLA_ESCALATE,
            proposal_id,
            payload,
            run_after=due_utc,
            idempotency_key=f"{EVENT_PROPOSAL_SLA_ESCALATE}:{tenant_id}:{proposal_id}",
            max_attempts=max_attempts,
        )

    def schedule_grace_window_expiry(
        self,
        session: Session,
        tenant_id: str,
        *,
        case_loop_id: str,
        grace_hours: int = 72,
        started_at: datetime | None = None,
        max_attempts: int = 3,
    ) -> OutboxEvent:
        """Schedule post-visit/discharge 72h grace-window expiry on durable `outbox_events`."""
        start_utc = _ensure_utc(started_at)
        due_utc = start_utc + timedelta(hours=max(1, int(grace_hours)))
        payload = {
            "case_loop_id": case_loop_id,
            "grace_hours": int(grace_hours),
            "started_at": start_utc.isoformat(),
            "expires_at": due_utc.isoformat(),
        }
        return self.enqueue(
            session,
            tenant_id,
            EVENT_GRACE_WINDOW_EXPIRE,
            case_loop_id,
            payload,
            run_after=due_utc,
            idempotency_key=f"{EVENT_GRACE_WINDOW_EXPIRE}:{tenant_id}:{case_loop_id}",
            max_attempts=max_attempts,
        )

    def schedule_action_commit_dispatch(
        self,
        session: Session,
        tenant_id: str,
        *,
        trace_id: str,
        case_loop_id: str,
        run_after: datetime,
        max_attempts: int = 3,
    ) -> OutboxEvent:
        """Schedule durable 10-second Undo window final dispatch event for an irreversible commit."""
        due_utc = _ensure_utc(run_after)
        payload = {
            "trace_id": trace_id,
            "case_loop_id": case_loop_id,
            "dispatch_at": due_utc.isoformat(),
        }
        return self.enqueue(
            session,
            tenant_id,
            EVENT_ACTION_COMMIT_DISPATCH,
            trace_id,
            payload,
            run_after=due_utc,
            idempotency_key=f"{EVENT_ACTION_COMMIT_DISPATCH}:{tenant_id}:{trace_id}",
            max_attempts=max_attempts,
        )

    def schedule_loop_nudge_retry(
        self,
        session: Session,
        tenant_id: str,
        *,
        case_loop_id: str,
        run_after: datetime,
        retry_cap: int = 2,
        nudge_seq: int = 1,
        max_attempts: int = 3,
    ) -> OutboxEvent:
        """Schedule a bounded patient follow-up nudge (2 nudges / 72h cap -> Nurse Rachel first)."""
        due_utc = _ensure_utc(run_after)
        payload = {
            "case_loop_id": case_loop_id,
            "retry_cap": max(1, int(retry_cap)),
            "nudge_seq": max(1, int(nudge_seq)),
        }
        return self.enqueue(
            session,
            tenant_id,
            EVENT_LOOP_NUDGE_RETRY,
            case_loop_id,
            payload,
            run_after=due_utc,
            idempotency_key=f"{EVENT_LOOP_NUDGE_RETRY}:{tenant_id}:{case_loop_id}:{nudge_seq}",
            max_attempts=max_attempts,
        )

    # ------------------------------------------------------------------ Default Clinical Handlers
    def register_default_handlers(
        self,
        *,
        whatsapp_adapter: WhatsAppDoorbellAdapter | None = None,
    ) -> None:
        """Register default handlers for all clinical and CaseLoop durable outbox events."""

        def _handle_booking_reminder(session: Session, event: OutboxEvent) -> None:
            p = event.payload_json
            ext_ref = ""
            if whatsapp_adapter is not None:
                res = whatsapp_adapter.send_template(
                    phone=p["phone"],
                    template_id="booking_reminder",
                    params={
                        "slot_label": p.get("slot_label", "upcoming slot"),
                        "doctor_name": p.get("doctor_name", "your doctor"),
                        "url": p.get("portal_url", "https://portal.tmrw-clinic.hk"),
                    },
                    lang=p.get("lang", "bilingual"),
                    conversation_id=p.get("encounter_id", event.aggregate_id),
                    idempotency_key=f"wa:{event.idempotency_key}",
                )
                ext_ref = res.external_ref
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="workflow.booking_reminder_sent",
                resource_type="encounter",
                resource_id=event.aggregate_id,
                metadata={"event_id": event.id, "whatsapp_ref": ext_ref},
            )

        def _handle_followup_48h(session: Session, event: OutboxEvent) -> None:
            p = event.payload_json
            ext_ref = ""
            if whatsapp_adapter is not None:
                res = whatsapp_adapter.send_template(
                    phone=p["phone"],
                    template_id="followup_48h",
                    params={
                        "patient_first_name": p.get("patient_first_name", "there"),
                        "url": p.get("portal_url", "https://portal.tmrw-clinic.hk"),
                    },
                    lang=p.get("lang", "bilingual"),
                    conversation_id=p.get("encounter_id", event.aggregate_id),
                    idempotency_key=f"wa:{event.idempotency_key}",
                )
                ext_ref = res.external_ref
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="workflow.followup_48h_sent",
                resource_type="encounter",
                resource_id=event.aggregate_id,
                metadata={"event_id": event.id, "whatsapp_ref": ext_ref},
            )

        def _handle_proposal_sla_escalate(session: Session, event: OutboxEvent) -> None:
            p = event.payload_json
            proposal_id = p.get("proposal_id") or event.aggregate_id
            proposal = session.execute(
                select(Proposal).where(Proposal.id == proposal_id)
            ).scalar_one_or_none()
            if proposal is None or proposal.status != "pending_review":
                # Proposal was already reviewed/closed before SLA expiry — safe idempotent no-op
                return
            proposal.status = "escalated"
            proposal.urgency = max(int(proposal.urgency), 4)
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="proposal.sla_escalated",
                resource_type="proposal",
                resource_id=proposal.id,
                metadata={
                    "event_id": event.id,
                    "encounter_id": proposal.encounter_id,
                    "new_urgency": proposal.urgency,
                },
            )

        def _handle_loop_checkpoint_wake(session: Session, event: OutboxEvent) -> None:
            case_id = event.payload_json.get("case_loop_id") or event.aggregate_id
            case = session.execute(select(CaseLoop).where(CaseLoop.id == case_id)).scalar_one_or_none()
            if case is None or case.state != "scheduled":
                return
            case.state = "running"
            case.baton = "with_patient"
            tl = list(case.timeline_json or [])
            tl.append({
                "date": "Wake",
                "label": f"Checkpoint woke automatically ({case.next_checkpoint_label or 'scheduled check-in'}).",
            })
            case.timeline_json = tl
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="loop.checkpoint_woke",
                resource_type="case_loop",
                resource_id=case.id,
                metadata={"event_id": event.id, "checkpoint": case.next_checkpoint_label},
            )

        def _handle_loop_nudge_retry(session: Session, event: OutboxEvent) -> None:
            case_id = event.payload_json.get("case_loop_id") or event.aggregate_id
            retry_cap = max(1, int(event.payload_json.get("retry_cap", 2)))
            case = session.execute(select(CaseLoop).where(CaseLoop.id == case_id)).scalar_one_or_none()
            if case is None or case.state not in ("running", "scheduled"):
                return
            case.retry_count += 1
            tl = list(case.timeline_json or [])
            if case.retry_count >= retry_cap:
                if case.risk_tier == "high":
                    case.state = "needs_decision"
                    case.baton = "with_doctor"
                    tl.append({
                        "date": "Escalated",
                        "label": f"High-risk loop reached {case.retry_count}/{retry_cap} nudges (72h cap) — escalated to doctor #my-work.",
                    })
                else:
                    # Low/standard risk stalls ALWAYS route to Nurse Rachel first (0 doctor interruption)
                    case.state = "running"
                    case.baton = "with_nurse"
                    tl.append({
                        "date": "Nurse Handoff",
                        "label": f"Patient unanswered after {case.retry_count}/{retry_cap} nudges (72h cap) — routed to Nurse Rachel first.",
                    })
            else:
                case.state = "running"
                case.baton = "with_patient"
                tl.append({
                    "date": "Nudge",
                    "label": f"Automated Careline follow-up nudge {case.retry_count}/{retry_cap} sent.",
                })
            case.timeline_json = tl
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="loop.nudge_retry_processed",
                resource_type="case_loop",
                resource_id=case.id,
                metadata={
                    "retry_count": case.retry_count,
                    "retry_cap": retry_cap,
                    "new_state": case.state,
                    "new_baton": case.baton,
                },
            )

        def _handle_action_commit_dispatch(session: Session, event: OutboxEvent) -> None:
            trace_id = event.payload_json.get("trace_id") or event.aggregate_id
            trace = session.execute(select(ExecutionTrace).where(ExecutionTrace.id == trace_id)).scalar_one_or_none()
            if trace is None or trace.status != "undo_window":
                return
            now_dt = utcnow()
            trace.status = "dispatched"
            if isinstance(trace.emr_addendum_json, dict) and trace.emr_addendum_json:
                updated_addendum = dict(trace.emr_addendum_json)
                updated_addendum["dispatch_status"] = "finalized"
                updated_addendum["dispatched_at"] = now_dt.isoformat()
                trace.emr_addendum_json = updated_addendum
                flag_modified(trace, "emr_addendum_json")
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="action.commit_dispatched",
                resource_type="execution_trace",
                resource_id=trace.id,
                metadata={"case_loop_id": trace.case_loop_id, "dispatched_at": now_dt.isoformat()},
            )

        def _handle_grace_window_expire(session: Session, event: OutboxEvent) -> None:
            case_id = event.payload_json.get("case_loop_id") or event.aggregate_id
            grace_hours = int(event.payload_json.get("grace_hours", 72))
            case = session.execute(select(CaseLoop).where(CaseLoop.id == case_id)).scalar_one_or_none()
            if case is None or case.state == "closed":
                return
            summary = dict(case.summary_json or {})
            summary["grace_window_expired"] = True
            case.summary_json = summary
            flag_modified(case, "summary_json")
            tl = list(case.timeline_json or [])
            tl.append({
                "date": "Grace Expired",
                "label": f"Post-visit {grace_hours}h grace window elapsed — new clinical queries route to booking options.",
            })
            case.timeline_json = tl
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
            record_audit(
                session,
                tenant_id=event.tenant_id,
                actor_role="system_workflow",
                actor_id="durable_outbox",
                action="loop.grace_window_expired",
                resource_type="case_loop",
                resource_id=case.id,
                metadata={"event_id": event.id, "grace_hours": grace_hours},
            )

        self.register_handler(EVENT_BOOKING_REMINDER, _handle_booking_reminder)
        self.register_handler(EVENT_FOLLOWUP_48H, _handle_followup_48h)
        self.register_handler(EVENT_PROPOSAL_SLA_ESCALATE, _handle_proposal_sla_escalate)
        self.register_handler(EVENT_LOOP_CHECKPOINT_WAKE, _handle_loop_checkpoint_wake)
        self.register_handler(EVENT_LOOP_NUDGE_RETRY, _handle_loop_nudge_retry)
        self.register_handler(EVENT_ACTION_COMMIT_DISPATCH, _handle_action_commit_dispatch)
        self.register_handler(EVENT_GRACE_WINDOW_EXPIRE, _handle_grace_window_expire)
