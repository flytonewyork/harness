"""24/7 Autonomous Cloud Infra On-Call SRE Agent (Observe -> Diagnose -> Guard -> Remediate -> Verify)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from backend.acute.observability import configure_structured_logger
from backend.acute.security import PHISafeLogFilter
from .diagnostician import VertexSREDiagnostician, diagnose_deterministic
from .gcp_client import GCPControlPlane, InfraControlPlane
from .models import (
    ActionKind,
    Diagnosis,
    IncidentClass,
    IncidentRecord,
    ProbeSnapshot,
    RemediationOutcome,
    SREConfig,
    SREState,
    utcnow_iso,
)

logger = configure_structured_logger("tmrw.sre")
logger.addFilter(PHISafeLogFilter())


class AutonomousSREAgent:
    """Senior Infra Engineer autonomous on-call loop with circuit breakers and zero-data-loss invariants."""

    def __init__(
        self,
        config: SREConfig | None = None,
        control_plane: InfraControlPlane | None = None,
        vertex_diagnostician: VertexSREDiagnostician | None = None,
    ):
        self.config = config or SREConfig()
        self.cp = control_plane or GCPControlPlane(self.config)
        self.vertex = vertex_diagnostician or VertexSREDiagnostician(self.config)

    # ---------------------------------------------------------------------------------- Observe
    def observe(self) -> ProbeSnapshot:
        cr = self.cp.get_cloud_run_state()
        sql = self.cp.get_cloud_sql_state()
        target_url = self.config.target_service_url or (cr.url if cr else "")
        probes = self.cp.probe_http(target_url)
        errors = self.cp.query_recent_errors(self.config.log_lookback_minutes)

        max_lat = max((p.latency_ms for p in probes), default=0.0)
        all_ok = bool(probes) and all(p.ok for p in probes)
        ready_probes = [p for p in probes if p.path == "/api/v1/health/ready"]
        ready_ok = all(p.ok for p in ready_probes) if ready_probes else all_ok

        outbox_pending = 0
        outbox_dlq = 0
        for p in probes:
            if p.path == "/api/v1/health/ready" and p.payload:
                ob = p.payload.get("outbox") or {}
                outbox_pending = int(ob.get("pending", 0) or 0)
                outbox_dlq = int(ob.get("dead_letter", 0) or 0)

        return ProbeSnapshot(
            http_probes=probes,
            cloud_run=cr,
            cloud_sql=sql,
            recent_errors=errors,
            max_latency_ms=round(max_lat, 1),
            all_http_ok=all_ok,
            ready_ok=ready_ok,
            outbox_pending=outbox_pending,
            outbox_dead_letter=outbox_dlq,
        )

    # ---------------------------------------------------------------------------------- Circuit Breaker
    def _check_circuit_breaker(self, state: SREState, now: datetime) -> tuple[bool, str]:
        cb = state.circuit_breaker
        if cb.tripped:
            return False, cb.tripped_reason or "Circuit breaker tripped; awaiting operator reset"

        # Prune old action timestamps outside sliding window
        cutoff = now - timedelta(seconds=self.config.window_seconds)
        recent_ts: list[str] = []
        for ts_str in cb.action_timestamps:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts >= cutoff:
                    recent_ts.append(ts_str)
            except Exception:
                pass
        cb.action_timestamps = recent_ts

        if len(recent_ts) >= self.config.max_actions_per_window:
            cb.tripped = True
            cb.tripped_reason = (
                f"Reached max autonomous remediations ({self.config.max_actions_per_window}) "
                f"within {self.config.window_seconds}s window"
            )
            return False, cb.tripped_reason

        if cb.last_action_at:
            try:
                last_dt = datetime.fromisoformat(cb.last_action_at)
                elapsed = (now - last_dt).total_seconds()
                if elapsed < self.config.cooldown_seconds:
                    remaining = int(self.config.cooldown_seconds - elapsed)
                    return False, f"Cooldown active ({remaining}s remaining before next autonomous action)"
            except Exception:
                pass

        return True, ""

    def reset_circuit_breaker(self) -> SREState:
        state = self.cp.load_state()
        state.circuit_breaker.tripped = False
        state.circuit_breaker.tripped_reason = ""
        state.circuit_breaker.action_timestamps = []
        state.circuit_breaker.last_action_at = ""
        self.cp.save_state(state)
        return state

    # ---------------------------------------------------------------------------------- Remediate
    def _execute_action(self, diagnosis: Diagnosis, target_url: str) -> tuple[bool, str]:
        act = diagnosis.recommended_action
        params = diagnosis.action_params
        if act == ActionKind.START_CLOUDSQL:
            return self.cp.start_cloudsql()
        if act == ActionKind.ROLLBACK_REVISION:
            target = str(params.get("target_revision", ""))
            if not target:
                return False, "No rollback target revision available"
            return self.cp.rollback_to_revision(target)
        if act == ActionKind.BUMP_MEMORY:
            mem = str(params.get("target_memory", "2Gi"))
            return self.cp.bump_memory(mem)
        if act == ActionKind.SCALE_MAX_INSTANCES:
            n = int(params.get("target_max_instances", 10))
            return self.cp.scale_max_instances(n)
        if act == ActionKind.RESTART_SERVICE:
            return self.cp.restart_service(str(params.get("reason", "autonomous_self_heal")))
        if act == ActionKind.KICK_OUTBOX:
            return self.cp.kick_outbox(target_url)
        return False, f"Non-executable action: {act.value}"

    # ---------------------------------------------------------------------------------- Main Tick
    def tick(self) -> dict:
        """Run one complete 24/7 SRE cycle: Observe -> Diagnose -> Guard -> Remediate -> Verify -> Persist."""
        now = datetime.now(timezone.utc)
        state = self.cp.load_state()
        snapshot = self.observe()
        state.last_tick_at = now.isoformat()
        state.total_ticks += 1
        state.last_snapshot = snapshot

        active_rev = (
            (snapshot.cloud_run.active_traffic_revision or snapshot.cloud_run.latest_ready_revision)
            if snapshot.cloud_run
            else ""
        )
        target_url = self.config.target_service_url or (snapshot.cloud_run.url if snapshot.cloud_run else "")

        diagnosis = diagnose_deterministic(snapshot, state, self.config)
        if diagnosis.incident_class == IncidentClass.NOVEL_5XX_ERROR:
            diagnosis = self.vertex.refine(snapshot, diagnosis, state)

        # Steady-state healthy: record last_healthy_revision and exit cleanly
        if diagnosis.incident_class == IncidentClass.HEALTHY:
            state.consecutive_failures = 0
            if active_rev and snapshot.all_http_ok:
                state.last_healthy_revision = active_rev
            self.cp.save_state(state)
            logger.info(
                "SRE tick healthy: service=%s rev=%s max_latency_ms=%.1f",
                self.config.target_service,
                active_rev,
                snapshot.max_latency_ms,
            )
            return {
                "ok": True,
                "healthy": True,
                "active_revision": active_rev,
                "last_healthy_revision": state.last_healthy_revision,
                "diagnosis": diagnosis.model_dump(),
                "remediation": RemediationOutcome(
                    action=ActionKind.NOOP, executed=False, succeeded=True, detail="Healthy — no action required"
                ).model_dump(),
            }

        # Unhealthy / incident detected
        state.consecutive_failures += 1
        escalated = False

        if not self.config.auto_remediate or diagnosis.recommended_action in (ActionKind.NOOP, ActionKind.ESCALATE_HUMAN):
            escalated = True
            self.cp.send_alert(
                f"{diagnosis.severity.value} {diagnosis.incident_class.value}: {diagnosis.summary}",
                {"diagnosis": diagnosis.model_dump(), "active_revision": active_rev},
            )
            outcome = RemediationOutcome(
                action=diagnosis.recommended_action,
                executed=False,
                succeeded=False,
                detail="Escalated to human operator (auto_remediate disabled or manual action required)",
            )
        else:
            allowed, cb_reason = self._check_circuit_breaker(state, now)
            if not allowed:
                escalated = True
                self.cp.send_alert(
                    f"Circuit Breaker Blocked Remediation ({diagnosis.incident_class.value})",
                    {"reason": cb_reason, "diagnosis": diagnosis.model_dump()},
                )
                outcome = RemediationOutcome(
                    action=diagnosis.recommended_action,
                    executed=False,
                    succeeded=False,
                    detail=cb_reason,
                    blocked_by_circuit_breaker=True,
                )
            else:
                ok, detail = self._execute_action(diagnosis, target_url)
                state.circuit_breaker.action_timestamps.append(now.isoformat())
                state.circuit_breaker.last_action_at = now.isoformat()
                if ok:
                    state.total_remediations += 1

                # Immediate post-action verification probe
                post_probes = self.cp.probe_http(target_url)
                post_ok = bool(post_probes) and all(p.ok for p in post_probes)
                if not ok:
                    escalated = True
                    self.cp.send_alert(
                        f"Autonomous remediation failed ({diagnosis.recommended_action.value})",
                        {"detail": detail, "diagnosis": diagnosis.model_dump()},
                    )
                outcome = RemediationOutcome(
                    action=diagnosis.recommended_action,
                    executed=True,
                    succeeded=ok,
                    detail=detail,
                    post_verify_ok=post_ok,
                )

        incident = IncidentRecord(
            id=f"inc_{uuid.uuid4().hex[:12]}",
            timestamp=now.isoformat(),
            target_service=self.config.target_service,
            active_revision=active_rev,
            diagnosis=diagnosis,
            remediation=outcome,
            escalated=escalated,
        )
        state.incidents.append(incident)
        self.cp.save_state(state)

        logger.warning(
            "SRE incident %s: class=%s action=%s executed=%s succeeded=%s escalated=%s",
            incident.id,
            diagnosis.incident_class.value,
            outcome.action.value,
            outcome.executed,
            outcome.succeeded,
            escalated,
        )
        return {
            "ok": outcome.succeeded,
            "healthy": False,
            "incident_id": incident.id,
            "active_revision": active_rev,
            "last_healthy_revision": state.last_healthy_revision,
            "diagnosis": diagnosis.model_dump(),
            "remediation": outcome.model_dump(),
            "escalated": escalated,
        }
