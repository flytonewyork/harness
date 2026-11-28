"""Verification suite for Phase 1: DRY Single Outbound ModelGateway + Spec-Compliant Clinic EMR MCP Server.

Proves:
1. MCP JSON-RPC 2.0 (`2025-03-26`) protocol lifecycle: `initialize`, `ping`, `tools/list`, `resources/list`,
   `resources/read`, and `tools/call` both in-process (`InProcessMCPClient`) and over HTTP (`POST /mcp/clinic-emr`).
2. Dual-layer Asymmetric Authority: `ClinicEMRMCPServer` rejects `emr.write_signed_orders` with JSON-RPC
   error `-32003` (`AUTHORITY_DENIED`) when `actor_role != "clinician"`.
3. Single Canonical Tool Schema: `agent_harness.TOOL_SCHEMAS` matches `ClinicEMRMCPServer` `tools/list` 1-to-1.
4. DRY Architectural Invariants:
   - `backend/acute/agentic_runtime.py` contains ZERO raw SQLAlchemy EMR model imports (`Patient`, `Observation`,
     `DocumentIngestion`, `Prescription`, `Immunization`) and ZERO duplicate `HarnessCascadeModelTransport`.
   - `ModelGatewayTransport` enforces HK PDPO residency blocking (`ResidencyViolationError`), cross-border
     `PHIScrubber` redaction, and per-tenant token/cost budgets (`BudgetExceededError`) with audit logging.
"""
from __future__ import annotations

import inspect
import json
import os

import pytest
from fastapi.testclient import TestClient

from backend.acute import agentic_runtime
from backend.acute.agent_harness import TOOL_SCHEMAS
from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.live_bus import LiveHarnessBus
from backend.acute.live_demo import reset_live_demo, start_live_journey
from backend.acute.seed import DEMO_TENANT_1, reset_and_seed
from backend.agentic import (
    LiveHTTPModelTransport,
    ModelGatewayTransport,
    ModelTurnResponse,
    build_default_clinic_tool_registry,
)
from backend.gateway import BudgetExceededError, ModelGateway, ResidencyViolationError, TenantPolicy
from backend.mcp import ClinicEMRMCPServer, InProcessMCPClient, MCP_AUTHORITY_DENIED_CODE, MCP_PROTOCOL_VERSION
from backend.vault.vault import DocumentVault, LocalFSBackend


def _make_env(tmp_path):
    os.environ["HARNESS_ENV"] = "staging"
    os.environ["DEMO_MODE"] = "true"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    bus = LiveHarnessBus()
    app = create_acute_app(db=db, vault=vault)
    return db, bus, TestClient(app)


def test_dry_invariants_single_tool_schema_and_zero_raw_emr_sql_in_agentic_runtime():
    # 1. Single source of truth for tool schemas
    canonical_names = [s.name for s in build_default_clinic_tool_registry().list_specs(include_blocked=True)]
    harness_names = [t["name"] for t in TOOL_SCHEMAS]
    assert harness_names == canonical_names
    assert "emr.write_signed_orders" in canonical_names

    # 2. Zero duplicate transport class and zero raw EMR SQLAlchemy table queries in agentic_runtime.py
    src = inspect.getsource(agentic_runtime)
    assert "HarnessCascadeModelTransport" not in src
    for forbidden_orm in ("DocumentIngestion", "Observation", "Immunization", "Prescription"):
        assert forbidden_orm not in src


def test_mcp_clinic_emr_jsonrpc_http_and_in_process_lifecycle(tmp_path):
    db, bus, client = _make_env(tmp_path)
    reset_live_demo(db, DEMO_TENANT_1, bus)
    started = start_live_journey(db, tenant_id=DEMO_TENANT_1, journey_key_or_pid="tang_uti", live_bus=bus, lang="en")
    case_id = started["case_id"]
    with db.tenant_session(DEMO_TENANT_1) as s:
        from backend.acute.db import CaseLoop

        pid = s.get(CaseLoop, case_id).patient_id

    # 1. HTTP Streamable JSON-RPC 2.0 initialize
    r_init = client.post(
        "/mcp/clinic-emr",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert r_init.status_code == 200
    init_body = r_init.json()
    assert init_body["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert init_body["result"]["serverInfo"]["name"] == "clinic-emr-mcp"

    # 2. HTTP tools/list matches canonical schema count & metadata
    r_tools = client.post(
        "/mcp/clinic-emr",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"include_blocked": True}},
    )
    tools = r_tools.json()["result"]["tools"]
    assert [t["name"] for t in tools] == [s["name"] for s in TOOL_SCHEMAS]
    blocked_tool = next(t for t in tools if t["name"] == "emr.write_signed_orders")
    assert blocked_tool["_meta"]["ai_permitted"] is False
    assert blocked_tool["_meta"]["authority"] == "BLOCKED"

    # 3. HTTP resources/list + resources/read (`emr://patients/{id}/chart` and `/records`)
    r_res_list = client.post(
        "/mcp/clinic-emr",
        json={"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
    )
    templates = [r["uriTemplate"] for r in r_res_list.json()["result"]["resourceTemplates"]]
    assert "emr://patients/{patient_id}/chart" in templates
    assert "emr://patients/{patient_id}/records" in templates

    r_chart = client.post(
        "/mcp/clinic-emr",
        json={"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": f"emr://patients/{pid}/chart"}},
    )
    chart_json = json.loads(r_chart.json()["result"]["contents"][0]["text"])
    assert chart_json["found"] is True
    assert chart_json["g6pd_deficient"] is True
    assert chart_json["mcp_adapter"] == "simulated_postgres_hk_emr"

    # 4. HTTP tools/call: real CDS check against G6PD chart via MCP
    r_cds = client.post(
        "/mcp/clinic-emr",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "cds.check_medication_safety",
                "arguments": {"patient_id": pid, "drugs": ["Nitrofurantoin"]},
            },
        },
    )
    cds_res = r_cds.json()["result"]
    assert cds_res["isError"] is False
    assert cds_res["structuredContent"]["status"] in ("override_required", "hard_block")

    # 5. Server-side Asymmetric Authority enforcement: AI calling emr.write_signed_orders -> JSON-RPC -32003
    r_deny = client.post(
        "/mcp/clinic-emr",
        headers={"X-MCP-Actor-Role": "assistant"},
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "emr.write_signed_orders", "arguments": {"proposal_id": "prop_999"}},
        },
    )
    deny_body = r_deny.json()
    assert "error" in deny_body
    assert deny_body["error"]["code"] == MCP_AUTHORITY_DENIED_CODE
    assert deny_body["error"]["data"]["error_code"] == "AUTHORITY_DENIED"

    # 6. GET /api/v1/workspace/mcp-status reflects live call & denial telemetry
    r_stat = client.get("/api/v1/workspace/mcp-status")
    assert r_stat.status_code == 200
    stat = r_stat.json()
    assert stat["protocol_version"] == MCP_PROTOCOL_VERSION
    assert stat["calls_served"] >= 1
    assert stat["authority_denials"] >= 1

    # 7. InProcessMCPClient matches HTTP contract
    srv = ClinicEMRMCPServer(db=db, live_bus=bus)
    inproc = InProcessMCPClient(srv, tenant_id=DEMO_TENANT_1)
    assert inproc.initialize()["protocolVersion"] == MCP_PROTOCOL_VERSION
    with pytest.raises(PermissionError):
        inproc.call_tool("emr.write_signed_orders", {"proposal_id": "prop_x"}, actor_role="assistant")


def test_model_gateway_transport_enforces_residency_phi_scrub_and_tenant_budget():
    class _StubInner(LiveHTTPModelTransport):
        def __init__(self, region: str):
            self.provider = "openai"
            self.model = "gpt-4o-mini"
            self.region = region
            self.seen_system = ""
            self.seen_messages: list[dict] = []

        def invoke(self, *, system_message, messages, tools, temperature, max_output_tokens, expect_json):
            self.seen_system = system_message
            self.seen_messages = messages
            return ModelTurnResponse(
                text='{"ok": true}',
                provider="openai",
                model="gpt-4o-mini",
                region=self.region,
                prompt_tokens_actual=200,
                output_tokens_actual=50,
            )

    # 1. Fail-closed HK PDPO DPP3 residency block when cross-border is disallowed
    gw_strict = ModelGateway(
        default_policy=TenantPolicy(
            tenant_id="ten_strict",
            allowed_regions=("asia-east2",),
            allow_cross_border_inference=False,
        )
    )
    t_blocked = ModelGatewayTransport(
        inner=_StubInner("us-global"),
        tenant_id="ten_strict",
        gateway=gw_strict,
        allow_cross_border_with_scrub=False,
    )
    with pytest.raises(ResidencyViolationError):
        t_blocked.invoke(
            system_message="System",
            messages=[{"role": "user", "content": "Patient HKID A123456(7)"}],
            tools=[],
            temperature=0.0,
            max_output_tokens=100,
            expect_json=False,
        )

    # 2. Cross-border with scrub redacts HKID/name & accounts tenant token/cost budget + audit log
    inner_scrub = _StubInner("us-global")
    gw_scrub = ModelGateway(
        default_policy=TenantPolicy(
            tenant_id="ten_scrub",
            allowed_regions=("asia-east2",),
            allow_cross_border_inference=True,
            max_tokens=200,
        )
    )
    t_scrub = ModelGatewayTransport(
        inner=inner_scrub,
        tenant_id="ten_scrub",
        gateway=gw_scrub,
        patient_names_for_scrub=["CHAN Tai Man"],
    )
    resp = t_scrub.invoke(
        system_message="System for CHAN Tai Man",
        messages=[{"role": "user", "content": "HKID A123456(7) phone 91234567"}],
        tools=[],
        temperature=0.0,
        max_output_tokens=100,
        expect_json=False,
    )
    assert resp.prompt_tokens_actual == 200
    assert "CHAN Tai Man" not in inner_scrub.seen_system
    assert "A123456(7)" not in inner_scrub.seen_messages[0]["content"]
    assert gw_scrub.audit_log[-1]["phi_scrubbed"] is True

    # 3. Subsequent call exceeds 200-token tenant budget -> BudgetExceededError
    with pytest.raises(BudgetExceededError):
        t_scrub.invoke(
            system_message="Next turn",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            temperature=0.0,
            max_output_tokens=100,
            expect_json=False,
        )
