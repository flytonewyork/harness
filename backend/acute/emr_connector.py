"""Additive-Only EMR Connector & Fail-Closed Deterministic 3-Point ID Document Ingestion Gate.

Design invariants:
1. Clinic EMR remains the sole clinical Source of Truth (SoT). This connector reads charts/PDFs
   deterministically and writes ONLY compact, doctor-confirmed encounter addenda, bookings, and
   order stubs — never mutating EMR medication/problem lists autonomously.
2. Fail-Closed 3-Point ID Gate: every incoming lab report or hospital discharge PDF must pass an
   EXACT 3-point identity match (Patient ID/HKID + Normalized Name + DOB) before reaching any
   doctor's `#my-work` queue or linking to a patient thread. Any partial match or discrepancy is
   quarantined strictly to Nurse Rachel for manual matching.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import DocumentIngestion, new_id, record_audit, utcnow

# Canonical simulated Clinic EMR patient directory for deterministic 3-point ID verification.
EMR_PATIENT_REGISTRY: dict[str, dict[str, Any]] = {
    "pat_chan_warfarin": {
        "patient_id": "pat_chan_warfarin",
        "mrn": "PAT-62041",
        "name_en": "CHAN Tai Man",
        "dob": "1964-03-14",
        "hkid_masked": "A412***(8)",
        "age_sex": "62M",
        "active_meds": ["Warfarin 5mg daily", "Simvastatin 20mg nocte"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_lam_urti_demo": {
        "patient_id": "pat_lam_urti_demo",
        "mrn": "PAT-32018",
        "name_en": "LAM Wing-Yan",
        "dob": "1994-04-18",
        "hkid_masked": "M528***(6)",
        "age_sex": "32F",
        "active_meds": [],
        "allergies": ["Penicillin (urticarial rash)"],
        "g6pd_deficient": True,
    },
    "pat_wong_htn": {
        "patient_id": "pat_wong_htn",
        "mrn": "PAT-58092",
        "name_en": "WONG Mei Ling",
        "dob": "1968-09-21",
        "hkid_masked": "K901***(2)",
        "age_sex": "58F",
        "active_meds": ["Amlodipine 5mg daily"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_lee_ldl": {
        "patient_id": "pat_lee_ldl",
        "mrn": "PAT-36044",
        "name_en": "LEE Ming Kit",
        "dob": "1990-02-04",
        "hkid_masked": "C219***(1)",
        "age_sex": "36M",
        "active_meds": [],
        "allergies": [],
        "g6pd_deficient": False,
    },
}


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())


def verify_three_point_id(
    *,
    claimed_mrn_or_hkid: str,
    claimed_name: str,
    claimed_dob: str,
) -> dict[str, Any]:
    """Fail-closed 3-point identity check: (1) MRN/HKID, (2) Normalized Name, (3) DOB."""
    target_id_clean = (claimed_mrn_or_hkid or "").strip().upper()
    target_name_norm = _norm_name(claimed_name)
    target_dob = (claimed_dob or "").strip()

    best_candidate: dict[str, Any] | None = None
    best_score = -1
    best_checks: dict[str, Any] = {"id_match": False, "name_match": False, "dob_match": False}

    for rec in EMR_PATIENT_REGISTRY.values():
        id_ok = target_id_clean in {rec["mrn"].upper(), rec["hkid_masked"].upper(), rec["patient_id"].upper()}
        name_ok = bool(target_name_norm) and target_name_norm == _norm_name(rec["name_en"])
        dob_ok = bool(target_dob) and target_dob == rec["dob"]
        score = int(id_ok) + int(name_ok) + int(dob_ok)
        if score > best_score:
            best_score = score
            best_candidate = rec
            best_checks = {
                "id_match": id_ok,
                "name_match": name_ok,
                "dob_match": dob_ok,
                "points_matched": score,
                "points_required": 3,
                "candidate_mrn": rec["mrn"] if score > 0 else None,
            }

    if best_score == 3 and best_candidate is not None:
        return {
            "match_status": "exact_3pt_match",
            "matched_patient_id": best_candidate["patient_id"],
            "matched_mrn": best_candidate["mrn"],
            "checks": best_checks,
            "route": "doctor_my_work",
        }

    return {
        "match_status": "quarantined_nurse",
        "matched_patient_id": None,
        "matched_mrn": None,
        "checks": {
            **best_checks,
            "quarantine_reason": (
                f"Fail-closed 3-point ID gate blocked auto-linking ({best_score}/3 points matched). "
                "Routed strictly to Nurse Rachel for manual verification — never exposed to doctor queue or patient chat."
            ),
        },
        "route": "nurse_quarantine",
    }


def ingest_external_document(
    session: Session,
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
    parsed_diff: dict[str, Any] | None = None,
) -> DocumentIngestion:
    """Ingest a lab report or hospital discharge PDF through the fail-closed 3-point ID gate."""
    verification = verify_three_point_id(
        claimed_mrn_or_hkid=claimed_mrn_or_hkid,
        claimed_name=claimed_name,
        claimed_dob=claimed_dob,
    )
    row = DocumentIngestion(
        id=new_id("doc"),
        tenant_id=tenant_id,
        external_doc_ref=external_doc_ref,
        doc_kind=doc_kind,
        source_org=source_org,
        matched_patient_id=verification["matched_patient_id"],
        match_status=verification["match_status"],
        match_checks_json={
            **verification["checks"],
            "claimed_id": claimed_mrn_or_hkid,
            "claimed_name": claimed_name,
            "claimed_dob": claimed_dob,
        },
        verbatim_quote=verbatim_quote,
        page_ref=page_ref,
        parsed_diff_json=parsed_diff or {},
    )
    session.add(row)
    record_audit(
        session,
        tenant_id=tenant_id,
        actor_role="system",
        actor_id="emr_connector",
        action="document_ingested" if verification["match_status"] == "exact_3pt_match" else "document_quarantined_3pt_mismatch",
        resource_type="document_ingestion",
        resource_id=row.id,
        metadata={
            "external_doc_ref": external_doc_ref,
            "match_status": verification["match_status"],
            "matched_patient_id": verification["matched_patient_id"],
        },
    )
    return row


def write_additive_emr_addendum(
    session: Session,
    *,
    tenant_id: str,
    patient_id: str,
    signed_by: str,
    entries: list[str],
    linked_doc_ref: str | None = None,
) -> dict[str, Any]:
    """Additive-only EMR connector writeback (never duplicates full chart or mutates EMR autonomously)."""
    addendum = {
        "addendum_id": new_id("emr_add"),
        "connector_mode": "additive_only",
        "emr_source_of_truth": "Central Medical EMR",
        "patient_id": patient_id,
        "signed_by": signed_by,
        "linked_doc_ref": linked_doc_ref,
        "entries": entries,
        "written_at": utcnow().isoformat(),
    }
    record_audit(
        session,
        tenant_id=tenant_id,
        actor_role="clinician",
        actor_id=signed_by,
        action="emr_additive_addendum_written",
        resource_type="patient",
        resource_id=patient_id,
        metadata=addendum,
    )
    return addendum


def list_quarantined_documents(session: Session, tenant_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(DocumentIngestion)
        .where(DocumentIngestion.tenant_id == tenant_id, DocumentIngestion.match_status == "quarantined_nurse")
        .order_by(DocumentIngestion.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": r.id,
            "external_doc_ref": r.external_doc_ref,
            "doc_kind": r.doc_kind,
            "source_org": r.source_org,
            "match_status": r.match_status,
            "checks": r.match_checks_json,
            "page_ref": r.page_ref,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
