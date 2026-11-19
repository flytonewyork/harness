"""Comprehensive Wave 1B test suite:
- 40+ case golden prescribing safety engine suite (zero LLM, 100% deterministic)
- MCHK sick-leave PDF generation, enforcement, and DocumentVault encryption tests
- WhatsApp zero-leakage guard, bilingual utility templates, and Meta HMAC webhook tests
- Fault-injecting adapter retry, idempotency, state-machine, and corporate k-anonymity tests
- Real PostgreSQL 15 Durable Transactional Outbox restart, backoff, DLQ, and RLS isolation tests
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.acute.adapters import (
    BookingAdapter,
    CorporateBillingAdapter,
    FluVaxAdapter,
    PharmacyFulfilmentAdapter,
    WA_TEMPLATE_IDS,
    WhatsAppDoorbellAdapter,
)
from backend.acute.certificates import (
    MCHK_ATTESTATION,
    generate_sick_leave_pdf,
    issue_encrypted_certificate,
)
from backend.acute.contracts import AdapterError, FaultInjector
from backend.acute.db import (
    AuditEvent,
    Database,
    Encounter,
    OutboxEvent,
    Patient,
    Proposal,
    Tenant,
    new_id,
    utcnow,
)
from backend.acute.rx_rules import (
    MIN_OVERRIDE_REASON_CHARS,
    check_prescription_safety,
)
from backend.acute.security import WhatsAppLeakageError
from backend.acute.workflows import (
    DurableWorkflowEngine,
    EVENT_BOOKING_REMINDER,
    EVENT_FOLLOWUP_48H,
    EVENT_PROPOSAL_SLA_ESCALATE,
)
from backend.vault.vault import DocumentVault, LocalFSBackend, LocalKMSProvider, VaultError


# ======================================================================================
# 1. GOLDEN PRESCRIBING SAFETY SUITE (42 CASES)
# ======================================================================================
GOLDEN_RX_CASES = [
    # --- Clean / Advisory Pass Cases ---
    (
        "01_adult_paracetamol_clean_otc",
        {"age": 34, "allergies": [], "current_meds": [], "g6pd_deficient": False},
        [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QID", "days": 5}],
        "pass",
        [],
    ),
    (
        "02_adult_paracetamol_cantonese_brand_panadol_clean",
        {"age": 40, "allergies": ["NKDA"], "current_meds": []},
        [{"drug": "必理痛", "dose_mg": 1000, "frequency": "QID", "days": 3}],
        "pass",
        [],
    ),
    (
        "03_adult_cetirizine_otc_clean",
        {"age": 28, "allergies": [], "current_meds": []},
        [{"drug": "Zyrtec", "dose_mg": 10, "frequency": "OD", "days": 7}],
        "pass",
        [],
    ),
    (
        "04_adult_ibuprofen_part1_poison_warn_only",
        {"age": 30, "allergies": [], "current_meds": []},
        [{"drug": "ibuprofen", "dose_mg": 400, "frequency": "TDS", "days": 5}],
        "warn",
        ["cap138_part1_poison_label"],
    ),
    (
        "05_adult_amoxicillin_no_allergy_part1_warn",
        {"age": 35, "allergies": [], "current_meds": []},
        [{"drug": "amoxicillin", "dose_mg": 500, "frequency": "TDS", "days": 7}],
        "warn",
        ["cap138_part1_poison_label"],
    ),
    (
        "06_adult_oseltamivir_normal_egfr_part1_warn",
        {"age": 45, "egfr": 88, "allergies": [], "current_meds": []},
        [{"drug": "Tamiflu", "dose_mg": 75, "frequency": "BD", "days": 5}],
        "warn",
        ["cap138_part1_poison_label"],
    ),
    (
        "07_oseltamivir_renally_adjusted_egfr_45_75mg_od_allowed",
        {"age": 72, "egfr": 45, "allergies": [], "current_meds": []},
        [{"drug": "oseltamivir", "dose_mg": 75, "frequency": "OD", "days": 5}],
        "warn",
        ["cap138_part1_poison_label"],
    ),
    (
        "08_pediatric_paracetamol_weight_safe",
        {"age": 8, "weight_kg": 25.0, "allergies": [], "current_meds": []},
        [{"drug": "paracetamol", "dose_mg": 375, "frequency": "QID", "days": 3}],
        "pass",
        [],
    ),
    (
        "09_pediatric_ibuprofen_weight_safe",
        {"age": 10, "weight_kg": 30.0, "allergies": [], "current_meds": []},
        [{"drug": "ibuprofen", "dose_mg": 200, "frequency": "TDS", "days": 3}],
        "warn",
        ["cap138_part1_poison_label"],
    ),
    (
        "10_g6pd_patient_paracetamol_safe",
        {"age": 29, "g6pd_deficient": True, "allergies": [], "current_meds": []},
        [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QID", "days": 3}],
        "pass",
        [],
    ),
    (
        "11_adult_aspirin_age_16_non_g6pd_otc_pass",
        {"age": 16, "g6pd_deficient": False, "allergies": [], "current_meds": []},
        [{"drug": "aspirin", "dose_mg": 300, "frequency": "TDS", "days": 3}],
        "pass",
        [],
    ),
    # --- Hard Blocks: HK Cap. 134 Dangerous Drugs ---
    (
        "12_hard_block_cap134_codeine",
        {"age": 35},
        [{"drug": "codeine", "dose_mg": 15, "frequency": "TDS", "days": 3}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "13_hard_block_cap134_codeine_linctus_alias",
        {"age": 35},
        [{"drug": "Codeine Linctus", "dose_mg": 15, "frequency": "TDS", "days": 3}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "14_hard_block_cap134_zolpidem_stilnox",
        {"age": 42},
        [{"drug": "Stilnox", "dose_mg": 10, "frequency": "ON", "days": 5}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "15_hard_block_cap134_diazepam_valium",
        {"age": 42},
        [{"drug": "Valium", "dose_mg": 5, "frequency": "BD", "days": 3}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "16_hard_block_cap134_tramadol",
        {"age": 50},
        [{"drug": "tramadol", "dose_mg": 50, "frequency": "TDS", "days": 3}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "17_hard_block_cap134_unlisted_opioid_morphine",
        {"age": 50},
        [{"drug": "morphine sulfate", "dose_mg": 10, "frequency": "Q4H", "days": 2}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "18_hard_block_cap134_unlisted_benzo_alprazolam",
        {"age": 40},
        [{"drug": "Xanax (alprazolam)", "dose_mg": 0.5, "frequency": "BD", "days": 5}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    (
        "19_hard_block_cap134_unlisted_zdrug_zopiclone",
        {"age": 40},
        [{"drug": "zopiclone", "dose_mg": 7.5, "frequency": "ON", "days": 5}],
        "hard_block",
        ["cap134_dangerous_drug"],
    ),
    # --- Hard Blocks: Unknown / Unlisted Drug Floor ---
    (
        "20_hard_block_unknown_unlisted_drug",
        {"age": 35},
        [{"drug": "ivermectin", "dose_mg": 12, "frequency": "OD", "days": 3}],
        "hard_block",
        ["unknown_formulary_drug"],
    ),
    (
        "21_hard_block_empty_drug_name",
        {"age": 35},
        [{"drug": "", "dose_mg": 500, "frequency": "OD", "days": 3}],
        "hard_block",
        ["unknown_formulary_drug"],
    ),
    # --- Hard Blocks: Pediatric Aspirin (Reye Syndrome) ---
    (
        "22_hard_block_pediatric_aspirin_age_12",
        {"age": 12, "weight_kg": 40},
        [{"drug": "aspirin", "dose_mg": 300, "frequency": "BD", "days": 2}],
        "hard_block",
        ["pediatric_aspirin_reye"],
    ),
    (
        "23_hard_block_pediatric_aspirin_via_dob_age_15",
        {"dob": "2011-06-01"},
        [{"drug": "aspirin", "dose_mg": 300, "frequency": "BD", "days": 2}],
        "hard_block",
        ["pediatric_aspirin_reye"],
    ),
    # --- Override Required: Allergy & Cross-Reactivity ---
    (
        "24_override_penicillin_allergy_cross_amoxicillin",
        {"age": 32, "allergies": ["Penicillin"]},
        [{"drug": "amoxicillin", "dose_mg": 500, "frequency": "TDS", "days": 5}],
        "override_required",
        ["allergy_contraindication"],
    ),
    (
        "25_override_penicillin_allergy_cross_augmentin",
        {"age": 32, "allergies": ["penicillin"]},
        [{"drug": "Augmentin", "dose_mg": 625, "frequency": "TDS", "days": 5}],
        "override_required",
        ["allergy_contraindication"],
    ),
    (
        "26_override_nsaid_allergy_cross_ibuprofen",
        {"age": 32, "allergies": ["NSAID"]},
        [{"drug": "ibuprofen", "dose_mg": 400, "frequency": "TDS", "days": 3}],
        "override_required",
        ["allergy_contraindication"],
    ),
    (
        "27_override_aspirin_allergy_cross_naproxen",
        {"age": 32, "allergies": ["aspirin"]},
        [{"drug": "naproxen", "dose_mg": 250, "frequency": "BD", "days": 3}],
        "override_required",
        ["allergy_contraindication"],
    ),
    (
        "28_override_sulfonamide_allergy_cross_cotrimoxazole",
        {"age": 32, "allergies": ["sulfonamide"]},
        [{"drug": "Bactrim", "dose_mg": 480, "frequency": "BD", "days": 5}],
        "override_required",
        ["allergy_contraindication"],
    ),
    (
        "29_override_exact_allergy_paracetamol",
        {"age": 32, "allergies": ["paracetamol"]},
        [{"drug": "Panadol", "dose_mg": 500, "frequency": "QID", "days": 3}],
        "override_required",
        ["allergy_contraindication"],
    ),
    # --- Override Required: HK G6PD Deficiency ---
    (
        "30_override_g6pd_cotrimoxazole",
        {"age": 30, "g6pd_deficient": True},
        [{"drug": "co-trimoxazole", "dose_mg": 480, "frequency": "BD", "days": 5}],
        "override_required",
        ["g6pd_contraindication"],
    ),
    (
        "31_override_g6pd_nitrofurantoin",
        {"age": 30, "g6pd_deficient": True, "egfr": 90},
        [{"drug": "nitrofurantoin", "dose_mg": 100, "frequency": "BD", "days": 5}],
        "override_required",
        ["g6pd_contraindication"],
    ),
    (
        "32_override_g6pd_aspirin",
        {"age": 30, "g6pd_deficient": True},
        [{"drug": "aspirin", "dose_mg": 300, "frequency": "BD", "days": 3}],
        "override_required",
        ["g6pd_contraindication"],
    ),
    # --- Override Required: Major Drug-Drug Interactions ---
    (
        "33_override_ddi_warfarin_ibuprofen",
        {"age": 65, "current_meds": ["warfarin 3mg daily"]},
        [{"drug": "ibuprofen", "dose_mg": 400, "frequency": "TDS", "days": 3}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "34_override_ddi_warfarin_clarithromycin",
        {"age": 65, "current_meds": ["warfarin"]},
        [{"drug": "clarithromycin", "dose_mg": 500, "frequency": "BD", "days": 5}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "35_override_ddi_warfarin_cotrimoxazole",
        {"age": 65, "current_meds": ["warfarin"]},
        [{"drug": "co-trimoxazole", "dose_mg": 480, "frequency": "BD", "days": 5}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "36_override_ddi_doac_apixaban_naproxen",
        {"age": 68, "current_meds": ["apixaban 5mg BD"]},
        [{"drug": "naproxen", "dose_mg": 250, "frequency": "BD", "days": 3}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "37_override_ddi_doac_rivaroxaban_ibuprofen",
        {"age": 68, "current_meds": ["rivaroxaban 20mg OD"]},
        [{"drug": "ibuprofen", "dose_mg": 400, "frequency": "BD", "days": 3}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "38_override_ddi_pseudoephedrine_maoi",
        {"age": 48, "current_meds": ["phenelzine 15mg TDS"]},
        [{"drug": "pseudoephedrine", "dose_mg": 60, "frequency": "TDS", "days": 3}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "39_override_pseudoephedrine_uncontrolled_htn",
        {"age": 58, "conditions": ["uncontrolled hypertension"]},
        [{"drug": "Sudafed", "dose_mg": 60, "frequency": "TDS", "days": 3}],
        "override_required",
        ["uncontrolled_hypertension_contraindication"],
    ),
    (
        "40_override_ddi_dextromethorphan_ssri_sertraline",
        {"age": 31, "current_meds": ["sertraline 50mg OD"]},
        [{"drug": "dextromethorphan", "dose_mg": 15, "frequency": "QID", "days": 5}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "41_override_ddi_dextromethorphan_maoi",
        {"age": 45, "current_meds": ["moclobemide 150mg BD"]},
        [{"drug": "dextromethorphan", "dose_mg": 15, "frequency": "TDS", "days": 3}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "42_override_ddi_clarithromycin_simvastatin",
        {"age": 60, "current_meds": ["simvastatin 40mg ON"]},
        [{"drug": "clarithromycin", "dose_mg": 500, "frequency": "BD", "days": 7}],
        "override_required",
        ["major_drug_interaction"],
    ),
    (
        "43_override_ddi_clarithromycin_atorvastatin",
        {"age": 60, "current_meds": ["atorvastatin 20mg ON"]},
        [{"drug": "Klacid", "dose_mg": 500, "frequency": "BD", "days": 7}],
        "override_required",
        ["major_drug_interaction"],
    ),
    # --- Override Required: Dose Ceilings, Pediatric Weight & Renal ---
    (
        "44_override_paracetamol_daily_ceiling_5000mg",
        {"age": 35},
        [{"drug": "paracetamol", "dose_mg": 1000, "doses_per_day": 5, "days": 3}],
        "override_required",
        ["daily_dose_ceiling_exceeded"],
    ),
    (
        "45_override_paracetamol_single_ceiling_1500mg",
        {"age": 35},
        [{"drug": "paracetamol", "dose_mg": 1500, "frequency": "BD", "days": 3}],
        "override_required",
        ["single_dose_ceiling_exceeded"],
    ),
    (
        "46_override_pediatric_paracetamol_weight_exceeded",
        {"age": 6, "weight_kg": 20.0},
        [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QID", "days": 3}],
        "override_required",
        ["pediatric_weight_dose_exceeded"],
    ),
    (
        "47_override_oseltamivir_egfr_45_unadjusted_150mg_day",
        {"age": 70, "egfr": 45},
        [{"drug": "oseltamivir", "dose_mg": 75, "frequency": "BD", "days": 5}],
        "override_required",
        ["renal_dose_adjustment_required"],
    ),
    (
        "48_override_nitrofurantoin_egfr_35_contraindicated",
        {"age": 74, "egfr": 35},
        [{"drug": "nitrofurantoin", "dose_mg": 100, "frequency": "BD", "days": 5}],
        "override_required",
        ["renal_contraindication"],
    ),
    # --- Advisory Warnings ---
    (
        "49_warn_duplicate_nsaid_class",
        {"age": 35},
        [
            {"drug": "ibuprofen", "dose_mg": 400, "frequency": "BD", "days": 3},
            {"drug": "naproxen", "dose_mg": 250, "frequency": "BD", "days": 3},
        ],
        "warn",
        ["duplicate_therapeutic_class", "cap138_part1_poison_label"],
    ),
    (
        "50_warn_sedating_antihistamine_chlorpheniramine",
        {"age": 35},
        [{"drug": "Piriton", "dose_mg": 4, "frequency": "TDS", "days": 3}],
        "warn",
        ["sedating_antihistamine_caution"],
    ),
]


@pytest.mark.parametrize("case_id,patient,items,expected_status,expected_codes", GOLDEN_RX_CASES)
def test_golden_rx_safety_suite(case_id, patient, items, expected_status, expected_codes):
    assert len(GOLDEN_RX_CASES) >= 30
    report = check_prescription_safety(patient, items, as_of=date(2026, 11, 19))
    assert report.status == expected_status, f"[{case_id}] expected {expected_status}, got {report.status}: {report.findings}"
    actual_codes = {f.code for f in report.findings}
    for code in expected_codes:
        assert code in actual_codes, f"[{case_id}] missing expected code {code} in {actual_codes}"


def test_rx_enrichment_and_override_validation():
    # Canonical enrichment check
    clean_report = check_prescription_safety(
        {"age": 30},
        [
            {"drug": "Panadol", "dose_mg": 500, "frequency": "QID", "days": 3},
            {"drug": "特敏福", "dose_mg": 75, "frequency": "BD", "days": 5},
        ],
    )
    assert len(clean_report.enriched_items) == 2
    p_item, o_item = clean_report.enriched_items
    assert p_item["rxnorm"] == "161"
    assert p_item["atc"] == "N02BE01"
    assert p_item["display"] == "Paracetamol 500mg Tablet"
    assert p_item["display_zh"] == "撲熱息痛 500毫克"
    assert p_item["hk_schedule"] == "OTC"
    assert p_item["daily_mg"] == 2000.0

    assert o_item["rxnorm"] == "261315"
    assert o_item["atc"] == "J05AH02"
    assert o_item["display"] == "Oseltamivir (Tamiflu) 75mg Capsule"
    assert o_item["display_zh"] == "奧司他韋（特敏福）75毫克"
    assert o_item["hk_schedule"] == "PART_I_POISON"
    assert o_item["daily_mg"] == 150.0

    # Hard block CANNOT be overridden even with a long reason
    hb = check_prescription_safety({"age": 35}, [{"drug": "codeine", "dose_mg": 15, "frequency": "TDS"}])
    ok, msg = hb.validate_override("Long clinical justification that must still be rejected by hard block")
    assert not ok
    assert "cannot be overridden" in (msg or "").lower()

    # Override-required needs >= MIN_OVERRIDE_REASON_CHARS chars
    ov = check_prescription_safety(
        {"age": 35, "allergies": ["penicillin"]},
        [{"drug": "amoxicillin", "dose_mg": 500, "frequency": "TDS"}],
    )
    assert ov.validate_override(None)[0] is False
    assert ov.validate_override("short")[0] is False
    assert ov.validate_override(" " * 15)[0] is False
    valid_reason = "Childhood rash non-IgE; discussed risks with patient and tolerance confirmed."
    assert len(valid_reason) >= MIN_OVERRIDE_REASON_CHARS
    assert ov.validate_override(valid_reason) == (True, None)


# ======================================================================================
# 2. MEDICAL SICK-LEAVE CERTIFICATE PDF + MCHK ENFORCEMENT + VAULT ENCRYPTION
# ======================================================================================
def test_sick_leave_pdf_generation_and_mchk_enforcement():
    pdf_bytes, vhash = generate_sick_leave_pdf(
        mchk_reg_no="M12345",
        doctor_name="Dr. Wing-Yan Chan",
        clinic_name="Central Harbour GP Clinic",
        patient_name="Tai-Man Wong",
        hkid_masked="A123XXX(X)",
        encounter_id="enc_flu_001",
        start_date="2026-11-19",
        end_date="2026-11-20",
        days_off=2,
        diagnosis_icd10="J11.1",
        diagnosis_icpc2="R80",
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(vhash) == 64
    pdf_text = pdf_bytes.replace(b"\\(", b"(").replace(b"\\)", b")")
    assert b"M12345" in pdf_text
    assert b"Dr. Wing-Yan Chan" in pdf_text
    assert b"Central Harbour GP Clinic" in pdf_text
    assert b"Tai-Man Wong" in pdf_text
    assert b"A123XXX(X)" in pdf_text
    assert b"enc_flu_001" in pdf_text
    assert b"J11.1" in pdf_text
    assert b"R80" in pdf_text
    assert vhash.encode("ascii") in pdf_text
    assert MCHK_ATTESTATION.encode("ascii") in pdf_text

    # Optional diagnosis withheld works cleanly
    pdf_no_diag, vhash2 = generate_sick_leave_pdf(
        mchk_reg_no="L09876",
        doctor_name="Dr. Wing-Yan Chan",
        clinic_name="Central Harbour GP Clinic",
        patient_name="Tai-Man Wong",
        hkid_masked="A123XXX(X)",
        encounter_id="enc_flu_002",
        start_date="2026-11-19",
        days_off=1,
    )
    assert pdf_no_diag.startswith(b"%PDF-")
    assert b"Withheld at patient request" in pdf_no_diag
    assert vhash2 != vhash

    # MCHK registration enforcement
    for bad_mchk in ("", "M123", "X12345", "12345", "INVALID"):
        with pytest.raises(ValueError, match="MCHK"):
            generate_sick_leave_pdf(
                mchk_reg_no=bad_mchk,
                doctor_name="Dr. Chan",
                clinic_name="Central Clinic",
                patient_name="Wong",
                hkid_masked="A123XXX(X)",
                encounter_id="enc_bad",
                start_date="2026-11-19",
                days_off=2,
            )

    # Sick-leave days_off bounds (1..7)
    for bad_days in (0, -1, 8, 14):
        with pytest.raises(ValueError, match="days_off"):
            generate_sick_leave_pdf(
                mchk_reg_no="M12345",
                doctor_name="Dr. Chan",
                clinic_name="Central Clinic",
                patient_name="Wong",
                hkid_masked="A123XXX(X)",
                encounter_id="enc_bad_days",
                start_date="2026-11-19",
                days_off=bad_days,
            )

    # Mismatched end_date vs days_off
    with pytest.raises(ValueError, match="does not match"):
        generate_sick_leave_pdf(
            mchk_reg_no="M12345",
            doctor_name="Dr. Chan",
            clinic_name="Central Clinic",
            patient_name="Wong",
            hkid_masked="A123XXX(X)",
            encounter_id="enc_bad_range",
            start_date="2026-11-19",
            end_date="2026-11-25",
            days_off=2,
        )


def test_issue_encrypted_certificate_in_document_vault(tmp_path: Path):
    vault = DocumentVault(
        backend=LocalFSBackend(tmp_path / "vault"),
        kms=LocalKMSProvider(kek_bytes=hashlib.sha256(b"unit-test-kek").digest()),
        signing_secret="unit-test-sign-secret",
    )
    issued = issue_encrypted_certificate(
        vault,
        "ten_hk_1",
        mchk_reg_no="M54321",
        doctor_name="Dr. Mei-Ling Lau",
        clinic_name="Causeway Bay Acute Clinic",
        patient_name="Siu-Ming Ho",
        hkid_masked="K888XXX(X)",
        encounter_id="enc_vault_01",
        start_date="2026-11-19",
        days_off=3,
        diagnosis_icd10="J10.1",
    )
    assert issued.vault_doc_id.startswith("doc_")
    assert issued.end_date == "2026-11-21"
    assert issued.diagnosis_icpc2 == "R80"

    # Confirm ciphertext at rest (no plaintext PDF header or patient name on disk)
    raw_disk = (tmp_path / "vault" / "ten_hk_1" / f"{issued.vault_doc_id}.vault.json").read_bytes()
    assert b"%PDF-" not in raw_disk
    assert b"Siu-Ming Ho" not in raw_disk

    # Retrieve and decrypt under correct tenant
    decrypted_pdf, meta = vault.retrieve("ten_hk_1", issued.vault_doc_id)
    assert decrypted_pdf.startswith(b"%PDF-")
    assert b"Siu-Ming Ho" in decrypted_pdf
    assert b"M54321" in decrypted_pdf
    assert meta["sha256"] == issued.pdf_sha256

    # Signed token verification & cross-tenant rejection
    token = vault.mint_signed_token("ten_hk_1", issued.vault_doc_id, "pat_01", ttl_seconds=120)
    vault.verify_signed_token(token, "ten_hk_1", issued.vault_doc_id, "pat_01")
    with pytest.raises(VaultError):
        vault.verify_signed_token(token, "ten_hk_2", issued.vault_doc_id, "pat_01")
    with pytest.raises(VaultError):
        vault.retrieve("ten_hk_2", issued.vault_doc_id)


# ======================================================================================
# 3. WHATSAPP ZERO-LEAKAGE GUARD, TEMPLATES & META HMAC WEBHOOK
# ======================================================================================
def test_whatsapp_templates_leakage_guard_and_hmac():
    wa = WhatsAppDoorbellAdapter(token="", phone_id="")

    # All 7 built-in templates pass leakage guard in EN, zh-HK, and bilingual
    template_params = {
        "welcome_magic_link": {"clinic_name": "TMRW HK Clinic", "url": "https://portal.tmrw.hk/m/abc"},
        "otp_code": {"otp": "482913", "ttl_minutes": 10},
        "booking_confirmed": {"slot_label": "2026-11-19 10:00 HKT", "doctor_name": "Dr. Chan", "url": "https://portal.tmrw.hk/c/1"},
        "booking_reminder": {"slot_label": "2026-11-19 10:00 HKT", "doctor_name": "Dr. Chan", "url": "https://portal.tmrw.hk/c/1"},
        "document_ready": {"url": "https://portal.tmrw.hk/docs/1"},
        "rx_status": {"status": "out_for_delivery", "url": "https://portal.tmrw.hk/orders/1"},
        "followup_48h": {"patient_first_name": "Alex", "url": "https://portal.tmrw.hk/f/1"},
    }
    for tpl_id in WA_TEMPLATE_IDS:
        for lang in ("en", "zh-HK", "bilingual"):
            res = wa.send_template(
                phone="+85291234567",
                template_id=tpl_id,
                params=template_params[tpl_id],
                lang=lang,
                idempotency_key=f"wa-{tpl_id}-{lang}",
            )
            assert res.ok
            assert res.payload["leakage_passed"] is True

    count_before = len(wa.outbox)

    # Hard invariant: clinical English, Cantonese, HKID, or MCHK promo MUST be blocked & NOT sent
    forbidden_bodies = [
        "Your fever and cough look like influenza; take Paracetamol 500mg.",
        "您發燒同咳嗽，醫生已開特敏福處方藥物。",
        "Please reply with your HKID A123456(7) to confirm.",
        "Special 50% off discount for best doctor free consultation!",
    ]
    for bad in forbidden_bodies:
        with pytest.raises(WhatsAppLeakageError):
            wa.send_raw(phone="+85291234567", body=bad)

    # Leakage injected inside template parameter is also blocked
    with pytest.raises(WhatsAppLeakageError):
        wa.send_template(
            phone="+85291234567",
            template_id="rx_status",
            params={"status": "Amoxicillin 500mg prescription ready", "url": "https://portal.tmrw.hk"},
        )
    assert len(wa.outbox) == count_before  # Nothing leaked to outbox

    # Meta Cloud API webhook HMAC-SHA256 verification
    secret = "meta-app-webhook-secret-hk"
    raw_payload = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode("utf-8")
    good_sig = "sha256=" + hmac.new(secret.encode(), raw_payload, hashlib.sha256).hexdigest()
    assert wa.verify_webhook(raw_payload, good_sig, secret) is True
    assert wa.verify_webhook(raw_payload + b" ", good_sig, secret) is False
    assert wa.verify_webhook(raw_payload, good_sig, "wrong-secret") is False
    assert wa.verify_webhook(raw_payload, "", secret) is False


def test_whatsapp_live_meta_graph_dispatch(monkeypatch):
    captured = {}

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"messages": [{"id": "wamid.live.xyz999"}]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResp()

    monkeypatch.setattr("backend.acute.adapters.httpx.post", _fake_post)
    wa = WhatsAppDoorbellAdapter(token="test-meta-token", phone_id="1234567890")
    assert wa.live_enabled is True
    res = wa.send_template(
        phone="+85291234567",
        template_id="otp_code",
        params={"otp": "654321", "ttl_minutes": 5},
        lang="en",
    )
    assert res.ok
    assert res.external_ref == "wamid.live.xyz999"
    assert "graph.facebook.com/v20.0/1234567890/messages" in captured["url"]
    assert captured["json"]["to"] == "85291234567"


# ======================================================================================
# 4. FAULT-INJECTING ADAPTERS: BOOKING, PHARMACY, FLUVAX, CORPORATE BILLING
# ======================================================================================
def test_booking_adapter_fault_retry_and_idempotency():
    faults = FaultInjector()
    booking = BookingAdapter(fault_injector=faults)
    slots = booking.list_slots(practitioner_id="prac_01", practitioner_name="Dr. Chan", date_str="2026-11-20")
    assert len(slots) == 6
    assert all(s["available"] for s in slots)
    target_slot = slots[0]["slot_id"]

    # Inject 2 transient 503 failures then verify retry succeeds
    faults.inject_transient_failures("booking.book_slot", 2)
    for _ in range(2):
        with pytest.raises(AdapterError) as ei:
            booking.book_slot(
                patient_id="pat_1",
                practitioner_id="prac_01",
                slot_id=target_slot,
                idempotency_key="bk-idem-1",
            )
        assert ei.value.retryable is True

    res1 = booking.book_slot(
        patient_id="pat_1",
        practitioner_id="prac_01",
        slot_id=target_slot,
        idempotency_key="bk-idem-1",
    )
    assert res1.ok and res1.status == "confirmed"

    # Idempotent replay returns identical result without double-booking error
    res2 = booking.book_slot(
        patient_id="pat_1",
        practitioner_id="prac_01",
        slot_id=target_slot,
        idempotency_key="bk-idem-1",
    )
    assert res2.external_ref == res1.external_ref

    # Different idempotency key for same slot is rejected
    with pytest.raises(AdapterError) as ei2:
        booking.book_slot(
            patient_id="pat_2",
            practitioner_id="prac_01",
            slot_id=target_slot,
            idempotency_key="bk-idem-2",
        )
    assert ei2.value.code == "slot_unavailable"

    # Cancel frees the slot
    cancel_res = booking.cancel_booking(booking_ref=res1.external_ref)
    assert cancel_res.status == "cancelled"
    refreshed = booking.list_slots(practitioner_id="prac_01", date_str="2026-11-20")
    assert refreshed[0]["available"] is True


def test_pharmacy_adapter_delivery_pickup_transitions_and_faults():
    faults = FaultInjector()
    phx = PharmacyFulfilmentAdapter(fault_injector=faults)
    items = [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QID", "days": 3}]

    # Delivery path with transient fault + idempotency
    faults.inject_transient_failures("pharmacy.submit_order", 1)
    with pytest.raises(AdapterError):
        phx.submit_order(
            prescription_id="rx_1",
            patient_id="pat_1",
            mode="delivery",
            items=items,
            delivery_address="Suite 1201, IFC, Central, HK",
            idempotency_key="phx-rx-1",
        )
    sub = phx.submit_order(
        prescription_id="rx_1",
        patient_id="pat_1",
        mode="delivery",
        items=items,
        delivery_address="Suite 1201, IFC, Central, HK",
        idempotency_key="phx-rx-1",
    )
    assert sub.status == "sent_to_pharmacy"
    assert phx.submit_order(
        prescription_id="rx_1",
        patient_id="pat_1",
        mode="delivery",
        items=items,
        delivery_address="Suite 1201, IFC, Central, HK",
        idempotency_key="phx-rx-1",
    ).external_ref == sub.external_ref

    # Illegal skip directly to completed rejected
    with pytest.raises(AdapterError) as bad_jump:
        phx.advance_status(fulfilment_ref=sub.external_ref, new_status="completed")
    assert bad_jump.value.code == "invalid_status_transition"

    # Valid delivery transitions
    assert phx.advance_status(fulfilment_ref=sub.external_ref, new_status="preparing").status == "preparing"
    # Mode mismatch rejected (ready_for_pickup not allowed on delivery)
    with pytest.raises(AdapterError):
        phx.advance_status(fulfilment_ref=sub.external_ref, new_status="ready_for_pickup")
    assert phx.advance_status(fulfilment_ref=sub.external_ref, new_status="out_for_delivery").status == "out_for_delivery"
    done = phx.advance_status(fulfilment_ref=sub.external_ref, new_status="completed")
    assert done.status == "completed"
    assert len(done.payload["tracking_events"]) == 5

    # Pickup lifecycle
    pick = phx.submit_order(
        prescription_id="rx_2",
        patient_id="pat_2",
        mode="pickup",
        items=items,
        pickup_branch="Causeway Bay Dispensary",
        idempotency_key="phx-rx-2",
    )
    assert phx.advance_status(fulfilment_ref=pick.external_ref, new_status="preparing").status == "preparing"
    assert phx.advance_status(fulfilment_ref=pick.external_ref, new_status="ready_for_pickup").status == "ready_for_pickup"
    assert phx.advance_status(fulfilment_ref=pick.external_ref, new_status="completed").status == "completed"

    # Webhook signature check
    raw = b'{"event":"completed","ref":"phx_1"}'
    sig = hmac.new(b"phx-secret", raw, hashlib.sha256).hexdigest()
    assert phx.verify_webhook(raw, sig, "phx-secret") is True
    assert phx.verify_webhook(raw, "bad", "phx-secret") is False


def test_fluvax_and_corporate_billing_adapters():
    # FluVax CVX validation & booking
    vax = FluVaxAdapter()
    slots = vax.list_slots(district="Central", cvx_code="150", date_str="2026-11-21")
    assert len(slots) == 3
    assert slots[0]["cvx_code"] == "150"

    bk = vax.book_vaccination(
        patient_id="pat_1",
        slot_id=slots[0]["slot_id"],
        cvx_code="171",
        idempotency_key="vax-1",
    )
    assert bk.ok and bk.payload["cvx_code"] == "171"
    assert vax.book_vaccination(
        patient_id="pat_1",
        slot_id=slots[0]["slot_id"],
        cvx_code="171",
        idempotency_key="vax-1",
    ).external_ref == bk.external_ref

    with pytest.raises(AdapterError) as bad_cvx:
        vax.book_vaccination(patient_id="pat_2", slot_id=slots[1]["slot_id"], cvx_code="999")
    assert bad_cvx.value.code == "invalid_cvx_code"

    # Corporate Billing: aggregate-only, zero PII, k-anonymity suppression, spend cap
    billing = CorporateBillingAdapter()
    noisy_records = [
        # Even if caller passes raw records containing PII/diagnoses, corporate output MUST strip all PII
        {
            "campaign_code": "FLU-HK-2026",
            "service_type": "teleconsult",
            "status": "completed",
            "fulfilment_status": "completed",
            "department": "Engineering",
            "patient_name": f"Employee {i}",
            "hkid": f"A12345{i}(0)",
            "icd10_code": "J11.1",
        }
        for i in range(6)
    ] + [
        {
            "campaign_code": "VAX-SMALL",
            "service_type": "flu_vax",
            "status": "completed",
            "department": "Executive",
            "patient_name": f"Exec {i}",
            "diagnosis": "Influenza",
        }
        for i in range(2)
    ] + [
        {"campaign_code": "FLU-HK-2026", "service_type": "teleconsult", "status": "cancelled"}
    ]

    inv = billing.reconcile_invoice(
        corporate_id="corp_1",
        corporate_name="Pacific FinTech HK Ltd",
        billing_period="2026-11",
        records=noisy_records,
        spend_cap_hkd=2500.0,
        unit_fee_hkd=420.0,
        flu_vax_fee_hkd=280.0,
        k_anonymity_min=5,
        idempotency_key="inv-corp1-2026-11",
    )
    assert inv.ok
    p = inv.payload
    # 6 * 420 + 2 * 280 = 2520 + 560 = 3080 HKD; capped at 2500 HKD
    assert p["completed_teleconsults"] == 6
    assert p["completed_flu_vax"] == 2
    assert p["cancelled_count"] == 1
    assert p["subtotal_hkd"] == 3080.0
    assert p["billable_hkd"] == 2500.0
    assert p["over_cap_hkd"] == 580.0
    assert p["reconciliation_status"] == "cap_exceeded"

    # Verify k-anonymity suppression for small subgroups (< 5)
    camp_map = {c["campaign_code"]: c for c in p["campaign_breakdown"]}
    assert camp_map["FLU-HK-2026"]["suppressed"] is False
    assert camp_map["FLU-HK-2026"]["count"] == 6
    assert camp_map["VAX-SMALL"]["suppressed"] is True
    assert camp_map["VAX-SMALL"]["count"] == "<5"
    assert camp_map["VAX-SMALL"]["spend_hkd"] is None

    dept_map = {d["department"]: d for d in p["department_breakdown"]}
    assert dept_map["Engineering"]["suppressed"] is False
    assert dept_map["Executive"]["suppressed"] is True

    # Zero PII serialized anywhere in invoice JSON
    raw_json = json.dumps(p)
    assert "Employee 0" not in raw_json
    assert "A123450" not in raw_json
    assert "J11.1" not in raw_json
    assert "Exec 0" not in raw_json


# ======================================================================================
# 5. REAL POSTGRESQL 15 DURABLE OUTBOX: RESTART, RETRY/BACKOFF, DLQ, SLA & RLS
# ======================================================================================
@pytest.fixture(scope="module")
def pg_db() -> Database:
    db = Database()
    db.init_schema()
    return db


def _create_tenant(db: Database, prefix: str = "ten") -> str:
    tid = f"{prefix}_{uuid.uuid4().hex[:10]}"
    with db.admin_session() as s:
        s.add(Tenant(id=tid, name=f"Test Clinic {tid}"))
    return tid


def test_postgres_outbox_restart_durability_and_idempotency(pg_db: Database):
    tid = _create_tenant(pg_db, "ten_wf")
    t0 = datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)

    # Engine 1 schedules durable reminders & 48h follow-up (including duplicate enqueue)
    engine_before_restart = DurableWorkflowEngine(base_backoff_seconds=10)
    with pg_db.tenant_session(tid) as s:
        ev1 = engine_before_restart.schedule_booking_reminder(
            s,
            tid,
            encounter_id="enc_r1",
            patient_id="pat_r1",
            phone="+85291230001",
            doctor_name="Dr. Chan",
            slot_at=t0 + timedelta(hours=2),  # reminder due at t0 + 90m
            portal_url="https://portal.tmrw.hk/enc_r1",
            remind_before_minutes=30,
        )
        # Idempotent duplicate enqueue returns the exact same event row
        ev1_dup = engine_before_restart.schedule_booking_reminder(
            s,
            tid,
            encounter_id="enc_r1",
            patient_id="pat_r1",
            phone="+85291230001",
            doctor_name="Dr. Chan",
            slot_at=t0 + timedelta(hours=2),
            portal_url="https://portal.tmrw.hk/enc_r1",
            remind_before_minutes=30,
        )
        assert ev1.id == ev1_dup.id

        ev2 = engine_before_restart.schedule_followup_48h(
            s,
            tid,
            encounter_id="enc_r1",
            patient_id="pat_r1",
            phone="+85291230001",
            patient_first_name="Chris",
            completed_at=t0 + timedelta(hours=3),  # due at t0 + 51h
            portal_url="https://portal.tmrw.hk/f/enc_r1",
        )
        assert ev2.event_type == EVENT_FOLLOWUP_48H

    # Simulate full process restart: brand new DurableWorkflowEngine + WhatsApp adapter
    wa = WhatsAppDoorbellAdapter(token="", phone_id="")
    engine_after_restart = DurableWorkflowEngine(base_backoff_seconds=10)
    engine_after_restart.register_default_handlers(whatsapp_adapter=wa)

    # Before reminder due time -> 0 processed
    early = engine_after_restart.process_due(pg_db, tid, now=t0 + timedelta(minutes=60))
    assert early.processed == 0
    assert len(wa.outbox) == 0

    # At reminder due time (t0 + 95m) -> booking.reminder fires, followup_48h still pending
    mid = engine_after_restart.process_due(pg_db, tid, now=t0 + timedelta(minutes=95))
    assert mid.succeeded == 1
    assert len(wa.outbox) == 1
    assert wa.outbox[0]["template_id"] == "booking_reminder"

    # At t0 + 52h -> followup_48h fires
    late = engine_after_restart.process_due(pg_db, tid, now=t0 + timedelta(hours=52))
    assert late.succeeded == 1
    assert len(wa.outbox) == 2
    assert wa.outbox[1]["template_id"] == "followup_48h"

    # Verify DB state and immutable audit events
    with pg_db.tenant_session(tid) as s:
        events = list(s.execute(select(OutboxEvent)).scalars())
        assert len(events) == 2
        assert all(e.status == "done" for e in events)
        audits = list(s.execute(select(AuditEvent)).scalars())
        actions = {a.action for a in audits}
        assert "workflow.booking_reminder_sent" in actions
        assert "workflow.followup_48h_sent" in actions


def test_postgres_outbox_proposal_sla_escalation(pg_db: Database):
    tid = _create_tenant(pg_db, "ten_sla")
    now = utcnow()
    engine = DurableWorkflowEngine()
    engine.register_default_handlers()

    with pg_db.tenant_session(tid) as s:
        pat = Patient(
            id=new_id("pat"),
            tenant_id=tid,
            phone_hash=hashlib.sha256(b"85291112222").hexdigest(),
            phone_masked="****2222",
            name_en="Grace Ho",
            dob="1990-05-10",
            referral_code=f"REF-{uuid.uuid4().hex[:8]}",
        )
        s.add(pat)
        s.flush()
        enc = Encounter(id=new_id("enc"), tenant_id=tid, patient_id=pat.id, status="intake")
        s.add(enc)
        s.flush()

        p_pending = Proposal(
            id=new_id("prop"),
            tenant_id=tid,
            patient_id=pat.id,
            encounter_id=enc.id,
            kind="acute_intake",
            urgency=2,
            sla_due_at=now + timedelta(minutes=15),
            status="pending_review",
            protocol_id="acute_flu_intake",
            protocol_version="1.0.0",
        )
        p_reviewed = Proposal(
            id=new_id("prop"),
            tenant_id=tid,
            patient_id=pat.id,
            encounter_id=enc.id,
            kind="acute_intake",
            urgency=2,
            sla_due_at=now + timedelta(minutes=15),
            status="approved",
            protocol_id="acute_flu_intake",
            protocol_version="1.0.0",
        )
        s.add_all([p_pending, p_reviewed])
        s.flush()

        engine.schedule_proposal_sla_escalation(
            s, tid, proposal_id=p_pending.id, encounter_id=enc.id, urgency=2, sla_due_at=p_pending.sla_due_at
        )
        engine.schedule_proposal_sla_escalation(
            s, tid, proposal_id=p_reviewed.id, encounter_id=enc.id, urgency=2, sla_due_at=p_reviewed.sla_due_at
        )
        pending_id, reviewed_id = p_pending.id, p_reviewed.id

    res = engine.process_due(pg_db, tid, now=now + timedelta(minutes=20))
    assert res.succeeded == 2

    with pg_db.tenant_session(tid) as s:
        p1 = s.execute(select(Proposal).where(Proposal.id == pending_id)).scalar_one()
        p2 = s.execute(select(Proposal).where(Proposal.id == reviewed_id)).scalar_one()
        assert p1.status == "escalated"
        assert p1.urgency == 4
        assert p2.status == "approved"  # Already reviewed -> untouched!


def test_postgres_outbox_retry_backoff_dlq_and_rls_isolation(pg_db: Database):
    tid_a = _create_tenant(pg_db, "ten_dlq_a")
    tid_b = _create_tenant(pg_db, "ten_dlq_b")
    t0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    engine = DurableWorkflowEngine(base_backoff_seconds=30)
    flaky_calls = {"n": 0}

    def _flaky_handler(session, event):
        flaky_calls["n"] += 1
        if flaky_calls["n"] < 3:
            raise AdapterError("Simulated 503 upstream", retryable=True)

    def _always_fail_handler(session, event):
        raise RuntimeError("Permanent upstream timeout")

    def _non_retryable_handler(session, event):
        raise AdapterError("Bad payload 400", retryable=False, code="bad_request")

    engine.register_handler("test.flaky", _flaky_handler)
    engine.register_handler("test.always_fail", _always_fail_handler)
    engine.register_handler("test.non_retryable", _non_retryable_handler)

    with pg_db.tenant_session(tid_a) as s:
        ev_flaky = engine.enqueue(
            s, tid_a, "test.flaky", "agg_1", {"k": 1}, run_after=t0, idempotency_key=f"flaky:{tid_a}", max_attempts=3
        )
        ev_fail = engine.enqueue(
            s, tid_a, "test.always_fail", "agg_2", {"k": 2}, run_after=t0, idempotency_key=f"fail:{tid_a}", max_attempts=3
        )
        ev_nonret = engine.enqueue(
            s, tid_a, "test.non_retryable", "agg_3", {"k": 3}, run_after=t0, idempotency_key=f"nonret:{tid_a}", max_attempts=3
        )
        flaky_id, fail_id, nonret_id = ev_flaky.id, ev_fail.id, ev_nonret.id

    # RLS isolation check: Tenant B sees and processes 0 events
    b_res = engine.process_due(pg_db, tid_b, now=t0 + timedelta(hours=1))
    assert b_res.processed == 0

    # Attempt 1 at t0: flaky & always_fail retry (+30s backoff), non_retryable goes straight to DLQ
    r1 = engine.process_due(pg_db, tid_a, now=t0)
    assert r1.processed == 3
    assert r1.retried == 2
    assert r1.dlq == 1

    # At t0 + 10s (before 30s backoff expires): nothing due
    assert engine.process_due(pg_db, tid_a, now=t0 + timedelta(seconds=10)).processed == 0

    # Attempt 2 at t0 + 35s: flaky & always_fail fail again (+60s exponential backoff)
    r2 = engine.process_due(pg_db, tid_a, now=t0 + timedelta(seconds=35))
    assert r2.retried == 2

    # Attempt 3 at t0 + 100s: flaky succeeds -> done; always_fail hits max_attempts=3 -> dlq
    r3 = engine.process_due(pg_db, tid_a, now=t0 + timedelta(seconds=100))
    assert r3.succeeded == 1
    assert r3.dlq == 1

    with pg_db.tenant_session(tid_a) as s:
        e_flaky = s.execute(select(OutboxEvent).where(OutboxEvent.id == flaky_id)).scalar_one()
        e_fail = s.execute(select(OutboxEvent).where(OutboxEvent.id == fail_id)).scalar_one()
        e_nonret = s.execute(select(OutboxEvent).where(OutboxEvent.id == nonret_id)).scalar_one()
        assert e_flaky.status == "done" and e_flaky.attempts == 3 and e_flaky.last_error is None
        assert e_fail.status == "dlq" and e_fail.attempts == 3 and "Permanent upstream timeout" in (e_fail.last_error or "")
        assert e_nonret.status == "dlq" and e_nonret.attempts == 1 and "Bad payload 400" in (e_nonret.last_error or "")
