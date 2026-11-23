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

    # 3. 4-State Case Loops (Zero UI scaffolding slop: AI title + 1-line lead + 3–4 implicit bullets + attachments + graphs)
    cases = [
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_chan_warfarin", patient_name="CHAN Tai Man", patient_age_sex="62M",
            case_key="chan", pathway_node_id="ebm.pathway.post_discharge_anticoag",
            graph_version_tag=graph["version_tag"],
            state="needs_decision", baton="with_doctor", risk_tier="high",
            ai_title="Warfarin & statin mismatch on QEH discharge ahead of 11:30 visit",
            lead_line="QEH lowered Warfarin to 3mg and switched to Atorvastatin; confirm whether to adopt now and assign Nurse Rachel a Cantonese pillbox call.",
            next_checkpoint_label="Today 11:30 clinic visit",
            next_checkpoint_at=now + timedelta(hours=2),
            summary_json={
                "bullets": [
                    "QEH discharge (22 Nov): Warfarin 3mg daily (INR 2.4) and Atorvastatin 20mg nocte.",
                    "Clinic active chart still lists Warfarin 5mg daily and Simvastatin 20mg nocte.",
                    "Booked post-hospital visit today at 11:30 with Locum Dr. Sarah.",
                    "Next INR due 29 Nov 2026 (7 days post-dose change).",
                ],
                "attachments": [
                    {
                        "id": qeh_doc.id,
                        "label": "QEH Discharge Summary (22 Nov, p.2)",
                        "quote": qeh_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "quick_options": [
                    {"label": "Adopt QEH 3mg + Nurse Cantonese call + 29 Nov INR", "instruction": "Adopt QEH Warfarin 3mg and Atorvastatin, stop Simvastatin, ask Nurse Rachel for Cantonese pillbox call today, and book repeat INR on 29 Nov"},
                    {"label": "Discuss during 11:30 visit", "instruction": "Discuss Warfarin and statin changes during today's 11:30 booked visit"},
                ],
                "original_chat": [
                    {"actor": "Sam", "time": "08:30", "text": "Welcome home from QEH, Mr. Chan. Reminder of your follow-up visit today at 11:30 — please bring your hospital medication bags."}
                ],
            },
            timeline_json=[
                {"date": "22 Nov", "label": "Discharged from QEH (AFib) — Warfarin reduced 5mg → 3mg, switched to Atorvastatin."},
                {"date": "Today 08:30", "label": "Discharge letter matched cleanly (3-pt ID); discrepancy flagged ahead of 11:30 visit."},
            ],
            goal_json={},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_lam_urti_demo", patient_name="LAM Wing-Yan", patient_age_sex="32F",
            case_key="wingyan", pathway_node_id="ebm.pathway.low_ferritin_normal_hb",
            graph_version_tag=graph["version_tag"],
            state="needs_decision", baton="with_doctor", risk_tier="standard",
            ai_title="URTI consult prep & Ferritin 14 follow-up (14:30 booked slot)",
            lead_line="Wing-Yan booked a 14:30 teleconsult for fever/sore throat and Ferritin 14; confirm G6PD-safe relief, optional alternate-day oral iron, and Feb repeat blood test.",
            next_checkpoint_label="Today 14:30 teleconsult",
            next_checkpoint_at=now + timedelta(hours=5),
            summary_json={
                "bullets": [
                    "Sore throat, cough, 38.2°C ×1d, Home RAT negative (red flags clear); requests 2-day sick note.",
                    "8 Nov Ferritin 14 ng/mL (down from 32 → 21 → 14), Hb normal 12.1 g/dL.",
                    "Safety alerts: G6PD deficient (avoid Aspirin) and Penicillin allergy.",
                    "Staged tasks: 9 Feb morning nurse repeat Ferritin draw; defer $0 flu shot 14 days.",
                ],
                "trend_chart": {
                    "kind": "ferritin",
                    "title": "Ferritin (ng/mL)",
                    "unit": "ng/mL",
                    "target_label": "Lower ref 15",
                    "target_value": 15,
                    "points": [{"label": "Nov 24", "v": 32}, {"label": "Nov 25", "v": 21}, {"label": "8 Nov", "v": 14}],
                },
                "attachments": [
                    {
                        "id": km_doc.id,
                        "label": "KingMed Blood Panel (8 Nov)",
                        "quote": km_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "quick_options": [
                    {"label": "Sign G6PD-safe URTI pack + add alternate-day oral iron + Feb 9 bloods", "instruction": "Sign G6PD-safe Paracetamol/Cetirizine + 2-day sick note, add alternate-day oral iron to consult plan, and book Tue 9 Feb repeat Ferritin"},
                    {"label": "Sign URTI pack only + Feb 9 repeat Ferritin", "instruction": "Finalise URTI pack with Paracetamol, Cetirizine, 2-day sick note, and book Tue 9 Feb repeat Ferritin blood draw"},
                ],
                "original_chat": [
                    {"actor": "LAM Wing-Yan", "time": "09:12", "text": "Woke up with sore throat & 38.2°C fever — can I start iron for Ferritin 14 too, and get a 2-day sick note?"},
                    {"actor": "Sam", "time": "09:12", "text": "Reminder from your file: avoid Aspirin (G6PD). Here are open teleconsult and clinic slots with Dr. Sarah Wong today so both can be reviewed together."},
                    {"actor": "LAM Wing-Yan", "time": "09:14", "text": "Booked 14:30 teleconsult today."}
                ],
            },
            timeline_json=[
                {"date": "8 Nov", "label": "Annual checkup panel: Ferritin 14 ng/mL (low), Hb 12.1 (normal)."},
                {"date": "Today 09:14", "label": "Selected 14:30 teleconsult option; chart prepped for Locum Dr. Sarah."},
            ],
            goal_json={},
        ),
        CaseLoop(
            id=new_id("case"), tenant_id=tenant_id,
            patient_id="pat_wong_htn", patient_name="WONG Mei Ling", patient_age_sex="58F",
            case_key="wong", pathway_node_id="ebm.pathway.chronic_htn_home_bp",
            graph_version_tag=graph["version_tag"],
            state="scheduled", baton="idle", risk_tier="low",
            ai_title="Hypertension on target — 21 Dec review scheduled",
            lead_line="7-day home BP averaged 129/79 mmHg on Amlodipine 5mg OD (within <135/85 goal); no doctor action needed before 21 Dec review.",
            next_checkpoint_label="14 Dec pre-visit BP check · 21 Dec review",
            next_checkpoint_at=now + timedelta(days=21),
            summary_json={
                "bullets": [
                    "Asymptomatic on Amlodipine 5mg OD; 100% adherence, no ankle oedema.",
                    "BP improved 152/94 (Sep) → 138/86 (Oct) → 129/79 7-day average (Nov).",
                    "Calendar: 4-week GP Hypertension Review booked Mon 21 Dec 10:00.",
                    "Task (Sam): Collect next 7-day BP log on 14 Dec; reopen only if avg > 140/90.",
                ],
                "trend_chart": {
                    "kind": "bp",
                    "title": "Blood Pressure (mmHg)",
                    "unit": "mmHg",
                    "target_label": "Goal < 135/85",
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
                        "label": "7-Day Home BP Log (Nov)",
                        "quote": "7-day home average: 129/79 mmHg (7/7 logged; range 124/76–134/82).",
                        "verified_3pt": True,
                    }
                ],
                "quick_options": [
                    {"label": "Tighten goal to <130/80 + pre-visit renal bloods", "instruction": "Tighten BP goal to <130/80 and book pre-visit renal bloods ahead of 21 Dec review"},
                    {"label": "Task: Nurse Rachel follow-up call", "instruction": "Ask Nurse Rachel for a 5-min follow-up call to check home cuff technique"},
                ],
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
            ai_title="Borderline LDL 3.6 — confirming 12 Feb fasting blood draw",
            lead_line="Sam offered Fri 12 Feb 08:00 morning nurse blood draw; waiting for patient slot confirmation (0 doctor minutes).",
            next_checkpoint_label="Waiting on patient slot tap",
            next_checkpoint_at=now + timedelta(days=2),
            summary_json={
                "bullets": [
                    "2 Nov lipid panel: LDL 3.6 mmol/L (borderline; ref < 3.4), triglycerides normal.",
                    "Sam sent 3-month repeat fasting blood-draw options per clinic standard.",
                    "Once patient confirms slot, loop moves automatically to Scheduled (12 Feb).",
                ],
                "attachments": [
                    {
                        "id": ldl_doc.id,
                        "label": "KingMed Lipid Panel (2 Nov)",
                        "quote": ldl_doc.verbatim_quote,
                        "verified_3pt": True,
                    }
                ],
                "quick_options": [
                    {"label": "Also offer 10-min GP results review after bloods", "instruction": "Book 10-min GP results review 1 week after the Feb fasting lipid draw"},
                ],
                "original_chat": [
                    {"actor": "Sam", "time": "Yesterday", "text": "Following up on your 2 Nov checkup (LDL 3.6) — tap to confirm your 3-month fasting morning blood test on Fri 12 Feb at 08:00."}
                ],
            },
            timeline_json=[
                {"date": "2 Nov", "label": "Checkup lipid panel: LDL 3.6 mmol/L; lifestyle + 3-month repeat advised."},
                {"date": "Yesterday", "label": "Sam sent 1-tap fasting blood-draw booking options (with patient)."},
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
