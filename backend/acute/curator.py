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
import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

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
    resolve_pathway_id,
    validate_careline_profile,
    validate_overlay_against_safety_floor,
)

_UNSAFE_CAPTURE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("allow_autonomous_rx", "autonomous prescribing"),
    ("auto-prescribe", "autonomous prescribing"),
    ("autonomous prescribing", "autonomous prescribing"),
    ("auto-hold", "autonomous medication holds"),
    ("withhold warfarin automatically", "autonomous medication holds"),
    ("disable 3-point id", "disabling 3-point ID verification"),
    ("skip 3-pt id", "disabling 3-point ID verification"),
    ("bypass g6pd", "bypassing G6PD safety floor"),
    ("disable safety floor", "disabling Immutable Safety Floor"),
)


def _bump_minor_version(tag: str) -> str:
    m = re.match(r"^v(\d+)\.(\d+)$", (tag or "v2.4").strip())
    if not m:
        return "v2.5"
    return f"v{int(m.group(1))}.{int(m.group(2)) + 1}"


def _is_locum_actor(actor_name: str, actor_role: str | None = None) -> bool:
    if (actor_role or "").strip().lower() == "locum":
        return True
    return "locum" in (actor_name or "").strip().lower()


def compute_diff_fingerprint(target_node_id: str, diff_key: str, normalized_value: Any) -> str:
    """Compute a deterministic 16-char cluster fingerprint over `(target_node_id, diff_key, normalized_value)`."""
    canon_node = resolve_pathway_id(target_node_id).strip().lower()
    canon_key = str(diff_key or "practice_note").strip().lower()
    canon_val = re.sub(r"\s+", " ", str(normalized_value or "").strip().lower())
    raw = f"{canon_node}::{canon_key}::{canon_val}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _extract_structured_diff(
    target_node_id: str,
    instruction_text: str,
    structured_diff: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Extract `(diff_key, normalized_value, node_patch_dict)` from a doctor override diff."""
    canon_node = resolve_pathway_id(target_node_id)
    if structured_diff and isinstance(structured_diff, dict):
        dkey = str(structured_diff.get("diff_key") or "practice_note").strip().lower()
        dval = str(structured_diff.get("normalized_value") or structured_diff.get("value") or "").strip().lower()
        patch_fields = copy.deepcopy(structured_diff.get("patch") or {})
        if not patch_fields and dkey != "practice_note":
            patch_fields[dkey] = structured_diff.get("value")
        patch_fields.setdefault("practice_note", f"Observed doctor customization: {instruction_text}")
        return dkey, dval or re.sub(r"\s+", " ", instruction_text.strip().lower()), patch_fields

    txt = (instruction_text or "").strip()
    low = txt.lower()

    # BP target extraction (e.g. "<130/80" or "135/85")
    m_bp = re.search(r"\b(\d{2,3})\s*/\s*(\d{2,3})\b", low)
    if m_bp and ("bp" in low or "target" in low or "goal" in low or "htn" in canon_node):
        sys_v, dia_v = int(m_bp.group(1)), int(m_bp.group(2))
        return "bp_target", f"{sys_v}/{dia_v}", {
            "target_sys": sys_v,
            "target_dia": dia_v,
            "practice_note": f"Observed doctor customization: {txt}",
        }

    # Oral iron cadence (daily vs alternate-day)
    if "iron" in low or "ferritin" in canon_node:
        if "alternate" in low or "every other day" in low or "eod" in low:
            return "oral_iron_regimen", "alternate_day", {
                "oral_iron_regimen": "Alternate-day gentle oral iron",
                "practice_note": f"Observed doctor customization: {txt}",
            }
        if "daily" in low or "od" in low:
            return "oral_iron_regimen", "daily", {
                "oral_iron_regimen": "Daily oral iron",
                "practice_note": f"Observed doctor customization: {txt}",
            }

    # Repeat interval in months
    m_mo = re.search(r"\b(\d+)\s*(?:month|months|mo|mth)\b", low)
    if m_mo:
        months = int(m_mo.group(1))
        return "repeat_months", f"{months}m", {
            "repeat_months": months,
            "practice_note": f"Observed doctor customization: {txt}",
        }

    # Fasting hours
    m_fast = re.search(r"\b(\d+)\s*(?:h|hr|hrs|hour|hours)\s*fast", low)
    if m_fast:
        hrs = int(m_fast.group(1))
        return "fasting_hours", f"{hrs}h", {
            "fasting_hours": hrs,
            "practice_note": f"Observed doctor customization: {txt}",
        }

    # Paid GP review auto-booking toggle
    if "paid review" in low or "gp review" in low:
        no_book = "no " in low or "skip " in low or "without " in low
        return "auto_book_paid_review", "false" if no_book else "true", {
            "auto_book_paid_review": not no_book,
            "practice_note": f"Observed doctor customization: {txt}",
        }

    norm_val = re.sub(r"\s+", " ", low)
    return "practice_note", norm_val, {"practice_note": f"Observed doctor customization: {txt}"}


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
        fp = compute_diff_fingerprint("ebm.pathway.borderline_ldl", "auto_book_paid_review", "true")
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
                    "diff_fingerprint": fp,
                    "diff_key": "auto_book_paid_review",
                    "normalized_value": "true",
                    "contributing_doctors": [
                        {"name": "Dr. Kenneth Lam", "is_locum": False, "case_key": "case_lee"},
                        {"name": "Dr. Kenneth Lam", "is_locum": False, "case_key": "hist_ldl_01"},
                        {"name": "Dr. Mei-Ling Lau", "is_locum": False, "case_key": "hist_ldl_02"},
                        {"name": "Dr. Sarah Wong (Locum GP)", "is_locum": True, "case_key": "hist_ldl_03"},
                    ],
                    "locum_only": False,
                    "needs_regular_corroboration": False,
                    "conflict_detected": False,
                    "conflicting_proposal_ids": [],
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


def list_practice_graph_versions(session: Session, tenant_id: str) -> list[dict[str, Any]]:
    """Return immutable version history of Practice Graphs for the tenant (newest first)."""
    ensure_active_practice_graph(session, tenant_id)
    rows = session.execute(
        select(PracticeGraphVersion)
        .where(PracticeGraphVersion.tenant_id == tenant_id)
        .order_by(PracticeGraphVersion.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": r.id,
            "version_tag": r.version_tag,
            "ebm_base_version": r.ebm_base_version,
            "content_hash": r.content_hash,
            "status": r.status,
            "approved_by": r.approved_by,
            "change_summary": r.change_summary,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


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
                    target_k = resolve_pathway_id(k) if sec == "node_overrides" else k
                    if isinstance(v, dict) and isinstance(next_overlay[sec].get(target_k), dict):
                        next_overlay[sec][target_k].update(v)
                    else:
                        next_overlay[sec][target_k] = copy.deepcopy(v)
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


def rollback_practice_graph(
    session: Session,
    *,
    tenant_id: str,
    rolled_back_by: str,
    target_version_tag: str | None = None,
    target_version_id: str | None = None,
    reason: str = "Rollback to prior verified Practice Graph snapshot",
) -> dict[str, Any]:
    """Publish a new immutable PracticeGraphVersion restoring a prior snapshot after Safety Floor re-verification."""
    if _is_locum_actor(rolled_back_by):
        raise SafetyFloorViolation(
            "Only Lead GP / regular clinic physician may roll back Practice Graph versions.",
            error_code="LEAD_GP_APPROVAL_REQUIRED",
            node_id="ebm.safety.doctor_only_authority",
        )

    current = ensure_active_practice_graph(session, tenant_id)

    query = select(PracticeGraphVersion).where(PracticeGraphVersion.tenant_id == tenant_id)
    if target_version_id:
        target = session.execute(query.where(PracticeGraphVersion.id == target_version_id)).scalars().first()
    elif target_version_tag:
        target = session.execute(
            query.where(PracticeGraphVersion.version_tag == target_version_tag).order_by(PracticeGraphVersion.created_at.desc())
        ).scalars().first()
    else:
        target = session.execute(
            query.where(PracticeGraphVersion.id != current.id).order_by(PracticeGraphVersion.created_at.desc())
        ).scalars().first()

    if target is None:
        raise ValueError("Target Practice Graph version for rollback not found.")

    restored_overlay = copy.deepcopy(target.overlay_json or DEFAULT_PRACTICE_OVERLAY)
    restored_profile = copy.deepcopy(target.careline_profile_json or DEFAULT_CARELINE_PROFILE)
    validate_overlay_against_safety_floor(restored_overlay)
    restored_profile = validate_careline_profile(restored_profile)

    current.status = "superseded"
    next_tag = _bump_minor_version(current.version_tag)
    new_ver = PracticeGraphVersion(
        id=new_id("pgv"),
        tenant_id=tenant_id,
        version_tag=next_tag,
        ebm_base_version=EBM_BASE_VERSION,
        content_hash=compute_graph_hash(restored_overlay, restored_profile),
        status="active",
        careline_profile_json=restored_profile,
        overlay_json=restored_overlay,
        approved_by=rolled_back_by,
        change_summary=f"Rollback from {current.version_tag} to {target.version_tag}: {reason}",
    )
    session.add(new_ver)
    record_audit(
        session,
        tenant_id=tenant_id,
        actor_role="clinician",
        actor_id=rolled_back_by,
        action="practice_graph_rolled_back",
        resource_type="practice_graph_version",
        resource_id=new_ver.id,
        metadata={
            "from_version": current.version_tag,
            "restored_source_version": target.version_tag,
            "restored_source_id": target.id,
            "published_version": next_tag,
            "reason": reason,
        },
    )
    session.flush()

    eff = get_effective_practice_graph(session, tenant_id)
    eff["rolled_back_from"] = current.version_tag
    eff["rolled_back_to_source_version"] = target.version_tag
    eff["rollback_reason"] = reason
    return eff


def record_override_diff(
    session: Session,
    *,
    tenant_id: str,
    patient_id: str,
    case_key: str,
    target_node_id: str,
    doctor_name: str,
    instruction_text: str,
    actor_role: str | None = None,
    structured_diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase 5B: Record structured doctor override diff into Agent Memory and cluster Curator proposals.

    Enforces:
    1. Capture-time Safety Floor guard (raises `SafetyFloorViolation` before queuing illegal diffs).
    2. Deterministic `(target_node_id, diff_key, normalized_value)` `diff_fingerprint` clustering.
    3. Multi-doctor conflict detection when differing values target the same `(target_node_id, diff_key)`.
    4. Locum corroboration safeguard (`locum_only` / `needs_regular_corroboration`).
    """
    canon_node_id = resolve_pathway_id(target_node_id)
    diff_key, normalized_value, node_patch_fields = _extract_structured_diff(
        canon_node_id, instruction_text, structured_diff=structured_diff
    )
    patch = {"node_overrides": {canon_node_id: node_patch_fields}}

    # 1. Capture-time Safety Floor guard (blocks unsafe instructions and immutable nodes immediately)
    low_instr = (instruction_text or "").lower()
    for pat, reason_label in _UNSAFE_CAPTURE_PATTERNS:
        if pat in low_instr:
            record_audit(
                session,
                tenant_id=tenant_id,
                actor_role=actor_role or "clinician",
                actor_id=doctor_name,
                action="curator_safety_floor_blocked",
                resource_type="curator_override_diff",
                resource_id=canon_node_id,
                metadata={"instruction_text": instruction_text, "reason": reason_label},
            )
            raise SafetyFloorViolation(
                f"Immutable Safety Floor violation at capture time: {reason_label} cannot be queued for Clinic Curator.",
                error_code="CAPTURE_TIME_SAFETY_FLOOR_BLOCK",
                node_id=canon_node_id,
            )
    try:
        validate_overlay_against_safety_floor(patch)
    except SafetyFloorViolation as exc:
        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role=actor_role or "clinician",
            actor_id=doctor_name,
            action="curator_safety_floor_blocked",
            resource_type="curator_override_diff",
            resource_id=canon_node_id,
            metadata={"instruction_text": instruction_text, "error_code": exc.error_code, "reason": str(exc)},
        )
        raise

    fingerprint = compute_diff_fingerprint(canon_node_id, diff_key, normalized_value)
    is_locum = _is_locum_actor(doctor_name, actor_role=actor_role)

    entry = AgentMemoryEntry(
        id=new_id("mem"),
        tenant_id=tenant_id,
        patient_id=patient_id,
        tier="override_diff",
        topic=canon_node_id,
        summary_text=f"{doctor_name} customized '{canon_node_id}' ({diff_key}={normalized_value}): {instruction_text}",
        source_quote=instruction_text,
        consent_scope="practice_quality_improvement",
        payload_json={
            "case_key": case_key,
            "doctor": doctor_name,
            "is_locum": is_locum,
            "node_id": canon_node_id,
            "diff_key": diff_key,
            "normalized_value": normalized_value,
            "diff_fingerprint": fingerprint,
        },
    )
    session.add(entry)

    # 2. Cluster by exact `diff_fingerprint` & detect conflicts on same `(target_node_id, diff_key)`
    open_props = session.execute(
        select(CuratorProposal).where(
            CuratorProposal.tenant_id == tenant_id,
            CuratorProposal.target_node_id == canon_node_id,
            CuratorProposal.status == "pending_lead_gp",
        )
    ).scalars().all()

    matched_prop: CuratorProposal | None = None
    conflicting_props: list[CuratorProposal] = []
    for p in open_props:
        meta = dict(p.ebm_safety_check_json or {})
        p_fp = meta.get("diff_fingerprint")
        p_key = meta.get("diff_key")
        if p_fp == fingerprint:
            matched_prop = p
            break
        if p_key and p_key == diff_key and p_fp != fingerprint:
            conflicting_props.append(p)

    conflict_detected = False
    conflicting_ids: list[str] = []

    if matched_prop is not None:
        matched_prop.observed_count += 1
        ev = list(matched_prop.evidence_case_ids_json or [])
        if case_key not in ev:
            ev.append(case_key)
        matched_prop.evidence_case_ids_json = ev

        meta = dict(matched_prop.ebm_safety_check_json or {})
        contributors = list(meta.get("contributing_doctors") or [])
        contributors.append({"name": doctor_name, "is_locum": is_locum, "case_key": case_key})
        locum_only = all(bool(c.get("is_locum")) for c in contributors)
        conflict_detected = bool(meta.get("conflict_detected", False))
        conflicting_ids = list(meta.get("conflicting_proposal_ids") or [])

        meta.update({
            "contributing_doctors": contributors,
            "locum_only": locum_only,
            "needs_regular_corroboration": locum_only,
            "auto_promote_eligible": (not locum_only) and (not conflict_detected) and matched_prop.observed_count >= 3,
        })
        matched_prop.ebm_safety_check_json = meta
        flag_modified(matched_prop, "ebm_safety_check_json")
        flag_modified(matched_prop, "evidence_case_ids_json")
        proposal_id = matched_prop.id
        needs_corroboration = locum_only
    else:
        conflict_detected = len(conflicting_props) > 0
        conflicting_ids = [p.id for p in conflicting_props]
        contributors = [{"name": doctor_name, "is_locum": is_locum, "case_key": case_key}]
        needs_corroboration = is_locum

        safety_check = {
            "passed": True,
            "safety_floor_intact": True,
            "diff_fingerprint": fingerprint,
            "diff_key": diff_key,
            "normalized_value": normalized_value,
            "contributing_doctors": contributors,
            "locum_only": is_locum,
            "needs_regular_corroboration": needs_corroboration,
            "conflict_detected": conflict_detected,
            "conflicting_proposal_ids": conflicting_ids,
            "auto_promote_eligible": False,
        }
        prop = CuratorProposal(
            id=new_id("cur"),
            tenant_id=tenant_id,
            target_node_id=canon_node_id,
            title=f"Observed customization on {canon_node_id.split('.')[-1].replace('_', ' ').title()} ({diff_key})",
            rationale=(
                f"Captured from {doctor_name}'s instruction: “{instruction_text}”. "
                + ("Multi-doctor conflict flagged against existing pending proposal. " if conflict_detected else "")
                + ("Locum-only observation — requires regular GP corroboration. " if needs_corroboration else "")
                + "Awaiting Lead GP review before publishing to Practice Graph."
            ),
            observed_count=1,
            proposed_patch_json=patch,
            ebm_safety_check_json=safety_check,
            evidence_case_ids_json=[case_key],
            status="pending_lead_gp",
        )
        session.add(prop)
        session.flush()
        proposal_id = prop.id

        # Cross-link conflict flag onto existing conflicting proposals
        for other in conflicting_props:
            other_meta = dict(other.ebm_safety_check_json or {})
            other_meta["conflict_detected"] = True
            other_conflicts = list(other_meta.get("conflicting_proposal_ids") or [])
            if proposal_id not in other_conflicts:
                other_conflicts.append(proposal_id)
            other_meta["conflicting_proposal_ids"] = other_conflicts
            other_meta["auto_promote_eligible"] = False
            other.ebm_safety_check_json = other_meta
            flag_modified(other, "ebm_safety_check_json")

    return {
        "memory_entry_id": entry.id,
        "curator_proposal_id": proposal_id,
        "diff_fingerprint": fingerprint,
        "diff_key": diff_key,
        "normalized_value": normalized_value,
        "conflict_detected": conflict_detected,
        "conflicting_proposal_ids": conflicting_ids,
        "locum_only": needs_corroboration,
        "needs_regular_corroboration": needs_corroboration,
    }


def list_curator_proposals(session: Session, tenant_id: str) -> list[dict[str, Any]]:
    ensure_active_practice_graph(session, tenant_id)
    rows = session.execute(
        select(CuratorProposal)
        .where(CuratorProposal.tenant_id == tenant_id)
        .order_by(CuratorProposal.created_at.desc())
    ).scalars().all()
    results: list[dict[str, Any]] = []
    for r in rows:
        meta = dict(r.ebm_safety_check_json or {})
        results.append(
            {
                "id": r.id,
                "target_node_id": r.target_node_id,
                "title": r.title,
                "rationale": r.rationale,
                "observed_count": r.observed_count,
                "proposed_patch": r.proposed_patch_json,
                "ebm_safety_check": meta,
                "evidence_case_ids": r.evidence_case_ids_json,
                "diff_fingerprint": meta.get("diff_fingerprint"),
                "diff_key": meta.get("diff_key"),
                "normalized_value": meta.get("normalized_value"),
                "conflict_detected": bool(meta.get("conflict_detected", False)),
                "conflicting_proposal_ids": list(meta.get("conflicting_proposal_ids") or []),
                "locum_only": bool(meta.get("locum_only", False)),
                "needs_regular_corroboration": bool(meta.get("needs_regular_corroboration", False)),
                "status": r.status,
                "decided_by": r.decided_by,
                "published_graph_version": r.published_graph_version,
                "created_at": r.created_at.isoformat(),
            }
        )
    return results


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

    if decision == "approve" and _is_locum_actor(decided_by):
        raise SafetyFloorViolation(
            "Locum clinicians cannot approve Curator proposals — Lead GP or regular clinic physician sign-off is required.",
            error_code="LEAD_GP_APPROVAL_REQUIRED",
            node_id=prop.target_node_id,
        )

    prop.decided_by = decided_by
    prop.decided_at = utcnow()
    if decision == "dismiss":
        prop.status = "dismissed"
        session.flush()
        return {"status": "dismissed", "proposal_id": prop.id, "graph": get_effective_practice_graph(session, tenant_id)}

    meta = dict(prop.ebm_safety_check_json or {})
    if meta.get("source") == "clinical_knowledge_factory":
        # Propose-only invariant (WO #62): clinician approval signs off the PR patch & synthetic
        # scenario stub for scripts/check.sh gating — NEVER mutates knowledge/*.yaml in place.
        prop.status = "approved"
        prop.published_graph_version = f"pr_ready:{meta.get('clinic_id', 'default')}"
        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role="clinician",
            actor_id=decided_by,
            action="knowledge_factory_proposal_signed_off",
            resource_type="curator_proposal",
            resource_id=prop.id,
            metadata={
                "clinic_id": meta.get("clinic_id"),
                "target_file": meta.get("target_file"),
                "diff_fingerprint": meta.get("diff_fingerprint"),
                "propose_only": True,
            },
        )
        session.flush()
        return {
            "status": "approved_for_pr",
            "proposal_id": prop.id,
            "propose_only": True,
            "target_file": meta.get("target_file"),
            "unified_diff": meta.get("unified_diff"),
            "synthetic_scenario_name": meta.get("synthetic_scenario_name"),
            "graph": get_effective_practice_graph(session, tenant_id),
        }

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


def bridge_knowledge_gaps_to_curator(
    session: Session,
    *,
    tenant_id: str,
    gaps_or_path: Any,
    clinic_id: str = "ec_clinic_1",
    min_count: int = 3,
    knowledge_root: Any = None,
) -> dict[str, Any]:
    """Bridge PHI-scrubbed WO #62 KnowledgeGap clusters into DB-tracked Lead GP CuratorProposals.

    Enforces:
    - Blind-set hygiene (via `load_gap_log` when given a file path)
    - Propose-only authority: stages deduplicated `pending_lead_gp` proposals with unified YAML
      diffs + synthetic scenario stubs; NEVER mutates `knowledge/` files on disk.
    """
    from pathlib import Path

    from backend.knowledge import curate_gaps, load_gap_log

    ensure_active_practice_graph(session, tenant_id)
    if isinstance(gaps_or_path, (str, Path)):
        gaps = load_gap_log(gaps_or_path)
    else:
        gaps = list(gaps_or_path)

    report = curate_gaps(
        gaps,
        clinic_id=clinic_id,
        min_count=min_count,
        knowledge_root=knowledge_root,
    )

    existing_pending = session.execute(
        select(CuratorProposal).where(
            CuratorProposal.tenant_id == tenant_id,
            CuratorProposal.status == "pending_lead_gp",
        )
    ).scalars().all()
    by_fp = {
        str((p.ebm_safety_check_json or {}).get("diff_fingerprint") or ""): p
        for p in existing_pending
    }

    staged_ids: list[str] = []
    updated_ids: list[str] = []
    for prop_spec in report.proposals:
        fp = compute_diff_fingerprint(
            f"knowledge.{clinic_id}.{prop_spec.target_file}",
            prop_spec.cluster.kind,
            prop_spec.cluster.pattern,
        )
        existing = by_fp.get(fp)
        if existing is not None:
            existing.observed_count = max(existing.observed_count, prop_spec.cluster.count)
            meta = dict(existing.ebm_safety_check_json or {})
            meta["unified_diff"] = prop_spec.unified_diff
            meta["synthetic_scenario_name"] = prop_spec.synthetic_scenario_name
            meta["synthetic_scenario_yaml"] = prop_spec.synthetic_scenario_yaml
            existing.ebm_safety_check_json = meta
            flag_modified(existing, "ebm_safety_check_json")
            updated_ids.append(existing.id)
            continue

        safety_meta = {
            "passed": True,
            "safety_floor_intact": True,
            "source": "clinical_knowledge_factory",
            "propose_only": True,
            "clinic_id": clinic_id,
            "target_file": prop_spec.target_file,
            "diff_fingerprint": fp,
            "diff_key": prop_spec.cluster.kind,
            "normalized_value": prop_spec.cluster.pattern.lower(),
            "unified_diff": prop_spec.unified_diff,
            "synthetic_scenario_name": prop_spec.synthetic_scenario_name,
            "synthetic_scenario_yaml": prop_spec.synthetic_scenario_yaml,
            "locum_only": False,
            "needs_regular_corroboration": False,
            "conflict_detected": False,
            "conflicting_proposal_ids": [],
            "auto_promote_eligible": False,
        }
        row = CuratorProposal(
            id=new_id("cur"),
            tenant_id=tenant_id,
            target_node_id=f"knowledge.{clinic_id}.{prop_spec.cluster.kind}",
            title=f"Knowledge Factory ({clinic_id}): {prop_spec.cluster.kind} — {prop_spec.cluster.pattern}",
            rationale=prop_spec.rationale,
            observed_count=prop_spec.cluster.count,
            proposed_patch_json=prop_spec.yaml_addition,
            ebm_safety_check_json=safety_meta,
            evidence_case_ids_json=list(prop_spec.cluster.channels or ("knowledge_gap_log",)),
            status="pending_lead_gp",
        )
        session.add(row)
        session.flush()
        by_fp[fp] = row
        staged_ids.append(row.id)

    return {
        "clinic_id": clinic_id,
        "total_gaps": report.total_gaps,
        "proposals_generated": len(report.proposals),
        "staged_proposal_ids": staged_ids,
        "updated_proposal_ids": updated_ids,
    }


def query_practice_playbook(
    effective_graph: dict[str, Any],
    question: str,
    *,
    clinic_knowledge: Any = None,
) -> dict[str, Any]:
    """Graph-grounded @ Clinic Playbook Q&A for covering locum doctors and nurses across all 8 HK GP pathways.

    When `clinic_knowledge` (`ClinicKnowledge`) is provided, also surfaces grounded clinic-specific
    drug alias, lab panel synonym, shorthand glossary, and cover-rule entries with pack provenance.
    """
    q = (question or "").strip()
    low = q.lower()
    nodes = effective_graph["nodes"]
    std = effective_graph["clinic_standards"]

    matched: list[dict[str, Any]] = []
    if any(k in low for k in ("ferritin", "iron", "anaem", "anem")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.low_ferritin_normal_hb"]
    elif any(k in low for k in ("warfarin", "qeh", "discharge", "inr", "anticoag", "statin")):
        matched = [n for n in nodes if n["id"] in ("ebm.safety.doctor_only_authority", "ebm.pathway.post_discharge_anticoag")]
    elif any(k in low for k in ("urti", "sick", "harbour", "flu", "penicillin")):
        matched = [n for n in nodes if n["id"] in ("ebm.safety.g6pd_allergy_floor", "ebm.pathway.acute_urti_g6pd")]
    elif any(k in low for k in ("bp", "hypertension", "blood pressure", "goal", "target", "wong")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.chronic_htn_home_bp"]
    elif any(k in low for k in ("ldl", "lipid", "cholesterol")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.borderline_ldl"]
    elif any(k in low for k in ("t2dm", "diabetes", "hba1c", "metformin", "uacr")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.t2dm_hba1c"]
    elif any(k in low for k in ("thyroid", "tsh", "levothyroxine", "carbimazole", "fatigue")):
        matched = [n for n in nodes if n["id"] == "ebm.pathway.thyroid_fatigue"]
    elif any(k in low for k in ("uti", "dysuria", "cystitis", "nitrofurantoin", "fosfomycin", "g6pd")):
        matched = [n for n in nodes if n["id"] in ("ebm.safety.g6pd_allergy_floor", "ebm.pathway.acute_uti")]
    else:
        matched = [n for n in nodes if n["category"] in ("triage", "safety")][:3]

    bullets = []
    for n in matched:
        note = n["params"].get("practice_note") or n["summary"]
        bullets.append(f"{n['title']} ({n['provenance']}): {note}")

    knowledge_bullets: list[str] = []
    if clinic_knowledge is not None:
        # 1. Drug aliases & monitoring rules
        for gen in clinic_knowledge.mentions(q):
            cls_name = clinic_knowledge.drug_class(gen) or "unclassified"
            mon = clinic_knowledge.monitoring_for(gen)
            mon_str = f"; repeat monitoring: {', '.join(mon[0])} within {mon[1]}d" if mon else ""
            knowledge_bullets.append(
                f"Drug Formulary ({clinic_knowledge.clinic_id}): {gen} (class={cls_name}{mon_str})"
            )
        # 2. Lab panel synonyms
        expanded_tests = clinic_knowledge.tests_for(q)
        if expanded_tests:
            knowledge_bullets.append(
                f"Lab Synonyms ({clinic_knowledge.clinic_id}): resolves to {', '.join(expanded_tests)}"
            )
        # 3. Local shorthand glossary matches
        for abbr, meaning in sorted(clinic_knowledge.domain.shorthand_glossary.items(), key=lambda kv: kv[0].lower()):
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(abbr)}(?![A-Za-z0-9])", q, re.I):
                knowledge_bullets.append(f"Clinic Shorthand ({clinic_knowledge.clinic_id}): {abbr} = {meaning}")
        # 4. Sessional / locum cover rules
        if any(k in low for k in ("cover", "locum", "sessional", "duty", "escalat")):
            for cr in clinic_knowledge.routing.cover_rules:
                knowledge_bullets.append(
                    f"Cover Rule ({clinic_knowledge.clinic_id}/{cr.id}): {cr.condition} -> {cr.fallback_role} ({cr.escalate_after_hours}h)"
                )

    result: dict[str, Any] = {
        "question": q,
        "graph_version_tag": effective_graph["version_tag"],
        "graph_content_hash": effective_graph["content_hash"],
        "approved_by": effective_graph["approved_by"],
        "matched_nodes": [{"id": n["id"], "title": n["title"], "provenance": n["provenance"], "guideline_ref": n["guideline_ref"]} for n in matched],
        "answer_bullets": bullets + knowledge_bullets,
        "grace_window_hours": std.get("grace_window_hours", 72),
    }
    if clinic_knowledge is not None:
        result["knowledge_clinic_id"] = clinic_knowledge.clinic_id
        result["knowledge_fingerprint"] = clinic_knowledge.fingerprint
    return result
