"""Clinical Execution Layer Orchestrator (Loops 1–5) over PostgreSQL FORCE RLS.

Unifies:
- 3-Layer Clinical Practice Knowledge Graph (`practice_graph.py`)
- Clinic Curator & Onboarding Moulding (`curator.py`)
- Additive-Only EMR Connector & Fail-Closed 3-Point ID Gate (`emr_connector.py`)
- Durable 4-State Case Loops (`needs_decision | running | scheduled | closed`) & Execution Traces

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .curator import (
    ensure_active_practice_graph,
    get_effective_practice_graph,
    list_curator_proposals,
    record_override_diff,
)
from .db import (
    AgentMemoryEntry,
    CaseLoop,
    CuratorProposal,
    Database,
    DocumentIngestion,
    ExecutionTrace,
    OutboxEvent,
    PracticeGraphVersion,
    new_id,
    record_audit,
    utcnow,
)
from .emr_connector import (
    ingest_external_document,
    list_quarantined_documents,
    write_additive_emr_addendum,
)
from .practice_graph import classify_inbound_triage, compile_instruction_plan
from .workflows import EVENT_ACTION_COMMIT_DISPATCH


def ensure_workspace_seeded(session: Session, tenant_id: str) -> None:
    """Seed the 4 canonical clinical cases, linked 3-pt ID docs, and compliance-gated Agent Memory."""
    graph = get_effective_practice_graph(session, tenant_id)
    existing = session.execute(
        select(CaseLoop).where(CaseLoop.tenant_id == tenant_id)
    ).scalars().first()
    if existing is not None:
        return

    now = utcnow()
    # 1. Deterministic 3-pt ID verified source documents
    qeh_doc = ingest_external_document(
        session,
        tenant_id=tenant_id,
        external_doc_ref="QEH-DC-2026-1122-8841",
        doc_kind="discharge_summary",
        source_org="Queen Elizabeth Hospital",
        claimed_mrn_or_hkid="PAT-62041",
        claimed_name="CHAN Tai Man",
        claimed_dob="1964-03-14",
        verbatim_quote="Discharge Medications (22/11/2026): 1. Warfarin 3 mg PO daily (reduced from 5 mg; INR 2.4 on 21/11). 2. Atorvastatin 20 mg PO nocte (switched from Simvastatin 20 mg). Next INR due 29/11/2026.",
        page_ref="p.2",
        parsed_diff={"emr_active": ["Warfarin 5mg daily", "Simvastatin 20mg nocte"], "qeh_discharge": ["Warfarin 3mg daily", "Atorvastatin 20mg nocte"]},
    )
    km_doc = ingest_external_document(
        session,
        tenant_id=tenant_id,
        external_doc_ref="KM-LAB-2026-1108-3319",
        doc_kind="lab_report",
        source_org="KingMed Diagnostics (HK)",
        claimed_mrn_or_hkid="PAT-32018",
        claimed_name="LAM Wing-Yan",
        claimed_dob="1994-04-18",
        verbatim_quote="Serum Ferritin: 14 ng/mL (Low; Ref 15–150) · Haemoglobin: 12.1 g/dL (Normal) · LDL-C: 2.3 mmol/L (Normal).",
        page_ref="p.1",
        parsed_diff={"ferritin_ng_ml": 14, "hb_g_dl": 12.1},
    )
    ldl_doc = ingest_external_document(
        session,
        tenant_id=tenant_id,
        external_doc_ref="KM-LAB-2026-1102-1904",
        doc_kind="lab_report",
        source_org="KingMed Diagnostics (HK)",
        claimed_mrn_or_hkid="PAT-36044",
        claimed_name="LEE Ming Kit",
        claimed_dob="1990-02-04",
        verbatim_quote="LDL Cholesterol: 3.6 mmol/L (Borderline; Ref < 3.4) · Triglycerides: 1.4 mmol/L (Normal).",
        page_ref="p.1",
        parsed_diff={"ldl_mmol_l": 3.6},
    )
    session.flush()

    # 2. Compliance-gated Agent Memory Store ("EMR for Agents")
    memories = [
        AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_chan_warfarin",
            tier="episodic", topic="qeh_discharge_medrec",
            summary_text="QEH reduced Warfarin 5mg → 3mg (INR 2.4) & switched Simvastatin → Atorvastatin; booked 11:30 visit today.",
            source_doc_id=qeh_doc.id, source_quote=qeh_doc.verbatim_quote,
            payload_json={"booked_visit": "Today 11:30"},
        ),
        AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_lam_urti_demo",
            tier="longitudinal", topic="ferritin_trend",
            summary_text="Ferritin trending down 32 (2024) → 21 (2025) → 14 ng/mL (8 Nov 2026); Hb normal 12.1; G6PD deficient + Penicillin allergy.",
            source_doc_id=km_doc.id, source_quote=km_doc.verbatim_quote,
            payload_json={"series": [{"label": "Nov 24", "v": 32}, {"label": "Nov 25", "v": 21}, {"label": "8 Nov 26", "v": 14}]},
        ),
        AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_wong_htn",
            tier="longitudinal", topic="bp_trajectory",
            summary_text="BP improved 152/94 (Sep) → 138/86 (Oct) → 129/79 7d home avg (Nov) on Amlodipine 5mg OD; goal < 135/85.",
            source_quote="7-day home average 129/79 mmHg (7/7 readings logged; 100% adherence).",
            payload_json={"series": [{"label": "12 Sep", "sys": 152, "dia": 94}, {"label": "18 Oct", "sys": 138, "dia": 86}, {"label": "Today", "sys": 129, "dia": 79}]},
        ),
        AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_lee_ldl",
            tier="episodic", topic="ldl_repeat_booking",
            summary_text="Borderline LDL 3.6 mmol/L; Sam confirming Fri 12 Feb 08:00 fasting blood draw.",
            source_doc_id=ldl_doc.id, source_quote=ldl_doc.verbatim_quote,
        ),
    ]
    for m in memories:
        session.add(m)

    # 3. 4-State Case Loops (Pre-determined Clinical Widgets: Direct Requests, Issues & Assessment, Full Labs, Side Alerts/Tasks)
    cases = [
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_chan_warfarin", patient_name="CHAN Tai Man", patient_age_sex="62M",
            case_key="chan", pathway_node_id="ebm.pathway.post_discharge_anticoag",
            graph_version_tag=graph["version_tag"],
            state="needs_decision", baton="with_doctor", risk_tier="high",
            ai_title="Post-QEH AFib Discharge — Warfarin & Statin Reconciliation",
            lead_line="QEH reduced Warfarin 5mg → 3mg (INR 2.4) and switched Simvastatin → Atorvastatin 20mg.",
            next_checkpoint_label="Today 11:30 visit",
            next_checkpoint_at=now + timedelta(hours=2),
            summary_json={
                "direct_requests": [
                    {
                        "id": "req_medrec",
                        "kind": "Medication Reconciliation",
                        "headline": "QEH lowered Warfarin 5mg → 3mg & switched Simvastatin → Atorvastatin",
                        "detail": "Confirm active regimen ahead of today's 11:30 visit (no drug altered autonomously)",
                        "action_label": "Adopt QEH Regimen",
                        "instruction": "Adopt QEH Warfarin 3mg daily and Atorvastatin 20mg nocte, stop Simvastatin 20mg, assign Nurse Rachel Cantonese pillbox call, and book INR on 29 Nov",
                    }
                ],
                "alerts": [
                    {"level": "high", "label": "Anticoagulation Dose Change", "detail": "Warfarin reduced 5mg → 3mg (INR 2.4 on 21 Nov)"},
                    {"level": "high", "label": "Duplicate Statin Risk", "detail": "Clinic active chart still lists Simvastatin 20mg alongside QEH Atorvastatin 20mg"},
                ],
                "scheduled_tasks": [
                    {"status": "staged", "label": "Repeat INR blood test · 29 Nov (7 days post-change)"},
                    {"status": "booked", "label": "Today 11:30 clinic follow-up · Patient bringing QEH pill bags"},
                ],
                "issues": [
                    {
                        "name": "1. Post-Discharge Medication Discrepancy (Atrial Fibrillation)",
                        "subjective": "Discharged from Queen Elizabeth Hospital yesterday following AFib rate-control admission. No bleeding or palpitations reported.",
                        "reasoning": "QEH Discharge Summary (p.2) reduces Warfarin 5mg → 3mg daily (INR 2.4 on 21 Nov) and switches Simvastatin 20mg → Atorvastatin 20mg nocte. Clinic chart last updated 14 Sep.",
                        "assessment": "High-risk medication discrepancy surfaced ahead of clinic visit and refill.",
                        "actions": [
                            {"label": "Adopt QEH 3mg + Atorvastatin", "instruction": "Adopt QEH Warfarin 3mg daily and Atorvastatin 20mg nocte, stop Simvastatin 20mg"},
                            {"label": "Assign Nurse Cantonese Pillbox Call", "instruction": "Assign Nurse Rachel a same-day Cantonese pillbox call and book 29 Nov INR"},
                            {"label": "Discuss at 11:30 Visit", "instruction": "Discuss Warfarin and statin changes during today's 11:30 booked visit"},
                        ],
                    }
                ],
                "attachments": [
                    {
                        "id": qeh_doc.id,
                        "label": "QEH Discharge Summary · 22 Nov 2026 (p.2)",
                        "quote": qeh_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "quick_questions": [
                    "What was his last clinic INR and renal function?",
                    "Has any refill been dispensed since discharge?",
                ],
                "agent_thread": [],
                "patient_chat_label": "Mr. Chan ↔ Sam Careline Chat (1 message)",
                "original_chat": [
                    {"actor": "Sam", "time": "08:30", "text": "Welcome home from QEH, Mr. Chan. Reminder of your follow-up visit today at 11:30 — please bring your hospital medication bags."}
                ],
            },
            timeline_json=[
                {"date": "22 Nov", "label": "Discharged from QEH (AFib) — Warfarin 5mg → 3mg, switched to Atorvastatin."},
                {"date": "Today 08:30", "label": "Discharge summary matched (3-pt ID); discrepancy surfaced ahead of 11:30 visit."},
            ],
            goal_json={},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_lam_urti_demo", patient_name="LAM Wing-Yan", patient_age_sex="32F",
            case_key="wingyan", pathway_node_id="ebm.pathway.low_ferritin_normal_hb",
            graph_version_tag=graph["version_tag"],
            state="needs_decision", baton="with_doctor", risk_tier="standard",
            ai_title="Acute URTI & Low Ferritin — Sick Leave & Iron Review",
            lead_line="Requesting 2-day sick leave for uncomplicated viral URTI (RAT neg) and iron advice for declining Ferritin (14 ng/mL, normal Hb).",
            next_checkpoint_label="Today 14:30 teleconsult",
            next_checkpoint_at=now + timedelta(hours=5),
            summary_json={
                "direct_requests": [
                    {
                        "id": "req_sl",
                        "kind": "Sick Leave Certificate",
                        "headline": "2-day medical certificate (23–24 Nov 2026)",
                        "detail": "Harbour FinTech corporate copy · Diagnosis omitted by clinic convention",
                        "action_label": "Approve 2-Day Sick Leave",
                        "instruction": "Approve and issue 2-day bilingual sick leave certificate (23–24 Nov 2026, omit employer diagnosis)",
                    }
                ],
                "alerts": [
                    {"level": "high", "label": "G6PD Deficient", "detail": "Avoid Aspirin, NSAIDs, sulfonamides, nitrofurantoin"},
                    {"level": "high", "label": "Penicillin Allergy", "detail": "Urticarial rash — avoid Amoxicillin / Augmentin"},
                ],
                "scheduled_tasks": [
                    {"status": "staged", "label": "Repeat Ferritin blood draw · Tue 9 Feb 08:30 (Nurse)"},
                    {"status": "deferred", "label": "Seasonal flu shot ($0) · Deferred 14 days while febrile"},
                ],
                "issues": [
                    {
                        "name": "1. Acute Viral URTI",
                        "subjective": "1-day history of sore throat, dry cough, and fever peaking at 38.2°C. Home COVID/Flu RAT negative.",
                        "red_flags": "Denies shortness of breath, chest pain, trouble swallowing, or neck stiffness.",
                        "assessment": "Uncomplicated viral URTI. No antibiotic indication.",
                        "actions": [
                            {"label": "Approve 2-Day Sick Leave", "instruction": "Approve and issue 2-day bilingual sick leave certificate (23–24 Nov 2026, omit employer diagnosis)"},
                            {"label": "Prescribe Paracetamol + Cetirizine", "instruction": "Prescribe Paracetamol 500mg QDS PRN and Cetirizine 10mg OD (G6PD-safe symptomatic relief)"},
                        ],
                    },
                    {
                        "name": "2. Non-Anaemic Iron Deficiency (Ferritin 14 ng/mL)",
                        "subjective": "Asymptomatic at recent annual checkup; asking whether to start oral iron or monitor.",
                        "reasoning": "Ferritin declined steadily over 2 years (32 → 21 → 14 ng/mL) while haemoglobin remains normal (12.1 g/dL), consistent with early iron depletion without anaemia.",
                        "assessment": "Mild low iron stores. Candidate for gentle alternate-day oral iron + 3-month repeat Ferritin.",
                        "actions": [
                            {"label": "Start Alternate-Day Oral Iron", "instruction": "Add gentle alternate-day oral iron to consult plan for Ferritin 14 ng/mL"},
                            {"label": "Order Feb Repeat Ferritin", "instruction": "Book Tue 9 Feb 08:30 morning nurse blood draw for repeat Ferritin"},
                        ],
                    },
                ],
                "lab_panel": {
                    "source": "KingMed Annual Blood Panel · 8 Nov 2026",
                    "rows": [
                        {"test": "Serum Ferritin", "val": "14 ng/mL", "ref": "15–150", "flag": "LOW"},
                        {"test": "Haemoglobin (Hb)", "val": "12.1 g/dL", "ref": "11.5–15.5", "flag": "Normal"},
                        {"test": "White Cell Count (WBC)", "val": "6.4 ×10⁹/L", "ref": "4.0–10.0", "flag": "Normal"},
                        {"test": "LDL Cholesterol", "val": "2.3 mmol/L", "ref": "< 2.6", "flag": "Normal"},
                        {"test": "Fasting Glucose", "val": "4.8 mmol/L", "ref": "3.9–5.5", "flag": "Normal"},
                        {"test": "eGFR (CKD-EPI)", "val": "104 mL/min", "ref": "> 90", "flag": "Normal"},
                    ],
                },
                "trend_chart": {
                    "kind": "ferritin",
                    "title": "Ferritin Trend (ng/mL)",
                    "unit": "ng/mL",
                    "target_label": "Lower ref 15",
                    "target_value": 15,
                    "points": [{"label": "Nov 24", "v": 32}, {"label": "Nov 25", "v": 21}, {"label": "Nov 26", "v": 14}],
                },
                "attachments": [
                    {
                        "id": km_doc.id,
                        "label": "KingMed Annual Blood Panel · 8 Nov 2026",
                        "quote": km_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "quick_questions": [
                    "Did she mention heavy periods or GI symptoms?",
                    "Has she taken Paracetamol yet today?",
                ],
                "agent_thread": [],
                "patient_chat_label": "Wing-Yan ↔ Sam Careline Chat (3 messages)",
                "original_chat": [
                    {"actor": "LAM Wing-Yan", "time": "09:12", "text": "Woke up with sore throat & 38.2°C fever — can I start iron for Ferritin 14 too, and get a 2-day sick note?"},
                    {"actor": "Sam", "time": "09:12", "text": "Reminder from your file: avoid Aspirin (G6PD). Here are open teleconsult and clinic slots with Dr. Sarah Wong today so both can be reviewed together."},
                    {"actor": "LAM Wing-Yan", "time": "09:14", "text": "Booked 14:30 teleconsult today."}
                ],
            },
            timeline_json=[
                {"date": "8 Nov", "label": "Annual checkup panel: Ferritin 14 ng/mL (low), Hb 12.1 (normal)."},
                {"date": "Today 09:14", "label": "Booked 14:30 teleconsult; clinical presentation prepped for Locum Dr. Sarah."},
            ],
            goal_json={},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_wong_htn", patient_name="WONG Mei Ling", patient_age_sex="58F",
            case_key="wong", pathway_node_id="ebm.pathway.chronic_htn_home_bp",
            graph_version_tag=graph["version_tag"],
            state="scheduled", baton="idle", risk_tier="low",
            ai_title="Hypertension, Amlodipine — Routine Follow-Up 21 Dec",
            lead_line="7-day home BP averaged 129/79 mmHg on Amlodipine 5mg OD (within <135/85 target).",
            next_checkpoint_label="21 Dec review",
            next_checkpoint_at=now + timedelta(days=21),
            summary_json={
                "direct_requests": [],
                "alerts": [
                    {"level": "info", "label": "BP Goal: < 135/85 mmHg", "detail": "Current 7-day home average 129/79 (On target)"},
                ],
                "scheduled_tasks": [
                    {"status": "booked", "label": "Mon 21 Dec 10:00 · Routine GP Hypertension Review"},
                    {"status": "auto", "label": "Mon 14 Dec · Collect 7-day home BP log (wakes doctor only if > 140/90)"},
                ],
                "issues": [
                    {
                        "name": "1. Essential Hypertension on Amlodipine 5mg OD",
                        "subjective": "Asymptomatic, 100% medication adherence, denies dizziness or ankle swelling.",
                        "reasoning": "Steady 3-month blood pressure response: 152/94 (Sep clinic) → 138/86 (Oct home avg) → 129/79 (Nov 7-day avg, 7/7 logged).",
                        "assessment": "Well controlled within target (< 135/85 mmHg). Continue Amlodipine 5mg OD.",
                        "actions": [
                            {"label": "Tighten Goal to <130/80", "instruction": "Tighten BP target goal to < 130/80 mmHg on summary & patient log"},
                            {"label": "Order Pre-Visit Renal Bloods", "instruction": "Order renal function & electrolytes blood draw ahead of 21 Dec review"},
                            {"label": "Nurse Follow-Up Call", "instruction": "Assign Nurse Rachel a 5-min call to check home cuff technique"},
                        ],
                    }
                ],
                "trend_chart": {
                    "kind": "bp",
                    "title": "BP Trajectory (mmHg)",
                    "unit": "mmHg",
                    "target_label": "Target < 135/85",
                    "target_sys": 135,
                    "points": [
                        {"label": "12 Sep", "sys": 152, "dia": 94},
                        {"label": "18 Oct", "sys": 138, "dia": 86},
                        {"label": "Today", "sys": 129, "dia": 79},
                    ],
                },
                "attachments": [
                    {
                        "id": "att_bp_nov",
                        "label": "7-Day Home BP Log · Nov 2026",
                        "quote": "7-day home average: 129/79 mmHg (7/7 logged; range 124/76–134/82).",
                        "verified_3pt": True,
                    }
                ],
                "quick_questions": [
                    "When were her last renal electrolytes checked?",
                    "Any home readings over 140 systolic this week?",
                ],
                "agent_thread": [],
                "patient_chat_label": "Ms. Wong ↔ Sam Careline Chat (3 messages)",
                "original_chat": [
                    {"actor": "Sam", "time": "08:15", "text": "Good morning Ms Wong — Day 7 of your home BP check. What was this morning's reading?"},
                    {"actor": "WONG Mei Ling", "time": "08:22", "text": "128 / 78, feeling great."},
                    {"actor": "Sam", "time": "08:22", "text": "7-day average is 129/79 (inside your <135/85 goal). Your routine review stays set for Mon 21 Dec 10:00."},
                ],
            },
            timeline_json=[
                {"date": "12 Sep", "label": "Clinic visit (Dr. Lam): BP 152/94 — started Amlodipine 5mg OD."},
                {"date": "18 Oct", "label": "Month-1 home BP log: 7d avg 138/86, no side effects."},
                {"date": "Today", "label": "7d home BP avg 129/79 (on target). Routine review booked 21 Dec."},
            ],
            goal_json={"metric": "Blood Pressure", "target": "< 135/85 mmHg", "current": "129/79 mmHg", "status": "on_track"},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_lee_ldl", patient_name="LEE Ming Kit", patient_age_sex="36M",
            case_key="lee", pathway_node_id="ebm.pathway.borderline_ldl",
            graph_version_tag=graph["version_tag"],
            state="running", baton="with_patient", risk_tier="low",
            ai_title="Borderline Hyperlipidaemia (LDL 3.6) — Repeat Lipid Booking",
            lead_line="Waiting on patient booking for 3-month fasting lipid blood draw.",
            next_checkpoint_label="Pending booking",
            next_checkpoint_at=now + timedelta(days=2),
            summary_json={
                "waiting_banner": {
                    "label": "Waiting on patient booking",
                    "sub": "Fri 12 Feb 08:00 fasting blood draw offered · Auto-chases after 3 days",
                },
                "direct_requests": [],
                "alerts": [
                    {"level": "info", "label": "No Drug Allergies", "detail": "No G6PD or medication alerts on file"},
                ],
                "scheduled_tasks": [
                    {"status": "waiting", "label": "Pending booking · 3-month fasting lipid panel (Nurse)"},
                    {"status": "auto", "label": "Auto-chase timer · Nudges patient after 3 days if unbooked"},
                ],
                "issues": [
                    {
                        "name": "1. Borderline LDL Cholesterol (3.6 mmol/L)",
                        "subjective": "Asymptomatic executive checkup. Advised 3-month dietary and exercise trial before reassessing statin need.",
                        "reasoning": "LDL mildly elevated (3.6 mmol/L; ref < 3.4) with normal triglycerides (1.4) and HDL (1.5). Clinic standard is a 3-month fasting repeat lipid panel.",
                        "assessment": "No doctor input required while awaiting patient booking confirmation.",
                        "actions": [
                            {"label": "Nurse to Chase", "instruction": "Assign Nurse Rachel to chase LEE Ming Kit for his 3-month fasting lipid blood draw booking"},
                            {"label": "Add Post-Lab GP Review", "instruction": "Book 10-min GP results review 1 week after the Feb fasting lipid draw"},
                        ],
                    }
                ],
                "lab_panel": {
                    "source": "KingMed Lipid Panel · 2 Nov 2026",
                    "rows": [
                        {"test": "LDL Cholesterol", "val": "3.6 mmol/L", "ref": "< 3.4", "flag": "HIGH"},
                        {"test": "HDL Cholesterol", "val": "1.5 mmol/L", "ref": "> 1.0", "flag": "Normal"},
                        {"test": "Triglycerides", "val": "1.4 mmol/L", "ref": "< 1.7", "flag": "Normal"},
                        {"test": "Fasting Glucose", "val": "5.0 mmol/L", "ref": "3.9–5.5", "flag": "Normal"},
                    ],
                },
                "attachments": [
                    {
                        "id": ldl_doc.id,
                        "label": "KingMed Lipid Panel · 2 Nov 2026",
                        "quote": ldl_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "quick_questions": [
                    "What was his calculated 10-year CV risk?",
                    "When does the automatic chase trigger?",
                ],
                "agent_thread": [],
                "patient_chat_label": "Mr. Lee ↔ Sam Careline Chat (1 message)",
                "original_chat": [
                    {"actor": "Sam", "time": "08:40", "text": "Hi Mr. Lee — ready to book your 3-month fasting lipid blood draw? Tap to confirm Fri 12 Feb at 08:00 (10-min nurse visit)."}
                ],
            },
            timeline_json=[
                {"date": "2 Nov", "label": "Lipid panel: LDL 3.6 mmol/L; lifestyle + 3-month repeat advised."},
                {"date": "Today 08:40", "label": "Fasting blood-draw slot offered — waiting on patient booking."},
            ],
            goal_json={"metric": "LDL-C", "target": "< 3.4 mmol/L", "current": "3.6 mmol/L", "status": "monitoring"},
        ),
    ]
    for c in cases:
        session.add(c)
    session.flush()


def _serialize_trace(t: ExecutionTrace) -> dict[str, Any]:
    now = utcnow()
    can_undo = (
        t.status == "undo_window"
        and t.undo_expires_at is not None
        and t.undo_expires_at > now
    )
    return {
        "id": t.id,
        "case_loop_id": t.case_loop_id,
        "actor_name": t.actor_name,
        "instruction_text": t.instruction_text,
        "graph_version_tag": t.graph_version_tag,
        "traversed_nodes": t.traversed_nodes_json,
        "categorised_plan": t.categorised_plan_json,
        "requires_irreversible_commit": t.requires_irreversible_commit,
        "status": "dispatched" if (t.status == "undo_window" and not can_undo) else t.status,
        "can_undo": can_undo,
        "undo_seconds_left": max(0, int((t.undo_expires_at - now).total_seconds())) if can_undo and t.undo_expires_at else 0,
        "doctor_outcome_summary": t.doctor_outcome_summary,
        "folded_steps": t.folded_steps_json,
        "emr_addendum": t.emr_addendum_json,
        "context_snapshot": t.context_snapshot_json,
        "created_at": t.created_at.isoformat(),
    }


def _serialize_case(c: CaseLoop, traces: list[ExecutionTrace]) -> dict[str, Any]:
    return {
        "id": c.id,
        "patient_id": c.patient_id,
        "patient_name": c.patient_name,
        "patient_age_sex": c.patient_age_sex,
        "case_key": c.case_key,
        "pathway_node_id": c.pathway_node_id,
        "graph_version_tag": c.graph_version_tag,
        "state": c.state,
        "baton": c.baton,
        "risk_tier": c.risk_tier,
        "ai_title": c.ai_title,
        "lead_line": c.lead_line,
        "retry_count": c.retry_count,
        "next_checkpoint_label": c.next_checkpoint_label,
        "summary": c.summary_json,
        "timeline": c.timeline_json,
        "goal": c.goal_json,
        "traces": [_serialize_trace(t) for t in traces],
        "updated_at": c.updated_at.isoformat(),
    }


def build_workspace_overview(db: Database, tenant_id: str) -> dict[str, Any]:
    """Return full Clinic Workspace state + Head-of-AI Technical X-Ray payload."""
    with db.tenant_session(tenant_id) as session:
        ensure_active_practice_graph(session, tenant_id)
        ensure_workspace_seeded(session, tenant_id)
        graph = get_effective_practice_graph(session, tenant_id)
        cases = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id).order_by(CaseLoop.updated_at.desc())
        ).scalars().all()
        traces = session.execute(
            select(ExecutionTrace).where(ExecutionTrace.tenant_id == tenant_id).order_by(ExecutionTrace.created_at.desc())
        ).scalars().all()
        memories = session.execute(
            select(AgentMemoryEntry).where(AgentMemoryEntry.tenant_id == tenant_id).order_by(AgentMemoryEntry.created_at.desc()).limit(20)
        ).scalars().all()
        quarantined = list_quarantined_documents(session, tenant_id)
        proposals = list_curator_proposals(session, tenant_id)

        traces_by_case: dict[str, list[ExecutionTrace]] = {}
        for t in traces:
            traces_by_case.setdefault(t.case_loop_id, []).append(t)

        serialized_cases = [_serialize_case(c, traces_by_case.get(c.id, [])) for c in cases]
        # Deterministic display order inside sections
        order = {"chan": 0, "wingyan": 1, "lee": 2, "wong": 3}
        serialized_cases.sort(key=lambda x: order.get(x["case_key"], 99))

        grouped = {
            "needs_decision": [c for c in serialized_cases if c["state"] == "needs_decision"],
            "running": [c for c in serialized_cases if c["state"] == "running"],
            "scheduled": [c for c in serialized_cases if c["state"] == "scheduled"],
            "closed": [c for c in serialized_cases if c["state"] == "closed"],
        }

        # Selective # clinic-team handoffs (ONLY explicit delegated nurse tasks from committed traces + nurse quarantine alerts)
        team_handoffs: list[dict[str, Any]] = []
        for t in traces:
            if t.status == "undone":
                continue
            for item in (t.categorised_plan_json or {}).get("admin_booking", []):
                if "nurse" in item.get("text", "").lower() or "rachel" in item.get("text", "").lower():
                    c_match = next((c for c in cases if c.id == t.case_loop_id), None)
                    team_handoffs.append({
                        "id": f"th_{t.id}",
                        "trace_id": t.id,
                        "case_id": t.case_loop_id,
                        "patient_name": c_match.patient_name if c_match else "",
                        "route": f"{t.actor_name} → {graph['careline_profile']['lead_nurse']}",
                        "task": item["text"],
                        "status": "done" if (c_match and c_match.state in ("scheduled", "closed")) else "pending_nurse",
                        "created_at": t.created_at.isoformat(),
                    })
        for q in quarantined:
            team_handoffs.append({
                "id": f"qr_{q['id']}",
                "doc_id": q["id"],
                "route": "3-Point ID Gate → Nurse Rachel (Quarantine)",
                "task": f"Manual ID verification required: {q['external_doc_ref']} ({q['source_org']}) — {q['checks'].get('points_matched', 0)}/3 matched",
                "status": "quarantined_nurse",
                "created_at": q["created_at"],
            })

        emr_addenda = [
            {
                "trace_id": t.id,
                "case_loop_id": t.case_loop_id,
                "actor": t.actor_name,
                "addendum": t.emr_addendum_json,
                "created_at": t.created_at.isoformat(),
            }
            for t in traces
            if t.status != "undone" and (t.emr_addendum_json or {}).get("entries")
        ]

        latest_context = traces[0].context_snapshot_json if traces else None

        return {
            "practice_graph": graph,
            "careline_profile": graph["careline_profile"],
            "cases": serialized_cases,
            "grouped": grouped,
            "counts": {k: len(v) for k, v in grouped.items()},
            "team_handoffs": team_handoffs,
            "quarantined_documents": quarantined,
            "curator_proposals": proposals,
            "xray": {
                "dual_store": {
                    "clinical_sot": "Central Medical EMR (Sole SoT · Additive-Only Connector)",
                    "agent_memory_store": f"PostgreSQL 15 FORCE RLS ({len(memories)} longitudinal entries)",
                    "emr_addenda_log": emr_addenda,
                },
                "agent_memory": [
                    {
                        "id": m.id,
                        "patient_id": m.patient_id,
                        "tier": m.tier,
                        "topic": m.topic,
                        "summary": m.summary_text,
                        "source_quote": m.source_quote,
                        "consent_scope": m.consent_scope,
                    }
                    for m in memories
                ],
                "latest_context_window": latest_context,
                "persistence": {
                    "database": "PostgreSQL 15 + FORCE ROW LEVEL SECURITY (clinic_app NOBYPASSRLS)",
                    "durable_loops": "Transactional Outbox (outbox_events, Temporal-swappable)",
                    "model_region": "Vertex AI asia-east2 (Hong Kong) via GCP ADC",
                },
            },
        }


def preview_case_instruction(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    instruction_text: str,
) -> dict[str, Any]:
    """Loop 3 Preview: traverse Practice Graph & compile categorised plan without mutating state."""
    with db.tenant_session(tenant_id) as session:
        ensure_workspace_seeded(session, tenant_id)
        graph = get_effective_practice_graph(session, tenant_id)
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is None:
            raise ValueError("Case not found.")
        mems = session.execute(
            select(AgentMemoryEntry)
            .where(AgentMemoryEntry.tenant_id == tenant_id, AgentMemoryEntry.patient_id == case.patient_id)
            .order_by(AgentMemoryEntry.created_at.desc())
        ).scalars().all()
        mem_dicts = [{"tier": m.tier, "topic": m.topic, "summary_text": m.summary_text, "source_quote": m.source_quote} for m in mems]
        plan = compile_instruction_plan(
            case_key=case.case_key,
            pathway_node_id=case.pathway_node_id,
            instruction_text=instruction_text,
            effective_graph=graph,
            patient_name=case.patient_name,
            agent_memory_entries=mem_dicts,
        )
        plan["case_id"] = case.id
        return plan


def commit_case_instruction(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    instruction_text: str,
    actor_id: str = "doc_locum_sarah",
    actor_name: str = "Locum Dr. Sarah Wong",
) -> dict[str, Any]:
    """Loop 3 Commit: persist ExecutionTrace (10s Undo for irreversible), transition Loop 4 state, write additive EMR addendum, notify Curator."""
    with db.tenant_session(tenant_id) as session:
        ensure_workspace_seeded(session, tenant_id)
        graph = get_effective_practice_graph(session, tenant_id)
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is None:
            raise ValueError("Case not found.")

        mems = session.execute(
            select(AgentMemoryEntry)
            .where(AgentMemoryEntry.tenant_id == tenant_id, AgentMemoryEntry.patient_id == case.patient_id)
            .order_by(AgentMemoryEntry.created_at.desc())
        ).scalars().all()
        mem_dicts = [{"tier": m.tier, "topic": m.topic, "summary_text": m.summary_text, "source_quote": m.source_quote} for m in mems]

        plan = compile_instruction_plan(
            case_key=case.case_key,
            pathway_node_id=case.pathway_node_id,
            instruction_text=instruction_text,
            effective_graph=graph,
            patient_name=case.patient_name,
            agent_memory_entries=mem_dicts,
        )

        now = utcnow()
        irreversible = bool(plan["requires_irreversible_commit"])
        undo_sec = int(plan["undo_window_seconds"]) if irreversible else 0
        undo_exp = now + timedelta(seconds=undo_sec) if undo_sec > 0 else None

        emr_add = {}
        if plan["emr_addendum"]["entries"]:
            emr_add = write_additive_emr_addendum(
                session,
                tenant_id=tenant_id,
                patient_id=case.patient_id,
                signed_by=actor_name,
                entries=plan["emr_addendum"]["entries"],
            )

        trace = ExecutionTrace(
            id=new_id("trc"),
            tenant_id=tenant_id,
            case_loop_id=case.id,
            actor_id=actor_id,
            actor_name=actor_name,
            instruction_text=instruction_text,
            graph_version_tag=graph["version_tag"],
            traversed_nodes_json=[n["id"] for n in plan["traversed_nodes"]],
            categorised_plan_json=plan["categorised_plan"],
            requires_irreversible_commit=irreversible,
            status="undo_window" if irreversible else "dispatched",
            undo_expires_at=undo_exp,
            doctor_outcome_summary=plan["doctor_outcome_summary"],
            folded_steps_json=plan["folded_steps"],
            emr_addendum_json=emr_add,
            context_snapshot_json=plan["context_snapshot"],
        )
        session.add(trace)
        session.flush()

        # Enqueue durable dispatch timer for irreversible 10s Undo window
        if irreversible and undo_exp is not None:
            session.add(
                OutboxEvent(
                    id=new_id("out"),
                    tenant_id=tenant_id,
                    event_type=EVENT_ACTION_COMMIT_DISPATCH,
                    aggregate_id=trace.id,
                    payload_json={"trace_id": trace.id, "case_loop_id": case.id},
                    run_after=undo_exp,
                    idempotency_key=f"{EVENT_ACTION_COMMIT_DISPATCH}:{tenant_id}:{trace.id}",
                )
            )

        # Loop 4 state transition: Needs Decision -> Running (with clear baton!) or Scheduled
        if plan["nurse_handoff"]:
            case.state = "running"
            case.baton = "with_nurse"
        elif plan["categorised_plan"]["patient_task"]:
            case.state = "running"
            case.baton = "with_patient"
        else:
            case.state = "scheduled"
            case.baton = "idle"

        # Update Wong goal if tightened
        if case.case_key == "wong" and "130" in instruction_text:
            goal = copy.deepcopy(case.goal_json or {})
            goal["target"] = "< 130/80 mmHg"
            case.goal_json = goal
            flag_modified(case, "goal_json")

        # Append longitudinal outcome card entry to Timeline (NEVER a messy tool-call dump!)
        tl = list(case.timeline_json or [])
        tl.append({
            "date": "Just now",
            "label": plan["doctor_outcome_summary"],
            "trace_id": trace.id,
            "actor": actor_name,
            "folded_step_count": len(plan["folded_steps"]),
        })
        case.timeline_json = tl
        case.updated_at = now
        flag_modified(case, "timeline_json")

        # Record override diff into Clinic Curator if doctor customized standard pathway
        curator_meta = None
        if plan["override_detected"]:
            curator_meta = record_override_diff(
                session,
                tenant_id=tenant_id,
                patient_id=case.patient_id,
                case_key=case.case_key,
                target_node_id=case.pathway_node_id,
                doctor_name=actor_name,
                instruction_text=instruction_text,
            )

        session.flush()
        return {
            "trace": _serialize_trace(trace),
            "case_state": case.state,
            "case_baton": case.baton,
            "curator_override_logged": curator_meta,
        }


def undo_case_execution(db: Database, *, tenant_id: str, case_id: str, trace_id: str) -> dict[str, Any]:
    """Cancel an irreversible execution trace inside its 10-second Undo window and restore Needs Decision."""
    with db.tenant_session(tenant_id) as session:
        trace = session.execute(
            select(ExecutionTrace).where(
                ExecutionTrace.tenant_id == tenant_id,
                ExecutionTrace.id == trace_id,
                ExecutionTrace.case_loop_id == case_id,
            )
        ).scalars().first()
        if trace is None:
            raise ValueError("Execution trace not found.")
        if trace.status != "undo_window" or (trace.undo_expires_at and trace.undo_expires_at <= utcnow()):
            raise ValueError("10-second Undo window has expired; action already dispatched.")
        trace.status = "undone"
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is not None:
            case.state = "needs_decision"
            case.baton = "with_doctor"
            case.timeline_json = [item for item in (case.timeline_json or []) if item.get("trace_id") != trace.id]
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role="clinician",
            actor_id=trace.actor_id,
            action="action.commit_undone",
            resource_type="execution_trace",
            resource_id=trace.id,
            metadata={"case_loop_id": case_id},
        )
        session.flush()
        return {"status": "undone", "trace_id": trace.id, "case_state": "needs_decision"}


def simulate_loop_progression(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    event_kind: str,
) -> dict[str, Any]:
    """Live Loop 4 demo simulator: nurse_completed | patient_confirmed | bp_threshold_breach | stall_timeout | close_loop."""
    with db.tenant_session(tenant_id) as session:
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is None:
            raise ValueError("Case not found.")

        tl = list(case.timeline_json or [])
        if event_kind == "nurse_completed":
            case.state = "scheduled"
            case.baton = "idle"
            tl.append({"date": "Update", "label": "Nurse Rachel completed call — loop moved cleanly to Scheduled."})
        elif event_kind == "patient_confirmed":
            case.state = "scheduled"
            case.baton = "idle"
            tl.append({"date": "Update", "label": "Patient confirmed booking — loop moved automatically to Scheduled."})
        elif event_kind == "bp_threshold_breach":
            case.state = "needs_decision"
            case.baton = "with_doctor"
            case.lead_line = "7-day home BP jumped to 146/92 mmHg (above >140/90 Practice Graph trigger); review Amlodipine titration or invite paid review."
            summary = copy.deepcopy(case.summary_json or {})
            if summary.get("trend_chart", {}).get("points"):
                summary["trend_chart"]["points"].append({"label": "New 7d", "sys": 146, "dia": 92})
            case.summary_json = summary
            flag_modified(case, "summary_json")
            goal = copy.deepcopy(case.goal_json or {})
            goal["current"] = "146/92 mmHg"
            goal["status"] = "above_target"
            case.goal_json = goal
            flag_modified(case, "goal_json")
            tl.append({"date": "Alert", "label": "BP 7d average reached 146/92 (>140/90 threshold) — automatically woke from Scheduled into Needs Decision."})
        elif event_kind == "stall_timeout":
            case.retry_count += 2
            if case.risk_tier == "high":
                case.state = "needs_decision"
                case.baton = "with_doctor"
                tl.append({"date": "Timeout", "label": "High-risk loop unanswered past 72h cap — escalated to doctor's Needs Decision."})
            else:
                case.state = "running"
                case.baton = "with_nurse"
                tl.append({"date": "Timeout", "label": "Low-risk loop unanswered after 2 nudges — routed to Nurse Rachel first (0 doctor interruption)."})
        elif event_kind == "close_loop":
            case.state = "closed"
            case.baton = "idle"
            tl.append({"date": "Closed", "label": "All tasks complete with no future checkpoint — loop archived cleanly to Closed."})
        else:
            raise ValueError(f"Unknown progression event '{event_kind}'.")

        case.timeline_json = tl
        flag_modified(case, "timeline_json")
        case.updated_at = utcnow()
        session.flush()
        return {"case_id": case.id, "state": case.state, "baton": case.baton, "retry_count": case.retry_count}


def simulate_patient_triage_turn(
    db: Database,
    *,
    tenant_id: str,
    message_text: str,
    hours_since_last_visit: float | None = 24.0,
    patient_g6pd: bool = True,
) -> dict[str, Any]:
    """Run Loop 1 deterministic Practice Graph inbound triage against live tenant Practice Graph."""
    with db.tenant_session(tenant_id) as session:
        graph = get_effective_practice_graph(session, tenant_id)
        result = classify_inbound_triage(
            message_text=message_text,
            hours_since_last_visit=hours_since_last_visit,
            effective_graph=graph,
            patient_g6pd=patient_g6pd,
        )
        result["graph_version_tag"] = graph["version_tag"]
        result["careline_name"] = graph["careline_profile"]["name"]
        return result


def ask_case_agent(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    question: str,
    actor_name: str = "Locum Dr. Sarah Wong",
) -> dict[str, Any]:
    """Back-and-forth clinical Q&A inside the case thread grounded in linked EMR notes & Agent Memory."""
    with db.tenant_session(tenant_id) as session:
        ensure_workspace_seeded(session, tenant_id)
        graph = get_effective_practice_graph(session, tenant_id)
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is None:
            raise ValueError("Case not found.")

        q = question.strip()
        low = q.lower()
        k = case.case_key
        if k == "wingyan":
            if any(w in low for w in ("period", "gi", "bleed", "menst")):
                ans = "From her 10 Nov consult note & intake: regular 5-day periods (not heavy) and denies melaena, dyspepsia, or weight loss. No GI red flags recorded."
            elif any(w in low for w in ("paracetamol", "panadol", "taken", "med")):
                ans = "She has not taken analgesia yet today — she checked first because she was unsure about Aspirin with G6PD."
            else:
                ans = "KingMed 8 Nov panel shows Ferritin 14 ng/mL (low, trending 32 → 21 → 14), Hb 12.1 g/dL, WBC 6.4, LDL 2.3, Glucose 4.8, eGFR 104. G6PD deficient + Penicillin allergic."
        elif k == "chan":
            if any(w in low for w in ("inr", "renal", "egfr")):
                ans = "Last clinic INR was 2.9 on 14 Sep (on Warfarin 5mg); QEH discharge INR was 2.4 on 21 Nov (after reduction to 3mg). Last eGFR 71 mL/min (Sep 2026)."
            elif any(w in low for w in ("refill", "dispens")):
                ans = "No clinic refills dispensed since discharge — next repeat was due 1 Dec, and no autonomous hold or premature message was sent."
            else:
                ans = "QEH Discharge Summary (p.2) specifies Warfarin 3mg PO daily (reduced from 5mg) and Atorvastatin 20mg PO nocte (replacing Simvastatin 20mg), with next INR due 29 Nov."
        elif k == "wong":
            if any(w in low for w in ("renal", "electrolyte", "u&e")):
                ans = "Baseline renal panel on 12 Sep (pre-Amlodipine): Na 140, K 4.2, Creatinine 64 µmol/L, eGFR 89 mL/min — all normal."
            elif any(w in low for w in ("140", "range", "high")):
                ans = "Zero readings > 140 systolic this week; 7-day home range was 124/76 to 134/82 (average 129/79 across 7/7 mornings)."
            else:
                ans = "Ms. Wong is stable on Amlodipine 5mg OD with 100% adherence and routine review scheduled Mon 21 Dec 10:00."
        else:
            if any(w in low for w in ("risk", "cv")):
                ans = "36M non-smoker, BP 118/74, fasting glucose 5.0 — 10-year CV risk < 2%, supporting lifestyle trial + 3-month fasting lipid repeat."
            else:
                ans = "Automatic booking chase triggers after 3 days if Mr. Lee hasn't booked, or click 'Nurse to Chase' to hand it to Nurse Rachel now."

        summary = copy.deepcopy(case.summary_json or {})
        thread = list(summary.get("agent_thread") or [])
        thread.append({"who": "doctor", "actor": actor_name, "text": q})
        thread.append({"who": "agent", "actor": graph["careline_profile"]["name"], "text": ans})
        summary["agent_thread"] = thread
        case.summary_json = summary
        flag_modified(case, "summary_json")
        case.updated_at = utcnow()
        session.flush()
        return {"case_id": case.id, "question": q, "answer": ans, "agent_thread": thread}


def reset_workspace(db: Database, tenant_id: str) -> dict[str, Any]:
    """Reset all workspace loops, traces, curator proposals, quarantined docs, and re-seed cleanly."""
    with db.tenant_session(tenant_id) as session:
        session.execute(delete(ExecutionTrace).where(ExecutionTrace.tenant_id == tenant_id))
        session.flush()
        session.execute(delete(CaseLoop).where(CaseLoop.tenant_id == tenant_id))
        session.execute(delete(DocumentIngestion).where(DocumentIngestion.tenant_id == tenant_id))
        session.execute(delete(AgentMemoryEntry).where(AgentMemoryEntry.tenant_id == tenant_id))
        session.execute(delete(CuratorProposal).where(CuratorProposal.tenant_id == tenant_id))
        session.execute(delete(PracticeGraphVersion).where(PracticeGraphVersion.tenant_id == tenant_id))
        session.flush()
    return build_workspace_overview(db, tenant_id)
