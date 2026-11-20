"""Comprehensive unit & integration tests for Sprints 1-3:
- Clinical guideline intelligence (HK CHP / NICE / IDSA / NEWS2 red flags, CDS, pregnancy/pediatric/renal/stewardship Rx)
- Security hardening (prod JWT fail-fast, bounded rate limiter, X-Forwarded-For IP parsing, statutory-hold lockout)
- Reliability (ModelGateway circuit breaker, Vault health check, durable outbox processing)
- Observability (/health/live, /health/ready + 21 FORCE RLS check, /metrics Prometheus, /admin/observability,
  PHI-scrubbed CloudJsonLogFormatter, request ID tracing, clinician secure reply, admin PDPO DCR resolution).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.observability import CloudJsonLogFormatter, TelemetryRegistry, normalize_route_group
from backend.acute.rx_rules import check_prescription_safety
from backend.acute.security import SlidingRateLimiter, _get_jwt_secret, client_ip_from_request
from backend.acute.triage import AcuteHarnessRuntime, screen_acute_message
from backend.gateway.gateway import GatewayRequest, ModelGateway, ProviderUnavailableError
from backend.vault import DocumentVault, LocalFSBackend


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def acute_app_client():
    with tempfile.TemporaryDirectory(prefix="tmrw-obs-vault-") as vdir:
        db = Database()
        vault = DocumentVault(backend=LocalFSBackend(vdir))
        app = create_acute_app(db=db, vault=vault, seed_on_startup=True)
        with TestClient(app) as client:
            yield client, app


# ------------------------------------------------------------------------------ 1. Security Hardening Unit Tests
def test_jwt_secret_prod_fail_fast(monkeypatch):
    monkeypatch.setenv("HARNESS_ENV", "prod")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _get_jwt_secret()

    monkeypatch.setenv("JWT_SECRET", "short-weak-secret")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _get_jwt_secret()

    strong = "a" * 48
    monkeypatch.setenv("JWT_SECRET", strong)
    assert _get_jwt_secret() == strong


def test_bounded_rate_limiter_eviction_and_xff_parsing():
    limiter = SlidingRateLimiter(max_requests=2, window_seconds=60, max_keys=5)
    for i in range(20):
        limiter.check(f"ip-{i}:api")
    # Bounded memory footprint under IP-spray load
    assert len(limiter._hits) <= 5

    # X-Forwarded-For left-most client IP extraction
    fake_req = SimpleNamespace(
        headers={"x-forwarded-for": "203.198.12.34, 10.0.0.1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert client_ip_from_request(fake_req) == "203.198.12.34"


# ------------------------------------------------------------------------------ 2. Gateway Circuit Breaker & Telemetry
def test_gateway_circuit_breaker_trips_and_skips_redundant_calls():
    calls = {"count": 0}

    class BrokenProvider:
        def invoke(self, call):
            calls["count"] += 1
            raise ProviderUnavailableError("Vertex publisher model 404")

    gw = ModelGateway(allow_fallback=True, circuit_breaker_ttl_s=60.0)
    gw.providers["vertex"] = BrokenProvider()  # type: ignore[assignment]

    r1 = gw.complete(GatewayRequest(prompt="Mild cough", provider="vertex", model_id="gemini-2.5-flash"))
    assert r1.fallback_used is True
    assert calls["count"] == 1
    assert gw.circuit_breaker_trips == 1
    assert gw.fallback_count == 1

    # Second call within TTL MUST short-circuit without hitting BrokenProvider again
    r2 = gw.complete(GatewayRequest(prompt="Mild sore throat", provider="vertex", model_id="gemini-2.5-flash"))
    assert r2.fallback_used is True
    assert calls["count"] == 1  # Circuit stayed open!
    assert gw.fallback_count == 2


def test_observability_telemetry_and_phi_scrubbed_json_logs():
    reg = TelemetryRegistry()
    for ms in (5.0, 10.0, 15.0, 20.0, 100.0):
        reg.record_http("/api/v1/clinician/proposals/prop_12345/decide", 200, ms)
    reg.record_http("/api/v1/health/live", 200, 1.0)
    reg.inc("red_flags_escalated_total", 2)

    snap = reg.snapshot()
    assert snap["http"]["requests_total"] == 6
    assert snap["http"]["latency_ms"]["p95"] >= 20.0
    assert "api.clinician.proposals" in snap["http"]["routes"]
    assert normalize_route_group("/api/v1/magic/secret-token-xyz/redeem") == "api.magic.secret-token-xyz" or normalize_route_group("/api/v1/health/ready") == "api.health.ready"
    prom = reg.render_prometheus()
    assert "clinic_harness_http_requests_total 6" in prom
    assert "clinic_harness_red_flags_escalated_total 2" in prom

    # Verify CloudJsonLogFormatter scrubs HKID & HK phone numbers from log messages
    fmt = CloudJsonLogFormatter()
    rec = logging.LogRecord(
        name="tmrw.acute",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Checked patient HKID A123456(7) phone +852 9123 4567",
        args=(),
        exc_info=None,
    )
    rendered = fmt.format(rec)
    parsed = json.loads(rendered)
    assert "A123456(7)" not in parsed["message"]
    assert "9123 4567" not in parsed["message"]
    assert parsed["severity"] == "INFO"


# ------------------------------------------------------------------------------ 3. Clinical Guideline Rules Unit Tests
@pytest.mark.parametrize(
    "text,vitals,expected_flag",
    [
        ("My 6-week-old baby has fever 38.2C", {"temp_c": 38.2}, "febrile_infant_under_3m"),
        ("我做緊化療，今晚發燒38.5度發冷", {"temp_c": 38.5}, "neutropenic_sepsis_risk"),
        ("我係kidney transplant食緊immunosuppressant，發燒38.6度", {"temp_c": 38.6}, "neutropenic_sepsis_risk"),
        ("28 weeks pregnant with bright red vaginal bleeding", {}, "obstetric_red_flag"),
        ("高燒39.5度、條頸梗晒兼畏光", {"temp_c": 39.5}, "meningitis_red_flag"),
        ("Elderly grandfather shivering, temp 34.8C", {"temp_c": 34.8}, "hypothermia_sepsis_temp_lte_35c"),
        ("Fever 38.8C and breathing fast RR 28/min", {"temp_c": 38.8, "rr": 28}, "tachypnoea_rr_gte_25"),
    ],
)
def test_guideline_red_flags_trigger_emergency(text, vitals, expected_flag):
    res = screen_acute_message(text, vitals=vitals, enable_model_screen=False)
    assert res.is_emergency is True
    assert res.urgency == 4
    assert expected_flag in res.red_flags


def test_double_sickening_elevates_priority_without_false_999():
    rt = AcuteHarnessRuntime(enable_model_screen=False)
    out = rt.process_intake(
        "My flu got better on day 3, then got worse again today on day 5 with fever 38.6C returning and productive cough.",
        vitals={"temp_c": 38.6, "spo2_pct": 97},
        patient={"dob": "1988-04-12"},
    )
    assert out["escalated"] is False
    assert out["triage"].urgency == 3
    cds = out["draft"].clinical_note["clinical_decision_support"]
    assert cds["double_sickening_suspected"] is True


def test_rx_pregnancy_pediatric_renal_and_stewardship_rules():
    # Pregnancy NSAID -> override_required
    r_preg = check_prescription_safety({"age": 31, "sex": "F", "pregnant": True}, [{"drug": "ibuprofen", "dose_mg": 400, "frequency": "TDS", "days": 3}])
    assert r_preg.status == "override_required"
    assert any(f.code == "pregnancy_nsaid_contraindication" for f in r_preg.findings)

    # Pediatric <6y DXM -> override_required (HK DH)
    r_dxm = check_prescription_safety({"age": 4, "weight_kg": 16.0}, [{"drug": "dextromethorphan", "dose_mg": 5, "frequency": "TDS", "days": 3}])
    assert r_dxm.status == "override_required"
    assert any(f.code == "pediatric_antitussive_under_6" for f in r_dxm.findings)

    # Severe renal eGFR < 30 Oseltamivir 30mg BD -> override_required; 30mg OD -> pass
    r_ren_over = check_prescription_safety({"age": 75, "weight_kg": 60, "egfr": 24.0}, [{"drug": "oseltamivir", "dose_mg": 30, "frequency": "BD", "days": 5}])
    assert r_ren_over.status == "override_required"
    r_ren_ok = check_prescription_safety({"age": 75, "weight_kg": 60, "egfr": 24.0}, [{"drug": "oseltamivir", "dose_mg": 30, "frequency": "OD", "days": 5}])
    assert r_ren_ok.can_sign_without_override is True

    # HK CHP IMPACT viral antibiotic stewardship advisory
    r_abx = check_prescription_safety(
        {"age": 35, "weight_kg": 68},
        [{"drug": "amoxicillin", "dose_mg": 500, "frequency": "TDS", "days": 5}],
        diagnosis_icd10="J10.1",
    )
    assert any(f.code == "antibiotic_stewardship_viral_illness" and f.severity == "warn" for f in r_abx.findings)


# ------------------------------------------------------------------------------ 4. Live FastAPI Endpoints Integration
def test_probes_metrics_observability_outbox_dcr_reply_and_statutory_hold(acute_app_client):
    client, _ = acute_app_client

    # Liveness, Readiness (21 FORCE RLS tables + Vault OK), Prometheus Metrics, Request-ID tracing
    live = client.get("/api/v1/health/live", headers={"X-Request-ID": "req_trace_test_1"})
    assert live.status_code == 200 and live.json()["status"] == "live"
    assert live.headers["x-request-id"] == "req_trace_test_1"
    assert "x-response-time-ms" in live.headers

    ready = client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    rj = ready.json()
    assert rj["ok"] is True
    assert rj["checks"]["postgres"]["ok"] is True
    assert rj["checks"]["forced_rls"]["rls_tables"] == 21
    assert rj["checks"]["vault"]["ok"] is True

    metrics = client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    assert "clinic_harness_http_requests_total" in metrics.text

    # Login as clinician & clinic_admin
    doc_tok = client.post("/api/v1/demo/login-as", json={"role": "clinician", "id": "doc_dr_lam"}).json()["token"]
    adm_tok = client.post("/api/v1/demo/login-as", json={"role": "clinic_admin", "id": "adm_central_ops"}).json()["token"]

    # RBAC check: clinician CANNOT access /admin/observability or /admin/outbox/process
    assert client.get("/api/v1/admin/observability", headers=_auth(doc_tok)).status_code == 403
    assert client.post("/api/v1/admin/outbox/process", headers=_auth(doc_tok)).status_code == 403

    # Admin Observability & Outbox Processing
    obs = client.get("/api/v1/admin/observability", headers=_auth(adm_tok))
    assert obs.status_code == 200
    oj = obs.json()
    assert oj["vault"]["ok"] is True
    assert "latency_ms" in oj["telemetry"]["http"]
    assert "fallback_count" in oj["gateway"]

    proc = client.post("/api/v1/admin/outbox/process", headers=_auth(adm_tok))
    assert proc.status_code == 200 and proc.json()["ok"] is True

    # Clinician Proposal Detail CDS + Suggested Meds + Viral Abx Stewardship Check + Secure Clinician Reply
    q = client.get("/api/v1/clinician/queue", headers=_auth(doc_tok)).json()["items"]
    target_prop = next(x for x in q if x["id"] == "prop_pat_wong_flu_g6pd")
    det = client.get(f"/api/v1/clinician/proposals/{target_prop['id']}", headers=_auth(doc_tok))
    assert det.status_code == 200
    dj = det.json()
    assert dj["payload"]["clinical_decision_support"] is not None
    assert len(dj["suggested_rx"]) >= 1

    # Viral antibiotic stewardship live via API
    stew = client.post(
        "/api/v1/clinician/rx-safety-check",
        headers=_auth(doc_tok),
        json={
            "patient_id": dj["patient"]["id"],
            "diagnosis_icd10": "J10.1",
            "items": [{"drug": "clarithromycin", "dose_mg": 500, "frequency": "BD", "days": 5}],
        },
    )
    assert stew.status_code == 200
    assert any(f["code"] == "antibiotic_stewardship_viral_illness" for f in stew.json()["findings"])

    # Secure clinician reply -> in-portal message + non-clinical WhatsApp doorbell
    rep = client.post(
        f"/api/v1/clinician/proposals/{target_prop['id']}/message",
        headers=_auth(doc_tok),
        json={"body_text": "Please hydrate well and re-check temperature tonight. Go to A&E if SpO2 drops below 94%."},
    )
    assert rep.status_code == 200
    assert rep.json()["doorbell_sent"] is True
    assert rep.json()["message"]["sender_role"] == "clinician"

    # Patient DCR -> Admin Resolve as append-only addendum -> 409 on double resolve
    pat_tok = client.post("/api/v1/demo/login-as", json={"role": "patient", "id": "pat_leung_completed"}).json()["token"]
    dcr_res = client.post(
        "/api/v1/patient/data-correction",
        headers=_auth(pat_tok),
        json={"field": "allergies", "requested_value": "Add shellfish allergy", "reason": "Confirmed by clinic allergy test"},
    )
    assert dcr_res.status_code == 200
    dcr_id = dcr_res.json()["request_id"]

    dlist = client.get("/api/v1/admin/data-requests", headers=_auth(adm_tok))
    assert dlist.status_code == 200
    assert any(r["id"] == dcr_id for r in dlist.json()["data_requests"])

    resolved = client.post(
        f"/api/v1/admin/data-requests/{dcr_id}/resolve",
        headers=_auth(adm_tok),
        json={"action": "approved", "resolution_note": "Verified against lab report and appended to PDPO ledger."},
    )
    assert resolved.status_code == 200
    assert resolved.json()["data_request"]["status"] == "approved_addendum"

    # Second resolution attempt must return 409
    again = client.post(
        f"/api/v1/admin/data-requests/{dcr_id}/resolve",
        headers=_auth(adm_tok),
        json={"action": "rejected", "resolution_note": "Duplicate attempt"},
    )
    assert again.status_code == 409

    # Statutory-Hold Lockout: after deletion request, clinical mutations return 403 while DAR FHIR export stays 200!
    hold = client.post("/api/v1/patient/delete-data", headers=_auth(pat_tok), json={"reason": "Account closure test"})
    assert hold.status_code == 200 and hold.json()["status"] == "statutory_hold_applied"

    assert client.post("/api/v1/patient/intake", headers=_auth(pat_tok), json={"symptom_text": "Mild cough"}).status_code == 403
    assert client.post("/api/v1/patient/messages", headers=_auth(pat_tok), json={"body_text": "Hello"}).status_code == 403
    assert client.post("/api/v1/patient/flu-vax/book", headers=_auth(pat_tok), json={"partner_clinic": "Central", "slot_at": "2026-11-25T10:00:00+08:00"}).status_code == 403
    # Data Access Request (FHIR R4 export) remains accessible under PDPO DPP6
    dar_after_hold = client.get("/api/v1/patient/export", headers=_auth(pat_tok))
    assert dar_after_hold.status_code == 200 and dar_after_hold.json()["resourceType"] == "Bundle"

    # Clean reset so subsequent tests start with fresh seed
    assert client.post("/api/v1/demo/reset").status_code == 200
