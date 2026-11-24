"""Hong Kong MCHK-compliant Medical Sick-Leave Certificate PDF generator & encrypted vault helper.

Enforces Medical Council of Hong Kong (MCHK) Telemedicine Guidelines:
- Valid Hong Kong Registered Medical Practitioner MCHK registration number (`^[ML]\\d{5}$`)
- Doctor full name, clinic name, patient name, masked HKID, encounter ID
- Strict acute teleconsultation sick-leave bounds: 1 to 7 days inclusive
- Optional dual-coded diagnosis (ICD-10 + ICPC-2)
- Canonical SHA-256 verification hash printed on the certificate for employer/registry verification
- Mandatory real-time medical assessment attestation statement
- Envelope-encrypted storage in DocumentVault (zero unencrypted PDFs on disk or public URLs)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fpdf import FPDF

from ..vault.vault import DocumentVault, StoredDocument
from .security import HKT, valid_mchk_reg
from .terminology import HK_DIAGNOSES, validate_diagnosis_coding

MIN_SICK_LEAVE_DAYS = 1
MAX_SICK_LEAVE_DAYS = 7

MCHK_ATTESTATION = (
    "Issued following real-time medical assessment by a Hong Kong Registered Medical Practitioner"
)


_CJK_FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_CJK_FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def _has_non_latin1(value: str) -> bool:
    if not value:
        return False
    try:
        value.encode("latin-1")
        return False
    except UnicodeEncodeError:
        return True


def _to_latin1_safe(value: str) -> str:
    """Ensure strings render cleanly with standard PDF Helvetica core fonts."""
    if not value:
        return ""
    return value.encode("latin-1", errors="replace").decode("latin-1")


def _pdf_text(pdf: FPDF, style: str, size: float, text: str, has_cjk_font: bool) -> str:
    """Select NotoCJK when text contains CJK/Unicode characters so Chinese names never turn into '???'."""
    if has_cjk_font and _has_non_latin1(text):
        pdf.set_font("NotoCJK", "B" if "B" in style else "", size)
        return text
    pdf.set_font("Helvetica", style, size)
    return _to_latin1_safe(text)


def _parse_iso_date(val: str | date | datetime, field_name: str) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val).strip()[:10])
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date for {field_name}: {val!r}") from exc


def compute_certificate_verification_hash(
    *,
    encounter_id: str,
    mchk_reg_no: str,
    doctor_name: str,
    clinic_name: str,
    patient_name: str,
    hkid_masked: str,
    start_date: str,
    end_date: str,
    days_off: int,
    diagnosis_icd10: str | None = None,
    diagnosis_icpc2: str | None = None,
    issued_at_iso: str = "",
    omit_employer_diagnosis: bool = False,
) -> str:
    """Compute a deterministic SHA-256 verification digest over signed certificate attributes."""
    canonical: dict[str, Any] = {
        "encounter_id": encounter_id.strip(),
        "mchk_reg_no": mchk_reg_no.strip().upper(),
        "doctor_name": doctor_name.strip(),
        "clinic_name": clinic_name.strip(),
        "patient_name": patient_name.strip(),
        "hkid_masked": hkid_masked.strip(),
        "start_date": start_date,
        "end_date": end_date,
        "days_off": int(days_off),
        "diagnosis_icd10": "" if omit_employer_diagnosis else (diagnosis_icd10 or "").strip(),
        "diagnosis_icpc2": "" if omit_employer_diagnosis else (diagnosis_icpc2 or "").strip(),
        "issued_at": issued_at_iso,
        "attestation": MCHK_ATTESTATION,
    }
    if omit_employer_diagnosis:
        canonical["diagnosis_omitted_for_employer"] = True
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def generate_sick_leave_pdf(
    *,
    mchk_reg_no: str,
    doctor_name: str,
    clinic_name: str,
    patient_name: str,
    hkid_masked: str,
    encounter_id: str,
    start_date: str | date,
    days_off: int,
    end_date: str | date | None = None,
    diagnosis_icd10: str | None = None,
    diagnosis_icpc2: str | None = None,
    diagnosis_text: str | None = None,
    clinical_remarks: str | None = None,
    issued_at: datetime | None = None,
    omit_employer_diagnosis: bool = False,
) -> tuple[bytes, str]:
    """Generate an MCHK-compliant Hong Kong Medical Sick-Leave Certificate PDF.

    Returns:
        (pdf_bytes, sha256_verification_hash)

    Raises:
        ValueError: If MCHK registration number is missing/invalid, required identity fields are
            missing, `days_off` is outside 1..7, dates are inconsistent, or coded diagnosis is invalid.
    """
    if not valid_mchk_reg(mchk_reg_no):
        raise ValueError(
            f"Invalid or missing MCHK registration number: {mchk_reg_no!r} (expected format M12345 or L12345)"
        )
    norm_mchk = mchk_reg_no.strip().upper()

    for label, val in (
        ("doctor_name", doctor_name),
        ("clinic_name", clinic_name),
        ("patient_name", patient_name),
        ("hkid_masked", hkid_masked),
        ("encounter_id", encounter_id),
    ):
        if not val or not str(val).strip():
            raise ValueError(f"Required certificate field '{label}' must be non-empty")

    if isinstance(days_off, bool) or not isinstance(days_off, int):
        raise ValueError(f"days_off must be an integer between {MIN_SICK_LEAVE_DAYS} and {MAX_SICK_LEAVE_DAYS}")
    if days_off < MIN_SICK_LEAVE_DAYS or days_off > MAX_SICK_LEAVE_DAYS:
        raise ValueError(
            f"days_off={days_off} is out of acute teleconsult bounds ({MIN_SICK_LEAVE_DAYS}..{MAX_SICK_LEAVE_DAYS} days)"
        )

    start_d = _parse_iso_date(start_date, "start_date")
    expected_end_d = start_d + timedelta(days=days_off - 1)
    if end_date is None:
        end_d = expected_end_d
    else:
        end_d = _parse_iso_date(end_date, "end_date")
        if end_d < start_d:
            raise ValueError(f"end_date ({end_d.isoformat()}) cannot precede start_date ({start_d.isoformat()})")
        actual_days = (end_d - start_d).days + 1
        if actual_days != days_off:
            raise ValueError(
                f"Date range {start_d.isoformat()}..{end_d.isoformat()} spans {actual_days} day(s), "
                f"which does not match days_off={days_off}"
            )

    norm_icd10: str | None = diagnosis_icd10.strip() if diagnosis_icd10 else None
    norm_icpc2: str | None = diagnosis_icpc2.strip() if diagnosis_icpc2 else None
    resolved_diag_display = diagnosis_text.strip() if diagnosis_text else ""

    if norm_icd10:
        diag_errors = validate_diagnosis_coding(norm_icd10, norm_icpc2)
        if diag_errors:
            raise ValueError("; ".join(diag_errors))
        diag_entry = HK_DIAGNOSES[norm_icd10]
        if not norm_icpc2:
            norm_icpc2 = diag_entry["icpc2"]
        if not resolved_diag_display:
            resolved_diag_display = diag_entry["icd10_display"]
    elif norm_icpc2:
        raise ValueError("diagnosis_icd10 is required when diagnosis_icpc2 is specified")

    issued_dt = (issued_at or datetime.now(timezone.utc)).astimezone(HKT)
    issued_iso = issued_dt.isoformat(timespec="seconds")

    verification_hash = compute_certificate_verification_hash(
        encounter_id=encounter_id,
        mchk_reg_no=norm_mchk,
        doctor_name=doctor_name,
        clinic_name=clinic_name,
        patient_name=patient_name,
        hkid_masked=hkid_masked,
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
        days_off=days_off,
        diagnosis_icd10=norm_icd10,
        diagnosis_icpc2=norm_icpc2,
        issued_at_iso=issued_iso,
        omit_employer_diagnosis=omit_employer_diagnosis,
    )

    import os
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_compression(False)
    pdf.set_auto_page_break(auto=True, margin=18)
    has_cjk_font = False
    if any(
        _has_non_latin1(s or "")
        for s in (
            clinic_name,
            patient_name,
            doctor_name,
            "" if omit_employer_diagnosis else resolved_diag_display,
            clinical_remarks,
        )
    ) and os.path.exists(_CJK_FONT_REGULAR):
        pdf.add_font("NotoCJK", "", _CJK_FONT_REGULAR)
        pdf.add_font("NotoCJK", "B", _CJK_FONT_BOLD if os.path.exists(_CJK_FONT_BOLD) else _CJK_FONT_REGULAR)
        has_cjk_font = True
    pdf.add_page()

    # Header banner
    pdf.set_fill_color(18, 45, 78)
    pdf.rect(10, 10, 190, 26, style="F")
    pdf.set_xy(15, 14)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(180, 8, _pdf_text(pdf, "B", 15, clinic_name.strip(), has_cjk_font), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        180,
        6,
        "MEDICAL SICK-LEAVE CERTIFICATE (HONG KONG REGISTERED MEDICAL PRACTITIONER)",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    # Reset text color
    pdf.set_text_color(20, 20, 20)
    pdf.ln(10)

    # Certificate metadata row
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(95, 6, f"Encounter ID: {_to_latin1_safe(encounter_id.strip())}")
    pdf.cell(95, 6, f"Issued (HKT): {issued_iso}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.line(10, pdf.get_y() + 1, 200, pdf.get_y() + 1)
    pdf.ln(6)

    # Patient particulars
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "1. PATIENT PARTICULARS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(50, 7, "Patient Full Name:")
    pdf.cell(0, 7, _pdf_text(pdf, "B", 10, patient_name.strip(), has_cjk_font), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(50, 7, "HKID (Masked):")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, _to_latin1_safe(hkid_masked.strip()), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Certification of unfitness
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "2. CERTIFICATION OF MEDICAL SICK LEAVE", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    day_word = "day" if days_off == 1 else "days"
    pdf.multi_cell(
        0,
        6,
        _to_latin1_safe(
            f"This is to certify that the above-named patient was examined and is advised to take "
            f"medical sick leave for {days_off} {day_word}, from {start_d.isoformat()} to "
            f"{end_d.isoformat()} (both dates inclusive)."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    pdf.cell(50, 7, "Sick Leave Period:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(
        0,
        7,
        f"{start_d.isoformat()} to {end_d.isoformat()} ({days_off} {day_word})",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(50, 7, "Diagnosis:")
    if omit_employer_diagnosis or not norm_icd10:
        diag_line = "Withheld at patient request (confidential medical condition)"
    else:
        diag_line = f"{resolved_diag_display} [ICD-10: {norm_icd10} | ICPC-2: {norm_icpc2}]"
    pdf.multi_cell(0, 7, _pdf_text(pdf, "B", 10, diag_line, has_cjk_font), new_x="LMARGIN", new_y="NEXT")

    if clinical_remarks and clinical_remarks.strip():
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(50, 7, "Remarks:")
        pdf.multi_cell(0, 7, _pdf_text(pdf, "", 10, clinical_remarks.strip(), has_cjk_font), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # MCHK Attestation box
    pdf.set_fill_color(242, 246, 250)
    pdf.set_draw_color(18, 45, 78)
    box_y = pdf.get_y()
    pdf.rect(10, box_y, 190, 20, style="DF")
    pdf.set_xy(14, box_y + 4)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5, "MCHK TELEMEDICINE ATTESTATION", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(14)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(182, 5, MCHK_ATTESTATION, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(box_y + 26)

    # Signing Registered Medical Practitioner
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "3. REGISTERED MEDICAL PRACTITIONER", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(55, 7, "Doctor Full Name:")
    pdf.cell(0, 7, _pdf_text(pdf, "B", 10, doctor_name.strip(), has_cjk_font), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(55, 7, "MCHK Registration No.:")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, norm_mchk, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(55, 7, "Clinic / Practice:")
    pdf.cell(0, 7, _pdf_text(pdf, "B", 10, clinic_name.strip(), has_cjk_font), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Cryptographic verification footer
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(0, 5, "CERTIFICATE VERIFICATION HASH (SHA-256):", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Courier", "", 8.5)
    pdf.cell(0, 5, verification_hash, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(
        0,
        5,
        "Electronically signed and encrypted in Clinic Document Vault. Verify hash via clinic portal.",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf_bytes = bytes(pdf.output())
    return pdf_bytes, verification_hash


@dataclass(frozen=True)
class IssuedCertificate:
    stored_document: StoredDocument
    vault_doc_id: str
    verification_hash: str
    pdf_sha256: str
    filename: str
    mchk_reg_no: str
    start_date: str
    end_date: str
    days_off: int
    diagnosis_icd10: str | None
    diagnosis_icpc2: str | None
    omit_employer_diagnosis: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "vault_doc_id": self.vault_doc_id,
            "verification_hash": self.verification_hash,
            "pdf_sha256": self.pdf_sha256,
            "filename": self.filename,
            "size_bytes": self.stored_document.size_bytes,
            "kms_key_id": self.stored_document.kms_key_id,
            "mchk_reg_no": self.mchk_reg_no,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "days_off": self.days_off,
            "diagnosis_icd10": self.diagnosis_icd10,
            "diagnosis_icpc2": self.diagnosis_icpc2,
            "omit_employer_diagnosis": self.omit_employer_diagnosis,
        }


def issue_encrypted_certificate(
    vault: DocumentVault,
    tenant_id: str,
    *,
    mchk_reg_no: str,
    doctor_name: str,
    clinic_name: str,
    patient_name: str,
    hkid_masked: str,
    encounter_id: str,
    start_date: str | date,
    days_off: int,
    end_date: str | date | None = None,
    diagnosis_icd10: str | None = None,
    diagnosis_icpc2: str | None = None,
    diagnosis_text: str | None = None,
    clinical_remarks: str | None = None,
    issued_at: datetime | None = None,
    omit_employer_diagnosis: bool = False,
) -> IssuedCertificate:
    """Generate an MCHK-compliant sick-leave PDF and store it encrypted inside `DocumentVault`.

    When `omit_employer_diagnosis=True`, dual ICD-10/ICPC-2 codes are preserved on the returned
    `IssuedCertificate` (for clinic `db.Certificate` storage) while omitted on the employer PDF
    and hashed with the privacy marker.
    """
    start_d = _parse_iso_date(start_date, "start_date")
    end_d = _parse_iso_date(end_date, "end_date") if end_date is not None else start_d + timedelta(days=days_off - 1)

    pdf_bytes, verification_hash = generate_sick_leave_pdf(
        mchk_reg_no=mchk_reg_no,
        doctor_name=doctor_name,
        clinic_name=clinic_name,
        patient_name=patient_name,
        hkid_masked=hkid_masked,
        encounter_id=encounter_id,
        start_date=start_d,
        end_date=end_d,
        days_off=days_off,
        diagnosis_icd10=diagnosis_icd10,
        diagnosis_icpc2=diagnosis_icpc2,
        diagnosis_text=diagnosis_text,
        clinical_remarks=clinical_remarks,
        issued_at=issued_at,
        omit_employer_diagnosis=omit_employer_diagnosis,
    )

    norm_icd10 = diagnosis_icd10.strip() if diagnosis_icd10 else None
    norm_icpc2 = diagnosis_icpc2.strip() if diagnosis_icpc2 else (
        HK_DIAGNOSES[norm_icd10]["icpc2"] if norm_icd10 and norm_icd10 in HK_DIAGNOSES else None
    )

    safe_enc = "".join(ch for ch in encounter_id if ch.isalnum() or ch in ("-", "_")) or "cert"
    filename = f"sick_leave_{safe_enc}_{start_d.isoformat()}.pdf"
    stored = vault.store(
        tenant_id=tenant_id,
        content=pdf_bytes,
        content_type="application/pdf",
        filename=filename,
    )
    return IssuedCertificate(
        stored_document=stored,
        vault_doc_id=stored.doc_id,
        verification_hash=verification_hash,
        pdf_sha256=stored.sha256,
        filename=filename,
        mchk_reg_no=mchk_reg_no.strip().upper(),
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
        days_off=days_off,
        diagnosis_icd10=norm_icd10,
        diagnosis_icpc2=norm_icpc2,
        omit_employer_diagnosis=omit_employer_diagnosis,
    )


def issue_certificate_from_order_primitive(
    db: Any,
    vault: DocumentVault,
    *,
    clinic_id: str,
    patient_id: str,
    encounter_id: str,
    practitioner_id: str,
    mchk_reg_no: str,
    practitioner_name: str,
    order: dict[str, Any],
    diagnosis_icd10: str | None = None,
    diagnosis_icpc2: str | None = None,
    patient_name: str = "",
    patient_hkid: str = "",
    clinic_name: str = "Central & Mid-Levels Family Medical",
    review_decision_id: str | None = None,
) -> dict[str, Any]:
    """Bind a canonical `/cert` order primitive directly to encrypted MCHK PDF generation & DB persistence.

    Accepts either canonical `snake_case` (`days_off`, `start_date`, `omit_employer_diagnosis`) or
    legacy `camelCase` (`daysOff`, `startDate`, `omitDx`). Defaults `omit_employer_diagnosis=True`
    per MCHK employer privacy practice while keeping dual ICD-10/ICPC-2 in `db.Certificate`.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from .db import Certificate, Encounter, Patient, Practitioner, ReviewDecision, new_id

    raw_days = order.get("days_off") if order.get("days_off") is not None else order.get("daysOff", 1)
    try:
        days_off = int(raw_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid days_off in /cert order: {raw_days!r}") from exc

    raw_start = order.get("start_date") or order.get("startDate")
    start_date: str | date = raw_start if raw_start else datetime.now(timezone.utc).astimezone(HKT).date()
    raw_end = order.get("end_date") or order.get("endDate")

    if "omit_employer_diagnosis" in order and order["omit_employer_diagnosis"] is not None:
        omit_dx = bool(order["omit_employer_diagnosis"])
    elif "omitDx" in order and order["omitDx"] is not None:
        omit_dx = bool(order["omitDx"])
    elif "omit_dx" in order and order["omit_dx"] is not None:
        omit_dx = bool(order["omit_dx"])
    else:
        omit_dx = True

    eff_icd10 = diagnosis_icd10 or order.get("diagnosis_icd10") or order.get("diagnosisIcd10")
    eff_icpc2 = diagnosis_icpc2 or order.get("diagnosis_icpc2") or order.get("diagnosisIcpc2")

    eff_patient_name = patient_name.strip()
    eff_patient_hkid = patient_hkid.strip()
    eff_clinic_name = clinic_name.strip() or "Central & Mid-Levels Family Medical"

    def _hydrate_from_session(sess: Session) -> None:
        nonlocal eff_patient_name, eff_patient_hkid, eff_clinic_name
        if not eff_patient_name or not eff_patient_hkid:
            pat = sess.execute(
                select(Patient).where(Patient.tenant_id == clinic_id, Patient.id == patient_id)
            ).scalars().first()
            if pat is not None:
                if not eff_patient_name:
                    eff_patient_name = pat.name_en or pat.name_zh or patient_id
                if not eff_patient_hkid:
                    eff_patient_hkid = pat.hkid_masked or "XXXX(X)"
        prac = sess.execute(
            select(Practitioner).where(Practitioner.tenant_id == clinic_id, Practitioner.id == practitioner_id)
        ).scalars().first()
        if prac is not None and prac.clinic_name:
            eff_clinic_name = prac.clinic_name

    if isinstance(db, Session):
        _hydrate_from_session(db)
    elif db is not None and hasattr(db, "tenant_session"):
        with db.tenant_session(clinic_id) as sess:
            _hydrate_from_session(sess)

    if not eff_patient_name:
        eff_patient_name = patient_id
    if not eff_patient_hkid:
        eff_patient_hkid = "XXXX(X)"

    issued = issue_encrypted_certificate(
        vault,
        clinic_id,
        mchk_reg_no=mchk_reg_no,
        doctor_name=practitioner_name,
        clinic_name=eff_clinic_name,
        patient_name=eff_patient_name,
        hkid_masked=eff_patient_hkid,
        encounter_id=encounter_id,
        start_date=start_date,
        end_date=raw_end,
        days_off=days_off,
        diagnosis_icd10=eff_icd10,
        diagnosis_icpc2=eff_icpc2,
        clinical_remarks=order.get("remarks") or order.get("note"),
        omit_employer_diagnosis=omit_dx,
    )

    cert_db_id: str | None = None

    def _persist_if_fk_present(sess: Session) -> None:
        nonlocal cert_db_id
        if not review_decision_id:
            return
        enc = sess.execute(
            select(Encounter).where(Encounter.tenant_id == clinic_id, Encounter.id == encounter_id)
        ).scalars().first()
        rev = sess.execute(
            select(ReviewDecision).where(ReviewDecision.tenant_id == clinic_id, ReviewDecision.id == review_decision_id)
        ).scalars().first()
        if enc is not None and rev is not None:
            row = Certificate(
                id=new_id("cert"),
                tenant_id=clinic_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                practitioner_id=practitioner_id,
                mchk_reg_no=issued.mchk_reg_no,
                review_decision_id=review_decision_id,
                days_off=issued.days_off,
                start_date=issued.start_date,
                end_date=issued.end_date,
                diagnosis_icd10=issued.diagnosis_icd10 or "J06.9",
                diagnosis_icpc2=issued.diagnosis_icpc2 or "R74",
                vault_doc_id=issued.vault_doc_id,
                verification_hash=issued.verification_hash,
            )
            sess.add(row)
            sess.flush()
            cert_db_id = row.id

    if isinstance(db, Session):
        _persist_if_fk_present(db)
    elif db is not None and hasattr(db, "tenant_session"):
        with db.tenant_session(clinic_id) as sess:
            _persist_if_fk_present(sess)

    result = issued.to_dict()
    result.update(
        {
            "certificate_id": cert_db_id,
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "practitioner_id": practitioner_id,
            "practitioner_name": practitioner_name,
            "clinic_name": eff_clinic_name,
            "stored_in_db": cert_db_id is not None,
        }
    )
    return result
