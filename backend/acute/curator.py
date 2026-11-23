"""Clinic Curator & Onboarding Moulding Engine (Loop 5).

Manages the 3-Layer Clinical Practice Knowledge Graph lifecycle:
- Phase 5A (Onboarding Moulding): Moulds the Gold-Standard EBM Base Tree into a tenant's
  Practice Overlay Graph (commercial windows, corporate plans, nurse delegation, Careline AI profile),
  strictly validated against the Immutable Safety Floor.
- Phase 5B (Continuous Curation): Captures structured `override_diff` telemetry whenever doctors
  customize or override a traversed recommendation, clusters recurring clinic patterns, verifies EBM
  Safety Floor compliance, and queues versioned Proposed Standard Diffs for Lead GP approval.
- Runtime Locum Continuity (`query_practice_playbook`): Answers locum/staff questions directly by
  traversing the active versioned Practice Graph with explicit node provenance.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import (
    AgentMemoryEntry,
    CuratorProposal,
    PracticeGraphVersion,
    new_id,
    record_audit,
    utcnow,
)
from .practice_graph import (
    DEFAULT_CARELINE_PROFILE,
    DEFAULT_PRACTICE_OVERLAY,
    EBM_BASE_VERSION,
    SafetyFloorViolation,
    build_effective_graph,
    compute_graph_hash,
    validate_careline_profile,
    validate_overlay_against_safety_floor,
)


def _bump_minor_version(tag: str) -> str:
    m = re.match(r"^v(\d+)\.(\d+)$", (tag or "v2.4").strip())
    if not m:
        return "v2.5"
    return f"v{int(m.group(1))}.{int(m.group(2)) + 1}"


def ensure_active_practice_graph(session: Session, tenant_id: str) -> PracticeGraphVersion:
    """Ensure the tenant has an active versioned Practice Graph (v2.4) and initial Curator proposal."""
    active = session.execute(
        select(PracticeGraphVersion)
        .where(PracticeGraphVersion.tenant_id == tenant_id, PracticeGraphVersion.status == "active")
        .order_by(PracticeGraphVersion.created_at.desc())
    ).scalars().first()
    if active is None:
        overlay = copy.deepcopy(DEFAULT_PRACTICE_OVERLAY)
        profile = copy.deepcopy(DEFAULT_CARELINE_PROFILE)
        active = PracticeGraphVersion(
            id=new_id("pgv"),
            tenant_id=tenant_id,
            version_tag="v2.4",
            ebm_base_version=EBM_BASE_VERSION,
            content_hash=compute_graph_hash(overlay, profile),
            status="active",
            careline_profile_json=profile,
            overlay_json=overlay,
            approved_by="Dr. Kenneth Lam (Lead GP)",
            change_summary="Initial Practice Graph moulded during Central Medical clinic onboarding.",
        )
        session.add(active)
        session.flush()

    existing_prop = session.execute(
        select(CuratorProposal).where(CuratorProposal.tenant_id == tenant_id)
    ).scalars().first()
    if existing_prop is None:
        session.add(
            CuratorProposal(
                id=new_id("cur"),
                tenant_id=tenant_id,
                target_node_id="ebm.pathway.borderline_ldl",
                title="Borderline LDL (3.4–4.1) · Add 3-Month Repeat + Paid GP Review Option",
                rationale=(
                    "Observed 4× across Central Medical checkups: doctors consistently asked Sam to schedule a "
                    "10-min fasting nurse blood draw at 3 months plus a follow-up GP review slot if repeat LDL stays > 3.4 mmol/L."
                ),
                observed_count=4,
                proposed_patch_json={
                    "node_overrides": {
                        "ebm.pathway.borderline_ldl": {
                            "repeat_months": 3,
                            "fasting_hours": 8,
                            "auto_book_paid_review": True,
                            "practice_note": "Standard Central Medical pathway: 8h fasting nurse draw at 3 months + GP review booked if repeat LDL > 3.4.",
                        }
                    }
                },
                ebm_safety_check_json={
                    "passed": True,
                    "safety_floor_intact": True,
                    "guideline_alignment": "Aligned with HK Cardiovascular Risk Primary Care Framework",
                },
                evidence_case_ids_json=["case_lee", "hist_ldl_01", "hist_ldl_02", "hist_ldl_03"],
                status="pending_lead_gp",
            )
        )
        session.flush()

    return active


def get_effective_practice_graph(session: Session, tenant_id: str) -> dict[str, Any]:
    active = ensure_active_practice_graph(session, tenant_id)
    graph = build_effective_graph(
        version_tag=active.version_tag,
        overlay=active.overlay_json,
        careline_profile=active.careline_profile_json,
        approved_by=active.approved_by,
    )
    graph["version_id"] = active.id
    graph["change_summary"] = active.change_summary
    graph["created_at"] = active.created_at.isoformat()
    return graph


def mould_practice_graph(
    session: Session,
    *,
    tenant_id: str,
    approved_by: str,
    careline_profile_patch: dict[str, Any] | None = None,
    overlay_patch: dict[str, Any] | None = None,
    change_summary: str = "Practice Graph updated via Clinic Onboarding / Settings",
) -> dict[str, Any]:
    """Phase 5A: Validate against Immutable Safety Floor and publish a new PracticeGraphVersion."""
    current = ensure_active_practice_graph(session, tenant_id)
    next_overlay = copy.deepcopy(current.overlay_json or DEFAULT_PRACTICE_OVERLAY)
    if overlay_patch:
        validate_overlay_against_safety_floor(overlay_patch)
        for sec in ("clinic_standards", "node_overrides"):
            if isinstance(overlay_patch.get(sec), dict):
                next_overlay.setdefault(sec, {})
                for k, v in overlay_patch[sec].items():
                    if isinstance(v, dict) and isinstance(next_overlay[sec].get(k), dict):
                        next_overlay[sec][k].update(v)
                    else:
                        next_overlay[sec][k] = copy.deepcopy(v)
    validate_overlay_against_safety_floor(next_overlay)

    next_profile = copy.deepcopy(current.careline_profile_json or DEFAULT_CARELINE_PROFILE)
    if careline_profile_patch:
        next_profile.update({k: v for k, v in careline_profile_patch.items() if v is not None})
    next_profile = validate_careline_profile(next_profile)

    current.status = "superseded"
    next_tag = _bump_minor_version(current.version_tag)
    new_ver = PracticeGraphVersion(
        id=new_id("pgv"),
        tenant_id=tenant_id,
        version_tag=next_tag,
        ebm_base_version=EBM_BASE_VERSION,
        content_hash=compute_graph_hash(next_overlay, next_profile),
        status="active",
        careline_profile_json=next_profile,
        overlay_json=next_overlay,
        approved_by=approved_by,
        change_summary=change_summary,
    )
    session.add(new_ver)
    record_audit(
        session,
        tenant_id=tenant_id,
        actor_role="clinician",
        actor_id=approved_by,
        action="practice_graph_published",
        resource_type="practice_graph_version",
        resource_id=new_ver.id,
        metadata={"from_version": current.version_tag, "to_version": next_tag, "content_hash": new_ver.content_hash},
    )
    session.flush()
    return get_effective_practice_graph(session, tenant_id)


def record_override_diff(
    session: Session,
    *,
    tenant_id: str,
    patient_id: str,
    case_key: str,
    target_node_id: str,
    doctor_name: str,
    instruction_text: str,
) -> dict[str, Any]:
    """Phase 5B: Record structured doctor override diff into Agent Memory and update Curator cluster."""
    entry = AgentMemoryEntry(
        id=new_id("mem"),
        tenant_id=tenant_id,
        patient_id=patient_id,
        tier="override_diff",
        topic=target_node_id,
        summary_text=f"{doctor_name} customized '{target_node_id}': {instruction_text}",
        source_quote=instruction_text,
        consent_scope="practice_quality_improvement",
        payload_json={"case_key": case_key, "doctor": doctor_name, "node_id": target_node_id},
    )
    session.add(entry)

    # Check if an open Curator proposal exists for this node; otherwise create one cleanly.
    existing = session.execute(
        select(CuratorProposal).where(
            CuratorProposal.tenant_id == tenant_id,
            CuratorProposal.target_node_id == target_node_id,
            CuratorProposal.status == "pending_lead_gp",
        )
    ).scalars().first()
    if existing is not None:
        existing.observed_count += 1
        ev = list(existing.evidence_case_ids_json or [])
        if case_key not in ev:
            ev.append(case_key)
        existing.evidence_case_ids_json = ev
        proposal_id = existing.id
    else:
        patch = {
            "node_overrides": {
                target_node_id: {
                    "practice_note": f"Observed doctor customization: {instruction_text}",
                }
            }
        }
        try:
            validate_overlay_against_safety_floor(patch)
            safety_check = {"passed": True, "safety_floor_intact": True}
        except SafetyFloorViolation as exc:
            safety_check = {"passed": False, "safety_floor_intact": False, "reason": str(exc)}
        prop = CuratorProposal(
            id=new_id("cur"),
            tenant_id=tenant_id,
            target_node_id=target_node_id,
            title=f"Observed customization on {target_node_id.split('.')[-1].replace('_', ' ').title()}",
            rationale=f"Captured from {doctor_name}'s instruction: “{instruction_text}”. Awaiting Lead GP review before publishing to Practice Graph.",
            observed_count=1,
            proposed_patch_json=patch,
            ebm_safety_check_json=safety_check,
            evidence_case_ids_json=[case_key],
            status="pending_lead_gp",
        )
        session.add(prop)
        session.flush()
        proposal_id = prop.id

    return {"memory_entry_id": entry.id, "curator_proposal_id": proposal_id}


def list_curator_proposals(session: Session, tenant_id: str) -> list[dict[str, Any]]:
    ensure_active_practice_graph(session, tenant_id)
    rows = session.execute(
        select(CuratorProposal)
        .where(CuratorProposal.tenant_id == tenant_id)
        .order_by(CuratorProposal.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": r.id,
            "target_node_id": r.target_node_id,
            "title": r.title,
            "rationale": r.rationale,
            "observed_count": r.observed_count,
            "proposed_patch": r.proposed_patch_json,
            "ebm_safety_check": r.ebm_safety_check_json,
            "evidence_case_ids": r.evidence_case_ids_json,
            "status": r.status,
            "decided_by": r.decided_by,
            "published_graph_version": r.published_graph_version,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def decide_curator_proposal(
    session: Session,
    *,
    tenant_id: str,
    proposal_id: str,
    decision: str,  # approve | dismiss
    decided_by: str,
    edited_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prop = session.execute(
        select(CuratorProposal).where(CuratorProposal.tenant_id == tenant_id, CuratorProposal.id == proposal_id)
    ).scalars().first()
    if prop is None:
        raise ValueError("Curator proposal not found.")
    if decision not in ("approve", "dismiss"):
        raise ValueError("Decision must be 'approve' or 'dismiss'.")

    prop.decided_by = decided_by
    prop.decided_at = utcnow()
    if decision == "dismiss":
        prop.status = "dismissed"
        session.flush()
        return {"status": "dismissed", "proposal_id": prop.id, "graph": get_effective_practice_graph(session, tenant_id)}

    patch = edited_patch or prop.proposed_patch_json
    validate_overlay_against_safety_floor(patch)
    updated_graph = mould_practice_graph(
        session,
        tenant_id=tenant_id,
        approved_by=decided_by,
        overlay_patch=patch,
        change_summary=f"Approved Curator proposal: {prop.title}",
    )
    prop.status = "approved"
    prop.published_graph_version = updated_graph["version_tag"]
    session.flush()
    return {"status": "approved", "proposal_id": prop.id, "graph": updated_graph}


def query_practice_playbook(effective_graph: dict[str, Any], question: str) -> dict[str, Any]:
    """Graph-grounded @ Clinic Playbook Q&A for covering locum doctors and nurses."""
    q = (question or "").strip()
    low = q.lower()
    nodes = effective_graph["nodes"]
    std = effective_graph["clinic_standards"]

    matched: list[dict[str, Any]] = []
    if any(k in low for k in ("ferritin", "iron", "anaem", "anem")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.low_ferritin_normal_hb"]
    elif any(k in low for k in ("warfarin", "qeh", "discharge", "inr", "anticoag", "statin")):
        matched = [n for n in nodes if n["id"] in ("ebm.safety.doctor_only_authority", "ebm.pathway.post_discharge_anticoag")]
    elif any(k in low for k in ("g6pd", "urti", "sick", "harbour", "flu", "penicillin")):
        matched = [n for n in nodes if n["id"] in ("ebm.safety.g6pd_allergy_floor", "ebm.pathway.acute_urti_g6pd")]
    elif any(k in low for k in ("bp", "hypertension", "blood pressure", "goal", "target", "wong")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.chronic_htn_home_bp"]
    elif any(k in low for k in ("ldl", "lipid", "cholesterol")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.borderline_ldl"]
    else:
        matched = [n for n in nodes if n["category"] in ("triage", "safety")][:3]

    bullets = []
    for n in matched:
        note = n["params"].get("practice_note") or n["summary"]
        bullets.append(f"{n['title']} ({n['provenance']}): {note}")

    return {
        "question": q,
        "graph_version_tag": effective_graph["version_tag"],
        "graph_content_hash": effective_graph["content_hash"],
        "approved_by": effective_graph["approved_by"],
        "matched_nodes": [{"id": n["id"], "title": n["title"], "provenance": n["provenance"], "guideline_ref": n["guideline_ref"]} for n in matched],
        "answer_bullets": bullets,
        "grace_window_hours": std.get("grace_window_hours", 72),
    }
