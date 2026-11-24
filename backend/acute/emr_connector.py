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

import copy
import re
from contextlib import contextmanager
from typing import Any, Iterator, Protocol, Sequence, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .db import (
    AuditEvent,
    CaseLoop,
    DocumentIngestion,
    ExecutionTrace,
    Patient,
    new_id,
    record_audit,
    utcnow,
)
from .security import HKT

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
    "pat_cheung_t2dm": {
        "patient_id": "pat_cheung_t2dm",
        "mrn": "PAT-56012",
        "name_en": "CHEUNG Kwok-Wai",
        "dob": "1970-06-12",
        "hkid_masked": "D412***(8)",
        "age_sex": "56M",
        "active_meds": ["Metformin 500mg BD"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_tang_uti": {
        "patient_id": "pat_tang_uti",
        "mrn": "PAT-29077",
        "name_en": "TANG Ka-Yan",
        "dob": "1997-07-09",
        "hkid_masked": "Y304***(5)",
        "age_sex": "29F",
        "active_meds": [],
        "allergies": [],
        "g6pd_deficient": True,
    },
    "pat_tsang_thin": {
        "patient_id": "pat_tsang_thin",
        "mrn": "PAT-34055",
        "name_en": "TSANG Chun-Hin",
        "dob": "1992-11-03",
        "hkid_masked": "G721***(3)",
        "age_sex": "34M",
        "active_meds": [],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_lau_chest": {
        "patient_id": "pat_lau_chest",
        "mrn": "PAT-64081",
        "name_en": "LAU Wai-Fong",
        "dob": "1962-05-27",
        "hkid_masked": "E118***(9)",
        "age_sex": "64F",
        "active_meds": ["Perindopril 4mg daily", "Rosuvastatin 10mg nocte"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_ho_thyroid": {
        "patient_id": "pat_ho_thyroid",
        "mrn": "PAT-44029",
        "name_en": "HO Siu-Fong",
        "dob": "1982-01-16",
        "hkid_masked": "P605***(4)",
        "age_sex": "44F",
        "active_meds": ["Ferrous sulfate 200mg daily"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_kwok_proxy": {
        "patient_id": "pat_kwok_proxy",
        "mrn": "PAT-78011",
        "name_en": "KWOK Wing-Keung",
        "dob": "1948-10-08",
        "hkid_masked": "B209***(7)",
        "age_sex": "78M",
        "active_meds": ["Amlodipine 5mg daily", "Losartan 50mg daily"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_ng_quarantine": {
        "patient_id": "pat_ng_quarantine",
        "mrn": "PAT-67033",
        "name_en": "NG Siu-Lun",
        "dob": "1959-08-14",
        "hkid_masked": "H512***(0)",
        "age_sex": "67M",
        "active_meds": ["Atorvastatin 20mg nocte"],
        "allergies": [],
        "g6pd_deficient": False,
    },
    "pat_chui_cough": {
        "patient_id": "pat_chui_cough",
        "mrn": "PAT-41066",
        "name_en": "CHUI Ka-Ming",
        "dob": "1985-03-22",
        "hkid_masked": "K448***(6)",
        "age_sex": "41M",
        "active_meds": [],
        "allergies": [],
        "g6pd_deficient": False,
    },
}

_PRIVATE_SAM_MARKERS: tuple[str, ...] = (
    "private · doctor & sam",
    "private · doctor and sam",
    "doctor & sam only",
    "private sam chat",
    "private_sam_chat",
    "ephemeral_private",
    "[private sam]",
    "private registrar chat",
)


@contextmanager
def _resolve_session(db_or_session: Any, tenant_id: str) -> Iterator[Session]:
    """Accept either an active SQLAlchemy `Session` or a `Database` instance."""
    if isinstance(db_or_session, Session):
        yield db_or_session
    elif hasattr(db_or_session, "tenant_session"):
        with db_or_session.tenant_session(tenant_id) as sess:
            yield sess
    else:
        raise TypeError("Expected SQLAlchemy Session or Database instance")


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())


def _norm_ws(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def assert_zero_sam_chat_leakage(
    entries: Sequence[str],
    private_sam_messages: Sequence[Any] | None = None,
) -> None:
    """Hard invariant guard: raise `ValueError` if any ephemeral private Sam chat leaks into EMR addendum entries."""
    cleaned_entries = [str(e or "") for e in (entries or [])]
    joined_norm = _norm_ws(" ".join(cleaned_entries))

    # 1. Explicit private-thread markers are unconditionally forbidden in EMR addenda
    for marker in _PRIVATE_SAM_MARKERS:
        if marker in joined_norm:
            raise ValueError(
                f"EMR additive invariant violation: private Sam thread marker '{marker}' detected in EMR addendum."
            )

    if not private_sam_messages:
        return

    # 2. Check every ephemeral private Sam message (doctor question or Sam answer) for verbatim or shingle leakage
    for raw_msg in private_sam_messages:
        if isinstance(raw_msg, dict):
            if raw_msg.get("ephemeral_private") is False:
                continue
            msg_text = str(raw_msg.get("text") or raw_msg.get("content") or raw_msg.get("answer") or "")
        else:
            msg_text = str(raw_msg or "")

        msg_norm = _norm_ws(msg_text)
        if len(msg_norm) < 12:
            continue

        # Direct verbatim substring leak
        for entry in cleaned_entries:
            entry_norm = _norm_ws(entry)
            if not entry_norm:
                continue
            if msg_norm in entry_norm:
                raise ValueError(
                    "EMR additive invariant violation: ephemeral private Sam chat content leaked into EMR addendum."
                )
            # Check 6-word sliding shingles for partial paste of longer private Q&A turns
            words = msg_norm.split()
            if len(words) >= 6:
                for idx in range(0, len(words) - 5):
                    shingle = " ".join(words[idx : idx + 6])
                    if len(shingle) >= 24 and shingle in entry_norm:
                        raise ValueError(
                            "EMR additive invariant violation: partial ephemeral private Sam chat leaked into EMR addendum."
                        )


def format_clipboard_addendum(
    *,
    patient_id: str,
    signed_by: str,
    entries: Sequence[str],
    patient_name: str = "",
    mchk_reg_no: str | None = None,
    artifact_ids: Sequence[str] | None = None,
    addendum_id: str | None = None,
    written_at: str | None = None,
) -> str:
    """Phase-1 Tier-0 zero-friction EMR bridge: 1-click formatted plain-text addendum for HK CMS paste-in."""
    reg = EMR_PATIENT_REGISTRY.get(patient_id, {})
    display_name = (patient_name or reg.get("name_en") or patient_id).strip()
    mrn = reg.get("mrn", "")
    header_pat = f"{display_name} ({mrn})" if mrn else display_name
    ts_label = written_at or utcnow().astimezone(HKT).strftime("%Y-%m-%d %H:%M HKT")
    signer = f"{signed_by} (MCHK {mchk_reg_no})" if mchk_reg_no else signed_by

    lines = [
        "[CLINIC EXECUTION LAYER — ADDITIVE ENCOUNTER ADDENDUM]",
        f"Patient: {header_pat}",
        f"Signed By: {signer} | Timestamp: {ts_label}",
    ]
    if addendum_id:
        lines.append(f"Addendum Ref: {addendum_id}")
    lines.append("Doctor-Confirmed Actions:")
    for item in entries:
        clean = str(item or "").strip()
        if clean:
            lines.append(f"  - {clean}")
    if artifact_ids:
        lines.append(f"Linked Artifacts: {', '.join(str(a) for a in artifact_ids if a)}")
    lines.append("SoT Notice: Clinic EMR remains sole clinical SoT (additive-only; excludes ephemeral AI chat).")
    return "\n".join(lines)


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
    trace_id: str | None = None,
    case_id: str | None = None,
    mchk_reg_no: str | None = None,
    patient_name: str = "",
    status: str = "pending_dispatch",
    artifact_ids: list[str] | None = None,
    private_sam_messages: Sequence[Any] | None = None,
    addendum_id: str | None = None,
) -> dict[str, Any]:
    """Additive-only EMR connector writeback (never duplicates full chart, never writes private Sam chat)."""
    assert_zero_sam_chat_leakage(entries, private_sam_messages)

    eff_addendum_id = addendum_id or new_id("emr_add")
    eff_status = status if status in ("pending_dispatch", "committed") else "pending_dispatch"
    eff_artifacts = [str(a) for a in (artifact_ids or []) if a]
    written_dt = utcnow()
    written_iso = written_dt.isoformat()
    hkt_label = written_dt.astimezone(HKT).strftime("%Y-%m-%d %H:%M HKT")
    clipboard_snippet = format_clipboard_addendum(
        patient_id=patient_id,
        patient_name=patient_name,
        signed_by=signed_by,
        mchk_reg_no=mchk_reg_no,
        entries=entries,
        artifact_ids=eff_artifacts,
        addendum_id=eff_addendum_id,
        written_at=hkt_label,
    )
    addendum = {
        "addendum_id": eff_addendum_id,
        "connector_mode": "additive_only",
        "emr_source_of_truth": "Central Medical EMR",
        "patient_id": patient_id,
        "patient_name": patient_name or EMR_PATIENT_REGISTRY.get(patient_id, {}).get("name_en", patient_id),
        "signed_by": signed_by,
        "mchk_reg_no": mchk_reg_no,
        "linked_doc_ref": linked_doc_ref,
        "trace_id": trace_id,
        "case_id": case_id,
        "status": eff_status,
        "entries": list(entries),
        "artifact_ids": eff_artifacts,
        "clipboard_snippet": clipboard_snippet,
        "contains_private_sam_chat": False,
        "retracted": False,
        "written_at": written_iso,
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


def retract_additive_emr_addendum(
    db: Any,
    tenant_id: str,
    addendum_id: str | None,
    trace_id: str,
    reason: str = "Clinician triggered 10-second Undo",
) -> dict[str, Any]:
    """Retract a staged additive EMR addendum during the 10-second Undo window with immutable audit log."""
    retracted_at = utcnow().isoformat()
    eff_addendum_id = addendum_id

    with _resolve_session(db, tenant_id) as session:
        trace = session.execute(
            select(ExecutionTrace).where(
                ExecutionTrace.tenant_id == tenant_id,
                ExecutionTrace.id == trace_id,
            )
        ).scalars().first()
        if trace is not None and isinstance(trace.emr_addendum_json, dict) and trace.emr_addendum_json:
            updated = dict(trace.emr_addendum_json)
            if not eff_addendum_id:
                eff_addendum_id = updated.get("addendum_id")
            updated["status"] = "retracted"
            updated["retracted"] = True
            updated["retracted_at"] = retracted_at
            updated["retraction_reason"] = reason
            trace.emr_addendum_json = updated
            flag_modified(trace, "emr_addendum_json")

        payload = {
            "addendum_id": eff_addendum_id,
            "trace_id": trace_id,
            "status": "retracted",
            "retracted": True,
            "reason": reason,
            "retracted_at": retracted_at,
        }
        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role="clinician",
            actor_id="emr_connector",
            action="emr_additive_addendum_retracted",
            resource_type="emr_addendum",
            resource_id=eff_addendum_id or trace_id,
            metadata=payload,
        )
        session.flush()
    return payload


def list_patient_emr_addenda(
    db: Any,
    tenant_id: str,
    patient_id: str | None = None,
    *,
    include_retracted: bool = False,
) -> list[dict[str, Any]]:
    """List additive EMR addenda visible in `/emr`, reconciling retractions and undone traces."""
    with _resolve_session(db, tenant_id) as session:
        audit_rows = session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.action.in_(("emr_additive_addendum_written", "emr_additive_addendum_retracted")),
            )
            .order_by(AuditEvent.created_at.asc())
        ).scalars().all()

        undone_traces = set(
            session.execute(
                select(ExecutionTrace.id).where(
                    ExecutionTrace.tenant_id == tenant_id,
                    ExecutionTrace.status == "undone",
                )
            ).scalars().all()
        )

    by_addendum_id: dict[str, dict[str, Any]] = {}
    retracted_addendum_ids: set[str] = set()
    retracted_trace_ids: set[str] = set(undone_traces)

    for ev in audit_rows:
        meta = dict(ev.metadata_json or {})
        if ev.action == "emr_additive_addendum_retracted":
            if meta.get("addendum_id"):
                retracted_addendum_ids.add(str(meta["addendum_id"]))
            if meta.get("trace_id"):
                retracted_trace_ids.add(str(meta["trace_id"]))
            continue

        aid = str(meta.get("addendum_id") or ev.id)
        meta.setdefault("addendum_id", aid)
        meta.setdefault("patient_id", ev.resource_id)
        meta.setdefault("connector_mode", "additive_only")
        meta.setdefault("contains_private_sam_chat", False)
        meta.setdefault("status", "committed")
        meta.setdefault("artifact_ids", [])
        if not meta.get("clipboard_snippet"):
            meta["clipboard_snippet"] = format_clipboard_addendum(
                patient_id=str(meta.get("patient_id") or ""),
                signed_by=str(meta.get("signed_by") or ev.actor_id),
                entries=list(meta.get("entries") or []),
                patient_name=str(meta.get("patient_name") or ""),
                mchk_reg_no=meta.get("mchk_reg_no"),
                artifact_ids=list(meta.get("artifact_ids") or []),
                addendum_id=aid,
            )
        # Coalesce updates to the same addendum_id (preserving earliest written_at, newest enrichment)
        if aid in by_addendum_id:
            prev = by_addendum_id[aid]
            merged = {**prev, **meta}
            if prev.get("written_at"):
                merged["written_at"] = prev["written_at"]
            by_addendum_id[aid] = merged
        else:
            by_addendum_id[aid] = meta

    results: list[dict[str, Any]] = []
    for aid, item in by_addendum_id.items():
        if patient_id and item.get("patient_id") != patient_id:
            continue
        tid = item.get("trace_id")
        is_retracted = (
            bool(item.get("retracted"))
            or aid in retracted_addendum_ids
            or (tid is not None and str(tid) in retracted_trace_ids)
        )
        if is_retracted:
            item["retracted"] = True
            item["status"] = "retracted"
            if not include_retracted:
                continue
        results.append(item)

    results.sort(key=lambda x: str(x.get("written_at") or ""), reverse=True)
    return results


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


def resolve_quarantined_document(
    db: Any,
    tenant_id: str,
    doc_id: str,
    *,
    action: str,
    verified_patient_id: str | None = None,
    verification_note: str = "",
    resolved_by: str = "Nurse Rachel Chan",
) -> dict[str, Any]:
    """Resolve a Nurse-quarantined external document by linking to a verified patient or rejecting wrong-patient scan."""
    if action not in ("link_verified", "reject_wrong_patient"):
        raise ValueError("action must be 'link_verified' or 'reject_wrong_patient'.")

    resolved_at = utcnow().isoformat()
    with _resolve_session(db, tenant_id) as session:
        row = session.execute(
            select(DocumentIngestion).where(
                DocumentIngestion.tenant_id == tenant_id,
                DocumentIngestion.id == doc_id,
            )
        ).scalars().first()
        if row is None:
            raise ValueError(f"Quarantined document '{doc_id}' not found.")

        checks = dict(row.match_checks_json or {})
        if action == "link_verified":
            if not verified_patient_id or not verified_patient_id.strip():
                raise ValueError("verified_patient_id is required when action='link_verified'.")
            target_pid = verified_patient_id.strip()
            # Validate patient exists either in DB or EMR registry
            db_pat = session.execute(
                select(Patient).where(Patient.tenant_id == tenant_id, Patient.id == target_pid)
            ).scalars().first()
            if db_pat is None and target_pid not in EMR_PATIENT_REGISTRY:
                raise ValueError(f"Verified patient '{target_pid}' not found in Clinic EMR directory.")

            row.matched_patient_id = target_pid
            row.match_status = "nurse_verified_match"
            checks.update(
                {
                    "resolved_by": resolved_by,
                    "resolved_at": resolved_at,
                    "resolution_action": action,
                    "verified_patient_id": target_pid,
                    "verification_note": verification_note.strip(),
                }
            )
            row.match_checks_json = checks
            flag_modified(row, "match_checks_json")

            # Surface verified attachment on the patient's active CaseLoop if present
            case = session.execute(
                select(CaseLoop).where(
                    CaseLoop.tenant_id == tenant_id,
                    CaseLoop.patient_id == target_pid,
                )
            ).scalars().first()
            if case is not None:
                summary = copy.deepcopy(case.summary_json or {})
                attachments = list(summary.get("attachments") or [])
                if not any(a.get("id") == row.id for a in attachments):
                    attachments.append(
                        {
                            "id": row.id,
                            "label": f"{row.source_org} · {row.external_doc_ref} ({row.page_ref}) [Nurse verified]",
                            "quote": row.verbatim_quote,
                            "verified_3pt": True,
                            "verified_by": resolved_by,
                        }
                    )
                    summary["attachments"] = attachments
                    case.summary_json = summary
                    flag_modified(case, "summary_json")
        else:
            row.matched_patient_id = None
            row.match_status = "rejected_wrong_patient"
            checks.update(
                {
                    "resolved_by": resolved_by,
                    "resolved_at": resolved_at,
                    "resolution_action": action,
                    "verification_note": verification_note.strip(),
                }
            )
            row.match_checks_json = checks
            flag_modified(row, "match_checks_json")

        record_audit(
            session,
            tenant_id=tenant_id,
            actor_role="nurse",
            actor_id=resolved_by,
            action="document_quarantine_resolved",
            resource_type="document_ingestion",
            resource_id=row.id,
            metadata={
                "external_doc_ref": row.external_doc_ref,
                "action": action,
                "match_status": row.match_status,
                "matched_patient_id": row.matched_patient_id,
                "verification_note": verification_note.strip(),
            },
        )
        session.flush()
        return {
            "id": row.id,
            "external_doc_ref": row.external_doc_ref,
            "match_status": row.match_status,
            "matched_patient_id": row.matched_patient_id,
            "action": action,
            "resolved_by": resolved_by,
            "verification_note": verification_note.strip(),
            "resolved_at": resolved_at,
            "checks": row.match_checks_json,
        }


@runtime_checkable
class EMRConnectorProtocol(Protocol):
    """Protocol for additive-only Hong Kong clinic EMR/CMS adapters."""

    connector_name: str
    connector_tier: str

    def read_patient_summary(self, db: Any, tenant_id: str, patient_id: str) -> dict[str, Any] | None: ...

    def write_addendum(
        self,
        session: Session,
        *,
        tenant_id: str,
        patient_id: str,
        signed_by: str,
        entries: list[str],
        linked_doc_ref: str | None = None,
        trace_id: str | None = None,
        case_id: str | None = None,
        mchk_reg_no: str | None = None,
        patient_name: str = "",
        status: str = "pending_dispatch",
        artifact_ids: list[str] | None = None,
        private_sam_messages: Sequence[Any] | None = None,
        addendum_id: str | None = None,
    ) -> dict[str, Any]: ...

    def retract_addendum(
        self,
        db: Any,
        *,
        tenant_id: str,
        addendum_id: str | None,
        trace_id: str,
        reason: str = "Clinician triggered 10-second Undo",
    ) -> dict[str, Any]: ...

    def list_addenda(self, db: Any, tenant_id: str, patient_id: str | None = None) -> list[dict[str, Any]]: ...


class SimulatedPostgresEMRAdapter:
    """PostgreSQL-backed additive-only EMR adapter implementing `EMRConnectorProtocol` for live demos & tests."""

    connector_name = "simulated_postgres_hk_emr"
    connector_tier = "tier_2_additive_api_with_tier_0_clipboard"

    def read_patient_summary(self, db: Any, tenant_id: str, patient_id: str) -> dict[str, Any] | None:
        with _resolve_session(db, tenant_id) as session:
            pat = session.execute(
                select(Patient).where(Patient.tenant_id == tenant_id, Patient.id == patient_id)
            ).scalars().first()
            if pat is not None:
                return {
                    "patient_id": pat.id,
                    "name_en": pat.name_en,
                    "name_zh": pat.name_zh,
                    "dob": pat.dob,
                    "sex": pat.sex,
                    "hkid_masked": pat.hkid_masked,
                    "g6pd_deficient": bool(pat.g6pd_deficient),
                    "allergies": list(pat.allergies_json or []),
                    "active_meds": list(pat.current_meds_json or []),
                    "egfr": pat.egfr,
                    "source": "postgres_emr",
                }
        reg = EMR_PATIENT_REGISTRY.get(patient_id)
        return dict(reg) if reg else None

    def write_addendum(
        self,
        session: Session,
        *,
        tenant_id: str,
        patient_id: str,
        signed_by: str,
        entries: list[str],
        linked_doc_ref: str | None = None,
        trace_id: str | None = None,
        case_id: str | None = None,
        mchk_reg_no: str | None = None,
        patient_name: str = "",
        status: str = "pending_dispatch",
        artifact_ids: list[str] | None = None,
        private_sam_messages: Sequence[Any] | None = None,
        addendum_id: str | None = None,
    ) -> dict[str, Any]:
        return write_additive_emr_addendum(
            session,
            tenant_id=tenant_id,
            patient_id=patient_id,
            signed_by=signed_by,
            entries=entries,
            linked_doc_ref=linked_doc_ref,
            trace_id=trace_id,
            case_id=case_id,
            mchk_reg_no=mchk_reg_no,
            patient_name=patient_name,
            status=status,
            artifact_ids=artifact_ids,
            private_sam_messages=private_sam_messages,
            addendum_id=addendum_id,
        )

    def retract_addendum(
        self,
        db: Any,
        *,
        tenant_id: str,
        addendum_id: str | None,
        trace_id: str,
        reason: str = "Clinician triggered 10-second Undo",
    ) -> dict[str, Any]:
        return retract_additive_emr_addendum(
            db,
            tenant_id=tenant_id,
            addendum_id=addendum_id,
            trace_id=trace_id,
            reason=reason,
        )

    def list_addenda(self, db: Any, tenant_id: str, patient_id: str | None = None) -> list[dict[str, Any]]:
        return list_patient_emr_addenda(db, tenant_id, patient_id=patient_id)

    def bridge_metadata(self, db: Any, tenant_id: str) -> dict[str, Any]:
        """Telemetry summary exposed at `xray.emr_bridge` in `/api/v1/workspace/overview`."""
        all_addenda = list_patient_emr_addenda(db, tenant_id, include_retracted=True)
        active_addenda = [a for a in all_addenda if not a.get("retracted")]
        retracted_addenda = [a for a in all_addenda if a.get("retracted")]
        with _resolve_session(db, tenant_id) as session:
            quarantined_count = len(list_quarantined_documents(session, tenant_id))
        return {
            "adapter": self.connector_name,
            "connector_mode": "additive_only",
            "tier": self.connector_tier,
            "clinical_sot": "Central Medical EMR (Sole Legal/Clinical Source of Truth)",
            "zero_sam_chat_leakage_enforced": True,
            "phase1_clipboard_bridge_enabled": True,
            "supported_hk_emrs": [
                {"name": "EC Healthcare Stack", "tier": "Tier 2 REST + Tier 0 Clipboard"},
                {"name": "ClinicSolution X", "tier": "Tier 0 Clipboard + Tier 1 HL7/PDF Drop"},
                {"name": "Apricot / VITA", "tier": "Tier 2/3 FHIR R4 + Webhooks"},
            ],
            "active_addenda_count": len(active_addenda),
            "retracted_addenda_count": len(retracted_addenda),
            "quarantined_docs_count": quarantined_count,
        }

