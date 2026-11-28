"""Integration tests for the 3-Layer Clinical Practice Knowledge Graph, Clinic Curator,
Fail-Closed 3-Point ID Document Gate, 4-State Case Loops, and Loop 3 Irreversible Commit + 10s Undo.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.seed import reset_and_seed
from backend.vault.vault import DocumentVault, LocalFSBackend


def _make_client(tmp_path) -> TestClient:
    os.environ["HARNESS_ENV"] = "staging"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    app = create_acute_app(db=db, vault=vault)
    return TestClient(app)


def test_practice_graph_safety_floor_and_forbidden_titles_blocked(tmp_path):
    client = _make_client(tmp_path)
    client.post("/api/v1/workspace/reset")

    # 1. Attempting to name Careline AI "Resident" or "Dr. Sam" must fail closed (HTTP 422)
    r_bad_name = client.post(
        "/api/v1/graph/onboard",
        json={"careline_profile_patch": {"name": "Resident Sam"}},
    )
    assert r_bad_name.status_code == 422
    assert "medical title" in r_bad_name.text.lower()

    # 2. Attempting to weaken Immutable Safety Floor node must fail closed (HTTP 422)
    r_bad_floor = client.post(
        "/api/v1/graph/onboard",
        json={"overlay_patch": {"node_overrides": {"ebm.safety.g6pd_allergy_floor": {"enforce_before_llm": False}}}},
    )
    assert r_bad_floor.status_code == 422
    assert "safety floor" in r_bad_floor.text.lower()

    # 3. Valid onboarding rename ("Maya") succeeds & bumps Practice Graph version
    r_ok = client.post(
        "/api/v1/graph/onboard",
        json={"careline_profile_patch": {"name": "Maya"}, "change_summary": "Named Careline AI Maya"},
    )
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["careline_profile"]["name"] == "Maya"
    assert body["version_tag"] == "v2.5"


def test_loop1_three_tier_triage_and_red_flag_floor(tmp_path):
    client = _make_client(tmp_path)
    client.post("/api/v1/workspace/reset")

    # Red flag -> Tier 0 immediate 999 exit
    r0 = client.post("/api/v1/workspace/triage/simulate", json={"message_text": "Severe chest pain and shortness of breath"})
    assert r0.status_code == 200
    assert r0.json()["route"] == "urgent_exit_999"

    # Routine flu slot -> Tier 1 self-serve (0 doctor mins)
    r1 = client.post("/api/v1/workspace/triage/simulate", json={"message_text": "Can I book a 10-min flu vaccine slot on Thursday?"})
    assert r1.status_code == 200
    assert r1.json()["tier"] == 1
    assert r1.json()["route"] == "self_serve_careline"

    # Small clarifying question within 72h grace window -> Tier 2 (#my-work, NO fake SLA)
    r2 = client.post(
        "/api/v1/workspace/triage/simulate",
        json={"message_text": "Quick question to clarify my discharge dose after visit yesterday", "hours_since_last_visit": 24},
    )
    assert r2.status_code == 200
    assert r2.json()["tier"] == 2
    assert r2.json()["promise_reply_sla"] is False

    # New fever + sick note + Aspirin mention -> Tier 3 booking options + G6PD guard (never unilateral billing)
    r3 = client.post(
        "/api/v1/workspace/triage/simulate",
        json={"message_text": "Woke up with 38.2 fever, can I take aspirin and get a 2-day sick note?", "hours_since_last_visit": 120, "patient_g6pd": True},
    )
    assert r3.status_code == 200
    j3 = r3.json()
    assert j3["tier"] == 3
    assert j3["ai_unilateral_billing"] is False
    assert "g6pd_aspirin_warning" in j3["guards_fired"]


def test_loop2_fail_closed_three_point_id_gate_quarantines_mismatch(tmp_path):
    client = _make_client(tmp_path)
    client.post("/api/v1/workspace/reset")

    # Wrong DOB (2/3 match only) MUST fail closed into Nurse Rachel quarantine
    r_bad = client.post(
        "/api/v1/workspace/documents/ingest",
        json={
            "external_doc_ref": "QEH-DC-MISMATCH-999",
            "doc_kind": "discharge_summary",
            "source_org": "Queen Elizabeth Hospital",
            "claimed_mrn_or_hkid": "PAT-62041",
            "claimed_name": "CHAN Tai Man",
            "claimed_dob": "1964-03-19",  # mismatch! actual is 1964-03-14
            "verbatim_quote": "Discharge meds: Warfarin 3mg daily",
            "page_ref": "p.2",
        },
    )
    assert r_bad.status_code == 200
    j = r_bad.json()
    assert j["match_status"] == "quarantined_nurse"
    assert j["matched_patient_id"] is None
    assert j["checks"]["points_matched"] == 2

    ov = client.get("/api/v1/workspace/overview").json()
    assert any(q["external_doc_ref"] == "QEH-DC-MISMATCH-999" for q in ov["quarantined_documents"])
    assert any(h["status"] == "quarantined_nurse" for h in ov["team_handoffs"])


def test_loop3_loop4_loop5_end_to_end_commit_undo_ticks_and_curator(tmp_path):
    client = _make_client(tmp_path)
    ov = client.post("/api/v1/workspace/reset").json()

    chan = next(c for c in ov["cases"] if c["case_key"] == "chan")
    wong = next(c for c in ov["cases"] if c["case_key"] == "wong")
    lee = next(c for c in ov["cases"] if c["case_key"] == "lee")
    assert chan["state"] == "needs_decision"
    assert wong["state"] == "scheduled"
    # Quiet multi-day waiting cases sit in Scheduled / Waiting (NOT cluttering Running · Today)
    assert lee["state"] == "scheduled"
    assert "2 Nov" in lee["summary"]["origin_strip"]["from"]
    assert len(ov["practice_graph"]["order_catalog"]) == 8
    assert len(ov["practice_graph"]["preventive_rules"]) >= 5

    # Loop 3 Preview (categorised + irreversible flag + bounded context window)
    instr = "Adopt QEH Warfarin 3mg and Atorvastatin, stop Simvastatin, ask Nurse Rachel for Cantonese pillbox call, and book INR on 29 Nov"
    prev = client.post(f"/api/v1/workspace/cases/{chan['id']}/preview", json={"instruction_text": instr}).json()
    assert prev["requires_irreversible_commit"] is True
    assert len(prev["categorised_plan"]["clinical_advice"]) >= 1
    assert len(prev["categorised_plan"]["lab_imaging"]) >= 1
    assert prev["context_snapshot"]["total_tokens_est"] < prev["context_snapshot"]["token_budget_ceiling"]

    # Loop 3 Commit -> 10s Undo window -> Undo -> restores Needs Decision cleanly
    c1 = client.post(f"/api/v1/workspace/cases/{chan['id']}/commit", json={"instruction_text": instr}).json()
    assert c1["trace"]["can_undo"] is True
    assert c1["case_state"] == "running" and c1["case_baton"] == "with_nurse"
    assert len(c1["trace"]["folded_steps"]) >= 3
    assert c1["trace"]["emr_addendum"]["connector_mode"] == "additive_only"

    u1 = client.post(f"/api/v1/workspace/cases/{chan['id']}/undo/{c1['trace']['id']}").json()
    assert u1["status"] == "undone"
    assert u1["case_state"] == "needs_decision"

    # Re-commit cleanly & run Loop 4 progression tick (Nurse Rachel completes call -> Scheduled)
    client.post(f"/api/v1/workspace/cases/{chan['id']}/commit", json={"instruction_text": instr})
    t_nurse = client.post(f"/api/v1/workspace/cases/{chan['id']}/simulate-tick", json={"event_kind": "nurse_completed"}).json()
    assert t_nurse["state"] == "scheduled"

    # Loop 4: BP threshold breach (>140/90) wakes Scheduled Wong Mei Ling into Needs Decision
    t_bp = client.post(f"/api/v1/workspace/cases/{wong['id']}/simulate-tick", json={"event_kind": "bp_threshold_breach"}).json()
    assert t_bp["state"] == "needs_decision" and t_bp["baton"] == "with_doctor"

    # Loop 4: Low-risk patient stall routes to Nurse Rachel (0 doctor interruption)
    t_stall = client.post(f"/api/v1/workspace/cases/{lee['id']}/simulate-tick", json={"event_kind": "stall_timeout"}).json()
    assert t_stall["state"] == "running" and t_stall["baton"] == "with_nurse"

    # Loop 5: Lead GP approves Curator proposal -> publishes Practice Graph vNext + Locum Q&A
    prop = ov["curator_proposals"][0]
    dec = client.post(f"/api/v1/curator/proposals/{prop['id']}/decide", json={"decision": "approve"}).json()
    assert dec["status"] == "approved"
    assert dec["graph"]["version_tag"] == "v2.5"

    qa = client.post("/api/v1/graph/query", json={"question": "How do we handle QEH Warfarin discharges?"}).json()
    assert qa["graph_version_tag"] == "v2.5"
    assert len(qa["matched_nodes"]) >= 1

    # Structured clinical widgets + Preventive Care + Presentation Linter (5/5) + Unified Thread /ask
    wingyan = next(c for c in ov["cases"] if c["case_key"] == "wingyan")
    assert wingyan["ai_title"] == "Acute Viral URTI & Non-Anaemic Iron Deficiency"
    assert len(wingyan["summary"]["issues"]) == 2
    assert len(chan["summary"]["issues"]) == 2  # AFib/Warfarin and Dyslipidaemia/Statin cleanly split
    assert len(wingyan["summary"]["preventive_care"]) == 2
    assert len(wingyan["summary"]["lab_panel"]["rows"]) >= 6
    assert len(chan["summary"]["lab_panel"]["rows"]) >= 4
    assert chan["summary"]["trend_chart"]["kind"] == "inr"
    assert len(chan["summary"]["clinical_scores"]) == 2
    assert len(chan["summary"]["imaging_studies"]) == 1
    assert len(ov["practice_graph"]["widget_catalog"]) == 14
    # Strict high-signal Sam gate: routine on-target/waiting cases stay quiet
    assert wong["summary"]["sam_proactive_insight"] is None
    assert lee["summary"]["sam_proactive_insight"] is None
    # Every seeded case must pass all 5 Clinical UI Design Guide linter invariants
    for c in ov["cases"]:
        assert c["lint_report"]["passed"] is True, f"{c['case_key']} failed presentation lint: {c['lint_report']['violations']}"
        assert c["lint_report"]["rules_passed"] == 5

    ask_res = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/ask",
        json={"instruction_text": "Did she mention heavy periods or GI symptoms?"},
    )
    assert ask_res.status_code == 200
    assert "GI red flags" in ask_res.json()["answer"]


def test_clinical_presentation_linter_rejects_slop_regressions():
    """Verify the Deterministic Clinical Presentation Linter catches all 5 slop failure modes."""
    from backend.acute.practice_graph import validate_case_presentation

    sloppy_case = {
        "trend_chart": {"points": [{"label": "14 Sep", "v": 2.9}, {"label": "21 Nov", "v": 2.4}]},
        "lab_panel": {"rows": [{"test": "eGFR", "val": "71 mL/min"}]},
        "issues": [
            {
                "name": "1. Post-Discharge Anticoagulation & Lipid Reconciliation (AFib)",
                "reasoning": "Clinic INR was 2.9 on Warfarin 5mg; QEH reduced to 3mg achieving INR 2.4 (eGFR 71).",
                "assessment": "Reconcile EMR active list, confirm home pillbox in Cantonese.",
                "bullets": ["Clinic INR 2.9 fell to 2.4 with eGFR 71."],
                "suggested_orders": [
                    {"id": "bad_rx", "type": "rx", "drug": "Warfarin 3mg + Atorvastatin 20mg"},
                    {"id": "bad_kind", "type": "freeform_prose", "text": "Do something"},
                ],
            }
        ],
        "sam_proactive_insight": "Pulled his Sep clinic INR and also spotted a few things to look at.",
    }
    res = validate_case_presentation(sloppy_case)
    assert res["passed"] is False
    violated_rules = {v["rule"] for v in res["violations"]}
    assert "R1_NO_WIDGET_DUPLICATION" in violated_rules
    assert "R2_ATOMIC_ISSUES_AND_ORDERS" in violated_rules
    assert "R3_NO_SLOP_OR_SCAFFOLDING" in violated_rules
    assert "R4_CANONICAL_ACTION_ONTOLOGY" in violated_rules
    assert "R5_HIGH_SIGNAL_SAM_GATE" in violated_rules


def test_seam_c_confirm_cds_422_hard_block_and_overridable_conflict(tmp_path):
    """Verify /confirm enforces deterministic rx_rules 422 hard-block (Cap. 134) and overridable G6PD conflict."""
    client = _make_client(tmp_path)
    ov = client.post("/api/v1/workspace/reset").json()
    wingyan = next(c for c in ov["cases"] if c["case_key"] == "wingyan")

    # 1. HK Cap. 134 Dangerous Drug (Codeine) -> HTTP 422 hard_block (can_override: False)
    r_cap134 = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/confirm",
        json={
            "orders": [
                {"id": "rx_bad_cod", "type": "rx", "action": "start", "drug": "Codeine 15mg", "freq": "TDS", "days": "3d"}
            ],
            "override_reason": "Even a long documented reason cannot override Cap. 134 dangerous drugs",
        },
    )
    assert r_cap134.status_code == 422
    j_cod = r_cap134.json()
    assert j_cod["error_code"] == "RX_HARD_BLOCK"
    assert j_cod["can_override"] is False
    assert any(f["code"] == "cap134_dangerous_drug" for f in j_cod["cds_report"]["hard_blocks"])

    # 2. G6PD Aspirin conflict without override -> HTTP 422 override_required (can_override: True)
    r_g6pd = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/confirm",
        json={
            "orders": [
                {"id": "rx_bad_asa", "type": "rx", "action": "start", "drug": "Aspirin 300mg", "freq": "TDS", "days": "3d"}
            ],
        },
    )
    assert r_g6pd.status_code == 422
    j_g6pd = r_g6pd.json()
    assert j_g6pd["error_code"] == "RX_OVERRIDE_REQUIRED"
    assert j_g6pd["can_override"] is True
    assert any(f["code"] == "g6pd_contraindication" for f in j_g6pd["cds_report"]["overrides_required"])

    # 3. Same G6PD Aspirin order WITH valid >=10 char documented override succeeds cleanly
    r_g6pd_ok = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/confirm",
        json={
            "orders": [
                {"id": "rx_ok_asa", "type": "rx", "action": "start", "drug": "Aspirin 300mg", "freq": "OD", "days": "3d"}
            ],
            "override_reason": "Specialist haematology clearance documented for low-dose indication.",
        },
    )
    assert r_g6pd_ok.status_code == 200
    assert len(r_g6pd_ok.json()["prescriptions"]) == 1


def test_seam_c_live_closure_vault_mchk_pdf_emr_addenda_live_bus_zero_sam_leak_and_undo(tmp_path):
    """End-to-end /workspace -> Vault MCHK PDF -> /emr addendum -> /p/qr live_bus push -> zero Sam leak -> 10s Undo."""
    import pytest
    from backend.acute.emr_connector import assert_zero_sam_chat_leakage

    client = _make_client(tmp_path)
    ov = client.post("/api/v1/workspace/reset").json()
    assert "emr_bridge" in ov["xray"]
    assert ov["xray"]["emr_bridge"]["zero_sam_chat_leakage_enforced"] is True

    wingyan = next(c for c in ov["cases"] if c["case_key"] == "wingyan")

    # 1. Doctor asks ephemeral private Sam Q&A (MUST NEVER leak into EMR addendum!)
    secret_q = "Private check: did Wing-Yan mention heavy menstrual periods or melena?"
    ask_res = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/ask",
        json={"instruction_text": secret_q},
    )
    assert ask_res.status_code == 200
    ask_body = ask_res.json()
    assert ask_body["ephemeral_private"] is True
    assert ask_body["written_to_emr"] is False

    # Unit guard check: assert_zero_sam_chat_leakage raises ValueError on leaked private Sam text
    with pytest.raises(ValueError, match="EMR additive invariant violation"):
        assert_zero_sam_chat_leakage(
            [f"Plan note including {ask_body['answer']}"],
            ask_body["agent_thread"],
        )

    # 2. Confirm & Sign Plan with Rx + 2-day MCHK Cert (omit_employer_diagnosis: true) + Flu Vax Booking
    confirm_res = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/confirm",
        json={
            "orders": [
                {"id": "wy_cert", "type": "cert", "issue": "1. Acute viral URTI", "days_off": 2, "omit_employer_diagnosis": True},
                {"id": "wy_rx_para", "type": "rx", "action": "start", "issue": "1. Acute viral URTI", "drug": "Paracetamol 500mg", "freq": "QDS PRN", "days": "5d"},
                {"id": "wy_book_flu", "type": "book", "issue": "Preventive Care", "visit_type": "10-min Nurse Flu Shot ($0)", "when": "In 14 days", "cvx_code": "150"},
            ],
            "actor_name": "Locum Dr. Sarah Wong",
            "mchk_reg_no": "M61042",
        },
    )
    assert confirm_res.status_code == 200
    body = confirm_res.json()

    # 3. Verify REAL encrypted MCHK PDF minted in DocumentVault with diagnosis omitted for employer
    assert len(body["certificates"]) == 1
    cert = body["certificates"][0]
    assert cert["days_off"] == 2
    assert cert["mchk_reg_no"] == "M61042"
    assert cert["omit_employer_diagnosis"] is True
    assert cert["download_token_endpoint"].endswith("/download-token")
    from backend.acute.seed import DEMO_TENANT_1
    pdf_bytes, meta = client.app.state.vault.retrieve(DEMO_TENANT_1, cert["vault_doc_id"])
    assert pdf_bytes.startswith(b"%PDF")
    assert b"Withheld at patient request" in pdf_bytes
    assert b"J06.9" not in pdf_bytes

    # 4. Verify FK-safe Prescription + Adapter dispatches (FluVax, Pharmacy, WhatsAppDoorbell)
    assert len(body["prescriptions"]) == 1
    assert body["prescriptions"][0]["mchk_reg_no"] == "M61042"
    assert len(body["adapter_dispatches"]["vaccinations"]) == 1
    assert len(body["adapter_dispatches"]["pharmacy_orders"]) == 1
    assert len(body["adapter_dispatches"]["whatsapp_doorbells"]) == 1
    assert body["adapter_dispatches"]["whatsapp_doorbells"][0]["leakage_passed"] is True

    # 5. Verify additive EMR addendum visible in `/api/v1/emr/overview` with ZERO private Sam chat leakage
    addendum = body["emr_addendum"]
    assert addendum["contains_private_sam_chat"] is False
    assert addendum["clipboard_snippet"]
    assert secret_q.lower() not in addendum["clipboard_snippet"].lower()
    assert ask_body["answer"].lower() not in addendum["clipboard_snippet"].lower()

    emr_ov = client.get("/api/v1/emr/overview?patient_id=pat_lam_urti_demo").json()
    assert any(a["addendum_id"] == addendum["addendum_id"] for a in emr_ov["emr_addenda"])
    lam_card = next(p for p in emr_ov["patients"] if p["id"] == "pat_lam_urti_demo")
    assert any(a["addendum_id"] == addendum["addendum_id"] for a in lam_card["emr_addenda"])

    # 6. Verify patient-scoped live_bus events pushed for `/p/qr/pat_lam_urti_demo`
    bus_events = client.app.state.live_bus.recent_events(patient_id="pat_lam_urti_demo", limit=30)
    ev_types = [e["type"] for e in bus_events]
    assert "workspace.committed" in ev_types
    assert "emr.mutated" in ev_types
    assert "patient.care_plan_delivered" in ev_types
    care_ev = next(e for e in reversed(bus_events) if e["type"] == "patient.care_plan_delivered")
    assert care_ev["data"]["contains_private_sam_chat"] is False
    assert len(care_ev["data"]["certificates"]) == 1

    # 7. Trigger 10-second Undo -> cancels outbox, retracts EMR addendum, cancels bookings & retracts staged artifacts
    trace_id = body["trace"]["id"]
    undo_res = client.post(f"/api/v1/workspace/cases/{wingyan['id']}/undo/{trace_id}")
    assert undo_res.status_code == 200
    u = undo_res.json()
    assert u["status"] == "undone"
    assert u["case_state"] == "needs_decision"
    assert u["outbox_cancelled"] is True
    assert u["emr_addendum_retracted"] is True
    assert u["retracted_addendum_id"] == addendum["addendum_id"]
    assert len(u["cancelled_bookings"]) >= 1
    assert len(u["retracted_artifacts"]) >= 1

    # Retracted addendum no longer appears in active /emr overview
    emr_after_undo = client.get("/api/v1/emr/overview?patient_id=pat_lam_urti_demo").json()
    assert not any(a["addendum_id"] == addendum["addendum_id"] for a in emr_after_undo["emr_addenda"])

    undo_ev_types = [e["type"] for e in client.app.state.live_bus.recent_events(patient_id="pat_lam_urti_demo", limit=30)]
    assert "workspace.undone" in undo_ev_types
    assert "patient.care_plan_retracted" in undo_ev_types


def test_seam_c_nurse_quarantine_resolve_compile_and_graph_rollback(tmp_path):
    """Verify Nurse Rachel quarantine resolution, /compile endpoint (with self-repair), and Practice Graph rollback."""
    client = _make_client(tmp_path)
    ov = client.post("/api/v1/workspace/reset").json()
    chan = next(c for c in ov["cases"] if c["case_key"] == "chan")

    # 1. Quarantine 2/3 mismatch doc -> Nurse Rachel resolves -> linked & document.resolved published
    ing = client.post(
        "/api/v1/workspace/documents/ingest",
        json={
            "external_doc_ref": "QEH-DC-RESOLVE-001",
            "doc_kind": "discharge_summary",
            "source_org": "Queen Elizabeth Hospital",
            "claimed_mrn_or_hkid": "PAT-62041",
            "claimed_name": "CHAN Tai Man",
            "claimed_dob": "1964-03-19",  # mismatch!
            "verbatim_quote": "Discharge INR 2.4 on 21/11/2026.",
            "page_ref": "p.2",
        },
    ).json()
    assert ing["match_status"] == "quarantined_nurse"

    res = client.post(
        f"/api/v1/workspace/documents/{ing['id']}/resolve",
        json={
            "action": "link_verified",
            "verified_patient_id": "pat_chan_warfarin",
            "verification_note": "Confirmed HKID & DOB directly with QEH medical records.",
            "resolved_by": "Nurse Rachel Chan",
        },
    )
    assert res.status_code == 200
    r_doc = res.json()
    assert r_doc["match_status"] == "nurse_verified_match"
    assert r_doc["matched_patient_id"] == "pat_chan_warfarin"

    ov_after = client.get("/api/v1/workspace/overview").json()
    assert not any(q["id"] == ing["id"] for q in ov_after["quarantined_documents"])
    ev_types = [e["type"] for e in client.app.state.live_bus.recent_events(limit=30)]
    assert "document.resolved" in ev_types

    # 2. On-demand /compile endpoint passes 5/5 linter within <4,096 token budget (even when repairing sloppy input)
    comp = client.post(
        f"/api/v1/workspace/cases/{chan['id']}/compile",
        json={
            "override_summary": {
                **chan["summary"],
                "sam_proactive_insight": "Just checking in — spotted a few things to look at!",
            }
        },
    )
    assert comp.status_code == 200
    c_body = comp.json()
    assert c_body["lint_report"]["passed"] is True
    assert c_body["lint_report"]["rules_passed"] == 5
    assert c_body["compilation_telemetry"]["within_budget"] is True
    assert c_body["compilation_telemetry"]["total_tokens_est"] < 4096
    assert c_body["compilation_telemetry"]["retry_count"] == 1

    # 3. Practice Graph versions & Lead GP rollback (locum blocked 422)
    onboard = client.post(
        "/api/v1/graph/onboard",
        json={"careline_profile_patch": {"name": "Maya"}, "change_summary": "Rename to Maya for test"},
    )
    assert onboard.status_code == 200
    assert onboard.json()["version_tag"] == "v2.5"

    vers = client.get("/api/v1/graph/versions").json()
    assert vers["count"] >= 2
    assert vers["versions"][0]["version_tag"] == "v2.5"

    # Locum rollback must be rejected 422
    rb_locum = client.post(
        "/api/v1/graph/rollback",
        json={"target_version_tag": "v2.4", "rolled_back_by": "Locum Dr. Sarah Wong"},
    )
    assert rb_locum.status_code == 422

    # Lead GP rollback restores v2.4 Careline profile ("Sam") as immutable v2.6
    rb_ok = client.post(
        "/api/v1/graph/rollback",
        json={
            "target_version_tag": "v2.4",
            "rolled_back_by": "Dr. Kenneth Lam (Lead GP)",
            "reason": "Revert Careline name back to Sam",
        },
    )
    assert rb_ok.status_code == 200
    rb_body = rb_ok.json()
    assert rb_body["version_tag"] == "v2.6"
    assert rb_body["careline_profile"]["name"] == "Sam"
    assert rb_body["rolled_back_to_source_version"] == "v2.4"


