"""Stage 3 Permission Gate verification suite (WO #72).

Covers all 4 acceptance criteria:
1. Spec-valid HL7 FHIR R4 `AuditEvent` (`type.system`, `agent[].requestor`, `source.observer`,
   `outcome`, `entity`) and `Provenance` resources in `build_fhir_r4_bundle` + `/api/v1/patient/export`.
2. Persisted `ModelGateway` audit records in Postgres `agent_runs` via `make_postgres_gateway_audit_sink`.
3. SMART on FHIR v2 scope enforcement (`patient/*.read`, `user/Proposal.write`,
   `user/MedicationRequest.write`) on `POST /mcp/clinic-emr` and clinical HTTP endpoints.
4. `LabsAdapter` implementing `AdapterContract` with `FaultInjector`, idempotency, critical-value
   alerts, HMAC-SHA256 webhooks, and `WorkspaceAdapterDispatcher` dispatch/rollback.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.acute.adapters import (
    BookingAdapter,
    FluVaxAdapter,
    LabsAdapter,
    PharmacyFulfilmentAdapter,
    WhatsAppDoorbellAdapter,
    WorkspaceAdapterDispatcher,
)
from backend.acute.app import create_acute_app
from backend.acute.contracts import AdapterError, FaultInjector
from backend.acute.db import AgentRun, Database
from backend.acute.security import (
    build_fhir_r4_bundle,
    mint_jwt,
    smart_scope_allows,
)
from backend.acute.seed import DEMO_TENANT_1, seed_demo_data
from backend.gateway.gateway import GatewayRequest, ModelGateway, ReplayProvider, TenantPolicy
from backend.gateway.persistence import make_postgres_gateway_audit_sink
from backend.vault import DocumentVault


# ------------------------------------------------------------------ 1. FHIR R4 AuditEvent & Provenance
def test_fhir_r4_bundle_emits_spec_valid_auditevent_and_provenance():
    bundle = build_fhir_r4_bundle(
        patient={
            "id": "pat_stage3_r4",
            "name_en": "CHAN Tai-Man",
            "name_zh": "陳大文",
            "dob": "1985-05-01",
            "sex": "M",
            "hkid_masked": "A123XXX(X)",
            "ehealth_consent": True,
            "g6pd_deficient": False,
            "allergies": ["Penicillin"],
            "status": "active",
        },
        consents=[],
        encounters=[{"id": "enc_1", "status": "completed", "icd10_code": "J11.1", "snomed_codes": []}],
        observations=[],
        certificates=[
            {
                "id": "cert_1",
                "mchk_reg_no": "M14829",
                "verification_hash": "abc123def456",
                "signed_at": "2026-11-28T10:00:00+00:00",
            }
        ],
        prescriptions=[
            {
                "id": "rx_1",
                "mchk_reg_no": "M14829",
                "signed_at": "2026-11-28T10:01:00+00:00",
                "items": [{"drug": "paracetamol", "rxnorm": "161", "atc": "N02BE01", "dose_mg": 500, "frequency": "QDS", "days": 3}],
            }
        ],
        immunizations=[],
        audit_events=[
            {
                "id": "aud_1",
                "action": "pdpo.dar_exported",
                "actor_id": "pat_stage3_r4",
                "actor_role": "patient",
                "resource_type": "Patient",
                "resource_id": "pat_stage3_r4",
                "created_at": "2026-11-28T10:05:00+00:00",
            }
        ],
        agent_runs=[
            {
                "id": "run_1",
                "encounter_id": "enc_1",
                "protocol_id": "acute_consult_draft_v1",
                "protocol_version": "1.0.0",
                "model_id": "gemini-4-hydrogen",
                "provider": "vertex",
                "region": "asia-east2",
                "prompt_hash": "a" * 64,
                "created_at": "2026-11-28T09:59:00+00:00",
            }
        ],
    )

    resources = [e["resource"] for e in bundle["entry"]]
    audits = [r for r in resources if r["resourceType"] == "AuditEvent"]
    provs = [r for r in resources if r["resourceType"] == "Provenance"]

    assert len(audits) == 1
    ae = audits[0]
    assert ae["type"]["system"] == "http://dicom.nema.org/resources/ontology/DCM"
    assert ae["type"]["code"] == "110106"
    assert ae["action"] == "R"
    assert ae["outcome"] == "0"
    assert ae["agent"][0]["requestor"] is True
    assert ae["source"]["observer"]["display"] == "TMRW HK Clinical Loop OS"
    assert ae["entity"][0]["what"]["reference"] == "Patient/pat_stage3_r4"

    # Provenance emitted for Rx, Certificate, and AI AgentRun
    assert len(provs) == 3
    prov_ids = {p["id"] for p in provs}
    assert {"prov-rx-rx_1", "prov-cert-cert_1", "prov-run-run_1"} == prov_ids
    rx_prov = next(p for p in provs if p["id"] == "prov-rx-rx_1")
    assert rx_prov["target"] == [{"reference": "MedicationRequest/rx_1-0"}]
    assert rx_prov["agent"][0]["who"]["identifier"]["value"] == "M14829"
    assert rx_prov["signature"][0]["type"][0]["code"] == "1.2.840.10065.1.12.1.1"
    run_prov = next(p for p in provs if p["id"] == "prov-run-run_1")
    assert run_prov["agent"][0]["type"]["coding"][0]["code"] == "assembler"
    assert "urn:tmrw:protocol:acute_consult_draft_v1:1.0.0" in run_prov["policy"][0]


# ------------------------------------------------------------------ 2. Persisted ModelGateway Audits
def test_model_gateway_audit_persists_to_postgres_agent_runs():
    db = Database()
    db.init_schema()
    seed_demo_data(db, DocumentVault())

    replay = ReplayProvider()
    gw = ModelGateway(
        replay_provider=replay,
        default_policy=TenantPolicy(tenant_id=DEMO_TENANT_1, allowed_regions=("asia-east2",)),
        audit_sink=make_postgres_gateway_audit_sink(db),
    )

    resp = gw.complete(
        GatewayRequest(
            tenant_id=DEMO_TENANT_1,
            task_type="triage",
            protocol_id="flu_intake_v1",
            provider="replay",
            model_id="gemini-4-hydrogen",
            region="asia-east2",
            prompt="38.2C fever and cough for 1 day, no red flags.",
            metadata={"patient_id": "pat_lam_urti_demo"},
        )
    )
    assert resp.ok is True

    with db.tenant_session(DEMO_TENANT_1) as s:
        runs = s.scalars(
            select(AgentRun).where(
                AgentRun.tenant_id == DEMO_TENANT_1,
                AgentRun.patient_id == "pat_lam_urti_demo",
                AgentRun.protocol_id == "flu_intake_v1",
            )
        ).all()
    assert any(r.id.startswith("gwrun_") and (r.output_json or {}).get("source") == "model_gateway_audit" for r in runs)
    persisted = next(r for r in runs if r.id.startswith("gwrun_"))
    assert persisted.model_id == "gemini-4-hydrogen"
    assert persisted.region == "asia-east2"
    assert persisted.input_tokens > 0
    assert persisted.output_tokens > 0


# ------------------------------------------------------------------ 3. SMART on FHIR Scopes (MCP + HTTP)
def test_smart_scope_matcher_semantics():
    assert smart_scope_allows(("patient/*.read",), "patient/Patient.read") is True
    assert smart_scope_allows(("patient/*.read",), "patient/Observation.read") is True
    assert smart_scope_allows(("patient/*.read",), "user/Proposal.write") is False
    assert smart_scope_allows(("patient/*.read",), "user/MedicationRequest.write") is False
    assert smart_scope_allows(("user/Proposal.write",), "user/Proposal.write") is True
    assert smart_scope_allows(("patient/Observation.rs",), "patient/Observation.read") is True


def test_smart_scopes_enforced_on_mcp_and_clinical_http_endpoints():
    db = Database()
    app = create_acute_app(db=db, vault=DocumentVault(), seed_on_startup=True)
    client = TestClient(app)

    # A. MCP status reports SMART scopes enforced + tools/list advertises smart_scope metadata
    status_resp = client.get("/api/v1/workspace/mcp-status")
    assert status_resp.status_code == 200
    assert status_resp.json()["smart_scopes_enforced"] is True

    list_resp = client.post(
        "/mcp/clinic-emr",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert list_resp.status_code == 200
    tools = {t["name"]: t for t in list_resp.json()["result"]["tools"]}
    assert tools["emr.get_patient_chart"]["_meta"]["smart_scope"] == "patient/Patient.read"
    assert tools["harness.stage_staff_proposal"]["_meta"]["smart_scope"] == "user/Proposal.write"

    # B. Read-only SMART scope (`patient/*.read`) ALLOWS chart read, DENIES proposal write on /mcp/clinic-emr
    read_ok = client.post(
        "/mcp/clinic-emr",
        headers={"X-SMART-Scope": "patient/*.read"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "emr.get_patient_chart", "arguments": {"patient_id": "pat_lam_urti_demo"}},
        },
    )
    assert read_ok.status_code == 200
    assert "result" in read_ok.json()
    assert read_ok.json()["result"]["structuredContent"]["found"] is True

    write_denied = client.post(
        "/mcp/clinic-emr",
        headers={"X-SMART-Scope": "patient/*.read"},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "harness.stage_staff_proposal",
                "arguments": {
                    "patient_id": "pat_lam_urti_demo",
                    "action_type": "message",
                    "summary": "Should be blocked without user/Proposal.write",
                },
            },
        },
    )
    assert write_denied.status_code == 200
    err = write_denied.json()["error"]
    assert err["code"] == -32003
    assert err["data"]["error_code"] == "SMART_SCOPE_DENIED"
    assert err["data"]["required_scope"] == "user/Proposal.write"

    # C. Bearer JWT with narrowed scope is also strictly enforced on /mcp/clinic-emr
    narrow_assistant_jwt = mint_jwt(
        subject_id="agent_ro",
        role="assistant",
        tenant_id=DEMO_TENANT_1,
        scopes=("patient/*.read",),
    )
    jwt_denied = client.post(
        "/mcp/clinic-emr",
        headers={"Authorization": f"Bearer {narrow_assistant_jwt}"},
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "emr://clinic/availability/gp"},
        },
    )
    assert jwt_denied.json()["error"]["code"] == -32003
    assert jwt_denied.json()["error"]["data"]["error_code"] == "SMART_SCOPE_DENIED"

    # D. Clinical HTTP endpoint `/api/v1/patient/export` denies token missing `patient/*.read`
    no_read_patient_jwt = mint_jwt(
        subject_id="pat_lam_urti_demo",
        role="patient",
        tenant_id=DEMO_TENANT_1,
        step_up_verified=True,
        scopes=("patient/Consent.write",),
    )
    r_denied = client.get("/api/v1/patient/export", headers={"Authorization": f"Bearer {no_read_patient_jwt}"})
    assert r_denied.status_code == 403
    assert "SMART_SCOPE_DENIED" in r_denied.json()["detail"]

    # And ALLOWS token with `patient/*.read`, emitting valid R4 Bundle
    ok_patient_jwt = mint_jwt(
        subject_id="pat_lam_urti_demo",
        role="patient",
        tenant_id=DEMO_TENANT_1,
        step_up_verified=True,
        scopes=("patient/*.read",),
    )
    r_ok = client.get("/api/v1/patient/export", headers={"Authorization": f"Bearer {ok_patient_jwt}"})
    assert r_ok.status_code == 200
    assert r_ok.json()["resourceType"] == "Bundle"

    # E. Clinician signing Rx requires `user/MedicationRequest.write`
    narrow_doc_jwt = mint_jwt(
        subject_id="doc_dr_lam",
        role="clinician",
        tenant_id=DEMO_TENANT_1,
        mchk_reg_no="M14829",
        scopes=("user/Proposal.write",),
    )
    r_rx_denied = client.post(
        "/api/v1/clinician/proposals/prop_lam_gi_referral/decide",
        headers={"Authorization": f"Bearer {narrow_doc_jwt}"},
        json={
            "decision": "approved",
            "issue_rx": {
                "items": [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QDS", "days": 3}]
            },
        },
    )
    assert r_rx_denied.status_code == 403
    assert "user/MedicationRequest.write" in r_rx_denied.json()["detail"]


# ------------------------------------------------------------------ 4. LabsAdapter + FaultInjector
def test_labs_adapter_contract_fault_injection_critical_alerts_and_rollback():
    faults = FaultInjector()
    labs = LabsAdapter(fault_injector=faults)
    assert labs.name == "hk_labs_pathology"
    assert labs.health()["ok"] is True

    # Transient failure injection
    faults.inject_transient_failures("labs.order", 1)
    with pytest.raises(AdapterError) as exc_info:
        labs.order_panel(
            patient_id="pat_1",
            practitioner_id="doc_dr_lam",
            panels=["iron_studies", "renal_electrolytes"],
            idempotency_key="lab_idem_1",
        )
    assert exc_info.value.retryable is True

    # Retry succeeds and idempotency returns identical external_ref
    res1 = labs.order_panel(
        patient_id="pat_1",
        practitioner_id="doc_dr_lam",
        panels=["iron_studies", "renal_electrolytes"],
        clinical_indication="Repeat ferritin + K+",
        idempotency_key="lab_idem_1",
    )
    res2 = labs.order_panel(
        patient_id="pat_1",
        practitioner_id="doc_dr_lam",
        panels=["iron_studies", "renal_electrolytes"],
        idempotency_key="lab_idem_1",
    )
    assert res1.ok and res1.external_ref == res2.external_ref
    assert res1.payload["fasting_required"] is True
    assert "2276-4" in res1.payload["loinc_codes"]

    # Webhook ingestion with HMAC verification & critical-value detection (K+ 6.4 mmol/L)
    secret = "hk-kingmed-webhook-secret"
    wh_body = json.dumps(
        {
            "order_ref": res1.external_ref,
            "observations": [
                {"loinc_code": "2823-3", "display": "Potassium", "value_num": 6.4, "ucum_unit": "mmol/L"},
            ],
        }
    ).encode("utf-8")
    good_sig = "sha256=" + hmac.new(secret.encode("utf-8"), wh_body, hashlib.sha256).hexdigest()

    with pytest.raises(AdapterError):
        labs.ingest_result_webhook(raw_body=wh_body, signature="sha256=bad", secret=secret)

    wh_res = labs.ingest_result_webhook(raw_body=wh_body, signature=good_sig, secret=secret, idempotency_key="wh_1")
    assert wh_res.ok is True
    assert wh_res.payload["has_critical"] is True
    assert wh_res.payload["critical_alerts"][0]["loinc_code"] == "2823-3"

    fetched = labs.fetch_results(res1.external_ref)
    assert fetched.status == "final"
    assert fetched.payload["has_critical"] is True

    # Permanent outage flips health().ok -> False
    faults.set_down("labs.order", True)
    assert labs.health()["ok"] is False
    faults.set_down("labs.order", False)

    # WorkspaceAdapterDispatcher end-to-end /lab dispatch & 10-second Undo rollback
    dispatcher = WorkspaceAdapterDispatcher(
        booking_adapter=BookingAdapter(),
        vax_adapter=FluVaxAdapter(),
        rx_adapter=PharmacyFulfilmentAdapter(),
        wa_adapter=WhatsAppDoorbellAdapter(),
        labs_adapter=labs,
    )
    dispatched = dispatcher.dispatch_orders(
        trace_id="tr_stage3_lab",
        patient_id="pat_1",
        patient_phone="+85291000001",
        practitioner_id="doc_dr_lam",
        practitioner_name="Dr. Kenneth Lam",
        orders=[{"type": "lab", "panel": "lipid_apob", "rationale": "90-day ApoB recheck"}],
    )
    assert len(dispatched["lab_orders"]) == 1
    lab_ref = dispatched["lab_orders"][0]["order_ref"]
    rolled = dispatcher.rollback_trace("tr_stage3_lab")
    assert lab_ref in rolled["cancelled_lab_orders"]
    assert labs.orders[lab_ref]["status"] == "cancelled"
