"""FastAPI control plane & live Mission Control UI for the 24/7 Autonomous Cloud Infra SRE Agent."""
from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from .agent import AutonomousSREAgent
from .gcp_client import InfraControlPlane
from .models import SREConfig

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>tmrw SRE 24/7 — Autonomous Cloud Infra Agent (asia-east2)</title>
  <style>
    :root { --bg:#0b1116; --card:#131c24; --line:#22303c; --ink:#e8f0f6; --muted:#8fa3b5; --ok:#22c55e; --warn:#f59e0b; --err:#ef4444; --accent:#14b8a6; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter,system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--ink); padding:24px; }
    .wrap { max-width:1100px; margin:0 auto; }
    header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; margin-bottom:20px; border-bottom:1px solid var(--line); padding-bottom:16px; }
    h1 { margin:0; font-size:20px; letter-spacing:-0.02em; }
    .sub { color:var(--muted); font-size:13px; margin-top:4px; }
    .btn { background:var(--accent); color:#042f2e; border:none; border-radius:8px; padding:9px 14px; font-weight:600; cursor:pointer; font-size:13px; }
    .btn.sec { background:#1e293b; color:var(--ink); border:1px solid var(--line); }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin-bottom:20px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
    .k { font-size:11px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); margin-bottom:6px; }
    .v { font-size:18px; font-weight:600; word-break:break-all; }
    .pill { display:inline-block; padding:3px 9px; border-radius:999px; font-size:12px; font-weight:600; }
    .pill.ok { background:rgba(34,197,94,.16); color:var(--ok); }
    .pill.err { background:rgba(239,68,68,.18); color:var(--err); }
    .pill.warn { background:rgba(245,158,11,.18); color:var(--warn); }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th,td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
    th { color:var(--muted); font-weight:500; font-size:12px; }
    code { font-family:ui-monospace,SFMono-Regular,monospace; font-size:12px; color:#99f6e4; }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>tmrw Autonomous Infra SRE Agent — 24/7 On-Call (Hong Kong asia-east2)</h1>
        <div class="sub" id="sub">Loading live telemetry...</div>
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn" onclick="runTick()">Run Health Check &amp; Self-Heal Now</button>
        <button class="btn sec" onclick="resetCB()">Reset Circuit Breaker</button>
      </div>
    </header>
    <div class="grid" id="cards"></div>
    <div class="card" style="margin-bottom:20px;">
      <div class="k">Live Synthetic HTTP Probes</div>
      <table><thead><tr><th>Route</th><th>Status</th><th>Latency</th><th>Detail</th></tr></thead><tbody id="probes"></tbody></table>
    </div>
    <div class="card">
      <div class="k">Autonomous Incident &amp; Remediation Ledger (PHI-Scrubbed)</div>
      <table><thead><tr><th>Time (UTC)</th><th>Class</th><th>Action</th><th>Outcome</th><th>Summary</th></tr></thead><tbody id="incidents"></tbody></table>
    </div>
  </div>
  <script>
    const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    async function refresh() {
      const r = await fetch("/api/v1/sre/status");
      const d = await r.json();
      const s = d.state || {};
      const snap = s.last_snapshot || {};
      const cr = snap.cloud_run || {};
      const sql = snap.cloud_sql || {};
      const cb = s.circuit_breaker || {};
      document.getElementById("sub").textContent =
        `Target: ${d.config.target_service} (${d.config.project_id}/${d.config.region}) · Last tick: ${s.last_tick_at || "never"} · Total ticks: ${s.total_ticks || 0}`;
      const healthy = snap.all_http_ok !== false && (!sql.state || sql.state === "RUNNABLE");
      document.getElementById("cards").innerHTML = [
        ["Service Status", `<span class="pill ${healthy ? "ok" : "err"}">${healthy ? "HEALTHY" : "DEGRADED"}</span>`],
        ["Active / Last Good Rev", `<code>${esc(cr.active_traffic_revision || "—")}</code><div class="sub">Good: ${esc(s.last_healthy_revision || "—")}</div>`],
        ["Cloud SQL PG15", `<code>${esc(sql.state || "—")}</code><div class="sub">${esc(sql.instance_name || "")} (${esc(cr.memory_limit || "1Gi")} / max ${esc(cr.max_instances || 5)})</div>`],
        ["Circuit Breaker", `<span class="pill ${cb.tripped ? "err" : "ok"}">${cb.tripped ? "TRIPPED" : "ARMED"}</span><div class="sub">Auto-fixes: ${s.total_remediations || 0}</div>`],
      ].map(([k,v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
      document.getElementById("probes").innerHTML = (snap.http_probes || []).map(p =>
        `<tr><td><code>${esc(p.path)}</code></td><td><span class="pill ${p.ok ? "ok" : "err"}">${p.status_code || "ERR"}</span></td><td>${p.latency_ms} ms</td><td>${esc(p.error || "OK")}</td></tr>`
      ).join("") || `<tr><td colspan="4" class="sub">No probes yet — click "Run Health Check & Self-Heal Now".</td></tr>`;
      const incs = (s.incidents || []).slice().reverse();
      document.getElementById("incidents").innerHTML = incs.map(i =>
        `<tr><td>${esc(i.timestamp)}</td><td><code>${esc(i.diagnosis.incident_class)}</code></td><td><code>${esc(i.remediation.action)}</code></td><td><span class="pill ${i.remediation.succeeded ? "ok" : "warn"}">${i.remediation.executed ? (i.remediation.succeeded ? "HEALED" : "FAILED") : "ESCALATED"}</span></td><td>${esc(i.diagnosis.summary)}</td></tr>`
      ).join("") || `<tr><td colspan="5" class="sub">Zero incidents recorded.</td></tr>`;
    }
    async function runTick() {
      await fetch("/api/v1/sre/tick", { method: "POST" });
      await refresh();
    }
    async function resetCB() {
      await fetch("/api/v1/sre/circuit-breaker/reset", { method: "POST" });
      await refresh();
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""


def _check_operator_token(request: Request) -> None:
    """Optional bearer/header token check if SRE_OPERATOR_TOKEN is configured (Cloud Scheduler OIDC also passes)."""
    expected = os.environ.get("SRE_OPERATOR_TOKEN", "")
    if not expected:
        return
    # Allow GCP Cloud Scheduler authenticated requests via X-CloudScheduler header on private/OIDC services
    if request.headers.get("X-CloudScheduler") == "true" and request.headers.get("Authorization", "").startswith("Bearer "):
        return
    provided = request.headers.get("X-SRE-Token", "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(403, "Invalid SRE operator token")


def create_sre_app(
    config: SREConfig | None = None,
    control_plane: InfraControlPlane | None = None,
    agent: AutonomousSREAgent | None = None,
) -> FastAPI:
    cfg = config or SREConfig()
    sre_agent = agent or AutonomousSREAgent(config=cfg, control_plane=control_plane)

    app = FastAPI(
        title="tmrw Autonomous Cloud Infra SRE Agent (24/7 On-Call)",
        version="1.0.0",
    )

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "service": "tmrw-sre-agent",
            "region": cfg.region,
            "target_service": cfg.target_service,
            "auto_remediate": cfg.auto_remediate,
        }

    @app.get("/api/v1/sre/status")
    def sre_status():
        state = sre_agent.cp.load_state()
        return {
            "ok": True,
            "config": {
                "project_id": cfg.project_id,
                "region": cfg.region,
                "target_service": cfg.target_service,
                "target_service_url": cfg.target_service_url,
                "target_sql_instance": cfg.target_sql_instance,
                "auto_remediate": cfg.auto_remediate,
                "enable_vertex_rca": cfg.enable_vertex_rca,
                "cooldown_seconds": cfg.cooldown_seconds,
                "max_actions_per_window": cfg.max_actions_per_window,
            },
            "state": state.model_dump(),
        }

    @app.get("/api/v1/sre/incidents")
    def sre_incidents():
        state = sre_agent.cp.load_state()
        return {"ok": True, "count": len(state.incidents), "incidents": [i.model_dump() for i in reversed(state.incidents)]}

    @app.post("/api/v1/sre/tick")
    def sre_tick(request: Request):
        _check_operator_token(request)
        return sre_agent.tick()

    @app.post("/api/v1/sre/circuit-breaker/reset")
    def sre_reset_cb(request: Request):
        _check_operator_token(request)
        state = sre_agent.reset_circuit_breaker()
        return {"ok": True, "circuit_breaker": state.circuit_breaker.model_dump()}

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(_DASHBOARD_HTML)

    return app


def app_from_env() -> FastAPI:
    return create_sre_app()
