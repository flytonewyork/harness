"""End-to-end integration tests for the Real-Phone Sync Multi-Agent Clinic EMR & Working-Age URTI Demo.

Covers:
- LoopBench-backed Simulated Clinic EMR overview (surfaces LoopBench patients + live PostgreSQL clinic hero)
- Zero-friction QR check-in -> LoopWatcher & CareAgent tool calls, 3-tier memory, context window, Lab & Flu rich cards
- Conversational URTI turn -> deterministic SafetySentinel, real G6PD/Penicillin CDS catch (blocks Aspirin), flu vax deferral
- Interactive URTI & 2-day Sick-Leave Pop-up submission -> stages Nurse -> Doctor proposal
- Asymmetric Authority guardrail probe -> AI agent blocked from calling emr.write_signed_orders
- Nurse RBAC prep -> Doctor MCHK sign-off -> EMR write-back + bilingual MCHK Sick Leave PDF delivered live to phone
- Deterministic 999 crisis interception & single-patient URTI demo reset
- Production fail-closed gating on demo QR endpoints
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from backend.acute.agent_harness import HERO_URTI_PATIENT_ID
from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.seed import reset_and_seed
from backend.vault.vault import DocumentVault, LocalFSBackend


def _boot_staging_client(tmp_path):
    os.environ["HARNESS_ENV"] = "staging"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    os.environ["AGENT_LLM_PROVIDER"] = "deterministic"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    app = create_acute_app(db=db, vault=vault)
    return TestClient(app)


def test_full_working_age_urti_phone_emr_nurse_doctor_lifecycle(tmp_path):
    client = _boot_staging_client(tmp_path)

    # 1. Separate Simulated EMR app loads LoopBench simulated patients + working-age hero LAM Wing-Yan
    emr0 = client.get("/api/v1/emr/overview").json()
    assert emr0["hero_patient_id"] == HERO_URTI_PATIENT_ID
    sources = {p["source"] for p in emr0["patients"]}
    assert "hero_live" in sources
    assert "loopbench_sim" in sources
    hero = next(p for p in emr0["patients"] if p["id"] == HERO_URTI_PATIENT_ID)
    assert hero["name_en"] == "LAM Wing-Yan"
    assert hero["g6pd_deficient"] is True
    assert "Penicillin" in hero["allergies"]
    assert hero["flu_vax_status"] == "due_2026_27"
    assert len(hero["documents"]) >= 2

    # 2. Real phone scans QR code -> auto-auth + LoopWatcher & CareAgent hydrate memory & context
    qr = client.post(f"/api/v1/demo/qr-checkin/{HERO_URTI_PATIENT_ID}")
    assert qr.status_code == 200
    qr_body = qr.json()
    pat_tok = qr_body["token"]
    ph = {"Authorization": f"Bearer {pat_tok}"}
    card_kinds = {c["kind"] for c in qr_body["rich_cards"]}
    assert {"lab_result", "flu_vaccine"} <= card_kinds

    # Verify tool calls, 3-tier memory, and context window appeared in Simulated EMR Inspector
    emr1 = client.get(f"/api/v1/emr/overview?patient_id={HERO_URTI_PATIENT_ID}").json()
    st1 = emr1["agent_state"]
    assert st1["phone_connected"] is True
    assert st1["workflow_stage"] == "qr_scanned"
    tool_names = [t["tool"] for t in st1["tool_calls"]]
    assert "emr.get_patient_chart" in tool_names
    assert "emr.get_clinical_records" in tool_names
    assert "emr.check_preventive_gaps" in tool_names
    assert "clinic.get_available_slots" in tool_names
    assert len(st1["memory"]["episodic_memory"]) >= 2
    assert st1["context_window"]["estimated_tokens"] > 100

    # 3. Patient asks about URTI symptoms + Aspirin + Panadol + 2-day sick leave on phone
    turn1 = client.post(
        "/api/v1/patient/agent/turn",
        headers=ph,
        json={
            "action": "message",
            "message": "Sore throat, dry cough and 38.2C fever since yesterday evening. Can I take aspirin or Panadol, and get a 2-day sick leave cert?",
            "lang": "en",
        },
    )
    assert turn1.status_code == 200
    t1 = turn1.json()
    assert t1["crisis"] is None
    assert t1["open_urti_intake"] is True
    # Real rx_rules CDS caught Aspirin in G6PD deficiency!
    flagged_drugs = {f["drug"].lower() for f in t1["cds"]["blocked_or_flagged"]}
    assert "aspirin" in flagged_drugs
    card_kinds_1 = {c["kind"] for c in t1["rich_cards"]}
    assert "medication_safety" in card_kinds_1
    assert "urti_intake_prompt" in card_kinds_1
    flu_card = next(c for c in t1["rich_cards"] if c["kind"] == "flu_vaccine")
    assert flu_card["status"] == "deferred"

    # 4. Patient submits structured URTI & 2-day Sick-Leave pop-up
    turn2 = client.post(
        "/api/v1/patient/agent/turn",
        headers=ph,
        json={
            "action": "submit_urti_intake",
            "payload": {
                "symptoms": ["Sore throat", "Blocked / runny nose", "Dry cough"],
                "onset": "Yesterday evening (<24h)",
                "temp_c": 38.2,
                "rat_result": "Negative (Viral URTI)",
                "sick_leave_days": 2,
            },
            "lang": "en",
        },
    )
    assert turn2.status_code == 200
    prop_id = turn2.json()["proposal_id"]
    assert prop_id

    # 5. Asymmetric Authority Probe: AI agent attempting emr.write_signed_orders is BLOCKED
    probe = client.post(f"/api/v1/emr/simulate-authority-probe?patient_id={HERO_URTI_PATIENT_ID}")
    assert probe.status_code == 200
    assert probe.json()["probe"]["allowed"] is False

    # 6. Nurse Rachel preps chart & marks Ready for Dr. Lam (still blocked 403 from signing!)
    nurse_tok = client.post("/api/v1/demo/login-as", json={"persona": "nurse:nurse_chan"}).json()["token"]
    nh = {"Authorization": f"Bearer {nurse_tok}"}
    ready = client.post(
        f"/api/v1/staff/proposals/{prop_id}/ready-for-doctor",
        headers=nh,
        json={"prep_note": "Confirmed G6PD & Penicillin allergy; home RAT negative; ready for Dr Lam."},
    )
    assert ready.status_code == 200 and ready.json()["nurse_ready"] is True
    assert client.post(
        f"/api/v1/clinician/proposals/{prop_id}/decide",
        headers=nh,
        json={"decision": "approved", "review_seconds": 10.0},
    ).status_code == 403

    # 7. Dr. Kenneth Lam signs G6PD-safe Rx + 2-day MCHK bilingual sick-leave certificate
    doc_tok = client.post("/api/v1/demo/login-as", json={"persona": "clinician:doc_dr_lam"}).json()["token"]
    dh = {"Authorization": f"Bearer {doc_tok}"}
    det = client.get(f"/api/v1/clinician/proposals/{prop_id}", headers=dh).json()
    sign = client.post(
        f"/api/v1/clinician/proposals/{prop_id}/decide",
        headers=dh,
        json={
            "decision": "approved",
            "review_seconds": 14.0,
            "rubber_stamp_confirmed": True,
            "issue_certificate": True,
            "cert_days_off": 2,
            "rx_items": det["suggested_rx"],
            "patient_reply": det["payload"]["patient_reply_draft"],
        },
    )
    assert sign.status_code == 200
    cert_id = sign.json()["certificate"]["id"]
    assert cert_id

    # 8. Simulated EMR reflects signed encounter, Rx, MCHK certificate & SIGNED_WRITE tool call
    emr2 = client.get(f"/api/v1/emr/overview?patient_id={HERO_URTI_PATIENT_ID}").json()
    hero2 = next(p for p in emr2["patients"] if p["id"] == HERO_URTI_PATIENT_ID)
    assert len(hero2["certificates"]) == 1
    assert hero2["certificates"][0]["days_off"] == 2
    assert len(hero2["prescriptions"]) == 1
    assert emr2["agent_state"]["workflow_stage"] == "doctor_signed"
    assert any(tc["authority"] == "SIGNED_WRITE" for tc in emr2["agent_state"]["tool_calls"])
    assert any(tc["authority"] == "BLOCKED" for tc in emr2["agent_state"]["tool_calls"])

    # 9. Patient phone receives Signed Care Plan card and can download valid bilingual MCHK PDF
    comp = client.get("/api/v1/patient/companion", headers=ph).json()
    signed_card = next(c for c in comp["rich_cards"] if c["kind"] == "signed_care_plan")
    assert signed_card["certificate"]["id"] == cert_id
    dl_tok = client.post(f"/api/v1/patient/certificates/{cert_id}/download-token", headers=ph).json()
    pdf_resp = client.get(dl_tok["download_url"], headers=ph)
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b"%PDF-")

    # 10. Resetting just the URTI hero restores clean pre-QR state without wiping other stories
    reset_r = client.post(f"/api/v1/emr/patient/{HERO_URTI_PATIENT_ID}/reset-urti")
    assert reset_r.status_code == 200
    emr3 = client.get(f"/api/v1/emr/overview?patient_id={HERO_URTI_PATIENT_ID}").json()
    hero3 = next(p for p in emr3["patients"] if p["id"] == HERO_URTI_PATIENT_ID)
    assert len(hero3["certificates"]) == 0
    assert emr3["agent_state"]["phone_connected"] is False
    assert any(p["id"] == "pat_wong_flu_g6pd" for p in emr3["patients"])


def test_phone_agent_books_flu_slot_and_catches_red_flags(tmp_path):
    client = _boot_staging_client(tmp_path)
    qr = client.post(f"/api/v1/demo/qr-checkin/{HERO_URTI_PATIENT_ID}").json()
    ph = {"Authorization": f"Bearer {qr['token']}"}

    # Book a flu vaccination slot via interactive slot picker pop-up
    booked = client.post(
        "/api/v1/patient/agent/turn",
        headers=ph,
        json={"action": "book_flu_slot", "payload": {"slot_id": "slot_flu_d2_lunch"}, "lang": "en"},
    )
    assert booked.status_code == 200
    assert booked.json()["booked_slot"]["id"] == "slot_flu_d2_lunch"

    # Deterministic 999 crisis interception cannot be bypassed
    crisis = client.post(
        "/api/v1/patient/agent/turn",
        headers=ph,
        json={"action": "message", "message": "I suddenly have severe chest pain and can't breathe", "lang": "en"},
    )
    assert crisis.status_code == 200
    assert crisis.json()["crisis"]["kind"] == "medical_999"
    assert crisis.json()["crisis"]["pager_acked"] is True


def test_prod_fail_closed_disables_demo_qr_and_probe(tmp_path):
    import base64
    keys = ("HARNESS_ENV", "HARNESS_RUNTIME_MODE", "DEMO_MODE", "JWT_SECRET", "VAULT_SIGNING_SECRET", "VAULT_KEK_B64")
    prev = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["HARNESS_ENV"] = "prod"
        os.environ["HARNESS_RUNTIME_MODE"] = "production"
        os.environ["DEMO_MODE"] = "false"
        os.environ["JWT_SECRET"] = "prod-test-strong-secret-32-bytes-minimum!!"
        os.environ["VAULT_SIGNING_SECRET"] = "prod-test-vault-signing-secret-32b!!"
        os.environ["VAULT_KEK_B64"] = base64.b64encode(b"0" * 32).decode("ascii")
        db = Database()
        vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault_prod"))
        app = create_acute_app(db=db, vault=vault, seed_on_startup=False)
        client = TestClient(app)
        assert client.post(f"/api/v1/demo/qr-checkin/{HERO_URTI_PATIENT_ID}").status_code == 404
        assert client.post("/api/v1/emr/simulate-authority-probe").status_code == 404
        assert client.get("/api/v1/emr/overview").status_code == 401
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_vertex_ai_native_tool_calling_loop_enforces_guardrails(tmp_path, monkeypatch):
    """Verify Cloud Run Vertex AI (asia-east2) native functionCall/functionResponse loop & authority blocking."""
    from backend.acute.agent_harness import MultiAgentHarness, detect_live_llm_engine

    client = _boot_staging_client(tmp_path)
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tmrw-hk-staging")
    monkeypatch.setenv("VERTEX_REGION", "asia-east2")
    monkeypatch.setenv("VERTEX_MODEL", "gemini-2.5-flash")

    eng = detect_live_llm_engine()
    assert eng["live"] is True
    assert eng["provider"] == "vertex"
    assert eng["region"] == "asia-east2"

    # Mock Vertex ADC token + 2-round native tool-calling HTTP response
    monkeypatch.setattr(MultiAgentHarness, "_vertex_adc_token", staticmethod(lambda _proj=None: ("fake-adc-tok", "tmrw-hk-staging")))
    calls_seen: list[dict] = []

    class _FakeResp:
        status_code = 200

        def __init__(self, payload: dict):
            self._payload = payload

        def json(self):
            return self._payload

    import httpx
    orig_post = httpx.Client.post

    def _fake_post(self_http, url, **kwargs):
        if "aiplatform.googleapis.com" not in str(url):
            return orig_post(self_http, url, **kwargs)
        calls_seen.append({"url": str(url), "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        if len(calls_seen) == 1:
            # Round 1: model invokes preventive gap check AND attempts unauthorized signed write
            return _FakeResp({
                "candidates": [{
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "emr_check_preventive_gaps", "args": {"patient_id": HERO_URTI_PATIENT_ID}}},
                            {"functionCall": {"name": "emr_write_signed_orders", "args": {"proposal_id": "prop_attempt"}}},
                        ]
                    }
                }]
            })
        # Round 2: model returns grounded bilingual reply after receiving tool outputs
        return _FakeResp({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "I checked your chart and flagged your G6PD deficiency — please avoid Aspirin. Nurse Rachel and Dr. Kenneth Lam will review your 2-day sick leave request."
                    }]
                }
            }]
        })

    monkeypatch.setattr(httpx.Client, "post", _fake_post)

    qr = client.post(f"/api/v1/demo/qr-checkin/{HERO_URTI_PATIENT_ID}").json()
    ph = {"Authorization": f"Bearer {qr['token']}"}
    resp = client.post(
        "/api/v1/patient/agent/turn",
        headers=ph,
        json={"action": "message", "message": "I have sore throat and 38.2C fever, can I take aspirin and get 2 days sick leave?", "lang": "en"},
    )
    assert resp.status_code == 200
    assert "G6PD" in resp.json()["reply"]
    assert len(calls_seen) == 2
    assert "asia-east2-aiplatform.googleapis.com" in calls_seen[0]["url"]
    assert calls_seen[0]["headers"]["X-Vertex-Zero-Data-Retention"] == "true"

    emr = client.get(f"/api/v1/emr/overview?patient_id={HERO_URTI_PATIENT_ID}").json()
    tool_calls = emr["agent_state"]["tool_calls"]
    assert any(tc["tool"] == "emr.write_signed_orders" and tc["authority"] == "BLOCKED" for tc in tool_calls)


