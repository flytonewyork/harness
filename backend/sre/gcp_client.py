"""GCP Hong Kong (asia-east2) Infrastructure Control Plane for the 24/7 Autonomous SRE Agent.

Uses direct REST calls via Application Default Credentials (`google.auth` + `httpx`) so the SRE
container needs zero `gcloud` binary overhead and starts in <1s on Cloud Run.

Hard safety boundaries:
- Every Cloud Logging entry is immediately scrubbed via `PHIScrubber.scrub()`.
- Zero destructive methods exist: no DB drops, no Vault deletes, no secret reads, no RLS changes.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from backend.acute.security import PHIScrubber


def _scrub_text(text: str) -> str:
    res = PHIScrubber.scrub(text)
    return res[0] if isinstance(res, tuple) else str(res)
from .models import (
    CloudRunState,
    CloudSQLState,
    HttpProbeResult,
    LogErrorEntry,
    RevisionInfo,
    SREConfig,
    SREState,
    utcnow_iso,
)

PROBE_PATHS: tuple[str, ...] = (
    "/api/v1/health/live",
    "/api/v1/health/ready",
    "/api/v1/health",
    "/c",
    "/p",
    "/wa",
)


class InfraControlPlane(Protocol):
    def probe_http(self, base_url: str) -> list[HttpProbeResult]: ...
    def get_cloud_run_state(self) -> CloudRunState | None: ...
    def get_cloud_sql_state(self) -> CloudSQLState | None: ...
    def query_recent_errors(self, lookback_minutes: int) -> list[LogErrorEntry]: ...
    def rollback_to_revision(self, revision_name: str) -> tuple[bool, str]: ...
    def bump_memory(self, target_memory: str) -> tuple[bool, str]: ...
    def scale_max_instances(self, target_max_instances: int) -> tuple[bool, str]: ...
    def restart_service(self, reason: str) -> tuple[bool, str]: ...
    def start_cloudsql(self) -> tuple[bool, str]: ...
    def kick_outbox(self, base_url: str) -> tuple[bool, str]: ...
    def send_alert(self, title: str, payload: dict[str, Any]) -> bool: ...
    def load_state(self) -> SREState: ...
    def save_state(self, state: SREState) -> None: ...


class GCPControlPlane:
    """Live Google Cloud REST control plane (Cloud Run v2, Cloud Logging v2, Cloud SQL v1, GCS state)."""

    def __init__(self, config: SREConfig, http_client: httpx.Client | None = None):
        self.config = config
        self._client = http_client or httpx.Client(timeout=20.0, follow_redirects=True)
        self._owns_client = http_client is None
        self._lock = threading.Lock()
        self._creds = None

    def _auth_headers(self) -> dict[str, str]:
        import google.auth  # type: ignore[import-untyped]
        import google.auth.transport.requests  # type: ignore[import-untyped]

        with self._lock:
            if self._creds is None:
                self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not self._creds.valid:
                self._creds.refresh(google.auth.transport.requests.Request())
            return {"Authorization": f"Bearer {self._creds.token}", "Content-Type": "application/json"}

    def _service_resource(self) -> str:
        return f"projects/{self.config.project_id}/locations/{self.config.region}/services/{self.config.target_service}"

    # ---------------------------------------------------------------------------------- Observe
    def probe_http(self, base_url: str) -> list[HttpProbeResult]:
        if not base_url:
            return []
        base = base_url.rstrip("/")
        results: list[HttpProbeResult] = []
        for path in PROBE_PATHS:
            t0 = time.perf_counter()
            try:
                r = self._client.get(f"{base}{path}", timeout=10.0)
                ms = (time.perf_counter() - t0) * 1000.0
                payload: dict[str, Any] = {}
                if "application/json" in r.headers.get("content-type", ""):
                    try:
                        payload = r.json()
                    except Exception:
                        payload = {}
                ok = 200 <= r.status_code < 300
                if path.startswith("/api/v1/health") and payload and payload.get("ok") is False:
                    ok = False
                results.append(
                    HttpProbeResult(
                        path=path,
                        status_code=r.status_code,
                        ok=ok,
                        latency_ms=round(ms, 1),
                        error="" if ok else f"HTTP {r.status_code}",
                        payload=payload if path.startswith("/api/v1/health") else {},
                    )
                )
            except Exception as exc:
                ms = (time.perf_counter() - t0) * 1000.0
                results.append(
                    HttpProbeResult(
                        path=path,
                        status_code=0,
                        ok=False,
                        latency_ms=round(ms, 1),
                        error=_scrub_text(str(exc))[:180],
                    )
                )
        return results

    def get_cloud_run_state(self) -> CloudRunState | None:
        try:
            headers = self._auth_headers()
            svc_url = f"https://run.googleapis.com/v2/{self._service_resource()}"
            r = self._client.get(svc_url, headers=headers)
            if r.status_code >= 300:
                return None
            data = r.json()
            latest_ready = str(data.get("latestReadyRevision", "")).rsplit("/", 1)[-1]
            latest_created = str(data.get("latestCreatedRevision", "")).rsplit("/", 1)[-1]
            active_rev = latest_ready
            for t in data.get("trafficStatuses") or []:
                if int(t.get("percent", 0)) >= 50:
                    active_rev = str(t.get("revision") or latest_ready).rsplit("/", 1)[-1]
                    break
            containers = (data.get("template") or {}).get("containers") or [{}]
            mem = str(((containers[0].get("resources") or {}).get("limits") or {}).get("memory", "1Gi"))
            max_inst = int(((data.get("template") or {}).get("scaling") or {}).get("maxInstanceCount", 5))
            ready = any(
                c.get("type") == "Ready" and c.get("state") == "CONDITION_SUCCEEDED"
                for c in (data.get("conditions") or [])
            ) or bool(latest_ready)

            revisions: list[RevisionInfo] = []
            rev_resp = self._client.get(f"{svc_url}/revisions", headers=headers, params={"pageSize": 10})
            if rev_resp.status_code < 300:
                for rv in rev_resp.json().get("revisions") or []:
                    rname = str(rv.get("name", "")).rsplit("/", 1)[-1]
                    rready = any(
                        c.get("type") == "Ready" and c.get("state") == "CONDITION_SUCCEEDED"
                        for c in (rv.get("conditions") or [])
                    )
                    revisions.append(
                        RevisionInfo(
                            name=rname,
                            ready=rready,
                            created_at=str(rv.get("createTime", "")),
                            traffic_percent=100 if rname == active_rev else 0,
                        )
                    )

            return CloudRunState(
                service_name=self.config.target_service,
                url=str(data.get("uri") or self.config.target_service_url),
                latest_ready_revision=latest_ready,
                latest_created_revision=latest_created,
                active_traffic_revision=active_rev,
                memory_limit=mem,
                max_instances=max_inst,
                ready=ready,
                revisions=revisions,
            )
        except Exception:
            return None

    def get_cloud_sql_state(self) -> CloudSQLState | None:
        if not self.config.target_sql_instance:
            return None
        try:
            headers = self._auth_headers()
            url = (
                f"https://sqladmin.googleapis.com/v1/projects/{self.config.project_id}"
                f"/instances/{self.config.target_sql_instance}"
            )
            r = self._client.get(url, headers=headers)
            if r.status_code >= 300:
                return None
            data = r.json()
            return CloudSQLState(
                instance_name=self.config.target_sql_instance,
                state=str(data.get("state", "UNKNOWN")),
                activation_policy=str((data.get("settings") or {}).get("activationPolicy", "ALWAYS")),
            )
        except Exception:
            return None

    def query_recent_errors(self, lookback_minutes: int) -> list[LogErrorEntry]:
        try:
            headers = self._auth_headers()
            since = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
            flt = (
                f'resource.type="cloud_run_revision" '
                f'AND resource.labels.service_name="{self.config.target_service}" '
                f'AND severity>=ERROR '
                f'AND timestamp>="{since}"'
            )
            r = self._client.post(
                "https://logging.googleapis.com/v2/entries:list",
                headers=headers,
                json={
                    "resourceNames": [f"projects/{self.config.project_id}"],
                    "filter": flt,
                    "orderBy": "timestamp desc",
                    "pageSize": 25,
                },
            )
            if r.status_code >= 300:
                return []
            out: list[LogErrorEntry] = []
            for entry in r.json().get("entries") or []:
                msg = (
                    entry.get("textPayload")
                    or (entry.get("jsonPayload") or {}).get("message")
                    or json.dumps(entry.get("jsonPayload") or {})
                )
                # Scrub PHI immediately on ingress — zero raw patient identifiers ever enter SRE state or Vertex AI
                scrubbed = _scrub_text(str(msg))[:400]
                rev = str(((entry.get("resource") or {}).get("labels") or {}).get("revision_name", ""))
                out.append(
                    LogErrorEntry(
                        timestamp=str(entry.get("timestamp", "")),
                        severity=str(entry.get("severity", "ERROR")),
                        revision=rev,
                        message=scrubbed,
                    )
                )
            return out
        except Exception:
            return []

    # ---------------------------------------------------------------------------------- Act (Non-Destructive Runbooks)
    def _patch_cloud_run(self, body: dict[str, Any], update_mask: str) -> tuple[bool, str]:
        try:
            headers = self._auth_headers()
            url = f"https://run.googleapis.com/v2/{self._service_resource()}"
            r = self._client.patch(url, headers=headers, params={"updateMask": update_mask}, json=body)
            if r.status_code < 300:
                op = r.json().get("name", "")
                return True, f"Cloud Run patch submitted ({op.rsplit('/', 1)[-1] if op else 'ok'})"
            return False, f"Cloud Run patch failed HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            return False, f"Cloud Run patch error: {exc}"

    def _get_raw_service(self) -> dict[str, Any] | None:
        headers = self._auth_headers()
        r = self._client.get(f"https://run.googleapis.com/v2/{self._service_resource()}", headers=headers)
        return r.json() if r.status_code < 300 else None

    def rollback_to_revision(self, revision_name: str) -> tuple[bool, str]:
        body = {
            "traffic": [
                {
                    "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                    "revision": revision_name,
                    "percent": 100,
                }
            ]
        }
        return self._patch_cloud_run(body, "traffic")

    def bump_memory(self, target_memory: str) -> tuple[bool, str]:
        svc = self._get_raw_service()
        if not svc:
            return False, "Could not fetch Cloud Run service spec"
        template = svc.get("template") or {}
        containers = template.get("containers") or []
        if not containers:
            return False, "Cloud Run service has no containers"
        containers[0].setdefault("resources", {}).setdefault("limits", {})["memory"] = target_memory
        template["containers"] = containers
        return self._patch_cloud_run({"template": template}, "template")

    def scale_max_instances(self, target_max_instances: int) -> tuple[bool, str]:
        svc = self._get_raw_service()
        if not svc:
            return False, "Could not fetch Cloud Run service spec"
        template = svc.get("template") or {}
        template.setdefault("scaling", {})["maxInstanceCount"] = int(target_max_instances)
        return self._patch_cloud_run({"template": template}, "template")

    def restart_service(self, reason: str) -> tuple[bool, str]:
        svc = self._get_raw_service()
        if not svc:
            return False, "Could not fetch Cloud Run service spec"
        template = svc.get("template") or {}
        ann = template.setdefault("annotations", {})
        ann["sre.tmrw.hk/restarted-at"] = utcnow_iso()
        ann["sre.tmrw.hk/restart-reason"] = reason[:120]
        return self._patch_cloud_run({"template": template}, "template")

    def start_cloudsql(self) -> tuple[bool, str]:
        try:
            headers = self._auth_headers()
            url = (
                f"https://sqladmin.googleapis.com/v1/projects/{self.config.project_id}"
                f"/instances/{self.config.target_sql_instance}"
            )
            r = self._client.patch(url, headers=headers, json={"settings": {"activationPolicy": "ALWAYS"}})
            if r.status_code < 300:
                return True, f"Cloud SQL {self.config.target_sql_instance} activationPolicy patched to ALWAYS"
            return False, f"Cloud SQL patch failed HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            return False, f"Cloud SQL patch error: {exc}"

    def kick_outbox(self, base_url: str) -> tuple[bool, str]:
        admin_tok = os.environ.get("SRE_ADMIN_TOKEN", "")
        if admin_tok and base_url:
            try:
                r = self._client.post(
                    f"{base_url.rstrip('/')}/api/v1/admin/outbox/process",
                    headers={"Authorization": f"Bearer {admin_tok}"},
                    timeout=10.0,
                )
                if r.status_code < 300:
                    return True, f"Outbox drain triggered via API ({r.text[:140]})"
            except Exception:
                pass
        return self.restart_service("outbox_worker_stalled")

    def send_alert(self, title: str, payload: dict[str, Any]) -> bool:
        if not self.config.alert_webhook_url:
            return False
        try:
            safe_payload = {
                "text": f"[tmrw SRE 24/7] {_scrub_text(title)}",
                "service": self.config.target_service,
                "region": self.config.region,
                "details": json.loads(_scrub_text(json.dumps(payload, ensure_ascii=False))),
            }
            r = self._client.post(self.config.alert_webhook_url, json=safe_payload, timeout=8.0)
            return r.status_code < 300
        except Exception:
            return False

    # ---------------------------------------------------------------------------------- Durable State (GCS / Local)
    def load_state(self) -> SREState:
        if self.config.state_gcs_bucket:
            try:
                headers = self._auth_headers()
                obj = quote(f"sre/{self.config.target_service}/state.json", safe="")
                url = f"https://storage.googleapis.com/storage/v1/b/{self.config.state_gcs_bucket}/o/{obj}?alt=media"
                r = self._client.get(url, headers=headers)
                if r.status_code == 200:
                    return SREState.model_validate_json(r.text)
            except Exception:
                pass
        p = Path(self.config.state_local_path)
        if p.exists():
            try:
                return SREState.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return SREState()

    def save_state(self, state: SREState) -> None:
        # Keep the last 100 incidents in durable state
        if len(state.incidents) > 100:
            state.incidents = state.incidents[-100:]
        raw = state.model_dump_json(indent=2)
        p = Path(self.config.state_local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw, encoding="utf-8")
        if self.config.state_gcs_bucket:
            try:
                headers = self._auth_headers()
                headers["Content-Type"] = "application/json"
                obj = quote(f"sre/{self.config.target_service}/state.json", safe="")
                url = (
                    f"https://storage.googleapis.com/upload/storage/v1/b/"
                    f"{self.config.state_gcs_bucket}/o?uploadType=media&name={obj}"
                )
                self._client.post(url, headers=headers, content=raw.encode("utf-8"))
            except Exception:
                pass


class InMemoryControlPlane:
    """Deterministic in-memory GCP control plane double for unit/integration testing."""

    def __init__(
        self,
        *,
        http_probes: list[HttpProbeResult] | None = None,
        cloud_run: CloudRunState | None = None,
        cloud_sql: CloudSQLState | None = None,
        recent_errors: list[LogErrorEntry] | None = None,
    ):
        self.http_probes = http_probes or [
            HttpProbeResult(path="/api/v1/health/live", status_code=200, ok=True, latency_ms=12.0, payload={"ok": True}),
            HttpProbeResult(path="/api/v1/health/ready", status_code=200, ok=True, latency_ms=18.0, payload={"ok": True}),
            HttpProbeResult(path="/api/v1/health", status_code=200, ok=True, latency_ms=20.0, payload={"ok": True}),
        ]
        self.cloud_run = cloud_run or CloudRunState(
            service_name="clinic-ai-harness-staging",
            url="https://staging.example.run.app",
            latest_ready_revision="rev-0001",
            latest_created_revision="rev-0001",
            active_traffic_revision="rev-0001",
            memory_limit="1Gi",
            max_instances=5,
            revisions=[RevisionInfo(name="rev-0001", ready=True, traffic_percent=100)],
        )
        self.cloud_sql = cloud_sql or CloudSQLState(instance_name="tmrw-acute-pg15", state="RUNNABLE", activation_policy="ALWAYS")
        self.recent_errors = recent_errors or []
        self.actions_executed: list[tuple[str, Any]] = []
        self.alerts_sent: list[tuple[str, dict[str, Any]]] = []
        self.state = SREState()
        self.recover_on_action = True

    def _simulate_recovery(self) -> None:
        if not self.recover_on_action:
            return
        self.http_probes = [
            HttpProbeResult(path="/api/v1/health/live", status_code=200, ok=True, latency_ms=15.0, payload={"ok": True}),
            HttpProbeResult(path="/api/v1/health/ready", status_code=200, ok=True, latency_ms=22.0, payload={"ok": True}),
            HttpProbeResult(path="/api/v1/health", status_code=200, ok=True, latency_ms=25.0, payload={"ok": True}),
        ]
        self.recent_errors = []

    def probe_http(self, base_url: str) -> list[HttpProbeResult]:
        return list(self.http_probes)

    def get_cloud_run_state(self) -> CloudRunState | None:
        return self.cloud_run

    def get_cloud_sql_state(self) -> CloudSQLState | None:
        return self.cloud_sql

    def query_recent_errors(self, lookback_minutes: int) -> list[LogErrorEntry]:
        return [
            LogErrorEntry(
                timestamp=e.timestamp,
                severity=e.severity,
                revision=e.revision,
                message=_scrub_text(e.message),
            )
            for e in self.recent_errors
        ]

    def rollback_to_revision(self, revision_name: str) -> tuple[bool, str]:
        self.actions_executed.append(("rollback_revision", revision_name))
        self.cloud_run.active_traffic_revision = revision_name
        self._simulate_recovery()
        return True, f"Shifted 100% traffic to {revision_name}"

    def bump_memory(self, target_memory: str) -> tuple[bool, str]:
        self.actions_executed.append(("bump_memory", target_memory))
        self.cloud_run.memory_limit = target_memory
        self._simulate_recovery()
        return True, f"Updated memory limit to {target_memory}"

    def scale_max_instances(self, target_max_instances: int) -> tuple[bool, str]:
        self.actions_executed.append(("scale_max_instances", target_max_instances))
        self.cloud_run.max_instances = target_max_instances
        self._simulate_recovery()
        return True, f"Updated maxInstances to {target_max_instances}"

    def restart_service(self, reason: str) -> tuple[bool, str]:
        self.actions_executed.append(("restart_service", reason))
        self._simulate_recovery()
        return True, f"Rolled fresh revision ({reason})"

    def start_cloudsql(self) -> tuple[bool, str]:
        self.actions_executed.append(("start_cloudsql", self.cloud_sql.instance_name))
        self.cloud_sql.state = "RUNNABLE"
        self.cloud_sql.activation_policy = "ALWAYS"
        self._simulate_recovery()
        return True, f"Started Cloud SQL {self.cloud_sql.instance_name}"

    def kick_outbox(self, base_url: str) -> tuple[bool, str]:
        self.actions_executed.append(("kick_outbox", base_url))
        self._simulate_recovery()
        return True, "Triggered outbox drain"

    def send_alert(self, title: str, payload: dict[str, Any]) -> bool:
        self.alerts_sent.append((title, payload))
        return True

    def load_state(self) -> SREState:
        return self.state

    def save_state(self, state: SREState) -> None:
        self.state = state
