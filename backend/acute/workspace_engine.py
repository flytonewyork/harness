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
import re
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.agentic import (
    CLINICAL_PRESENTATION_SLOT_FILL_V1,
    PRESENTATION_SELF_REPAIR_V1,
    SAM_PRIVATE_REGISTRAR_QA_V1,
    ContextCompiler,
    SkillRegistry,
    detect_prompt_injection_signals,
    estimate_tokens,
    screen_agent_output,
)

from .agent_harness import (
    detect_live_llm_engine,
    invoke_clinical_qa,
    invoke_structured_json,
)
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
    Patient,
    PracticeGraphVersion,
    Practitioner,
    new_id,
    record_audit,
    utcnow,
)
from .emr_connector import (
    EMR_PATIENT_REGISTRY,
    ingest_external_document,
    list_quarantined_documents,
    verify_three_point_id,
    write_additive_emr_addendum,
)
from .practice_graph import (
    CHATTY_SAM_PHRASES,
    OBJECTIVE_SAM_MARKERS,
    PATHWAY_WIDGET_STACKS,
    classify_inbound_triage,
    compile_instruction_plan,
    compute_cha2ds2_vasc,
    compute_cvd_10yr_tier,
    compute_has_bled,
    normalize_order_primitive,
    resolve_pathway_id,
    validate_case_presentation,
)
from .rx_rules import evaluate_rx_order_primitives
from .security import hash_secret, mask_phone, sha256_hex
from .triage import classify_bilingual_async_triage
from .verification import verify_external_document_payload
from .workflows import EVENT_ACTION_COMMIT_DISPATCH, DurableWorkflowEngine

_SKILL_REGISTRY_CACHE: SkillRegistry | None = None
_CONTEXT_COMPILER = ContextCompiler(token_ceiling=4096)


def _get_skill_registry() -> SkillRegistry:
    global _SKILL_REGISTRY_CACHE
    if _SKILL_REGISTRY_CACHE is None:
        try:
            _SKILL_REGISTRY_CACHE = SkillRegistry.load_from_directory()
        except Exception:
            _SKILL_REGISTRY_CACHE = SkillRegistry([])
    return _SKILL_REGISTRY_CACHE


def has_objective_sam_signal(text: str | None) -> bool:
    """Enforce R5 high-signal gate: objective score/guideline/threshold present AND zero chatty UI narration."""
    if not text or not str(text).strip():
        return False
    low = str(text).lower()
    if any(phrase in low for phrase in CHATTY_SAM_PHRASES):
        return False
    return any(marker in low for marker in OBJECTIVE_SAM_MARKERS)


_WORKSPACE_FK_PATIENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "pat_chan_warfarin",
        "name_en": "CHAN Tai Man",
        "name_zh": "陳大文",
        "dob": "1964-03-14",
        "sex": "M",
        "hkid_masked": "A412***(8)",
        "phone": "+85296204101",
        "g6pd_deficient": False,
        "egfr": 71.0,
        "weight_kg": 72.0,
        "allergies": [],
        "current_meds": ["Warfarin 5mg daily", "Simvastatin 20mg nocte"],
        "referral_code": "REF-CHAN-62041",
    },
    {
        "id": "pat_wong_htn",
        "name_en": "WONG Mei Ling",
        "name_zh": "黃美玲",
        "dob": "1968-09-21",
        "sex": "F",
        "hkid_masked": "K901***(2)",
        "phone": "+85295809202",
        "g6pd_deficient": False,
        "egfr": 89.0,
        "weight_kg": 58.0,
        "allergies": [],
        "current_meds": ["Amlodipine 5mg daily"],
        "referral_code": "REF-WONG-58092",
    },
    {
        "id": "pat_lee_ldl",
        "name_en": "LEE Ming Kit",
        "name_zh": "李明傑",
        "dob": "1990-02-04",
        "sex": "M",
        "hkid_masked": "C219***(1)",
        "phone": "+85293604403",
        "g6pd_deficient": False,
        "egfr": 102.0,
        "weight_kg": 76.0,
        "allergies": [],
        "current_meds": [],
        "referral_code": "REF-LEE-36044",
    },
    {
        "id": "pat_lam_urti_demo",
        "name_en": "LAM Wing-Yan",
        "name_zh": "林詠欣",
        "dob": "1994-04-18",
        "sex": "F",
        "hkid_masked": "M528***(6)",
        "phone": "+85293201804",
        "g6pd_deficient": True,
        "egfr": 104.0,
        "weight_kg": 54.0,
        "allergies": ["Penicillin"],
        "current_meds": [],
        "referral_code": "REF-LAM-32018",
    },
)


def _ensure_workspace_fk_entities(session: Session, tenant_id: str) -> None:
    """Idempotently ensure synthetic workspace patients & `doc_locum_sarah` exist so Prescription/Certificate FKs never fail."""
    now = utcnow()
    # 1. Ensure locum practitioner `doc_locum_sarah` exists for MCHK doctor sign-offs
    prac_stmt = (
        pg_insert(Practitioner)
        .values(
            id="doc_locum_sarah",
            tenant_id=tenant_id,
            email="sarah.locum@central-gp.tmrw.hk",
            password_hash=hash_secret("DemoClinic!2026"),
            name_en="Locum Dr. Sarah Wong",
            name_zh="黃莎拉醫生",
            role="clinician",
            mchk_reg_no="M61042",
            clinic_name="Central & Mid-Levels Family Medical (HK)",
            availability_json=[],
            active=True,
            created_at=now,
        )
        .on_conflict_do_nothing()
    )
    session.execute(prac_stmt)

    # 2. Ensure synthetic workspace patients exist in `patients` table
    for p in _WORKSPACE_FK_PATIENTS:
        pat_stmt = (
            pg_insert(Patient)
            .values(
                id=p["id"],
                tenant_id=tenant_id,
                phone_hash=sha256_hex(p["phone"]),
                phone_masked=mask_phone(p["phone"]),
                name_en=p["name_en"],
                name_zh=p["name_zh"],
                dob=p["dob"],
                sex=p["sex"],
                hkid_masked=p["hkid_masked"],
                ehealth_consent=True,
                preferred_lang="en",
                g6pd_deficient=p["g6pd_deficient"],
                weight_kg=p["weight_kg"],
                egfr=p["egfr"],
                allergies_json=list(p["allergies"]),
                current_meds_json=list(p["current_meds"]),
                referral_code=p["referral_code"],
                status="active",
                created_at=now,
            )
            .on_conflict_do_nothing()
        )
        session.execute(pat_stmt)
    session.flush()


def ensure_workspace_seeded(session: Session, tenant_id: str) -> None:
    """Seed FK-safe patients/practitioners, 4 canonical clinical cases, linked 3-pt ID docs, and Agent Memory."""
    _ensure_workspace_fk_entities(session, tenant_id)
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

    # 3. 4-State Case Loops (Compiled strictly against docs/CLINICAL_UI_DESIGN_SYSTEM.md)
    cases = [
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_chan_warfarin", patient_name="CHAN Tai Man", patient_age_sex="62M",
            case_key="chan", pathway_node_id="ebm.pathway.post_discharge_anticoag",
            graph_version_tag=graph["version_tag"],
            state="needs_decision", baton="with_doctor", risk_tier="high",
            ai_title="Post-QEH AFib Discharge — Medication Review",
            lead_line="QEH discharge adjusted Warfarin and switched statin therapy ahead of today's 11:30 follow-up.",
            next_checkpoint_label="Today 11:30 visit",
            next_checkpoint_at=now + timedelta(hours=2),
            summary_json={
                "vitals_exam": {
                    "source": "QEH Discharge · 22 Nov",
                    "items": [
                        {"label": "BP", "val": "128/76 mmHg"},
                        {"label": "HR", "val": "72 bpm (irregular)"},
                        {"label": "SpO₂", "val": "98% RA"},
                        {"label": "Exam", "val": "Chest clear · No peripheral oedema"},
                    ],
                },
                "clinical_scores": [
                    compute_cha2ds2_vasc(age=62, sex="M", hypertension=True, diabetes=True, vascular_disease=True),
                    compute_has_bled(labile_inr=True),
                ],
                "med_diff": {
                    "rows": [
                        {"drug": "Warfarin", "qeh": "3 mg PO daily", "clinic": "5 mg PO daily", "tag": "Dose reduced"},
                        {"drug": "Statin", "qeh": "Atorvastatin 20 mg PO nocte", "clinic": "Simvastatin 20 mg PO nocte", "tag": "Switched · Duplicate risk"},
                    ],
                    "qeh": ["Warfarin 3mg PO daily (reduced from 5mg)", "Atorvastatin 20mg PO nocte"],
                    "clinic": ["Warfarin 5mg PO daily (prior dose)", "Simvastatin 20mg PO nocte (prior statin)"],
                },
                "imaging_studies": [
                    {
                        "id": "img_chan_ecg",
                        "modality": "12-Lead ECG",
                        "date": "21 Nov 2026 · QEH",
                        "summary": "Atrial fibrillation, rate-controlled (ventricular rate 72 bpm). No acute ST-T ischaemic changes.",
                        "status": "Reviewed at QEH",
                    }
                ],
                "alerts": [
                    {"level": "high", "label": "Warfarin Reduced (5mg → 3mg)", "detail": "QEH discharge 22 Nov; Clinic EMR still lists 5mg daily"},
                    {"level": "high", "label": "Duplicate Statin Risk", "detail": "QEH switched Simvastatin 20mg → Atorvastatin 20mg"},
                ],
                "preventive_care": [
                    {
                        "id": "prev_fit",
                        "title": "HK Colorectal FIT Screening",
                        "badge": "Overdue 18m",
                        "detail": "Age 50–75 HK subsidy eligible · Queue kit after INR stabilizes",
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
                    {"status": "booked", "label": "Today 11:30 · Post-QEH clinic visit (bringing hospital med bags)"},
                ],
                "issues": [
                    {
                        "id": "iss_afib",
                        "name": "1. AFib — Warfarin dose reduction",
                        "bullets": [
                            "Discharged from QEH yesterday after AFib rate-control admission; no clinic refill dispensed since admission.",
                            "Discharge plan reduced daily Warfarin dose with a 7-day repeat check (see Med Diff & INR Trend).",
                        ],
                        "safety_gap": "Confirm current home Warfarin tablet strength and screen for bleeding or bruising at 11:30.",
                        "suggested_orders": [
                            {"id": "ch_rx_warf", "type": "rx", "action": "update", "issue": "1. AFib — Warfarin dose reduction", "drug": "Warfarin 3mg", "freq": "PO daily", "days": "30d", "note": "Matches QEH discharge p.2"},
                            {"id": "ch_lab_inr", "type": "lab", "issue": "1. AFib — Warfarin dose reduction", "testName": "Repeat INR (7 days)", "lab": "KingMed", "fasting": False, "when": "Sun 29 Nov 2026"},
                            {"id": "ch_nurse_call", "type": "nurse", "issue": "1. AFib — Warfarin dose reduction", "assignee": "Nurse Rachel", "task": "Medication adherence check (Cantonese)", "due": "Today before 16:00"},
                        ],
                    },
                    {
                        "id": "iss_lipids",
                        "name": "2. Dyslipidaemia — Statin switch",
                        "bullets": [
                            "Hospital transitioned statin therapy on discharge; Clinic EMR still lists prior agent (see Med Diff).",
                        ],
                        "safety_gap": "Confirm prior Simvastatin supply is removed at home to prevent duplicate statin dosing.",
                        "suggested_orders": [
                            {"id": "ch_rx_stop_simva", "type": "rx", "action": "stop", "issue": "2. Dyslipidaemia — Statin switch", "drug": "Simvastatin 20mg", "freq": "Discontinue", "days": "—", "note": "Replaced by Atorvastatin"},
                            {"id": "ch_rx_atorva", "type": "rx", "action": "start", "issue": "2. Dyslipidaemia — Statin switch", "drug": "Atorvastatin 20mg", "freq": "PO nocte", "days": "30d", "note": "Matches QEH discharge p.2"},
                        ],
                    },
                ],
                "lab_panel": {
                    "source": "INR & Renal Baseline · QEH 21 Nov / Clinic 14 Sep",
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
                "sam_proactive_insight": "CHA₂DS₂-VASc 3 · HAS-BLED 1 (NICE NG196) — ongoing anticoagulation indicated with 7-day repeat INR after dose change. HK Colorectal FIT screening is 18m overdue (eligible to queue once INR is steady).",
                "agent_thread": [],
                "patient_chat_label": "Mr. Chan ↔ Sam Careline Chat",
                "original_chat": [
                    {"actor": "Sam", "time": "08:30", "text": "Welcome home from QEH, Mr. Chan. Reminder of your follow-up visit today at 11:30 — please bring your hospital medication bags."}
                ],
            },
            timeline_json=[
                {"date": "22 Nov", "label": "Discharged from QEH (AFib) — Warfarin reduced to 3mg; statin switched to Atorvastatin."},
                {"date": "Today 08:30", "label": "Discharge summary matched (3-pt ID); labs, ECG, and med comparison prepared for 11:30 visit."},
            ],
            goal_json={},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_lam_urti_demo", patient_name="LAM Wing-Yan", patient_age_sex="32F",
            case_key="wingyan", pathway_node_id="ebm.pathway.low_ferritin_normal_hb",
            graph_version_tag=graph["version_tag"],
            state="needs_decision", baton="with_doctor", risk_tier="standard",
            ai_title="Acute Viral URTI & Non-Anaemic Iron Deficiency",
            lead_line="1-day viral URTI requesting 2-day sick leave alongside declining annual Ferritin with normal haemoglobin.",
            next_checkpoint_label="Today 14:30 teleconsult",
            next_checkpoint_at=now + timedelta(hours=5),
            summary_json={
                "vitals_exam": {
                    "source": "Patient Intake · Today 09:12",
                    "items": [
                        {"label": "Temp (home)", "val": "38.2 °C"},
                        {"label": "COVID / Flu RAT", "val": "Negative"},
                        {"label": "Red flags", "val": "Denies SOB, chest pain, or neck stiffness"},
                    ],
                },
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
                        "detail": "Age 25–64 HK guideline · Reminder & pre-visit guidance",
                        "order": {"id": "ord_prev_cerv", "type": "book", "issue": "Preventive Care", "visitType": "Cervical Screening Recall", "when": "Apr 2027"},
                    },
                ],
                "scheduled_tasks": [
                    {"status": "staged", "label": "Today 14:30 · Teleconsult with Locum Dr. Sarah"},
                ],
                "issues": [
                    {
                        "id": "iss_urti",
                        "name": "1. Acute viral URTI",
                        "bullets": [
                            "1-day sore throat and dry cough; requesting 2-day sick leave certificate.",
                            "No clinical antibiotic indication; G6PD and Penicillin safety blocks active.",
                        ],
                        "safety_gap": None,
                        "suggested_orders": [
                            {"id": "wy_cert", "type": "cert", "issue": "1. Acute viral URTI", "daysOff": 2, "dates": "23–24 Nov 2026", "omitDx": True},
                            {"id": "wy_rx_para", "type": "rx", "action": "start", "issue": "1. Acute viral URTI", "drug": "Paracetamol 500mg", "freq": "QDS PRN", "days": "5d", "note": "G6PD-safe analgesic"},
                            {"id": "wy_rx_cet", "type": "rx", "action": "start", "issue": "1. Acute viral URTI", "drug": "Cetirizine 10mg", "freq": "OD PRN", "days": "5d", "note": "Non-sedating antihistamine"},
                        ],
                    },
                    {
                        "id": "iss_ferr",
                        "name": "2. Non-anaemic iron deficiency",
                        "bullets": [
                            "Steady decline in ferritin across annual panels with preserved haemoglobin (see Trend & Labs).",
                            "Prior 10 Nov note documented regular periods and no GI bleeding; patient asking whether to start oral iron.",
                        ],
                        "safety_gap": None,
                        "suggested_orders": [
                            {"id": "wy_rx_iron", "type": "rx", "action": "start", "issue": "2. Non-anaemic iron deficiency", "drug": "Ferrous bisglycinate 25mg", "freq": "Alternate days", "days": "90d", "note": "Gentle oral iron · minimizes GI intolerance"},
                            {"id": "wy_lab_ferr", "type": "lab", "issue": "2. Non-anaemic iron deficiency", "testName": "Repeat Serum Ferritin + FBC", "lab": "KingMed", "fasting": False, "when": "Tue 9 Feb 2027 (08:30 Nurse)"},
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
                "sam_proactive_insight": "BSG Iron Deficiency Guideline: Ferritin < 15 ng/mL with normal Hb meets non-anaemic iron deficiency threshold (alternate-day gentle oral iron + 3-month repeat). G6PD & Penicillin formulary blocks active; Harbour FinTech $0 flu shot eligible after 14d recovery.",
                "agent_thread": [],
                "patient_chat_label": "Wing-Yan ↔ Sam Careline Chat",
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
            ai_title="Essential Hypertension — Routine Review 21 Dec",
            lead_line="7-day home BP log is on target on Amlodipine 5mg OD; routine GP review booked 21 Dec.",
            next_checkpoint_label="21 Dec review",
            next_checkpoint_at=now + timedelta(days=21),
            summary_json={
                "vitals_exam": {
                    "source": "7-Day Home BP Log · Today",
                    "items": [
                        {"label": "7-Day Home Avg", "val": "129/79 mmHg"},
                        {"label": "Range", "val": "124/76 – 134/82"},
                        {"label": "Adherence", "val": "7/7 days logged"},
                        {"label": "Side effects", "val": "No dizziness or ankle oedema"},
                    ],
                },
                "alerts": [
                    {"level": "info", "label": "BP Target: < 135/85 mmHg", "detail": "On target · Routine review remains scheduled 21 Dec"},
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
                        "name": "1. Essential hypertension — on target",
                        "bullets": [
                            "Asymptomatic and well controlled on Amlodipine 5mg OD within home target (see BP Trajectory & Baseline Renal Panel).",
                            "Routine GP review remains scheduled for 21 Dec; optional target adjustment or pre-visit renal bloods.",
                        ],
                        "safety_gap": None,
                        "suggested_orders": [
                            {"id": "wg_goal", "type": "goal", "issue": "1. Essential hypertension — on target", "metric": "Blood Pressure", "targetSys": 135, "targetDia": 85},
                            {"id": "wg_lab_ren", "type": "lab", "issue": "1. Essential hypertension — on target", "testName": "Renal Function & Electrolytes (U&E)", "lab": "KingMed", "fasting": False, "when": "1 week before 21 Dec review"},
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
                "sam_proactive_insight": None,
                "agent_thread": [],
                "patient_chat_label": "Ms. Wong ↔ Sam Careline Chat",
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
            ai_title="Borderline Hyperlipidaemia — Repeat Lipid Booking",
            lead_line="From 2 Nov checkup · Fasting lipid draw invite sent 2 days ago · Waiting on patient booking.",
            next_checkpoint_label="Pending booking · 2d",
            next_checkpoint_at=now + timedelta(days=1),
            summary_json={
                "origin_strip": {
                    "from": "2 Nov Annual Checkup (Dr. Lam)",
                    "why": "Waiting on patient booking · Fasting lipid draw invite sent 2 days ago",
                    "chase": "Auto-chase in 24h (3-day rule)",
                },
                "clinical_scores": [
                    compute_cvd_10yr_tier(age=36, smoker=False, systolic_bp=118, ldl_mmol_l=3.6),
                ],
                "alerts": [
                    {"level": "info", "label": "No Drug Allergies", "detail": "Non-smoker · Lifestyle trial active (no statin)"},
                ],
                "preventive_care": [
                    {
                        "id": "prev_lee_flu",
                        "title": "Seasonal Influenza Vaccine",
                        "badge": "Eligible",
                        "detail": "Bundle with morning nurse lipid blood draw",
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
                        "name": "1. Borderline LDL cholesterol",
                        "bullets": [
                            "Asymptomatic executive checkup (2 Nov); 3-month lifestyle trial advised given low 10-year CVD risk (see Score & Lipid Panel).",
                            "Statin therapy not indicated; awaiting confirmation for February fasting blood draw.",
                        ],
                        "safety_gap": None,
                        "suggested_orders": [
                            {"id": "lee_chase", "type": "nurse", "issue": "1. Borderline LDL cholesterol", "assignee": "Nurse Rachel", "task": "Booking follow-up call (fasting lipid slot)", "due": "This week"},
                            {"id": "lee_rev", "type": "book", "issue": "1. Borderline LDL cholesterol", "visitType": "10-min GP Results Review", "when": "1 week post-blood draw"},
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
                "sam_proactive_insight": None,
                "agent_thread": [],
                "patient_chat_label": "Mr. Lee ↔ Sam Careline Chat",
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


seed_workspace_cases = ensure_workspace_seeded


# -------------------------------------------------------------------------------------- Presentation Compiler & Helpers
def _prune_active_graph_nodes(graph: dict[str, Any], pathway_node_id: str) -> list[dict[str, Any]]:
    """Prune Practice Graph to 2–4 active GraphNodes (Safety Floor + pathway node + triage/clinic standards)."""
    nodes = list(graph.get("nodes") or [])
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("id")}
    resolved_pathway = resolve_pathway_id(pathway_node_id)
    selected: list[dict[str, Any]] = []
    preferred_ids = [
        "ebm.safety.g6pd_allergy_floor",
        "ebm.safety.doctor_only_authority",
        resolved_pathway,
        "ebm.safety.crisis_999",
    ]
    for nid in preferred_ids:
        node = by_id.get(nid)
        if node is not None and node not in selected:
            selected.append(node)
        if len(selected) >= 4:
            break
    if len(selected) < 2:
        for n in nodes:
            if n not in selected:
                selected.append(n)
            if len(selected) >= 2:
                break
    return selected[:4]


def _normalize_summary_orders(summary: dict[str, Any]) -> dict[str, Any]:
    """Normalize every order inside `issues[].suggested_orders` and `preventive_care[].order` via Seam A normalizer."""
    out = copy.deepcopy(summary or {})
    for iss in out.get("issues") or []:
        if isinstance(iss, dict) and isinstance(iss.get("suggested_orders"), list):
            iss["suggested_orders"] = [
                normalize_order_primitive(o, include_camel_aliases=True)
                for o in iss["suggested_orders"]
                if isinstance(o, dict)
            ]
    for prev in out.get("preventive_care") or []:
        if isinstance(prev, dict) and isinstance(prev.get("order"), dict):
            prev["order"] = normalize_order_primitive(prev["order"], include_camel_aliases=True)
    return out


def _strip_internal_summary_keys(summary: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (summary or {}).items() if not str(k).startswith("_")}


def _build_patient_clinical_context(session: Session, tenant_id: str, case: CaseLoop) -> dict[str, Any]:
    """Build patient profile dict for `rx_rules` from DB Patient row with EMR_PATIENT_REGISTRY fallback."""
    pat = session.execute(
        select(Patient).where(Patient.tenant_id == tenant_id, Patient.id == case.patient_id)
    ).scalars().first()
    reg = EMR_PATIENT_REGISTRY.get(case.patient_id, {})
    if pat is not None:
        return {
            "patient_id": pat.id,
            "name_en": pat.name_en,
            "dob": pat.dob,
            "sex": pat.sex,
            "g6pd_deficient": bool(pat.g6pd_deficient),
            "allergies": list(pat.allergies_json or []),
            "current_meds": list(pat.current_meds_json or []),
            "egfr": pat.egfr,
            "weight_kg": pat.weight_kg,
        }
    return {
        "patient_id": case.patient_id,
        "name_en": reg.get("name_en", case.patient_name),
        "dob": reg.get("dob"),
        "sex": (case.patient_age_sex[-1:] if case.patient_age_sex else "U"),
        "g6pd_deficient": bool(reg.get("g6pd_deficient", False)),
        "allergies": list(reg.get("allergies", [])),
        "current_meds": list(reg.get("active_meds", [])),
        "egfr": None,
        "weight_kg": None,
    }


def _compile_5slot_context_snapshot(
    *,
    graph: dict[str, Any],
    case: CaseLoop,
    memories: list[dict[str, Any]],
    engine: dict[str, Any],
    current_input: str = "",
    channel: str = "workspace",
    template: Any = None,
    repair_diagnostics: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Compile a strict <4,096 CJK-aware token 5-slot ContextSnapshot via `backend.agentic.ContextCompiler`."""
    active_nodes = _prune_active_graph_nodes(graph, case.pathway_node_id)
    registry = _get_skill_registry()
    intent = "doctor_private_qa" if channel == "private_sam" else "workspace_compile"
    selected_skills = registry.select_skills(
        intent=intent,
        pathway_node_id=resolve_pathway_id(case.pathway_node_id),
        message_text=f"{case.lead_line} {current_input}",
        max_skills=3,
    )
    summary_clean = _strip_internal_summary_keys(case.summary_json)
    # Exclude private Sam thread from non-private context windows
    clinical_state = {
        k: v
        for k, v in summary_clean.items()
        if k not in ("agent_thread", "original_chat", "attachments")
    }
    verified_quotes = [
        {"id": a.get("id"), "label": a.get("label"), "quote": a.get("quote")}
        for a in (summary_clean.get("attachments") or [])
        if isinstance(a, dict) and a.get("quote")
    ]
    conv_turns = list(summary_clean.get("original_chat") or [])
    if channel == "private_sam":
        conv_turns = conv_turns[-4:] + list(summary_clean.get("agent_thread") or [])[-4:]

    region = str(engine.get("region") or ("asia-east2" if engine.get("provider") == "vertex" else "asia-east2"))
    snap = _CONTEXT_COMPILER.compile(
        template=template or CLINICAL_PRESENTATION_SLOT_FILL_V1,
        skills=selected_skills,
        active_graph_nodes=active_nodes,
        total_graph_nodes=len(graph.get("nodes") or []) or 14,
        clinical_state=clinical_state,
        memories=memories,
        verified_quotes=verified_quotes,
        conversation_turns=conv_turns,
        current_input=current_input or case.lead_line,
        repair_diagnostics=repair_diagnostics,
        template_vars={"careline_name": graph.get("careline_profile", {}).get("name", "Sam")},
        region=region,
        channel=channel,
        patient_names_for_scrub=[case.patient_name],
    )
    tel = snap.to_telemetry_dict()
    tel["model_routing"] = {
        "provider": engine.get("provider", "deterministic"),
        "region": engine.get("region", "local"),
        "model": engine.get("model", "clinic-harness-tool-engine-v1"),
        "temperature": float(engine.get("temperature", 0.1)),
        "live": bool(engine.get("live", False)),
        "phi_scrubbed": snap.phi_scrubbed,
        "deterministic_pre_gates": ["triage.screen_acute_message", "rx_rules.evaluate_rx_order_primitives", "emr_connector.verify_three_point_id"],
    }
    return snap, tel


def _merge_compiled_slots(base_summary: dict[str, Any], slot_payload: dict[str, Any]) -> dict[str, Any]:
    """Merge LLM-filled presentation slots onto deterministic widget skeleton without touching Python scores/labs."""
    merged = copy.deepcopy(base_summary)
    if isinstance(slot_payload.get("intake_sufficiency"), str) and slot_payload["intake_sufficiency"] in ("sufficient", "insufficient"):
        merged["intake_sufficiency"] = slot_payload["intake_sufficiency"]
    if isinstance(slot_payload.get("issues"), list) and slot_payload["issues"]:
        merged["issues"] = copy.deepcopy(slot_payload["issues"])
    if "sam_proactive_insight" in slot_payload:
        sam_val = slot_payload.get("sam_proactive_insight")
        merged["sam_proactive_insight"] = str(sam_val).strip() if isinstance(sam_val, str) and sam_val.strip() else None
    return _normalize_summary_orders(merged)


def _deterministic_repair_presentation(summary: dict[str, Any], safe_fallback: dict[str, Any]) -> dict[str, Any]:
    """Deterministic safe fallback repair guaranteeing 5/5 `validate_case_presentation` pass."""
    candidate = _normalize_summary_orders(summary)
    lint = validate_case_presentation(candidate)
    if lint["passed"]:
        return candidate
    # Strip chatty/non-objective Sam insight if R5 failed
    failed_rules = {v["rule_id"] for v in lint["violations"]}
    if "R5_HIGH_SIGNAL_SAM_GATE" in failed_rules:
        candidate["sam_proactive_insight"] = safe_fallback.get("sam_proactive_insight")
    # Remove forbidden legacy essay fields on issues if R3 failed
    if "R3_NO_SLOP_OR_SCAFFOLDING" in failed_rules:
        for iss in candidate.get("issues") or []:
            if isinstance(iss, dict):
                for bad_k in ("subjective", "reasoning", "assessment", "history"):
                    iss.pop(bad_k, None)
    re_lint = validate_case_presentation(candidate)
    if re_lint["passed"]:
        return candidate
    # Final guaranteed safe fallback: restore deterministic baseline issues & Sam signal
    repaired = copy.deepcopy(candidate)
    repaired["issues"] = copy.deepcopy(safe_fallback.get("issues") or [])
    repaired["sam_proactive_insight"] = safe_fallback.get("sam_proactive_insight")
    return _normalize_summary_orders(repaired)


def compile_case_presentation(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    patient_intake_text: str | None = None,
    override_summary: dict[str, Any] | None = None,
    engine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """5-Slot <4,096-Token Presentation Compiler + Bounded 1-Retry `validate_case_presentation` Self-Repair Loop."""
    t0 = time.perf_counter()
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
            .limit(8)
        ).scalars().all()
        mem_dicts = [
            {"tier": m.tier, "topic": m.topic, "summary": m.summary_text, "source_quote": m.source_quote}
            for m in mems
        ]

        engine = dict(engine_override) if engine_override is not None else detect_live_llm_engine()
        base_summary = _normalize_summary_orders(_strip_internal_summary_keys(case.summary_json))
        snap, ctx_tel = _compile_5slot_context_snapshot(
            graph=graph,
            case=case,
            memories=mem_dicts,
            engine=engine,
            current_input=patient_intake_text or case.lead_line,
            channel="workspace",
            template=CLINICAL_PRESENTATION_SLOT_FILL_V1,
        )

        retry_count = 0
        fallback_used = False
        repair_violations_log: list[dict[str, Any]] = []
        model_error: str | None = None
        phi_scrubbed = bool(snap.phi_scrubbed)

        if override_summary is not None:
            candidate = _normalize_summary_orders(_strip_internal_summary_keys(override_summary))
            lint = validate_case_presentation(candidate)
            if not lint["passed"]:
                repair_violations_log = list(lint["violations"])
                retry_count = 1
                if engine.get("live"):
                    rep_snap, _ = _compile_5slot_context_snapshot(
                        graph=graph,
                        case=case,
                        memories=mem_dicts,
                        engine=engine,
                        current_input=patient_intake_text or case.lead_line,
                        channel="workspace",
                        template=PRESENTATION_SELF_REPAIR_V1,
                        repair_diagnostics={
                            "prior_payload": {
                                "intake_sufficiency": candidate.get("intake_sufficiency", "sufficient"),
                                "issues": candidate.get("issues", []),
                                "sam_proactive_insight": candidate.get("sam_proactive_insight"),
                            },
                            "violations": [
                                {
                                    "rule_id": v["rule_id"],
                                    "field_path": v["field_path"],
                                    "message": v["message"],
                                    "repair_hint": v["repair_hint"],
                                }
                                for v in lint["violations"]
                            ],
                        },
                    )
                    rep_call = invoke_structured_json(
                        system_prompt=rep_snap.system_message,
                        user_prompt=rep_snap.user_message,
                        temperature=0.0,
                        max_output_tokens=1100,
                        patient_names_for_scrub=[case.patient_name],
                        engine_override=engine,
                    )
                    phi_scrubbed = phi_scrubbed or rep_call.phi_scrubbed
                    if rep_call.ok and rep_call.json_payload:
                        candidate = _merge_compiled_slots(base_summary, rep_call.json_payload)
                        lint = validate_case_presentation(candidate)
                    else:
                        model_error = rep_call.error
                if not lint["passed"]:
                    candidate = _deterministic_repair_presentation(candidate, base_summary)
                    lint = validate_case_presentation(candidate)
                    fallback_used = True
        elif engine.get("live"):
            call1 = invoke_structured_json(
                system_prompt=snap.system_message,
                user_prompt=snap.user_message,
                temperature=0.1,
                max_output_tokens=1100,
                patient_names_for_scrub=[case.patient_name],
                engine_override=engine,
            )
            phi_scrubbed = phi_scrubbed or call1.phi_scrubbed
            if call1.ok and call1.json_payload:
                candidate = _merge_compiled_slots(base_summary, call1.json_payload)
                lint = validate_case_presentation(candidate)
                if not lint["passed"]:
                    # Bounded 1-retry diagnostic self-repair loop (MAX 1 retry!)
                    retry_count = 1
                    repair_violations_log = list(lint["violations"])
                    rep_snap, _ = _compile_5slot_context_snapshot(
                        graph=graph,
                        case=case,
                        memories=mem_dicts,
                        engine=engine,
                        current_input=patient_intake_text or case.lead_line,
                        channel="workspace",
                        template=PRESENTATION_SELF_REPAIR_V1,
                        repair_diagnostics={
                            "prior_payload": call1.json_payload,
                            "violations": [
                                {
                                    "rule_id": v["rule_id"],
                                    "field_path": v["field_path"],
                                    "message": v["message"],
                                    "repair_hint": v["repair_hint"],
                                }
                                for v in lint["violations"]
                            ],
                        },
                    )
                    call2 = invoke_structured_json(
                        system_prompt=rep_snap.system_message,
                        user_prompt=rep_snap.user_message,
                        temperature=0.0,
                        max_output_tokens=1100,
                        patient_names_for_scrub=[case.patient_name],
                        engine_override=engine,
                    )
                    phi_scrubbed = phi_scrubbed or call2.phi_scrubbed
                    if call2.ok and call2.json_payload:
                        candidate = _merge_compiled_slots(base_summary, call2.json_payload)
                        lint = validate_case_presentation(candidate)
                    else:
                        model_error = call2.error
                    if not lint["passed"]:
                        candidate = _deterministic_repair_presentation(candidate, base_summary)
                        lint = validate_case_presentation(candidate)
                        fallback_used = True
            else:
                model_error = call1.error
                candidate = base_summary
                lint = validate_case_presentation(candidate)
                fallback_used = True
        else:
            candidate = base_summary
            lint = validate_case_presentation(candidate)
            if not lint["passed"]:
                candidate = _deterministic_repair_presentation(candidate, base_summary)
                lint = validate_case_presentation(candidate)
                fallback_used = True

        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))
        telemetry = {
            "provider": engine.get("provider", "deterministic"),
            "model": engine.get("model", "clinic-harness-tool-engine-v1"),
            "region": engine.get("region", "local"),
            "live_model_invoked": bool(engine.get("live")) and not fallback_used,
            "total_tokens_est": snap.total_tokens_est,
            "token_budget_ceiling": snap.token_budget_ceiling,
            "within_budget": snap.within_budget,
            "active_node_ids": list(snap.active_node_ids),
            "subgraph_pruning_ratio": snap.subgraph_pruning_ratio,
            "skills_loaded": list(snap.skills_loaded),
            "slots": [s.to_dict() for s in snap.slots],
            "retry_count": retry_count,
            "linter_passed": bool(lint["passed"]),
            "rules_passed": lint["rules_passed"],
            "rules_total": lint["rules_total"],
            "fallback_used": fallback_used,
            "phi_scrubbed": phi_scrubbed,
            "context_hash": snap.context_hash,
            "latency_ms": latency_ms,
            "repair_violations_fixed": len(repair_violations_log),
            "error": model_error,
        }

        persisted_summary = copy.deepcopy(candidate)
        persisted_summary["_compilation_telemetry"] = telemetry
        persisted_summary["_context_snapshot"] = ctx_tel
        case.summary_json = persisted_summary
        flag_modified(case, "summary_json")
        case.updated_at = utcnow()
        session.flush()

        return {
            "case_id": case.id,
            "pathway_node_id": resolve_pathway_id(case.pathway_node_id),
            "widget_stack": PATHWAY_WIDGET_STACKS.get(resolve_pathway_id(case.pathway_node_id), []),
            "summary": candidate,
            "lint_report": lint,
            "context_snapshot": ctx_tel,
            "compilation_telemetry": telemetry,
        }


def _extract_orders_from_instruction_and_summary(
    *,
    case: CaseLoop,
    instruction_text: str,
    explicit_orders: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize explicit structured orders OR derive implied structured orders from instruction + case summary."""
    if explicit_orders:
        return [
            normalize_order_primitive(o, include_camel_aliases=True)
            for o in explicit_orders
            if isinstance(o, dict)
        ]

    txt = (instruction_text or "").strip()
    low = txt.lower()
    summary = _strip_internal_summary_keys(case.summary_json)
    suggested_all: list[dict[str, Any]] = []
    for iss in summary.get("issues") or []:
        for ord_item in (iss.get("suggested_orders") or []):
            if isinstance(ord_item, dict):
                suggested_all.append(normalize_order_primitive(ord_item, include_camel_aliases=True))

    matched: list[dict[str, Any]] = []
    for ord_item in suggested_all:
        otype = ord_item.get("type")
        if otype == "rx":
            drug_first = str(ord_item.get("drug") or "").lower().split()[0] if ord_item.get("drug") else ""
            action = str(ord_item.get("action") or "").lower()
            if (
                (drug_first and drug_first in low)
                or ("adopt" in low and case.case_key == "chan")
                or ("statin" in low and drug_first in ("simvastatin", "atorvastatin"))
                or ("iron" in low and "ferrous" in drug_first)
                or (("urti" in low or "pack" in low or "finalise" in low) and drug_first in ("paracetamol", "cetirizine"))
            ):
                if action == "stop" and drug_first == "simvastatin" and ("atorvastatin" in low or "adopt" in low or "stop simvastatin" in low):
                    matched.append(ord_item)
                elif action != "stop":
                    matched.append(ord_item)
                elif f"stop {drug_first}" in low or "discontinue" in low:
                    matched.append(ord_item)
        elif otype == "cert":
            if any(k in low for k in ("sick", "cert", "2-day", "2d", "pack", "finalise")):
                matched.append(ord_item)
        elif otype == "lab":
            tname = str(ord_item.get("test_name") or "").lower()
            if any(k in low for k in ("inr", "29 nov", "adopt", "ferritin", "blood", "lab", "renal", "lipid")):
                if any(tok in tname for tok in ("inr", "ferritin", "renal", "lipid")) or "lab" in low or "blood" in low:
                    matched.append(ord_item)
        elif otype == "nurse":
            if any(k in low for k in ("nurse", "rachel", "call", "cantonese", "adherence", "chase", "adopt")):
                matched.append(ord_item)
        elif otype == "goal":
            if any(k in low for k in ("130", "goal", "tighten", "target")):
                updated_goal = dict(ord_item)
                if "130" in low:
                    updated_goal["target_sys"] = 130
                    updated_goal["target_dia"] = 80
                    updated_goal = normalize_order_primitive(updated_goal, include_camel_aliases=True)
                matched.append(updated_goal)
        elif otype == "book":
            if any(k in low for k in ("book", "review", "visit", "16 feb", "flu")):
                matched.append(ord_item)

    # Deduplicate by order id while preserving order
    seen_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for o in matched:
        oid = str(o.get("id") or "")
        if oid and oid in seen_ids:
            continue
        if oid:
            seen_ids.add(oid)
        deduped.append(o)
    return deduped


def _evaluate_orders_rx_safety(
    orders: list[dict[str, Any]],
    patient_context: dict[str, Any],
    *,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Run real deterministic `rx_rules.evaluate_rx_order_primitives` on all `rx` orders."""
    rx_orders = [o for o in orders if isinstance(o, dict) and o.get("type") == "rx"]
    if not rx_orders:
        return {
            "status": "pass",
            "can_sign": True,
            "rx_count": 0,
            "hard_blocks": [],
            "overrides_required": [],
            "findings": [],
        }
    report = evaluate_rx_order_primitives(
        rx_orders,
        patient_context,
        active_meds=patient_context.get("current_meds"),
        override_reason=override_reason,
    )
    rep_dict = report.to_dict()
    hard_blocks = [f for f in rep_dict.get("findings", []) if f.get("severity") == "hard_block"]
    overrides = [f for f in rep_dict.get("findings", []) if f.get("severity") == "override_required"]
    override_ok = bool(getattr(report, "override_accepted", False)) if override_reason else False
    can_sign = (len(hard_blocks) == 0) and (len(overrides) == 0 or override_ok)
    return {
        **rep_dict,
        "can_sign": can_sign,
        "rx_count": len(rx_orders),
        "hard_blocks": hard_blocks,
        "overrides_required": overrides,
        "override_accepted": override_ok,
        "override_error": getattr(report, "override_error", None),
    }


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


def _serialize_case(
    c: CaseLoop,
    traces: list[ExecutionTrace],
    *,
    graph: dict[str, Any] | None = None,
    engine: dict[str, Any] | None = None,
    memories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_summary = c.summary_json or {}
    stored_tel = raw_summary.get("_compilation_telemetry") if isinstance(raw_summary, dict) else None
    clean_summary = _strip_internal_summary_keys(raw_summary)
    lint_report = validate_case_presentation(clean_summary)
    resolved_pw = resolve_pathway_id(c.pathway_node_id)

    if isinstance(stored_tel, dict):
        comp_tel = dict(stored_tel)
        comp_tel["linter_passed"] = bool(lint_report["passed"])
        comp_tel["rules_passed"] = lint_report["rules_passed"]
        comp_tel["rules_total"] = lint_report["rules_total"]
    else:
        eng = engine or detect_live_llm_engine()
        active_nodes = _prune_active_graph_nodes(graph, c.pathway_node_id) if graph else []
        est_tok = min(4096, estimate_tokens(clean_summary) + 420)
        comp_tel = {
            "provider": eng.get("provider", "deterministic"),
            "model": eng.get("model", "clinic-harness-tool-engine-v1"),
            "region": eng.get("region", "local"),
            "live_model_invoked": False,
            "total_tokens_est": est_tok,
            "token_budget_ceiling": 4096,
            "within_budget": est_tok <= 4096,
            "active_node_ids": [str(n.get("id")) for n in active_nodes if n.get("id")],
            "retry_count": 0,
            "linter_passed": bool(lint_report["passed"]),
            "rules_passed": lint_report["rules_passed"],
            "rules_total": lint_report["rules_total"],
            "fallback_used": False,
            "latency_ms": 1,
        }

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
        "summary": clean_summary,
        "widget_stack": PATHWAY_WIDGET_STACKS.get(resolved_pw, []),
        "lint_report": lint_report,
        "compilation_telemetry": comp_tel,
        "timeline": c.timeline_json,
        "goal": c.goal_json,
        "traces": [_serialize_trace(t) for t in traces],
        "updated_at": c.updated_at.isoformat(),
    }


def build_workspace_overview(db: Database, tenant_id: str) -> dict[str, Any]:
    """Return full Clinic Workspace state + Head-of-AI Technical X-Ray payload."""
    engine = detect_live_llm_engine()
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

        mems_by_patient: dict[str, list[dict[str, Any]]] = {}
        for m in memories:
            mems_by_patient.setdefault(m.patient_id, []).append(
                {"tier": m.tier, "topic": m.topic, "summary": m.summary_text, "source_quote": m.source_quote}
            )

        serialized_cases = [
            _serialize_case(
                c,
                traces_by_case.get(c.id, []),
                graph=graph,
                engine=engine,
                memories=mems_by_patient.get(c.patient_id, []),
            )
            for c in cases
        ]
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

        # Always non-null latest_context_window (real <4,096 token telemetry even pre-commit!)
        latest_context: dict[str, Any] | None = traces[0].context_snapshot_json if traces else None
        if not latest_context and cases:
            focus_case = next((c for c in cases if c.state == "needs_decision"), cases[0])
            stored_ctx = (focus_case.summary_json or {}).get("_context_snapshot")
            if isinstance(stored_ctx, dict) and stored_ctx.get("total_tokens_est"):
                latest_context = stored_ctx
            else:
                _, latest_context = _compile_5slot_context_snapshot(
                    graph=graph,
                    case=focus_case,
                    memories=mems_by_patient.get(focus_case.patient_id, []),
                    engine=engine,
                    current_input=focus_case.lead_line,
                    channel="workspace",
                )

        # Aggregate Presentation Linter telemetry across all cases
        cases_passed = sum(1 for c in serialized_cases if c["lint_report"]["passed"])
        rules_passed_total = sum(int(c["lint_report"]["rules_passed"]) for c in serialized_cases)
        rules_possible_total = sum(int(c["lint_report"]["rules_total"]) for c in serialized_cases)
        violations_count = sum(len(c["lint_report"]["violations"]) for c in serialized_cases)
        linter_summary = {
            "cases_total": len(serialized_cases),
            "cases_passed": cases_passed,
            "all_passed": cases_passed == len(serialized_cases),
            "rules_passed_total": rules_passed_total,
            "rules_possible_total": rules_possible_total,
            "violations_count": violations_count,
        }

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
                "llm_runtime": engine,
                "model_runtime": engine,
                "linter_summary": linter_summary,
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
                    "model_region": (
                        f"Vertex AI {engine.get('region', 'asia-east2')} ({engine.get('model')})"
                        if engine.get("provider") == "vertex"
                        else f"{engine.get('provider')} ({engine.get('region', 'local')})"
                    ),
                },
            },
        }


get_workspace_overview = build_workspace_overview


def preview_case_instruction(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    instruction_text: str = "",
    orders: list[dict[str, Any]] | None = None,
    structured_orders: list[dict[str, Any]] | None = None,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """Loop 3 Preview: traverse Practice Graph, normalize 8-primitive orders, run real rx_rules CDS, compile <4,096 context."""
    engine = detect_live_llm_engine()
    explicit_orders = orders if orders is not None else structured_orders
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

        parsed_orders = _extract_orders_from_instruction_and_summary(
            case=case,
            instruction_text=instruction_text,
            explicit_orders=explicit_orders,
        )
        eff_instruction = (instruction_text or "").strip()
        if not eff_instruction and parsed_orders:
            eff_instruction = "; ".join(
                f"{o.get('type')}: {o.get('drug') or o.get('test_name') or o.get('visit_type') or o.get('task') or o.get('metric') or o.get('modality') or o.get('specialty') or o.get('dates') or o.get('id')}"
                for o in parsed_orders
            )

        plan = compile_instruction_plan(
            case_key=case.case_key,
            pathway_node_id=case.pathway_node_id,
            instruction_text=eff_instruction,
            effective_graph=graph,
            patient_name=case.patient_name,
            agent_memory_entries=mem_dicts,
        )

        # Evaluate real deterministic HK rx_rules CDS on any `rx` orders
        patient_ctx = _build_patient_clinical_context(session, tenant_id, case)
        rx_safety_report = _evaluate_orders_rx_safety(
            parsed_orders,
            patient_ctx,
            override_reason=override_reason,
        )

        # Upgrade context_snapshot with real <4,096 CJK-aware ContextCompiler telemetry while preserving legacy keys
        _, real_ctx = _compile_5slot_context_snapshot(
            graph=graph,
            case=case,
            memories=[{"tier": m.tier, "topic": m.topic, "summary": m.summary_text, "source_quote": m.source_quote} for m in mems],
            engine=engine,
            current_input=eff_instruction,
            channel="workspace",
        )
        merged_ctx = dict(plan.get("context_snapshot") or {})
        merged_ctx.update(real_ctx)
        plan["context_snapshot"] = merged_ctx
        plan["case_id"] = case.id
        plan["parsed_orders"] = parsed_orders
        plan["rx_safety_report"] = rx_safety_report
        if any(o.get("requires_sign") for o in parsed_orders):
            plan["requires_irreversible_commit"] = True
            if not plan.get("undo_window_seconds"):
                plan["undo_window_seconds"] = int(graph["clinic_standards"].get("undo_window_seconds", 10))
        return plan


def commit_case_instruction(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    instruction_text: str = "",
    orders: list[dict[str, Any]] | None = None,
    structured_orders: list[dict[str, Any]] | None = None,
    override_reason: str | None = None,
    actor_id: str = "doc_locum_sarah",
    actor_name: str = "Locum Dr. Sarah Wong",
) -> dict[str, Any]:
    """Loop 3 Commit: normalize orders, run rx_rules CDS, persist ExecutionTrace + DurableWorkflowEngine 10s Undo outbox, write additive EMR addendum (zero private Sam chat)."""
    engine = detect_live_llm_engine()
    wf_engine = DurableWorkflowEngine()
    explicit_orders = orders if orders is not None else structured_orders

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

        parsed_orders = _extract_orders_from_instruction_and_summary(
            case=case,
            instruction_text=instruction_text,
            explicit_orders=explicit_orders,
        )
        eff_instruction = (instruction_text or "").strip()
        if not eff_instruction and parsed_orders:
            eff_instruction = "; ".join(
                f"{o.get('type')}: {o.get('drug') or o.get('test_name') or o.get('visit_type') or o.get('task') or o.get('metric') or o.get('modality') or o.get('specialty') or o.get('dates') or o.get('id')}"
                for o in parsed_orders
            )

        patient_ctx = _build_patient_clinical_context(session, tenant_id, case)
        rx_safety_report = _evaluate_orders_rx_safety(
            parsed_orders,
            patient_ctx,
            override_reason=override_reason,
        )

        plan = compile_instruction_plan(
            case_key=case.case_key,
            pathway_node_id=case.pathway_node_id,
            instruction_text=eff_instruction,
            effective_graph=graph,
            patient_name=case.patient_name,
            agent_memory_entries=mem_dicts,
        )
        _, real_ctx = _compile_5slot_context_snapshot(
            graph=graph,
            case=case,
            memories=[{"tier": m.tier, "topic": m.topic, "summary": m.summary_text, "source_quote": m.source_quote} for m in mems],
            engine=engine,
            current_input=eff_instruction,
            channel="workspace",
        )
        merged_ctx = dict(plan.get("context_snapshot") or {})
        merged_ctx.update(real_ctx)
        plan["context_snapshot"] = merged_ctx

        now = utcnow()
        irreversible = bool(plan["requires_irreversible_commit"]) or any(bool(o.get("requires_sign")) for o in parsed_orders)
        undo_sec = int(plan["undo_window_seconds"] or graph["clinic_standards"].get("undo_window_seconds", 10)) if irreversible else 0
        undo_exp = now + timedelta(seconds=undo_sec) if undo_sec > 0 else None

        # Compact additive EMR addendum — strictly excludes ephemeral private Sam chat
        addendum_entries = list(plan["emr_addendum"]["entries"])
        if not addendum_entries and parsed_orders:
            for o in parsed_orders:
                otype = o.get("type")
                if otype == "rx":
                    addendum_entries.append(
                        f"Rx ({o.get('action', 'start')}): {o.get('drug', '')} {o.get('freq', '')} {o.get('days', '')}".strip()
                    )
                elif otype == "cert":
                    omit_note = "diagnosis omitted on employer copy" if o.get("omit_employer_diagnosis", True) else "diagnosis included"
                    addendum_entries.append(
                        f"Sick leave certificate: {o.get('days_off', 1)}d ({o.get('dates') or o.get('start_date') or 'today'}; {omit_note})."
                    )
                elif otype == "lab":
                    addendum_entries.append(f"Lab order: {o.get('test_name', '')} ({o.get('when', 'scheduled')}).")

        emr_add = {}
        if addendum_entries:
            emr_add = write_additive_emr_addendum(
                session,
                tenant_id=tenant_id,
                patient_id=case.patient_id,
                signed_by=actor_name,
                entries=addendum_entries,
            )

        trace = ExecutionTrace(
            id=new_id("trc"),
            tenant_id=tenant_id,
            case_loop_id=case.id,
            actor_id=actor_id,
            actor_name=actor_name,
            instruction_text=eff_instruction,
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

        # Enqueue durable dispatch timer via DurableWorkflowEngine for irreversible 10s Undo window
        if irreversible and undo_exp is not None:
            wf_engine.schedule_action_commit_dispatch(
                session,
                tenant_id,
                trace_id=trace.id,
                case_loop_id=case.id,
                run_after=undo_exp,
            )

        # Loop 4 state transition: Needs Decision -> Running (with clear baton!) or Scheduled
        has_nurse_order = bool(plan["nurse_handoff"]) or any(o.get("type") == "nurse" for o in parsed_orders)
        if has_nurse_order:
            case.state = "running"
            case.baton = "with_nurse"
        elif plan["categorised_plan"]["patient_task"]:
            case.state = "running"
            case.baton = "with_patient"
        else:
            case.state = "scheduled"
            case.baton = "idle"

        # Update Wong goal if tightened
        if case.case_key == "wong" and ("130" in eff_instruction or any(o.get("type") == "goal" and o.get("target_sys") == 130 for o in parsed_orders)):
            goal = copy.deepcopy(case.goal_json or {})
            goal["target"] = "< 130/80 mmHg"
            goal["target_sys"] = 130
            goal["target_dia"] = 80
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
                instruction_text=eff_instruction,
            )

        session.flush()
        handoff_payload = {
            "patient_id": case.patient_id,
            "patient_name": case.patient_name,
            "case_id": case.id,
            "case_key": case.case_key,
            "trace_id": trace.id,
            "actor_id": actor_id,
            "signed_by": actor_name,
            "doctor_outcome_summary": plan["doctor_outcome_summary"],
            "parsed_orders": parsed_orders,
            "rx_orders": [o for o in parsed_orders if o.get("type") == "rx"],
            "cert_orders": [o for o in parsed_orders if o.get("type") == "cert"],
            "lab_orders": [o for o in parsed_orders if o.get("type") == "lab"],
            "imaging_orders": [o for o in parsed_orders if o.get("type") == "imaging"],
            "refer_orders": [o for o in parsed_orders if o.get("type") == "refer"],
            "nurse_orders": [o for o in parsed_orders if o.get("type") == "nurse"],
            "book_orders": [o for o in parsed_orders if o.get("type") == "book"],
            "goal_orders": [o for o in parsed_orders if o.get("type") == "goal"],
            "emr_addendum": emr_add,
            "rx_safety_report": rx_safety_report,
            "nurse_handoff": plan["nurse_handoff"],
            "excludes_ephemeral_sam_chat": True,
        }
        return {
            "trace": _serialize_trace(trace),
            "case_state": case.state,
            "case_baton": case.baton,
            "curator_override_logged": curator_meta,
            "parsed_orders": parsed_orders,
            "rx_safety_report": rx_safety_report,
            "handoff_payload": handoff_payload,
        }


def undo_case_execution(db: Database, *, tenant_id: str, case_id: str, trace_id: str) -> dict[str, Any]:
    """Cancel an irreversible execution trace inside its 10-second Undo window, cancel queued outbox events, and restore Needs Decision."""
    wf_engine = DurableWorkflowEngine()
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

        now_dt = utcnow()
        trace.status = "undone"
        if isinstance(trace.emr_addendum_json, dict) and trace.emr_addendum_json:
            retracted_addendum = dict(trace.emr_addendum_json)
            retracted_addendum["retracted"] = True
            retracted_addendum["retracted_at"] = now_dt.isoformat()
            trace.emr_addendum_json = retracted_addendum
            flag_modified(trace, "emr_addendum_json")

        cancelled_count = wf_engine.cancel_pending(
            session,
            tenant_id,
            event_type=EVENT_ACTION_COMMIT_DISPATCH,
            trace_id=trace.id,
        )

        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is not None:
            case.state = "needs_decision"
            case.baton = "with_doctor"
            case.timeline_json = [item for item in (case.timeline_json or []) if item.get("trace_id") != trace.id]
            flag_modified(case, "timeline_json")
            case.updated_at = now_dt
        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role="clinician",
            actor_id=trace.actor_id,
            action="action.commit_undone",
            resource_type="execution_trace",
            resource_id=trace.id,
            metadata={"case_loop_id": case_id, "cancelled_outbox_events": cancelled_count},
        )
        session.flush()
        return {
            "status": "undone",
            "trace_id": trace.id,
            "case_state": "needs_decision",
            "cancelled_outbox_events": cancelled_count,
        }


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
    patient_allergies: list[str] | None = None,
    vitals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Loop 1 wired to `triage.screen_acute_message()` + `triage.extract_intake()` + `rx_rules` + bilingual 3-tier async triage."""
    with db.tenant_session(tenant_id) as session:
        graph = get_effective_practice_graph(session, tenant_id)
        grace_hours = int(graph.get("clinic_standards", {}).get("grace_window_hours", 72))
        result = classify_bilingual_async_triage(
            message_text=message_text,
            hours_since_last_visit=hours_since_last_visit,
            grace_window_hours=grace_hours,
            patient_g6pd=patient_g6pd,
            patient_allergies=patient_allergies,
            vitals=vitals,
        )
        # Union any additional Practice Graph guard markers cleanly (keeping triage.py's Cantonese hyperbole safety)
        graph_res = classify_inbound_triage(
            message_text=message_text,
            hours_since_last_visit=hours_since_last_visit,
            effective_graph=graph,
            patient_g6pd=patient_g6pd,
        )
        for g in graph_res.get("guards_fired", []):
            if g == "crisis_999_triggered" and result["triage_screen"].get("suppressed_negations"):
                continue
            if g not in result["guards_fired"]:
                result["guards_fired"].append(g)

        result["graph_version_tag"] = graph["version_tag"]
        result["careline_name"] = graph["careline_profile"]["name"]
        return result


def ingest_workspace_document(
    db: Database,
    *,
    tenant_id: str,
    external_doc_ref: str,
    doc_kind: str,
    source_org: str,
    claimed_mrn_or_hkid: str,
    claimed_name: str,
    claimed_dob: str,
    verbatim_quote: str,
    page_ref: str = "p.1",
    source_text: str | None = None,
    parsed_diff: dict[str, Any] | None = None,
    proposed_meds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Loop 2 wired to `emr_connector.verify_three_point_id()` + `verification.verify_external_document_payload()`.

    Fails closed strictly to Nurse Rachel quarantine on `< 3/3` ID match OR any multi-page HKID/DOB/MRN collision.
    """
    full_source_text = (source_text or "").strip() or (
        f"Document: {external_doc_ref} ({source_org})\n"
        f"Patient: {claimed_name} | DOB: {claimed_dob} | ID: {claimed_mrn_or_hkid}\n"
        f"{verbatim_quote}"
    )

    with db.tenant_session(tenant_id) as session:
        ensure_workspace_seeded(session, tenant_id)
        three_pt = verify_three_point_id(
            claimed_mrn_or_hkid=claimed_mrn_or_hkid,
            claimed_name=claimed_name,
            claimed_dob=claimed_dob,
        )
        matched_pid = three_pt.get("matched_patient_id")
        patient_ctx: dict[str, Any] = {}
        if matched_pid and matched_pid in EMR_PATIENT_REGISTRY:
            reg = EMR_PATIENT_REGISTRY[matched_pid]
            patient_ctx = {
                "name_en": reg["name_en"],
                "dob": reg["dob"],
                "g6pd_deficient": reg.get("g6pd_deficient", False),
                "allergies": reg.get("allergies", []),
                "current_meds": reg.get("active_meds", []),
            }

        doc_ver = verify_external_document_payload(
            full_source_text,
            verbatim_quote=verbatim_quote,
            parsed_diff=parsed_diff,
            proposed_meds=proposed_meds,
            patient_context=patient_ctx,
        )

        collision = bool(doc_ver.get("has_identity_collision"))
        failed_3pt = three_pt["match_status"] != "exact_3pt_match"
        quarantined = failed_3pt or collision

        match_status = "quarantined_nurse" if quarantined else "exact_3pt_match"
        final_patient_id = None if quarantined else matched_pid
        route = "nurse_quarantine" if quarantined else "doctor_my_work"

        checks_payload: dict[str, Any] = {
            **three_pt["checks"],
            "claimed_id": claimed_mrn_or_hkid,
            "claimed_name": claimed_name,
            "claimed_dob": claimed_dob,
            "has_identity_collision": collision,
            "collision_reasons": list(doc_ver.get("collision_reasons") or []),
            "quote_verified": bool(doc_ver.get("quote_verified")),
            "dose_conflicts_count": len(doc_ver.get("dose_conflicts") or []),
        }
        if collision:
            checks_payload["quarantine_reason"] = (
                "Fail-closed multi-page identity collision detected ("
                + "; ".join(doc_ver.get("collision_reasons") or ["distinct patient identifiers across pages"])
                + "). Routed strictly to Nurse Rachel quarantine — never exposed to doctor queue."
            )

        row = DocumentIngestion(
            id=new_id("doc"),
            tenant_id=tenant_id,
            external_doc_ref=external_doc_ref,
            doc_kind=doc_kind,
            source_org=source_org,
            matched_patient_id=final_patient_id,
            match_status=match_status,
            match_checks_json=checks_payload,
            verbatim_quote=verbatim_quote,
            page_ref=page_ref,
            parsed_diff_json=parsed_diff or {},
        )
        session.add(row)
        action_name = (
            "document_quarantined_id_collision"
            if collision
            else ("document_quarantined_3pt_mismatch" if failed_3pt else "document_ingested")
        )
        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role="system",
            actor_id="workspace_doc_gate",
            action=action_name,
            resource_type="document_ingestion",
            resource_id=row.id,
            metadata={
                "external_doc_ref": external_doc_ref,
                "match_status": match_status,
                "matched_patient_id": final_patient_id,
                "has_identity_collision": collision,
            },
        )
        session.flush()
        return {
            "id": row.id,
            "external_doc_ref": row.external_doc_ref,
            "doc_kind": row.doc_kind,
            "source_org": row.source_org,
            "match_status": match_status,
            "matched_patient_id": final_patient_id,
            "route": route,
            "quarantined": quarantined,
            "checks": checks_payload,
            "verification": doc_ver,
        }


def _deterministic_sam_qa_answer(case: CaseLoop, question: str) -> str:
    """Grounded deterministic private registrar Q&A fallback when offline or if LLM output fails safety screen."""
    low = (question or "").strip().lower()
    k = case.case_key
    if k == "wingyan":
        if any(w in low for w in ("period", "gi", "bleed", "menst")):
            return "From her 10 Nov consult note & intake: regular 5-day periods (not heavy) and denies melaena, dyspepsia, or weight loss. No GI red flags recorded."
        if any(w in low for w in ("paracetamol", "panadol", "taken", "med")):
            return "She has not taken analgesia yet today — she checked first because she was unsure about Aspirin with G6PD (G6PD contraindication active)."
        return "KingMed 8 Nov panel shows Ferritin 14 ng/mL (low, trending 32 → 21 → 14; BSG non-anaemic iron deficiency threshold < 15), Hb 12.1 g/dL, WBC 6.4, LDL 2.3, Glucose 4.8, eGFR 104. G6PD deficient + Penicillin allergy active."
    if k == "chan":
        if any(w in low for w in ("inr", "renal", "egfr")):
            return "Last clinic INR was 2.9 on 14 Sep (on Warfarin 5mg); QEH discharge INR was 2.4 on 21 Nov (after reduction to 3mg; CHA₂DS₂-VASc 3 / HAS-BLED 1). Last eGFR 71 mL/min (Sep 2026)."
        if any(w in low for w in ("refill", "dispens")):
            return "No clinic refills dispensed since discharge — next repeat was due 1 Dec, and no autonomous hold or premature message was sent."
        return "QEH Discharge Summary (p.2) specifies Warfarin 3mg PO daily (reduced from 5mg) and Atorvastatin 20mg PO nocte (replacing Simvastatin 20mg), with next INR due 29 Nov (NICE NG196)."
    if k == "wong":
        if any(w in low for w in ("renal", "electrolyte", "u&e")):
            return "Baseline renal panel on 12 Sep (pre-Amlodipine): Na 140, K 4.2, Creatinine 64 µmol/L, eGFR 89 mL/min — all normal."
        if any(w in low for w in ("140", "range", "high")):
            return "Zero readings > 140 systolic this week; 7-day home range was 124/76 to 134/82 (average 129/79 across 7/7 mornings, inside < 135/85 NICE NG136 threshold)."
        return "Ms. Wong is stable on Amlodipine 5mg OD with 100% adherence (7d home avg 129/79, within < 135/85 threshold) and routine review scheduled Mon 21 Dec 10:00."
    if any(w in low for w in ("risk", "cv")):
        return "36M non-smoker, BP 118/74, fasting glucose 5.0 — HK 10-year CVD risk < 2% (low threshold), supporting lifestyle trial + 3-month fasting lipid repeat."
    return "Automatic booking chase triggers after 3 days (2 nudges / 72h cap -> Nurse Rachel first) if Mr. Lee hasn't booked, or delegate directly to Nurse Rachel."


def ask_case_agent(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    question: str,
    actor_name: str = "Locum Dr. Sarah Wong",
    engine_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Live Private Sam Q&A routed through `agent_harness.invoke_clinical_qa()` + `SAM_PRIVATE_REGISTRAR_QA_V1` + `screen_agent_output`.

    Strictly ephemeral & private (`ephemeral_private: True`, `written_to_emr: False`) — NEVER written to Clinic EMR.
    """
    engine = dict(engine_override) if engine_override is not None else detect_live_llm_engine()
    with db.tenant_session(tenant_id) as session:
        ensure_workspace_seeded(session, tenant_id)
        graph = get_effective_practice_graph(session, tenant_id)
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is None:
            raise ValueError("Case not found.")

        q = (question or "").strip()
        inj_signals = detect_prompt_injection_signals(q)
        mems = session.execute(
            select(AgentMemoryEntry)
            .where(AgentMemoryEntry.tenant_id == tenant_id, AgentMemoryEntry.patient_id == case.patient_id)
            .order_by(AgentMemoryEntry.created_at.desc())
            .limit(6)
        ).scalars().all()
        mem_dicts = [
            {"tier": m.tier, "topic": m.topic, "summary": m.summary_text, "source_quote": m.source_quote}
            for m in mems
        ]

        snap, ctx_tel = _compile_5slot_context_snapshot(
            graph=graph,
            case=case,
            memories=mem_dicts,
            engine=engine,
            current_input=q,
            channel="private_sam",
            template=SAM_PRIVATE_REGISTRAR_QA_V1,
        )

        ans = ""
        fallback_used = False
        model_meta: dict[str, Any] = {
            "provider": engine.get("provider", "deterministic"),
            "model": engine.get("model", "clinic-harness-tool-engine-v1"),
            "region": engine.get("region", "local"),
            "total_tokens_est": snap.total_tokens_est,
            "phi_scrubbed": snap.phi_scrubbed,
            "fallback_used": False,
        }

        if engine.get("live") and not inj_signals:
            call_res = invoke_clinical_qa(
                system_prompt=snap.system_message,
                user_prompt=snap.user_message,
                temperature=0.1,
                max_output_tokens=360,
                patient_names_for_scrub=[case.patient_name],
                engine_override=engine,
            )
            model_meta.update(call_res.to_dict())
            if call_res.ok and call_res.text:
                screen = screen_agent_output(
                    call_res.text,
                    banned_claims=SAM_PRIVATE_REGISTRAR_QA_V1.banned_output_patterns,
                    channel="workspace",
                    is_sam_note=True,
                )
                if screen.passed and not any(p in call_res.text.lower() for p in CHATTY_SAM_PHRASES):
                    ans = call_res.text.strip()
                else:
                    fallback_used = True
                    model_meta["screen_violations"] = screen.violations
            else:
                fallback_used = True

        if not ans:
            ans = _deterministic_sam_qa_answer(case, q)
            if engine.get("live"):
                fallback_used = True

        model_meta["fallback_used"] = fallback_used
        model_meta["objective_signal"] = has_objective_sam_signal(ans)

        summary = copy.deepcopy(case.summary_json or {})
        thread = list(summary.get("agent_thread") or [])
        thread.append({"who": "doctor", "actor": actor_name, "text": q, "ephemeral_private": True})
        thread.append({
            "who": "agent",
            "actor": graph["careline_profile"]["name"],
            "text": ans,
            "ephemeral_private": True,
        })
        summary["agent_thread"] = thread
        case.summary_json = summary
        flag_modified(case, "summary_json")
        case.updated_at = utcnow()
        session.flush()
        return {
            "case_id": case.id,
            "question": q,
            "answer": ans,
            "agent_thread": thread,
            "ephemeral_private": True,
            "written_to_emr": False,
            "objective_signal": has_objective_sam_signal(ans),
            "prompt_injection_detected": bool(inj_signals),
            "model_telemetry": model_meta,
            "context_snapshot": ctx_tel,
        }


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
