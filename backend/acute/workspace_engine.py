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

    # 3. 4-State Case Loops (Graph-driven context_widgets + Always-On Preventive Care + Polymorphic Checkout Primitives)
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
                "med_diff": {
                    "qeh": ["Warfarin 3mg PO daily (reduced from 5mg)", "Atorvastatin 20mg PO nocte"],
                    "clinic": ["Warfarin 5mg PO daily (outdated)", "Simvastatin 20mg PO nocte (duplicate)"],
                },
                "alerts": [
                    {"level": "high", "label": "Warfarin Reduced (5mg → 3mg)", "detail": "QEH discharge 22 Nov (INR 2.4); clinic EMR still shows 5mg"},
                    {"level": "high", "label": "Duplicate Statin Risk", "detail": "QEH switched Simvastatin 20mg → Atorvastatin 20mg"},
                ],
                "preventive_care": [
                    {
                        "id": "prev_fit",
                        "title": "HK Colorectal FIT Screening",
                        "badge": "Overdue 18m",
                        "detail": "Age 50–75 HK subsidy eligible · Queue kit after Warfarin stabilizes",
                        "order": {"id": "ord_prev_fit", "type": "lab", "issue": "Preventive Care", "testName": "HK Colorectal FIT Kit", "lab": "Clinic / Govt Programme", "fasting": False, "when": "After INR stable (Dec)"},
                    },
                    {
                        "id": "prev_pcv",
                        "title": "Pneumococcal (PCV20) + Seasonal Flu",
                        "badge": "Age 60+ High-Risk",
                        "detail": "Recommended for chronic cardiac / AFib patients",
                        "order": {"id": "ord_prev_pcv", "type": "book", "issue": "Preventive Care", "visitType": "Nurse Vaccine Session (Flu + PCV20)", "when": "With 29 Nov INR visit"},
                    },
                ],
                "scheduled_tasks": [
                    {"status": "booked", "label": "Today 11:30 · Post-QEH clinic visit (bringing pill bags)"},
                ],
                "issues": [
                    {
                        "id": "iss_afib",
                        "name": "1. Post-Discharge Anticoagulation & Lipid Reconciliation (AFib)",
                        "subjective": "Discharged from QEH yesterday following AFib rate-control admission. No bleeding, bruising, or palpitations.",
                        "reasoning": "Clinic INR was 2.9 on Warfarin 5mg (14 Sep); QEH reduced to 3mg daily achieving therapeutic INR 2.4 on 21 Nov (eGFR 71 mL/min) and switched Simvastatin → Atorvastatin 20mg. No clinic refills dispensed since admission.",
                        "assessment": "High-risk medication discrepancy surfaced ahead of 11:30 visit. Reconcile EMR active list, confirm home pillbox in Cantonese, and repeat INR at 7 days.",
                        "suggested_orders": [
                            {"id": "ch_rx_warf", "type": "rx", "issue": "1. Post-Discharge Reconciliation", "drug": "Warfarin 3mg OD + Atorvastatin 20mg ON", "freq": "Stop Simvastatin 20mg", "days": "30d", "note": "Matches QEH Discharge p.2"},
                            {"id": "ch_lab_inr", "type": "lab", "issue": "1. Post-Discharge Reconciliation", "testName": "Repeat INR (7 days post-change)", "lab": "KingMed", "fasting": False, "when": "Sun 29 Nov 2026"},
                            {"id": "ch_nurse_call", "type": "nurse", "issue": "1. Post-Discharge Reconciliation", "assignee": "Nurse Rachel", "task": "Cantonese pillbox verification call", "due": "Today before 16:00"},
                        ],
                    }
                ],
                "lab_panel": {
                    "source": "Combined INR & Renal Panel · QEH 21 Nov / Clinic 14 Sep",
                    "rows": [
                        {"test": "INR (QEH 21 Nov)", "val": "2.4", "ref": "2.0–3.0", "flag": "Normal"},
                        {"test": "INR (Clinic 14 Sep)", "val": "2.9", "ref": "2.0–3.0", "flag": "Normal"},
                        {"test": "eGFR (14 Sep)", "val": "71 mL/min", "ref": "> 60", "flag": "Normal"},
                        {"test": "Haemoglobin (QEH)", "val": "13.4 g/dL", "ref": "13.0–17.0", "flag": "Normal"},
                    ],
                },
                "trend_chart": {
                    "kind": "inr",
                    "title": "INR Trend",
                    "unit": "INR",
                    "target_label": "Therapeutic 2.0–3.0",
                    "points": [{"label": "Jul 26", "v": 2.3}, {"label": "14 Sep", "v": 2.9}, {"label": "21 Nov (QEH)", "v": 2.4}],
                },
                "attachments": [
                    {
                        "id": qeh_doc.id,
                        "label": "QEH Discharge Summary · 22 Nov 2026 (p.2)",
                        "quote": qeh_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "sam_proactive_insight": "Pulled his Sep clinic INR (2.9) and eGFR (71) beside QEH's 21 Nov INR (2.4). Also spotted his HK Colorectal FIT screen is 18m overdue — safe to queue once his Warfarin is steady.",
                "agent_thread": [],
                "patient_chat_label": "Mr. Chan ↔ Sam Careline Chat (1 message)",
                "original_chat": [
                    {"actor": "Sam", "time": "08:30", "text": "Welcome home from QEH, Mr. Chan. Reminder of your follow-up visit today at 11:30 — please bring your hospital medication bags."}
                ],
            },
            timeline_json=[
                {"date": "22 Nov", "label": "Discharged from QEH (AFib) — Warfarin 5mg → 3mg, switched to Atorvastatin."},
                {"date": "Today 08:30", "label": "Discharge summary matched (3-pt ID); INR & renal panel compiled ahead of 11:30 visit."},
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
            lead_line="Uncomplicated viral URTI (RAT neg, 2-day sick leave request) and declining Ferritin (14 ng/mL, normal Hb).",
            next_checkpoint_label="Today 14:30 teleconsult",
            next_checkpoint_at=now + timedelta(hours=5),
            summary_json={
                "alerts": [
                    {"level": "high", "label": "G6PD Deficient", "detail": "Avoid Aspirin, NSAIDs, sulfonamides, nitrofurantoin"},
                    {"level": "high", "label": "Penicillin Allergy", "detail": "Urticarial rash — avoid Amoxicillin / Augmentin"},
                ],
                "preventive_care": [
                    {
                        "id": "prev_flu",
                        "title": "Seasonal Influenza Vaccine (2026/27)",
                        "badge": "$0 Harbour FinTech",
                        "detail": "Inactivated (G6PD-safe) · Defer 14d until afebrile · 10-min nurse visit",
                        "order": {"id": "ord_prev_flu", "type": "book", "issue": "Preventive Care", "visitType": "10-min Nurse Flu Shot ($0)", "when": "In 14 days (post-recovery)"},
                    },
                    {
                        "id": "prev_cerv",
                        "title": "Cervical Screening (HPV / Pap)",
                        "badge": "Due Q2 2027",
                        "detail": "Age 25–64 HK guideline · Sam can send reminder & pre-visit info",
                        "order": {"id": "ord_prev_cerv", "type": "book", "issue": "Preventive Care", "visitType": "Cervical Screening Recall", "when": "Apr 2027"},
                    },
                ],
                "scheduled_tasks": [
                    {"status": "staged", "label": "Today 14:30 · Teleconsult with Locum Dr. Sarah"},
                ],
                "issues": [
                    {
                        "id": "iss_urti",
                        "name": "1. Acute Viral URTI",
                        "subjective": "1-day sore throat, dry cough, fever 38.2°C. Home COVID/Flu RAT negative. Requesting 2-day sick leave.",
                        "red_flags": "Denies shortness of breath, chest pain, odynophagia, or neck stiffness.",
                        "assessment": "Uncomplicated viral URTI. No antibiotic indication.",
                        "suggested_orders": [
                            {"id": "wy_cert", "type": "cert", "issue": "1. Acute Viral URTI", "daysOff": 2, "dates": "23–24 Nov 2026", "omitDx": True},
                            {"id": "wy_rx_urti", "type": "rx", "issue": "1. Acute Viral URTI", "drug": "Paracetamol 500mg + Cetirizine 10mg", "freq": "Para QDS PRN · Cet OD", "days": "5d", "note": "G6PD & Penicillin safe"},
                        ],
                    },
                    {
                        "id": "iss_ferr",
                        "name": "2. Non-Anaemic Iron Deficiency (Ferritin 14 ng/mL)",
                        "subjective": "Asymptomatic on annual panel; asking whether to start oral iron or monitor.",
                        "reasoning": "Ferritin declined steadily over 2 years (32 → 21 → 14 ng/mL) while Hb remains normal (12.1 g/dL). 10 Nov note documented regular periods and no GI bleeding symptoms.",
                        "assessment": "Early iron depletion without anaemia. Alternate-day oral iron + 3-month repeat ferritin appropriate.",
                        "suggested_orders": [
                            {"id": "wy_rx_iron", "type": "rx", "issue": "2. Non-Anaemic Iron Deficiency", "drug": "Ferrous bisglycinate 25mg (gentle iron)", "freq": "Alternate days", "days": "90d", "note": "Minimizes GI side effects"},
                            {"id": "wy_lab_ferr", "type": "lab", "issue": "2. Non-Anaemic Iron Deficiency", "testName": "Repeat Serum Ferritin + FBC", "lab": "KingMed", "fasting": False, "when": "Tue 9 Feb 2027 (08:30 Nurse)"},
                        ],
                    },
                ],
                "lab_panel": {
                    "source": "KingMed Annual Blood Panel · 8 Nov 2026",
                    "rows": [
                        {"test": "Serum Ferritin", "val": "14 ng/mL", "ref": "15–150", "flag": "LOW"},
                        {"test": "Haemoglobin (Hb)", "val": "12.1 g/dL", "ref": "11.5–15.5", "flag": "Normal"},
                        {"test": "WBC", "val": "6.4 ×10⁹/L", "ref": "4.0–10.0", "flag": "Normal"},
                        {"test": "LDL-C", "val": "2.3 mmol/L", "ref": "< 2.6", "flag": "Normal"},
                        {"test": "Fasting Glucose", "val": "4.8 mmol/L", "ref": "3.9–5.5", "flag": "Normal"},
                        {"test": "eGFR", "val": "104 mL/min", "ref": "> 90", "flag": "Normal"},
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
                "sam_proactive_insight": "Ferritin fell 32 → 21 → 14 over 24m with normal Hb (12.1) and no GI symptoms on her 10 Nov note. She also avoided taking analgesia today until G6PD safety was checked.",
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
                {"date": "Today 09:14", "label": "Booked 14:30 teleconsult; full labs & issues prepared."},
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
                "alerts": [
                    {"level": "info", "label": "BP Goal: < 135/85 mmHg", "detail": "7-day home average 129/79 (7/7 logged · On target)"},
                ],
                "preventive_care": [
                    {
                        "id": "prev_mammo",
                        "title": "Biennial Breast Mammogram",
                        "badge": "Due Dec 2026",
                        "detail": "Age 44–69 HK guideline · Bundle imaging request ahead of 21 Dec visit",
                        "order": {"id": "ord_prev_mammo", "type": "imaging", "issue": "Preventive Care", "modality": "Bilateral Screening Mammogram", "indication": "HK biennial breast screening (58F)", "priority": "Routine"},
                    },
                    {
                        "id": "prev_hba1c",
                        "title": "Annual HbA1c & Fasting Lipids",
                        "badge": "Due Jan 2027",
                        "detail": "Cardiometabolic co-screening for hypertension",
                        "order": {"id": "ord_prev_hba1c", "type": "lab", "issue": "Preventive Care", "testName": "HbA1c + Fasting Lipids", "lab": "KingMed", "fasting": True, "when": "Prior to 21 Dec review"},
                    },
                ],
                "scheduled_tasks": [
                    {"status": "booked", "label": "Mon 21 Dec 10:00 · Routine GP Hypertension Review"},
                    {"status": "auto", "label": "Mon 14 Dec · Collect 7-day home BP log (wakes only if > 140/90)"},
                ],
                "issues": [
                    {
                        "id": "iss_htn",
                        "name": "1. Essential Hypertension on Amlodipine 5mg OD",
                        "subjective": "Asymptomatic, 100% logged adherence, denies dizziness or ankle swelling.",
                        "reasoning": "BP improved steadily across 3 months: 152/94 (Sep clinic) → 138/86 (Oct home avg) → 129/79 (Nov 7-day avg; range 124/76–134/82). Baseline renal function normal (eGFR 89, K+ 4.2).",
                        "assessment": "Well controlled on Amlodipine 5mg OD within target. Continue regimen; optional target adjustment or pre-visit renal panel.",
                        "suggested_orders": [
                            {"id": "wg_goal", "type": "goal", "issue": "1. Essential Hypertension", "metric": "Blood Pressure", "targetSys": 135, "targetDia": 85},
                            {"id": "wg_lab_ren", "type": "lab", "issue": "1. Essential Hypertension", "testName": "Renal Function & Electrolytes (U&E)", "lab": "KingMed", "fasting": False, "when": "1 week before 21 Dec review"},
                        ],
                    }
                ],
                "lab_panel": {
                    "source": "Baseline Renal Panel · 12 Sep 2026 (Pre-Amlodipine)",
                    "rows": [
                        {"test": "eGFR", "val": "89 mL/min", "ref": "> 60", "flag": "Normal"},
                        {"test": "Potassium (K+)", "val": "4.2 mmol/L", "ref": "3.5–5.1", "flag": "Normal"},
                        {"test": "Sodium (Na+)", "val": "140 mmol/L", "ref": "136–145", "flag": "Normal"},
                        {"test": "Creatinine", "val": "64 µmol/L", "ref": "45–84", "flag": "Normal"},
                    ],
                },
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
                "sam_proactive_insight": "BP is steady at 129/79 across 7/7 mornings with zero spikes >140. Drag the BP Goal slider in the Checkout if you want to tighten her target to <130/80 — her biennial mammogram is also due in Dec.",
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
            goal_json={"metric": "Blood Pressure", "target": "< 135/85 mmHg", "target_sys": 135, "target_dia": 85, "current": "129/79 mmHg", "status": "on_track"},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_lee_ldl", patient_name="LEE Ming Kit", patient_age_sex="36M",
            case_key="lee", pathway_node_id="ebm.pathway.borderline_ldl",
            graph_version_tag=graph["version_tag"],
            state="scheduled", baton="idle", risk_tier="low",
            ai_title="Borderline Hyperlipidaemia (LDL 3.6) — Repeat Lipid Booking",
            lead_line="From 2 Nov checkup · Fasting lipid draw invite sent 2 days ago · Waiting on patient booking.",
            next_checkpoint_label="Pending booking · 2d",
            next_checkpoint_at=now + timedelta(days=1),
            summary_json={
                "origin_strip": {
                    "from": "2 Nov Annual Checkup (Dr. Lam)",
                    "why": "Waiting on patient booking · Fasting lipid draw invite sent 2 days ago",
                    "chase": "Auto-chase in 24h (3-day rule)",
                },
                "alerts": [
                    {"level": "info", "label": "10-Yr CV Risk: < 2% (Low)", "detail": "36M non-smoker · BP 118/74 · Fasting glucose 5.0"},
                ],
                "preventive_care": [
                    {
                        "id": "prev_lee_flu",
                        "title": "Seasonal Influenza Vaccine",
                        "badge": "Eligible",
                        "detail": "Bundle with his 10-min morning nurse lipid blood draw",
                        "order": {"id": "ord_lee_flu", "type": "book", "issue": "Preventive Care", "visitType": "Bundle Flu Shot with Lipid Draw", "when": "Feb 2027"},
                    }
                ],
                "scheduled_tasks": [
                    {"status": "waiting", "label": "Waiting on patient booking (sent 2d ago)"},
                    {"status": "auto", "label": "Auto-chase triggers in 24h if unbooked"},
                ],
                "issues": [
                    {
                        "id": "iss_ldl",
                        "name": "1. Borderline LDL Cholesterol (3.6 mmol/L)",
                        "subjective": "Asymptomatic executive checkup (2 Nov). Lifestyle trial advised for 3 months.",
                        "reasoning": "LDL 3.6 mmol/L (ref < 3.4) with normal HDL (1.5), triglycerides (1.4), and calculated 10-year CV risk < 2% (low). Statin not indicated; repeat fasting lipid panel due Feb 2027.",
                        "assessment": "Low cardiovascular risk; await 3-month repeat fasting lipids. No doctor input needed unless unbooked after chase.",
                        "suggested_orders": [
                            {"id": "lee_chase", "type": "nurse", "issue": "1. Borderline LDL", "assignee": "Nurse Rachel", "task": "Phone chase to confirm Feb fasting lipid slot", "due": "This week"},
                            {"id": "lee_rev", "type": "book", "issue": "1. Borderline LDL", "visitType": "10-min GP Results Review", "when": "1 week post-blood draw"},
                        ],
                    }
                ],
                "lab_panel": {
                    "source": "KingMed Lipid Panel · 2 Nov 2026",
                    "rows": [
                        {"test": "LDL-C", "val": "3.6 mmol/L", "ref": "< 3.4", "flag": "HIGH"},
                        {"test": "HDL-C", "val": "1.5 mmol/L", "ref": "> 1.0", "flag": "Normal"},
                        {"test": "Triglycerides", "val": "1.4 mmol/L", "ref": "< 1.7", "flag": "Normal"},
                        {"test": "Fasting Glucose", "val": "5.0 mmol/L", "ref": "3.9–5.5", "flag": "Normal"},
                        {"test": "10-Yr CV Risk", "val": "< 2%", "ref": "Low < 10%", "flag": "Normal"},
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
                "sam_proactive_insight": "Calculated his 10-year CV risk upfront (< 2%, non-smoker, BP 118/74, glucose 5.0) — confirms lifestyle + repeat check rather than statin. Kept him in Scheduled / Waiting since his booking invite is still within the 3-day window.",
                "agent_thread": [],
                "patient_chat_label": "Mr. Lee ↔ Sam Careline Chat (1 message)",
                "original_chat": [
                    {"actor": "Sam", "time": "2 days ago", "text": "Hi Mr. Lee — ready to book your 3-month fasting lipid blood draw? Tap to confirm Fri 12 Feb at 08:00 (10-min nurse visit)."}
                ],
            },
            timeline_json=[
                {"date": "2 Nov", "label": "Lipid panel: LDL 3.6 mmol/L; lifestyle + 3-month repeat advised."},
                {"date": "2 days ago", "label": "Fasting blood-draw slot offered — waiting on patient booking (auto-chase in 24h)."},
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
