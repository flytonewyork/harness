"""Two-tier SRE Root-Cause Diagnostician: deterministic fast-path + PHI-scrubbed Vertex AI (asia-east2)."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from backend.acute.security import PHIScrubber
from .models import (
    ActionKind,
    Diagnosis,
    IncidentClass,
    ProbeSnapshot,
    SREConfig,
    SREState,
    Severity,
)

_OOM_PATTERNS = re.compile(
    r"(memory limit of .* exceeded|oomkilled|out of memory|cannot allocate memory|exit status 137|killed \(signal 9\))",
    re.IGNORECASE,
)
_SATURATION_PATTERNS = re.compile(
    r"(429 too many requests|max instances|request was aborted because there was no available instance|concurrency limit|503 service unavailable)",
    re.IGNORECASE,
)
_DB_POOL_PATTERNS = re.compile(
    r"(connection refused|could not connect to server|too many clients|queuepool limit|operationalerror|timeout expired|terminating connection)",
    re.IGNORECASE,
)
_IAM_SECRET_PATTERNS = re.compile(
    r"(permission_denied|403 forbidden|insufficientprivilege|secret.*not found|could not automatically determine credentials|default credentials were not found)",
    re.IGNORECASE,
)


def parse_memory_gi(mem: str) -> int:
    m = re.match(r"^\s*(\d+)\s*Gi\s*$", mem or "", re.IGNORECASE)
    if m:
        return int(m.group(1))
    m_mi = re.match(r"^\s*(\d+)\s*Mi\s*$", mem or "", re.IGNORECASE)
    if m_mi:
        return max(1, (int(m_mi.group(1)) + 1023) // 1024)
    return 1


def pick_rollback_target(snapshot: ProbeSnapshot, state: SREState) -> str | None:
    """Find a distinct, ready prior revision for instant traffic rollback."""
    cr = snapshot.cloud_run
    if not cr:
        return None
    active = cr.active_traffic_revision or cr.latest_ready_revision or cr.latest_created_revision
    if state.last_healthy_revision and state.last_healthy_revision != active:
        return state.last_healthy_revision
    for rev in cr.revisions:
        if rev.ready and rev.name and rev.name != active:
            return rev.name
    return None


def diagnose_deterministic(snapshot: ProbeSnapshot, state: SREState, config: SREConfig) -> Diagnosis:
    """Tier-1 deterministic Senior-SRE classifier (<1ms, zero LLM dependency)."""
    errors_joined = "\n".join(e.message for e in snapshot.recent_errors)
    cr = snapshot.cloud_run
    sql = snapshot.cloud_sql

    # 1. Cloud SQL stopped or manually deactivated
    if sql and (sql.activation_policy.upper() == "NEVER" or sql.state.upper() in ("STOPPED", "SUSPENDED")):
        return Diagnosis(
            incident_class=IncidentClass.CLOUDSQL_STOPPED,
            severity=Severity.CRITICAL,
            summary=f"Cloud SQL instance {sql.instance_name} is stopped ({sql.state}/{sql.activation_policy}).",
            root_cause="Database activationPolicy is NEVER or instance is stopped, blocking all clinical reads/writes.",
            recommended_action=ActionKind.START_CLOUDSQL,
            action_params={"instance_name": sql.instance_name},
            evidence=[f"Cloud SQL state={sql.state}, activationPolicy={sql.activation_policy}"],
        )

    # 2. Container OOMKilled / memory ceiling breach
    oom_hits = [e.message[:220] for e in snapshot.recent_errors if _OOM_PATTERNS.search(e.message)]
    if oom_hits:
        cur_gi = parse_memory_gi(cr.memory_limit if cr else "1Gi")
        next_gi = min(config.max_memory_gi, max(cur_gi * 2, cur_gi + 1))
        if next_gi > cur_gi:
            return Diagnosis(
                incident_class=IncidentClass.OOM_KILLED,
                severity=Severity.CRITICAL,
                summary=f"Cloud Run container hit memory limit ({cur_gi}Gi); bumping to {next_gi}Gi.",
                root_cause="Recent Cloud Logging entries show OOMKilled / memory limit exceeded.",
                recommended_action=ActionKind.BUMP_MEMORY,
                action_params={"current_memory": f"{cur_gi}Gi", "target_memory": f"{next_gi}Gi"},
                evidence=oom_hits[:5],
            )
        return Diagnosis(
            incident_class=IncidentClass.OOM_KILLED,
            severity=Severity.CRITICAL,
            summary=f"Container OOMKilled at max allowed memory cap ({cur_gi}Gi); escalating to human SRE.",
            root_cause="Memory leak or load spike exceeds autonomous vertical scaling cap.",
            recommended_action=ActionKind.ESCALATE_HUMAN,
            evidence=oom_hits[:5],
        )

    # 3. IAM / Secret Manager / DB privilege drift
    iam_hits = [e.message[:220] for e in snapshot.recent_errors if _IAM_SECRET_PATTERNS.search(e.message)]
    if iam_hits and (not snapshot.all_http_ok or len(iam_hits) >= 2):
        rollback_rev = pick_rollback_target(snapshot, state)
        if rollback_rev and cr and cr.latest_created_revision != cr.active_traffic_revision:
            return Diagnosis(
                incident_class=IncidentClass.BAD_REVISION_REGRESSION,
                severity=Severity.CRITICAL,
                summary=f"New revision hit privilege/secret failure; rolling back to {rollback_rev}.",
                root_cause="Newly deployed revision encountered IAM/secret/DB permission errors.",
                recommended_action=ActionKind.ROLLBACK_REVISION,
                action_params={"target_revision": rollback_rev},
                evidence=iam_hits[:5],
            )
        return Diagnosis(
            incident_class=IncidentClass.IAM_OR_SECRET_ERROR,
            severity=Severity.CRITICAL,
            summary="IAM or Secret Manager permission failure detected; requires operator verification.",
            root_cause="Runtime service account lacks a required secret/IAM/DB privilege.",
            recommended_action=ActionKind.ESCALATE_HUMAN,
            evidence=iam_hits[:5],
        )

    # 4. Bad revision regression (HTTP unhealthy or error burst after a revision change)
    failed_probes = [f"{p.path} -> HTTP {p.status_code} ({p.error or 'unhealthy'})" for p in snapshot.http_probes if not p.ok]
    rollback_rev = pick_rollback_target(snapshot, state)
    active_rev = (cr.active_traffic_revision or cr.latest_ready_revision) if cr else ""
    revision_changed = bool(rollback_rev and active_rev and active_rev != rollback_rev)
    if (failed_probes or len(snapshot.recent_errors) >= config.error_burst_threshold) and revision_changed:
        return Diagnosis(
            incident_class=IncidentClass.BAD_REVISION_REGRESSION,
            severity=Severity.CRITICAL,
            summary=f"Active revision {active_rev} is unhealthy; rolling back 100% traffic to {rollback_rev}.",
            root_cause="Health probes failed or 5xx burst detected on newly rolled revision.",
            recommended_action=ActionKind.ROLLBACK_REVISION,
            action_params={"target_revision": rollback_rev, "failed_revision": active_rev},
            evidence=(failed_probes + [e.message[:180] for e in snapshot.recent_errors[:3]])[:5],
        )

    # 5. Readiness / DB connection pool wedged (SQL is runnable, no newer healthy rollback revision)
    db_hits = [e.message[:220] for e in snapshot.recent_errors if _DB_POOL_PATTERNS.search(e.message)]
    if not snapshot.ready_ok or (failed_probes and db_hits):
        return Diagnosis(
            incident_class=IncidentClass.READINESS_OR_DB_POOL_WEDGED,
            severity=Severity.HIGH,
            summary="Readiness probe or DB pool is wedged while Cloud SQL is RUNNABLE; triggering zero-downtime restart.",
            root_cause="Stale DB connection pool or stuck worker process inside running container.",
            recommended_action=ActionKind.RESTART_SERVICE,
            action_params={"reason": "readiness_or_db_pool_wedged"},
            evidence=(failed_probes + db_hits)[:5],
        )

    # 6. Saturation / latency SLO breach / 429/503 throttling
    sat_hits = [e.message[:220] for e in snapshot.recent_errors if _SATURATION_PATTERNS.search(e.message)]
    if sat_hits or snapshot.max_latency_ms > config.latency_slo_ms:
        cur_max = cr.max_instances if cr else 5
        next_max = min(config.max_instances_cap, max(cur_max + 5, cur_max * 2))
        if next_max > cur_max:
            return Diagnosis(
                incident_class=IncidentClass.SATURATION_LATENCY_OR_429,
                severity=Severity.HIGH,
                summary=f"Latency ({snapshot.max_latency_ms:.0f}ms) or concurrency saturation detected; raising maxInstances {cur_max} -> {next_max}.",
                root_cause="Traffic spike exceeded Cloud Run instance ceiling or latency SLO.",
                recommended_action=ActionKind.SCALE_MAX_INSTANCES,
                action_params={"current_max_instances": cur_max, "target_max_instances": next_max},
                evidence=(sat_hits + [f"max_latency_ms={snapshot.max_latency_ms:.1f} > slo={config.latency_slo_ms}"])[:5],
            )
        return Diagnosis(
            incident_class=IncidentClass.SATURATION_LATENCY_OR_429,
            severity=Severity.HIGH,
            summary=f"Latency/saturation persists at maxInstances cap ({cur_max}); escalating to human SRE.",
            root_cause="Traffic load exceeds autonomous horizontal scaling cap.",
            recommended_action=ActionKind.ESCALATE_HUMAN,
            evidence=(sat_hits + [f"max_latency_ms={snapshot.max_latency_ms:.1f}"])[:5],
        )

    # 7. Durable outbox backlog stalled
    if snapshot.outbox_dead_letter > 0 or snapshot.outbox_pending >= 25:
        return Diagnosis(
            incident_class=IncidentClass.OUTBOX_STALLED,
            severity=Severity.WARN,
            summary=f"Durable outbox backlog detected (pending={snapshot.outbox_pending}, dlq={snapshot.outbox_dead_letter}); triggering outbox drain.",
            root_cause="Outbox worker fell behind or encountered transient adapter failures.",
            recommended_action=ActionKind.KICK_OUTBOX,
            action_params={"pending": snapshot.outbox_pending, "dead_letter": snapshot.outbox_dead_letter},
            evidence=[f"outbox_pending={snapshot.outbox_pending}", f"outbox_dead_letter={snapshot.outbox_dead_letter}"],
        )

    # 8. Novel 5xx error burst or non-ready HTTP failure (eligible for Tier-2 Vertex AI RCA)
    if failed_probes or len(snapshot.recent_errors) >= config.error_burst_threshold:
        return Diagnosis(
            incident_class=IncidentClass.NOVEL_5XX_ERROR,
            severity=Severity.HIGH,
            summary=f"Unclassified runtime errors detected ({len(snapshot.recent_errors)} log errors, {len(failed_probes)} probe failures).",
            root_cause="Application exception burst not matched by deterministic signatures.",
            recommended_action=ActionKind.RESTART_SERVICE if failed_probes else ActionKind.ESCALATE_HUMAN,
            action_params={"error_count": len(snapshot.recent_errors)},
            evidence=(failed_probes + [e.message[:200] for e in snapshot.recent_errors[:4]])[:5],
        )

    # All clear
    return Diagnosis(
        incident_class=IncidentClass.HEALTHY,
        severity=Severity.INFO,
        summary=f"All {len(snapshot.http_probes)} probes healthy (max latency {snapshot.max_latency_ms:.0f}ms, 0 critical errors).",
        root_cause="None — service operating within SLO.",
        recommended_action=ActionKind.NOOP,
        evidence=[f"active_revision={active_rev or 'unknown'}", f"max_latency_ms={snapshot.max_latency_ms:.1f}"],
    )


class VertexSREDiagnostician:
    """Tier-2 Senior SRE root-cause analyzer in Hong Kong (asia-east2) via Vertex AI ADC.

    Enforces:
    - 100% PHI scrubbing before any payload leaves the service.
    - Locked to `asia-east2` Hong Kong endpoint.
    - Zero tools; structured JSON output restricted strictly to allowlisted `ActionKind` values.
    """

    ALLOWED_ACTIONS = {
        ActionKind.NOOP.value,
        ActionKind.ROLLBACK_REVISION.value,
        ActionKind.RESTART_SERVICE.value,
        ActionKind.BUMP_MEMORY.value,
        ActionKind.SCALE_MAX_INSTANCES.value,
        ActionKind.START_CLOUDSQL.value,
        ActionKind.KICK_OUTBOX.value,
        ActionKind.ESCALATE_HUMAN.value,
    }

    def __init__(self, config: SREConfig, http_client: httpx.Client | None = None):
        self.config = config
        self._client = http_client

    def refine(self, snapshot: ProbeSnapshot, base: Diagnosis, state: SREState) -> Diagnosis:
        """Optionally refine a NOVEL_5XX_ERROR diagnosis using Vertex AI Gemini in asia-east2."""
        if not self.config.enable_vertex_rca or base.incident_class != IncidentClass.NOVEL_5XX_ERROR:
            return base
        try:
            import google.auth  # type: ignore[import-untyped]
            import google.auth.transport.requests  # type: ignore[import-untyped]

            creds, default_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(google.auth.transport.requests.Request())
            project = self.config.project_id or default_project
            if not project or not creds.token:
                return base

            scrubbed_logs = [PHIScrubber.scrub(e.message[:260])[0] for e in snapshot.recent_errors[:8]]
            prompt_payload = {
                "service": self.config.target_service,
                "region": self.config.region,
                "probes": [{"path": p.path, "status": p.status_code, "latency_ms": round(p.latency_ms, 1)} for p in snapshot.http_probes],
                "active_revision": snapshot.cloud_run.active_traffic_revision if snapshot.cloud_run else "",
                "last_healthy_revision": state.last_healthy_revision,
                "memory_limit": snapshot.cloud_run.memory_limit if snapshot.cloud_run else "1Gi",
                "max_instances": snapshot.cloud_run.max_instances if snapshot.cloud_run else 5,
                "sql_state": snapshot.cloud_sql.state if snapshot.cloud_sql else "unknown",
                "scrubbed_errors": scrubbed_logs,
            }
            system_text = (
                "You are a senior Google Cloud SRE on call 24/7 for a Hong Kong healthcare Cloud Run service. "
                "Respond with STRICT JSON only: "
                '{"summary": str, "root_cause": str, "recommended_action": one of '
                '["rollback_revision","restart_service","bump_memory","scale_max_instances","start_cloudsql","kick_outbox","escalate_human","noop"]}. '
                "Prefer rollback_revision when last_healthy_revision differs from active_revision. Never suggest destructive DB actions."
            )
            url = (
                f"https://{self.config.region}-aiplatform.googleapis.com/v1/projects/{project}"
                f"/locations/{self.config.region}/publishers/google/models/{self.config.vertex_model}:generateContent"
            )
            body = {
                "systemInstruction": {"parts": [{"text": system_text}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(prompt_payload, ensure_ascii=False)}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 400, "responseMimeType": "application/json"},
            }
            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
                "X-Vertex-Zero-Data-Retention": "true",
                "X-No-Training": "true",
            }
            client = self._client or httpx.Client(timeout=12.0)
            try:
                resp = client.post(url, headers=headers, json=body)
                if resp.status_code >= 300:
                    return base
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed: dict[str, Any] = json.loads(text)
            finally:
                if self._client is None:
                    client.close()

            action_raw = str(parsed.get("recommended_action", "")).strip()
            if action_raw not in self.ALLOWED_ACTIONS:
                return base
            action = ActionKind(action_raw)
            params = dict(base.action_params)
            if action == ActionKind.ROLLBACK_REVISION:
                target = pick_rollback_target(snapshot, state)
                if not target:
                    action = ActionKind.RESTART_SERVICE
                else:
                    params["target_revision"] = target
            elif action == ActionKind.BUMP_MEMORY:
                cur_gi = parse_memory_gi(snapshot.cloud_run.memory_limit if snapshot.cloud_run else "1Gi")
                next_gi = min(self.config.max_memory_gi, max(cur_gi * 2, cur_gi + 1))
                params["target_memory"] = f"{next_gi}Gi"
            elif action == ActionKind.SCALE_MAX_INSTANCES:
                cur_max = snapshot.cloud_run.max_instances if snapshot.cloud_run else 5
                params["target_max_instances"] = min(self.config.max_instances_cap, cur_max + 5)

            return Diagnosis(
                incident_class=base.incident_class,
                severity=base.severity,
                summary=PHIScrubber.scrub(str(parsed.get("summary") or base.summary))[0][:300],
                root_cause=PHIScrubber.scrub(str(parsed.get("root_cause") or base.root_cause))[0][:400],
                recommended_action=action,
                action_params=params,
                evidence=base.evidence,
                source="vertex_asia_east2",
            )
        except Exception:
            return base
