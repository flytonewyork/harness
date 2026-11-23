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

    # Structured clinical widgets + Preventive Care + Proactive Ephemeral Registrar /ask
    wingyan = next(c for c in ov["cases"] if c["case_key"] == "wingyan")
    assert wingyan["ai_title"] == "Acute URTI & Low Ferritin — Sick Leave & Iron Review"
    assert len(wingyan["summary"]["issues"]) == 2
    assert len(wingyan["summary"]["preventive_care"]) == 2
    assert len(wingyan["summary"]["lab_panel"]["rows"]) >= 6
    assert len(chan["summary"]["lab_panel"]["rows"]) >= 4
    assert chan["summary"]["trend_chart"]["kind"] == "inr"

    ask_res = client.post(
        f"/api/v1/workspace/cases/{wingyan['id']}/ask",
        json={"instruction_text": "Did she mention heavy periods or GI symptoms?"},
    )
    assert ask_res.status_code == 200
    assert "GI red flags" in ask_res.json()["answer"]
