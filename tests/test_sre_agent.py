"""Tests for the 24/7 Autonomous Cloud Infra On-Call SRE Agent."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.sre.agent import AutonomousSREAgent
from backend.sre.app import create_sre_app
from backend.sre.gcp_client import InMemoryControlPlane
from backend.sre.models import (
    ActionKind,
    CloudRunState,
    CloudSQLState,
    HttpProbeResult,
    IncidentClass,
    LogErrorEntry,
    RevisionInfo,
    SREConfig,
)


def _cfg(**overrides) -> SREConfig:
    base = dict(
        project_id="bard-388408",
        region="asia-east2",
        target_service="clinic-ai-harness-staging",
        target_service_url="https://staging.example.run.app",
        target_sql_instance="tmrw-acute-pg15",
        enable_vertex_rca=False,
        auto_remediate=True,
        cooldown_seconds=0,
        max_actions_per_window=3,
        window_seconds=1800,
    )
    base.update(overrides)
    return SREConfig(**base)


def test_healthy_tick_records_last_healthy_revision_and_takes_no_action():
    cp = InMemoryControlPlane()
    agent = AutonomousSREAgent(config=_cfg(), control_plane=cp)
    res = agent.tick()
    assert res["healthy"] is True
    assert res["remediation"]["action"] == ActionKind.NOOP.value
    assert res["remediation"]["executed"] is False
    assert cp.state.last_healthy_revision == "rev-0001"
    assert cp.actions_executed == []


def test_bad_revision_triggers_instant_automatic_rollback():
    cp = InMemoryControlPlane()
    agent = AutonomousSREAgent(config=_cfg(), control_plane=cp)
    # First tick records rev-0001 as healthy
    assert agent.tick()["healthy"] is True

    # Simulate bad rollout rev-0002 failing readiness
    cp.cloud_run = CloudRunState(
        service_name="clinic-ai-harness-staging",
        url="https://staging.example.run.app",
        latest_ready_revision="rev-0002",
        latest_created_revision="rev-0002",
        active_traffic_revision="rev-0002",
        revisions=[
            RevisionInfo(name="rev-0002", ready=True, traffic_percent=100),
            RevisionInfo(name="rev-0001", ready=True, traffic_percent=0),
        ],
    )
    cp.http_probes = [
        HttpProbeResult(path="/api/v1/health/ready", status_code=503, ok=False, latency_ms=40.0, error="HTTP 503"),
    ]
    res = agent.tick()
    assert res["healthy"] is False
    assert res["diagnosis"]["incident_class"] == IncidentClass.BAD_REVISION_REGRESSION.value
    assert res["remediation"]["action"] == ActionKind.ROLLBACK_REVISION.value
    assert res["remediation"]["executed"] is True
    assert res["remediation"]["succeeded"] is True
    assert res["remediation"]["post_verify_ok"] is True
    assert cp.actions_executed == [("rollback_revision", "rev-0001")]
    assert cp.cloud_run.active_traffic_revision == "rev-0001"


def test_oom_killed_triggers_automatic_memory_bump_up_to_cap():
    cp = InMemoryControlPlane(
        recent_errors=[
            LogErrorEntry(
                timestamp="2026-11-23T06:00:00Z",
                severity="ERROR",
                revision="rev-0001",
                message="Memory limit of 1024 MiB exceeded with 1042 MiB used. Consider increasing the memory limit.",
            )
        ]
    )
    agent = AutonomousSREAgent(config=_cfg(max_memory_gi=4), control_plane=cp)
    res = agent.tick()
    assert res["diagnosis"]["incident_class"] == IncidentClass.OOM_KILLED.value
    assert res["remediation"]["action"] == ActionKind.BUMP_MEMORY.value
    assert cp.actions_executed == [("bump_memory", "2Gi")]
    assert cp.cloud_run.memory_limit == "2Gi"

    # At cap (4Gi), must escalate instead of exceeding budget cap
    cp.cloud_run.memory_limit = "4Gi"
    cp.recent_errors = [
        LogErrorEntry(timestamp="2026-11-23T06:05:00Z", severity="ERROR", message="OOMKilled exit status 137")
    ]
    res2 = agent.tick()
    assert res2["remediation"]["action"] == ActionKind.ESCALATE_HUMAN.value
    assert res2["remediation"]["executed"] is False
    assert res2["escalated"] is True
    assert len(cp.alerts_sent) == 1


def test_cloudsql_stopped_triggers_start_cloudsql():
    cp = InMemoryControlPlane(
        cloud_sql=CloudSQLState(instance_name="tmrw-acute-pg15", state="STOPPED", activation_policy="NEVER"),
        http_probes=[HttpProbeResult(path="/api/v1/health/ready", status_code=503, ok=False, latency_ms=20.0)],
    )
    agent = AutonomousSREAgent(config=_cfg(), control_plane=cp)
    res = agent.tick()
    assert res["diagnosis"]["incident_class"] == IncidentClass.CLOUDSQL_STOPPED.value
    assert res["remediation"]["action"] == ActionKind.START_CLOUDSQL.value
    assert cp.actions_executed == [("start_cloudsql", "tmrw-acute-pg15")]
    assert cp.cloud_sql.state == "RUNNABLE"


def test_latency_saturation_scales_max_instances():
    cp = InMemoryControlPlane(
        http_probes=[
            HttpProbeResult(path="/api/v1/health/ready", status_code=200, ok=True, latency_ms=3400.0, payload={"ok": True}),
        ]
    )
    agent = AutonomousSREAgent(config=_cfg(latency_slo_ms=2500.0, max_instances_cap=20), control_plane=cp)
    res = agent.tick()
    assert res["diagnosis"]["incident_class"] == IncidentClass.SATURATION_LATENCY_OR_429.value
    assert res["remediation"]["action"] == ActionKind.SCALE_MAX_INSTANCES.value
    assert cp.cloud_run.max_instances == 10


def test_circuit_breaker_stops_thrashing_and_scrubs_phi_in_incidents():
    cp = InMemoryControlPlane()
    cp.recover_on_action = False
    cp.http_probes = [HttpProbeResult(path="/api/v1/health/ready", status_code=503, ok=False, latency_ms=50.0)]
    cp.recent_errors = [
        LogErrorEntry(
            timestamp="2026-11-23T06:00:00Z",
            severity="ERROR",
            message="OperationalError timeout expired for patient HKID A123456(7) phone +85291234567",
        )
    ]
    agent = AutonomousSREAgent(config=_cfg(max_actions_per_window=2, cooldown_seconds=0), control_plane=cp)

    r1 = agent.tick()
    r2 = agent.tick()
    r3 = agent.tick()
    assert r1["remediation"]["executed"] is True
    assert r2["remediation"]["executed"] is True
    assert r3["remediation"]["executed"] is False
    assert r3["remediation"]["blocked_by_circuit_breaker"] is True
    assert r3["escalated"] is True
    assert cp.state.circuit_breaker.tripped is True

    # Ensure zero PHI leaked into stored incident evidence
    raw_state = cp.state.model_dump_json()
    assert "A123456" not in raw_state
    assert "91234567" not in raw_state
    assert "[REDACTED_HKID]" in raw_state


def test_sre_fastapi_control_plane_and_dashboard():
    cp = InMemoryControlPlane()
    app = create_sre_app(config=_cfg(), control_plane=cp)
    with TestClient(app) as client:
        assert client.get("/health").json()["ok"] is True
        assert client.get("/").status_code == 200
        tick = client.post("/api/v1/sre/tick")
        assert tick.status_code == 200
        assert tick.json()["healthy"] is True
        st = client.get("/api/v1/sre/status").json()
        assert st["state"]["total_ticks"] == 1
        assert st["state"]["last_healthy_revision"] == "rev-0001"
        inc = client.get("/api/v1/sre/incidents").json()
        assert inc["ok"] is True and inc["count"] == 0
        rst = client.post("/api/v1/sre/circuit-breaker/reset").json()
        assert rst["ok"] is True and rst["circuit_breaker"]["tripped"] is False
