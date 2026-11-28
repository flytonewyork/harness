"""Data contracts and configuration for the 24/7 Autonomous Cloud Infra On-Call SRE Agent."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentClass(str, Enum):
    HEALTHY = "healthy"
    CLOUDSQL_STOPPED = "cloudsql_stopped"
    OOM_KILLED = "oom_killed"
    BAD_REVISION_REGRESSION = "bad_revision_regression"
    READINESS_OR_DB_POOL_WEDGED = "readiness_or_db_pool_wedged"
    SATURATION_LATENCY_OR_429 = "saturation_latency_or_429"
    OUTBOX_STALLED = "outbox_stalled"
    IAM_OR_SECRET_ERROR = "iam_or_secret_error"
    NOVEL_5XX_ERROR = "novel_5xx_error"


class ActionKind(str, Enum):
    NOOP = "noop"
    START_CLOUDSQL = "start_cloudsql"
    ROLLBACK_REVISION = "rollback_revision"
    BUMP_MEMORY = "bump_memory"
    RESTART_SERVICE = "restart_service"
    SCALE_MAX_INSTANCES = "scale_max_instances"
    KICK_OUTBOX = "kick_outbox"
    ESCALATE_HUMAN = "escalate_human"


class Severity(str, Enum):
    INFO = "P3"
    WARN = "P2"
    HIGH = "P1"
    CRITICAL = "P0"


class SREConfig(BaseModel):
    project_id: str = Field(default_factory=lambda: os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "bard-388408")
    region: str = Field(default_factory=lambda: os.environ.get("GCP_REGION", "asia-east2"))
    target_service: str = Field(default_factory=lambda: os.environ.get("TARGET_SERVICE", "clinic-ai-harness-staging"))
    target_service_url: str = Field(default_factory=lambda: os.environ.get("TARGET_SERVICE_URL", ""))
    target_sql_instance: str = Field(default_factory=lambda: os.environ.get("TARGET_SQL_INSTANCE", "tmrw-acute-pg15"))
    state_gcs_bucket: str = Field(default_factory=lambda: os.environ.get("SRE_STATE_GCS_BUCKET", ""))
    state_local_path: str = Field(default_factory=lambda: os.environ.get("SRE_STATE_LOCAL_PATH", "/tmp/tmrw-sre-state.json"))
    alert_webhook_url: str = Field(default_factory=lambda: os.environ.get("ALERT_WEBHOOK_URL", ""))
    vertex_model: str = Field(default_factory=lambda: os.environ.get("VERTEX_MODEL", "gemini-4-hydrogen"))
    enable_vertex_rca: bool = Field(default_factory=lambda: os.environ.get("ENABLE_VERTEX_RCA", "true").lower() in ("1", "true", "yes"))
    allow_llm_mutations: bool = Field(default_factory=lambda: os.environ.get("SRE_ALLOW_LLM_MUTATIONS", "false").lower() in ("1", "true", "yes"))
    auto_remediate: bool = Field(default_factory=lambda: os.environ.get("AUTO_REMEDIATE", "true").lower() in ("1", "true", "yes"))
    cooldown_seconds: int = Field(default_factory=lambda: int(os.environ.get("SRE_COOLDOWN_SECONDS", "180")))
    max_actions_per_window: int = Field(default_factory=lambda: int(os.environ.get("SRE_MAX_ACTIONS_PER_WINDOW", "3")))
    window_seconds: int = Field(default_factory=lambda: int(os.environ.get("SRE_WINDOW_SECONDS", "1800")))
    latency_slo_ms: float = Field(default_factory=lambda: float(os.environ.get("SRE_LATENCY_SLO_MS", "2500")))
    max_memory_gi: int = Field(default_factory=lambda: int(os.environ.get("SRE_MAX_MEMORY_GI", "4")))
    max_instances_cap: int = Field(default_factory=lambda: int(os.environ.get("SRE_MAX_INSTANCES_CAP", "20")))
    log_lookback_minutes: int = Field(default_factory=lambda: int(os.environ.get("SRE_LOG_LOOKBACK_MINUTES", "5")))
    error_burst_threshold: int = Field(default_factory=lambda: int(os.environ.get("SRE_ERROR_BURST_THRESHOLD", "5")))


class HttpProbeResult(BaseModel):
    path: str
    status_code: int
    ok: bool
    latency_ms: float
    error: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RevisionInfo(BaseModel):
    name: str
    ready: bool = True
    created_at: str = ""
    traffic_percent: int = 0


class CloudRunState(BaseModel):
    service_name: str
    url: str = ""
    latest_ready_revision: str = ""
    latest_created_revision: str = ""
    active_traffic_revision: str = ""
    memory_limit: str = "1Gi"
    max_instances: int = 5
    ready: bool = True
    revisions: list[RevisionInfo] = Field(default_factory=list)


class CloudSQLState(BaseModel):
    instance_name: str
    state: str = "RUNNABLE"
    activation_policy: str = "ALWAYS"


class LogErrorEntry(BaseModel):
    timestamp: str
    severity: str
    revision: str = ""
    message: str  # Always PHI-scrubbed before storage or Vertex RCA


class ProbeSnapshot(BaseModel):
    observed_at: str = Field(default_factory=utcnow_iso)
    http_probes: list[HttpProbeResult] = Field(default_factory=list)
    cloud_run: CloudRunState | None = None
    cloud_sql: CloudSQLState | None = None
    recent_errors: list[LogErrorEntry] = Field(default_factory=list)
    max_latency_ms: float = 0.0
    all_http_ok: bool = True
    ready_ok: bool = True
    outbox_pending: int = 0
    outbox_dead_letter: int = 0


class Diagnosis(BaseModel):
    incident_class: IncidentClass
    severity: Severity
    summary: str
    root_cause: str
    recommended_action: ActionKind
    action_params: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    source: str = "deterministic"  # deterministic | vertex_asia_east2


class RemediationOutcome(BaseModel):
    action: ActionKind
    executed: bool
    succeeded: bool
    detail: str
    blocked_by_circuit_breaker: bool = False
    post_verify_ok: bool | None = None


class IncidentRecord(BaseModel):
    id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    target_service: str
    active_revision: str = ""
    diagnosis: Diagnosis
    remediation: RemediationOutcome
    escalated: bool = False


class CircuitBreakerState(BaseModel):
    action_timestamps: list[str] = Field(default_factory=list)
    tripped: bool = False
    tripped_reason: str = ""
    last_action_at: str = ""


class SREState(BaseModel):
    last_healthy_revision: str = ""
    last_tick_at: str = ""
    total_ticks: int = 0
    total_remediations: int = 0
    consecutive_failures: int = 0
    circuit_breaker: CircuitBreakerState = Field(default_factory=CircuitBreakerState)
    last_snapshot: ProbeSnapshot | None = None
    incidents: list[IncidentRecord] = Field(default_factory=list)
