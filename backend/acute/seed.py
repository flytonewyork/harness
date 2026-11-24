"""Idempotent synthetic Hong Kong Flu-Season Acute Care demo seeder (ZERO real PHI).

Seeds two Hong Kong clinics, two corporate accounts (one above k-anonymity threshold >=5 and one
below <5 to demonstrate live k-anonymity suppression), HK MCHK-registered doctors, clinic ops admins,
corporate admins, git-versioned YAML protocols, and realistic synthetic Cantonese/English patient journeys.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from ..gateway import load_protocol
from ..vault import DocumentVault
from .certificates import issue_encrypted_certificate
from .db import (
    Campaign,
    Certificate,
    Consent,
    Corporate,
    Database,
    Encounter,
    Immunization,
    Message,
    Observation,
    Patient,
    Practitioner,
    Prescription,
    Proposal,
    ProtocolVersion,
    ReviewDecision,
    Tenant,
    WhatsAppOutbox,
    new_id,
    record_audit,
    utcnow,
)
from .rx_rules import check_prescription_safety
from .security import HKT, hash_secret, mask_hkid, mask_phone, sha256_hex
from .triage import AcuteHarnessRuntime

DEMO_TENANT_1 = "ten_hk_central"
DEMO_TENANT_2 = "ten_hk_kowloon"
DEMO_CORP_1 = "corp_harbour_fin"
DEMO_CORP_2 = "corp_pacific_bio"
DEMO_CAMP_1 = "HARBOUR-FLU-2026"
DEMO_CAMP_2 = "PACIFIC-FLU-2026"
DEMO_PASSWORD = "DemoClinic!2026"


def reset_and_seed(db: Database, vault: DocumentVault, runtime: AcuteHarnessRuntime | None = None) -> dict[str, Any]:
    """Wipe demo rows (admin bypass of triggers only for clean demo reset) and seed a fresh synthetic world."""
    db.init_schema()
    with db.admin_session() as s:
        # Temporarily disable immutability triggers ONLY during synthetic demo reset (never in prod)
        s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER trg_immutable_audit_events"))
        s.execute(text("ALTER TABLE consents DISABLE TRIGGER trg_immutable_consents"))
        s.execute(text("""
            TRUNCATE TABLE
              audit_events, outbox_events, protocol_versions, data_requests, whatsapp_outbox,
              messages, immunizations, prescriptions, certificates, review_decisions,
              proposals, agent_runs, observations, encounters, consents, otp_challenges,
              magic_links, patients, practitioners, campaigns, corporates, tenants
            CASCADE
        """))
        s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER trg_immutable_audit_events"))
        s.execute(text("ALTER TABLE consents ENABLE TRIGGER trg_immutable_consents"))
    return seed_demo_data(db, vault, runtime=runtime)


def seed_demo_data(db: Database, vault: DocumentVault, runtime: AcuteHarnessRuntime | None = None) -> dict[str, Any]:
    """Idempotently populate synthetic HK flu-season demo data if tenants are not yet seeded."""
    db.init_schema()
    with db.admin_session() as s:
        existing = s.get(Tenant, DEMO_TENANT_1)
        if existing is not None:
            return {"seeded": False, "tenant_id": DEMO_TENANT_1}

        s.add(Tenant(
            id=DEMO_TENANT_1,
            name="Central & Mid-Levels Family Medical (HK)",
            region="asia-east2",
            allowed_regions=["asia-east2"],
            allow_cross_border_inference=False,
            ai_enabled=True,
            dpo_contact="dpo@central-gp.tmrw.hk",
            config_json={"clinic_hours_hkt": "08:30-20:00 Mon-Sat", "emergency_line": "999"},
        ))
        s.add(Tenant(
            id=DEMO_TENANT_2,
            name="Kowloon East Corporate Care Clinic (HK)",
            region="asia-east2",
            allowed_regions=["asia-east2"],
            allow_cross_border_inference=False,
            ai_enabled=True,
            dpo_contact="dpo@kowloon-gp.tmrw.hk",
            config_json={"clinic_hours_hkt": "09:00-19:00 Mon-Sat", "emergency_line": "999"},
        ))

    rt = runtime or AcuteHarnessRuntime(enable_model_screen=False)
    pw_hash = hash_secret(DEMO_PASSWORD)
    now = utcnow()
    today_hkt = now.astimezone(HKT).date()

    # Seed Tenant 1 (Primary Demo Clinic)
    with db.tenant_session(DEMO_TENANT_1) as s:
        corp1 = Corporate(
            id=DEMO_CORP_1,
            tenant_id=DEMO_TENANT_1,
            name="Harbour FinTech Holdings Ltd",
            spend_cap_hkd=80000.0,
            spent_hkd=2940.0,
            billing_email="benefits@harbour-fintech.example.hk",
            admin_email="hr@harbour-fintech.example.hk",
            admin_password_hash=pw_hash,
            active=True,
        )
        corp2 = Corporate(
            id=DEMO_CORP_2,
            tenant_id=DEMO_TENANT_1,
            name="Pacific BioLabs Hong Kong",
            spend_cap_hkd=25000.0,
            spent_hkd=840.0,
            billing_email="ops@pacific-biolabs.example.hk",
            admin_email="hr@pacific-biolabs.example.hk",
            admin_password_hash=pw_hash,
            active=True,
        )
        s.add_all([corp1, corp2])
        s.flush()

        camp1 = Campaign(
            id="camp_harbour_flu",
            tenant_id=DEMO_TENANT_1,
            corporate_id=DEMO_CORP_1,
            code=DEMO_CAMP_1,
            name="Harbour FinTech Winter Flu Shield 2026",
            spend_cap_hkd=50000.0,
            spent_hkd=2940.0,
            unit_fee_hkd=420.0,
            active=True,
        )
        camp2 = Campaign(
            id="camp_pacific_flu",
            tenant_id=DEMO_TENANT_1,
            corporate_id=DEMO_CORP_2,
            code=DEMO_CAMP_2,
            name="Pacific BioLabs Flu Care 2026 (Small Team)",
            spend_cap_hkd=15000.0,
            spent_hkd=840.0,
            unit_fee_hkd=420.0,
            active=True,
        )
        s.add_all([camp1, camp2])

        doc1 = Practitioner(
            id="doc_dr_lam",
            tenant_id=DEMO_TENANT_1,
            email="dr.lam@central-gp.tmrw.hk",
            password_hash=pw_hash,
            name_en="Dr. Kenneth Lam",
            name_zh="林啟賢醫生",
            role="clinician",
            mchk_reg_no="M14829",
            clinic_name="Central & Mid-Levels Family Medical",
            availability_json=[
                {"day": "Mon-Fri", "slots": ["09:00", "09:30", "10:00", "11:00", "14:00", "15:00", "16:30"]},
                {"day": "Sat", "slots": ["09:30", "10:30", "11:30"]},
            ],
        )
        doc2 = Practitioner(
            id="doc_dr_cheung",
            tenant_id=DEMO_TENANT_1,
            email="dr.cheung@central-gp.tmrw.hk",
            password_hash=pw_hash,
            name_en="Dr. Wing-Yan Cheung",
            name_zh="張詠欣醫生",
            role="clinician",
            mchk_reg_no="M18204",
            clinic_name="Central & Mid-Levels Family Medical",
            availability_json=[
                {"day": "Mon-Sat", "slots": ["10:00", "11:30", "14:30", "16:00", "18:00"]},
            ],
        )
        nurse1 = Practitioner(
            id="nurse_chan",
            tenant_id=DEMO_TENANT_1,
            email="nurse.chan@central-gp.tmrw.hk",
            password_hash=pw_hash,
            name_en="Nurse Rachel Chan (RN)",
            name_zh="陳嘉敏註冊護士",
            role="nurse",
            mchk_reg_no=None,
            clinic_name="Central & Mid-Levels Family Medical",
        )
        admin1 = Practitioner(
            id="adm_central_ops",
            tenant_id=DEMO_TENANT_1,
            email="ops@central-gp.tmrw.hk",
            password_hash=pw_hash,
            name_en="Grace Ho (Clinic Ops & Medical Director Delegate)",
            name_zh="何嘉恩",
            role="clinic_admin",
            mchk_reg_no=None,
            clinic_name="Central & Mid-Levels Family Medical",
        )
        s.add_all([doc1, doc2, nurse1, admin1])
        s.flush()

        # Load git-versioned YAML protocols into ProtocolVersion table
        for pid in ("flu_intake_v1", "acute_consult_draft_v1", "followup_48h_v1"):
            bundle = load_protocol(pid)
            s.add(ProtocolVersion(
                id=new_id("prv"),
                tenant_id=DEMO_TENANT_1,
                protocol_id=bundle.protocol_id,
                version=bundle.version,
                content_hash=bundle.protocol_hash,
                yaml_content=bundle.raw_yaml,
                autonomy_level=bundle.autonomy_level,
                status="approved",
                approved_by="doc_dr_lam",
                approved_at=now - timedelta(days=3),
            ))

        # Seed 7 synthetic patients under Harbour FinTech (>=5 so corporate aggregates show exact counts)
        # Plus 2 under Pacific BioLabs (<5 so corporate dashboard demonstrates k-anonymity "<5" suppression!)
        synthetic_patients = [
            {
                "id": "pat_chan_urgent",
                "corp": DEMO_CORP_1, "camp": "camp_harbour_flu", "phone": "+85291000001",
                "name_en": "CHAN Ka-Ho", "name_zh": "陳家豪", "dob": "1983-05-14", "sex": "M",
                "hkid": "Y482193(4)", "lang": "zh", "g6pd": False,
                "allergies": ["Penicillin"], "meds": ["Warfarin 3mg daily"], "weight_kg": 72.0, "egfr": 82.0,
                "symptom": "發燒兩日39.2度，頭先開始心口好翳同埋唞唔到氣，講唔到成句。",
                "temp_c": 39.2, "spo2": 91.0, "status": "red_flag_escalated", "completed": False,
            },
            {
                "id": "pat_wong_flu_g6pd",
                "corp": DEMO_CORP_1, "camp": "camp_harbour_flu", "phone": "+85291000002",
                "name_en": "WONG Siu-Mei", "name_zh": "黃小美", "dob": "1991-11-03", "sex": "F",
                "hkid": "K773120(2)", "lang": "en", "g6pd": True,
                "allergies": ["Penicillin"], "meds": [], "weight_kg": 56.0, "egfr": 95.0,
                "symptom": "Started yesterday evening with fever 38.6C, body aches, dry cough and sore throat. Flu A RAT positive this morning. Need doctor consult and sick leave note.",
                "temp_c": 38.6, "spo2": 98.0, "status": "booked", "completed": False,
            },
            {
                "id": "pat_leung_completed",
                "corp": DEMO_CORP_1, "camp": "camp_harbour_flu", "phone": "+85291000003",
                "name_en": "LEUNG Chi-Wai", "name_zh": "梁志偉", "dob": "1988-02-19", "sex": "M",
                "hkid": "C519822(1)", "lang": "en", "g6pd": False,
                "allergies": [], "meds": [], "weight_kg": 70.0, "egfr": 92.0,
                "symptom": "Fever 38.4C, runny nose, mild sore throat for 2 days. RAT Flu A positive.",
                "temp_c": 38.4, "spo2": 99.0, "status": "completed", "completed": True,
            },
        ]
        # 4 more completed Harbour visits so Harbour has 7 encounters, 5 certs, 5 vaccines (all >=5)
        for i in range(4, 8):
            synthetic_patients.append({
                "id": f"pat_harbour_{i}",
                "corp": DEMO_CORP_1, "camp": "camp_harbour_flu", "phone": f"+8529100000{i}",
                "name_en": f"HO Wing-{i}", "name_zh": f"何詠{i}", "dob": f"198{i}-06-1{i}", "sex": "F" if i % 2 == 0 else "M",
                "hkid": f"D30{i}441(8)", "lang": "en", "g6pd": False,
                "allergies": [], "meds": [], "weight_kg": 64.0, "egfr": 94.0,
                "symptom": f"Mild fever 38.{i-3}C, sore throat and cough for 1 day.",
                "temp_c": 38.0 + (i - 3) * 0.1, "spo2": 99.0, "status": "completed", "completed": True,
            })
        # 2 patients under Pacific BioLabs (<5 -> suppressed as "<5" in corporate dashboard!)
        for i in range(1, 3):
            synthetic_patients.append({
                "id": f"pat_pacific_{i}",
                "corp": DEMO_CORP_2, "camp": "camp_pacific_flu", "phone": f"+8529200000{i}",
                "name_en": f"TAM Lok-{i}", "name_zh": f"譚樂{i}", "dob": f"199{i}-03-1{i}", "sex": "M",
                "hkid": f"R60{i}912(3)", "lang": "zh", "g6pd": False,
                "allergies": [], "meds": [], "weight_kg": 68.0, "egfr": 98.0,
                "symptom": "喉嚨痛同流鼻水兩日，微燒37.9度。",
                "temp_c": 37.9, "spo2": 99.0, "status": "completed", "completed": True,
            })

        for idx, sp in enumerate(synthetic_patients):
            pat = Patient(
                id=sp["id"],
                tenant_id=DEMO_TENANT_1,
                corporate_id=sp["corp"],
                campaign_id=sp["camp"],
                phone_hash=sha256_hex(sp["phone"]),
                phone_masked=mask_phone(sp["phone"]),
                name_en=sp["name_en"],
                name_zh=sp["name_zh"],
                dob=sp["dob"],
                sex=sp["sex"],
                hkid_masked=mask_hkid(sp["hkid"]),
                ehealth_consent=True,
                preferred_lang=sp["lang"],
                g6pd_deficient=sp["g6pd"],
                weight_kg=sp["weight_kg"],
                egfr=sp["egfr"],
                allergies_json=sp["allergies"],
                current_meds_json=sp["meds"],
                referral_code=f"REF-{sp['id'][-6:].upper()}",
                status="active",
            )
            s.add(pat)
            s.flush()

            for scope in ("clinical_care", "ai_assisted_intake", "ehealth_sharing"):
                s.add(Consent(
                    id=new_id("con"),
                    tenant_id=DEMO_TENANT_1,
                    patient_id=pat.id,
                    scope=scope,
                    pics_version="HK-PICS-v1.0",
                    granted=True,
                    channel="pwa",
                    wording_hash=sha256_hex(f"HK-PICS-v1.0:{scope}"),
                ))

            res = rt.process_intake(
                sp["symptom"],
                vitals={"temp_c": sp["temp_c"], "spo2": sp["spo2"]},
                patient={
                    "id": pat.id, "name_en": pat.name_en, "dob": pat.dob,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "g6pd_deficient": pat.g6pd_deficient, "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                },
                lang=sp["lang"],
                tenant_id=DEMO_TENANT_1,
            )
            triage = res["triage"]
            intake = res["intake"]
            draft = res["draft"]

            note_text = (
                "\n".join(f"{k.upper()}: {v}" for k, v in draft.clinical_note.items())
                if draft and isinstance(draft.clinical_note, dict)
                else "RED FLAG ESCALATED: Immediate 999/A&E advised."
            )
            enc = Encounter(
                id=f"enc_{sp['id']}",
                tenant_id=DEMO_TENANT_1,
                patient_id=pat.id,
                practitioner_id="doc_dr_lam" if idx % 2 == 0 else "doc_dr_cheung",
                campaign_id=sp["camp"],
                encounter_type="acute_flu_teleconsult",
                status=sp["status"],
                scheduled_at=now + timedelta(minutes=30 * (idx + 1)) if not sp["completed"] else now - timedelta(hours=6),
                chief_complaint_text=sp["symptom"],
                structured_intake_json=intake.to_dict() if intake else {"triage": triage.to_dict()},
                icd10_code=draft.working_diagnosis["icd10"] if draft else "R50.9",
                icpc2_code=draft.working_diagnosis["icpc2"] if draft else "A03",
                snomed_codes_json=intake.snomed_codes if intake else ["267036007", "29857009"],
                clinical_note=note_text,
            )
            s.add(enc)
            s.flush()

            s.add(Observation(
                id=new_id("obs"),
                tenant_id=DEMO_TENANT_1,
                patient_id=pat.id,
                encounter_id=enc.id,
                loinc_code="8310-5",
                display="Body temperature",
                value_num=sp["temp_c"],
                ucum_unit="Cel",
                source_quote=sp["symptom"][:120],
            ))
            s.add(Observation(
                id=new_id("obs"),
                tenant_id=DEMO_TENANT_1,
                patient_id=pat.id,
                encounter_id=enc.id,
                loinc_code="2708-6",
                display="Oxygen saturation in Arterial blood by Pulse oximetry",
                value_num=sp["spo2"],
                ucum_unit="%",
                source_quote=f"SpO2 {sp['spo2']}%",
            ))

            urgency = triage.urgency
            sla_mins = {4: 15, 3: 60, 2: 240, 1: 720}.get(urgency, 240)
            prop = Proposal(
                id=f"prop_{sp['id']}",
                tenant_id=DEMO_TENANT_1,
                patient_id=pat.id,
                encounter_id=enc.id,
                kind="red_flag_alert" if triage.is_emergency else "consult_draft",
                urgency=urgency,
                sla_due_at=now + timedelta(minutes=sla_mins),
                status="approved" if sp["completed"] else "pending_review",
                protocol_id="flu_intake_v1" if triage.is_emergency else "acute_consult_draft_v1",
                protocol_version="1.0.0",
                payload_json={
                    "triage": triage.to_dict(),
                    "intake": intake.to_dict() if intake else None,
                    "draft": draft.to_dict() if draft else None,
                },
                source_quotes_json=list(dict.fromkeys((triage.evidence_quotes or []) + (intake.evidence_quotes if intake else []) + [sp["symptom"][:140]])),
                checks_json=[
                    {"id": "quotes", "status": "pass", "detail": "All claims verified word-for-word against patient input"},
                    {"id": "terminology", "status": "pass", "detail": "SNOMED CT / LOINC+UCUM / ICD-10+ICPC-2 validated"},
                    {"id": "authority", "status": "pass", "detail": "L1 draft-only; requires HK MCHK clinician sign-off"},
                ],
            )
            s.add(prop)
            s.flush()

            s.add(Message(
                id=new_id("msg"),
                tenant_id=DEMO_TENANT_1,
                patient_id=pat.id,
                encounter_id=enc.id,
                direction="in",
                sender_role="patient",
                body_text=sp["symptom"],
            ))
            if triage.is_emergency and triage.banner:
                s.add(Message(
                    id=new_id("msg"),
                    tenant_id=DEMO_TENANT_1,
                    patient_id=pat.id,
                    encounter_id=enc.id,
                    direction="out",
                    sender_role="system_template",
                    body_text=triage.banner,
                    template_id="emergency_999_fixed",
                ))

            # For completed visits, seed real doctor-signed ReviewDecision, encrypted Vault PDF Cert, Rx & Flu Vax
            if sp["completed"]:
                rd = ReviewDecision(
                    id=new_id("rev"),
                    tenant_id=DEMO_TENANT_1,
                    proposal_id=prop.id,
                    practitioner_id="doc_dr_lam",
                    decision="approved",
                    edit_diff_json={},
                    review_seconds=42.5,
                    rubber_stamp_confirmed=True,
                    note="Assessed via live video teleconsult; confirmed acute viral URTI/influenza.",
                )
                s.add(rd)
                s.flush()

                start_s = today_hkt.isoformat()
                end_s = (today_hkt + timedelta(days=1)).isoformat()
                issued = issue_encrypted_certificate(
                    vault=vault,
                    tenant_id=DEMO_TENANT_1,
                    mchk_reg_no="M14829",
                    doctor_name="Dr. Kenneth Lam",
                    clinic_name="Central & Mid-Levels Family Medical",
                    patient_name=pat.name_en,
                    hkid_masked=pat.hkid_masked,
                    encounter_id=enc.id,
                    start_date=start_s,
                    days_off=2,
                    diagnosis_icd10="J11.1",
                    diagnosis_icpc2="R80",
                )
                s.add(Certificate(
                    id=f"cert_{sp['id']}",
                    tenant_id=DEMO_TENANT_1,
                    patient_id=pat.id,
                    encounter_id=enc.id,
                    practitioner_id="doc_dr_lam",
                    mchk_reg_no="M14829",
                    review_decision_id=rd.id,
                    days_off=2,
                    start_date=start_s,
                    end_date=end_s,
                    diagnosis_icd10="J11.1",
                    diagnosis_icpc2="R80",
                    vault_doc_id=issued.vault_doc_id,
                    verification_hash=issued.verification_hash,
                ))

                rx_items = [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QID", "days": 3}]
                safety = check_prescription_safety(
                    {"allergies": pat.allergies_json, "g6pd_deficient": pat.g6pd_deficient, "current_meds": pat.current_meds_json},
                    rx_items,
                )
                s.add(Prescription(
                    id=f"rx_{sp['id']}",
                    tenant_id=DEMO_TENANT_1,
                    patient_id=pat.id,
                    encounter_id=enc.id,
                    practitioner_id="doc_dr_lam",
                    mchk_reg_no="M14829",
                    review_decision_id=rd.id,
                    items_json=safety.enriched_items,
                    safety_checks_json=[f.to_dict() for f in safety.findings],
                    fulfilment_mode="delivery" if idx % 2 == 0 else "pickup",
                    fulfilment_status="out_for_delivery" if sp["id"] == "pat_leung_completed" else "completed",
                    fulfilment_ref=f"RXHK-{idx+1001}",
                    tracking_events_json=[
                        {"status": "signed", "at": (now - timedelta(hours=5)).isoformat(), "note": "Signed by Dr. Kenneth Lam (M14829)"},
                        {"status": "sent_to_pharmacy", "at": (now - timedelta(hours=4)).isoformat(), "note": "Sent to HK Central Partner Pharmacy"},
                        {"status": "out_for_delivery", "at": (now - timedelta(hours=2)).isoformat(), "note": "SF Cold-Chain Courier dispatched"},
                    ],
                ))

                s.add(Immunization(
                    id=new_id("imm"),
                    tenant_id=DEMO_TENANT_1,
                    patient_id=pat.id,
                    campaign_id=sp["camp"],
                    cvx_code="150",
                    vaccine_display="Influenza, injectable, quadrivalent, preservative free (IIV4)",
                    partner_clinic="Central & Mid-Levels Family Medical",
                    slot_at=now + timedelta(days=3),
                    status="booked",
                    booking_ref=f"VAX-{idx+5001}",
                ))

        # ------------------------------------------------------------------
        # Seed 3 Flagship Longitudinal Demo Stories (corporate_id=None so
        # corporate k-anonymity counts stay untouched)
        # ------------------------------------------------------------------
        # Story A: CHAN Tai Man (M 71) — QEH Discharge PDF with Warfarin DoseConflict + Duplicate Statin
        qeh_doc_text = (
            "QUEEN ELIZABETH HOSPITAL (HA SOPC) — DISCHARGE SUMMARY\n"
            "Patient: CHAN, Tai Man (陳大文)  |  DOB: 18/03/1955  |  HKID: D505XXX(X)\n"
            "Admission: 11 Nov 2026  |  Discharge: 14 Nov 2026  |  Ward: Medical E4\n\n"
            "Principal Diagnosis: Atrial fibrillation with supratherapeutic INR (3.8), minor epistaxis (resolved).\n\n"
            "Discharge Medications:\n"
            "1. Warfarin 3mg nocte (reduced from 5mg due to INR 3.8 on 12 Nov — recheck INR in 5 days).\n"
            "2. Atorvastatin 40mg nocte (NEW — switched from Simvastatin 20mg for secondary CV prevention).\n"
            "3. Bisoprolol 2.5mg OM (unchanged).\n\n"
            "GP Clinic Dispensing Record (Central & Mid-Levels Family Medical, 02 Oct 2026):\n"
            "- Dispensed Warfarin Sodium 5mg nocte x 28d\n"
            "- Active Repeat: Simvastatin 20mg nocte x 56d\n"
        )
        pat_qeh = Patient(
            id="pat_chan_qeh_discharge",
            tenant_id=DEMO_TENANT_1,
            corporate_id=None,
            campaign_id=None,
            phone_hash=sha256_hex("+85291000010"),
            phone_masked="****0010",
            name_en="CHAN Tai Man",
            name_zh="陳大文",
            dob="1955-03-18",
            sex="M",
            hkid_masked="D505XXX(X)",
            preferred_lang="zh-HK",
            g6pd_deficient=False,
            allergies_json=[],
            current_meds_json=["Warfarin 5mg nocte (Central GP, 02 Oct)", "Simvastatin 20mg nocte (GP active repeat)", "Bisoprolol 2.5mg OM"],
            weight_kg=69.0,
            egfr=68.0,
            ehealth_consent=True,
            referral_code="REF-QEH010",
            status="active",
        )
        s.add(pat_qeh)
        s.flush()
        enc_qeh = Encounter(
            id="enc_qeh_discharge",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_qeh.id,
            encounter_type="discharge_reconciliation",
            status="pending_review",
            chief_complaint_text=qeh_doc_text,
            icd10_code="I48.9",
            icpc2_code="K78",
            snomed_codes_json=[{"code": "49436004", "display": "Atrial fibrillation"}],
            clinical_note="QEH discharge 14 Nov: Warfarin reduced 5mg -> 3mg nocte (INR 3.8); Atorvastatin 40mg started (duplicate with GP Simvastatin 20mg).",
            created_at=now - timedelta(minutes=25),
        )
        s.add(enc_qeh)
        s.flush()
        s.add(Proposal(
            id="prop_qeh_discharge",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_qeh.id,
            encounter_id=enc_qeh.id,
            kind="discharge_reconciliation",
            urgency=3,
            status="pending_review",
            sla_due_at=now + timedelta(hours=2),
            protocol_id="acute_consult_draft_v1",
            protocol_version="1.0.0",
            payload_json={
                "story_tag": "qeh_discharge",
                "title": "QEH discharge, 14 Nov: settle 1 dose conflict before signing.",
                "summary_points": [
                    {"text": "Admitted QEH with supratherapeutic INR 3.8 and minor epistaxis; discharged 14 Nov.", "quote": "reduced from 5mg due to INR 3.8 on 12 Nov"},
                    {"text": "QEH started Atorvastatin 40mg nocte while our GP Simvastatin 20mg repeat is still active.", "quote": "Atorvastatin 40mg nocte (NEW — switched from Simvastatin 20mg"},
                ],
                "case_sites": ["Central GP", "QEH HA SOPC", "eHealth"],
                "conflicts": [
                    {
                        "id": "conf_warfarin_qeh",
                        "drug": "Warfarin",
                        "resolved": False,
                        "chosen_side": None,
                        "sides": [
                            {"key": "1", "site": "QEH HA SOPC", "date": "14 Nov", "regimen": "Warfarin 3 mg nocte", "quote": "Warfarin 3mg nocte (reduced from 5mg due to INR 3.8 on 12 Nov"},
                            {"key": "2", "site": "Central GP", "date": "02 Oct", "regimen": "Warfarin 5 mg nocte", "quote": "Dispensed Warfarin Sodium 5mg nocte x 28d"},
                        ],
                    }
                ],
                "extra_chart_meds": ["Atorvastatin 40mg nocte", "Simvastatin 20mg nocte"],
                "draft": {
                    "working_diagnosis": {"icd10": "I48.9", "icpc2": "K78", "display": "Atrial fibrillation — post-discharge medication reconciliation"},
                    "suggested_medications": [
                        {"drug": "atorvastatin", "display": "Continue Atorvastatin 40 mg nocte (QEH)", "dose_mg": 40, "frequency": "nocte", "days": 28, "is_non_acute_order": True},
                        {"drug": "simvastatin", "display": "Stop Simvastatin 20 mg repeat (duplicate statin)", "dose_mg": 20, "frequency": "STOP", "days": 0, "is_non_acute_order": True},
                        {"drug": "inr_blood", "display": "Order INR blood test in 5 days (19 Nov)", "is_non_acute_order": True},
                    ],
                    "patient_summary_draft": "陳先生你好，我已核對你 11 月 14 日伊利沙伯醫院出院摘要。請確認你今晚服用邊一款華法林劑量（醫院已減至每晚 3mg），並停止舊有嘅 Simvastatin（現改服醫院新開嘅 Atorvastatin 40mg）。診所已安排你 5 日後返嚟抽血覆檢 INR。",
                },
            },
            source_quotes_json=[
                {"field": "Warfarin reduction", "quote": "Warfarin 3mg nocte (reduced from 5mg due to INR 3.8 on 12 Nov", "source": "QEH Discharge 14 Nov"},
                {"field": "GP prior dispense", "quote": "Dispensed Warfarin Sodium 5mg nocte x 28d", "source": "Central GP Dispensing"},
                {"field": "New statin", "quote": "Atorvastatin 40mg nocte (NEW — switched from Simvastatin 20mg", "source": "QEH Discharge 14 Nov"},
            ],
            checks_json=[],
            created_at=now - timedelta(minutes=24),
        ))
        s.add(Message(
            id=new_id("msg"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_qeh.id,
            encounter_id=enc_qeh.id,
            direction="inbound",
            sender_role="patient",
            body_text="林醫生，我啱啱喺伊利沙伯醫院出院，上傳咗出院紙。請問今晚薄血丸同膽固醇藥應該點食？",
            created_at=now - timedelta(minutes=22),
        ))

        # Story B: HO Ka-Yan (F 58) — Chronic HTN 3-Week Home BP Drift (avg 152/94) + 365-Day Companion
        bp_log_text = (
            "7-DAY HOME BLOOD PRESSURE PROTOCOL LOG (Validated Omron HEM-7156 Upper-Arm Cuff)\n"
            "Patient: HO, Ka-Yan (何嘉欣)  |  DOB: 22/07/1968  |  HKID: E412XXX(X)\n"
            "Goal set by Dr. Kenneth Lam: < 135/85 mmHg  |  Current regimen: Amlodipine 5mg mane (dispensed here 01 Nov)\n\n"
            "24 Readings across 3 weeks — Average: 152/94 mmHg (morning avg 154/95, evening avg 150/93)\n"
            "- 19 Nov 07:45: 156/96 mmHg (monitor photo confirmed)\n"
            "- 18 Nov 20:10: 150/92 mmHg (monitor photo confirmed)\n"
            "- 17 Nov 07:50: 154/95 mmHg (monitor photo confirmed)\n"
            "- 16 Nov 20:05: 149/93 mmHg (monitor photo confirmed)\n"
            "- 14 Nov 08:00: 151/94 mmHg (monitor photo confirmed)\n\n"
            "Relevant Clinic Labs (03 May 2026): Creatinine 88 µmol/L (eGFR 78), Potassium 4.1 mmol/L, HbA1c 7.8%.\n"
        )
        pat_htn = Patient(
            id="pat_ho_htn_drift",
            tenant_id=DEMO_TENANT_1,
            corporate_id=None,
            campaign_id=None,
            phone_hash=sha256_hex("+85291000011"),
            phone_masked="****0011",
            name_en="HO Ka-Yan",
            name_zh="何嘉欣",
            dob="1968-07-22",
            sex="F",
            hkid_masked="E412XXX(X)",
            preferred_lang="en",
            g6pd_deficient=False,
            allergies_json=[],
            current_meds_json=["Amlodipine 5mg mane (dispensed here 01 Nov)"],
            weight_kg=62.0,
            egfr=78.0,
            ehealth_consent=True,
            referral_code="REF-HTN011",
            status="active",
        )
        s.add(pat_htn)
        s.flush()
        enc_htn = Encounter(
            id="enc_htn_drift",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_htn.id,
            encounter_type="chronic_bp_drift",
            status="pending_review",
            chief_complaint_text=bp_log_text,
            icd10_code="I10",
            icpc2_code="K86",
            snomed_codes_json=[{"code": "38341003", "display": "Essential hypertension"}],
            clinical_note="Home BP above goal (<135/85) for 3 weeks: avg 152/94 across 24 validated readings. Cr 88, K 4.1 (5m ago). Propose Amlodipine 5 -> 10mg mane.",
            created_at=now - timedelta(hours=1),
        )
        s.add(enc_htn)
        s.flush()
        s.add(Observation(
            id=new_id("obs"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_htn.id,
            encounter_id=enc_htn.id,
            loinc_code="85354-9",
            display="Home Blood Pressure (7-day average)",
            value_num=152.0,
            value_text="152/94 mmHg",
            ucum_unit="mm[Hg]",
            source_quote="Average: 152/94 mmHg",
        ))
        s.add(Proposal(
            id="prop_htn_bp_drift",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_htn.id,
            encounter_id=enc_htn.id,
            kind="chronic_bp_drift",
            urgency=2,
            status="pending_review",
            sla_due_at=now + timedelta(hours=6),
            protocol_id="followup_48h_v1",
            protocol_version="1.0.0",
            payload_json={
                "story_tag": "htn_drift",
                "title": "Home BP above goal for 3 weeks: avg 152/94 (24 readings).",
                "summary_points": [
                    {"text": "7-day validated home BP protocol shows sustained elevation above <135/85 goal.", "quote": "Average: 152/94 mmHg (morning avg 154/95, evening avg 150/93)"},
                    {"text": "Most recent reading 156/96 confirmed via monitor photo; no chest pain, headache, or visual symptoms.", "quote": "19 Nov 07:45: 156/96 mmHg (monitor photo confirmed)"},
                ],
                "case_sites": ["Central GP", "Home Monitor"],
                "labs_context": "Cr 88 µmol/L, K+ 4.1 mmol/L · 5 months ago",
                "bp_series": [
                    {"date": "29 Oct", "sys": 144, "dia": 89},
                    {"date": "01 Nov", "sys": 148, "dia": 91},
                    {"date": "04 Nov", "sys": 150, "dia": 92},
                    {"date": "07 Nov", "sys": 153, "dia": 95},
                    {"date": "10 Nov", "sys": 151, "dia": 93},
                    {"date": "13 Nov", "sys": 154, "dia": 95},
                    {"date": "16 Nov", "sys": 149, "dia": 93},
                    {"date": "17 Nov", "sys": 154, "dia": 95},
                    {"date": "18 Nov", "sys": 150, "dia": 92},
                    {"date": "19 Nov", "sys": 156, "dia": 96},
                ],
                "conflicts": [],
                "draft": {
                    "working_diagnosis": {"icd10": "I10", "icpc2": "K86", "display": "Essential hypertension — suboptimal home BP control"},
                    "suggested_medications": [
                        {"drug": "amlodipine", "display": "Amlodipine 5 → 10 mg mane (28 days)", "dose_mg": 10, "frequency": "OM", "days": 28, "is_non_acute_order": True},
                        {"drug": "home_bp_recheck", "display": "Repeat 7-day home BP log in 2 weeks", "is_non_acute_order": True},
                    ],
                    "patient_summary_draft": "Ms Ho, your 3-week home blood pressure averaged 152/94, above our <135/85 goal. I am increasing your Amlodipine from 5 mg to 10 mg each morning. Please watch for mild ankle swelling and send another 7-day BP log in 2 weeks.",
                },
            },
            source_quotes_json=[
                {"field": "3-week average", "quote": "Average: 152/94 mmHg (morning avg 154/95, evening avg 150/93)", "source": "Home BP Protocol"},
                {"field": "Latest monitor photo", "quote": "19 Nov 07:45: 156/96 mmHg (monitor photo confirmed)", "source": "Home BP Protocol"},
                {"field": "Renal & K+ baseline", "quote": "Creatinine 88 µmol/L (eGFR 78), Potassium 4.1 mmol/L, HbA1c 7.8%", "source": "PathLab 03 May"},
            ],
            checks_json=[],
            created_at=now - timedelta(minutes=55),
        ))
        s.add(Message(
            id=new_id("msg"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_htn.id,
            encounter_id=enc_htn.id,
            direction="outbound",
            sender_role="clinician",
            body_text="Your HbA1c was 7.8% on 3 May. Please recheck your HbA1c in 6 months — any weekday 8–10am at the clinic.",
            created_at=now - timedelta(days=14),
        ))
        s.add(Message(
            id=new_id("msg"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_htn.id,
            encounter_id=enc_htn.id,
            direction="inbound",
            sender_role="patient",
            body_text="Sent this morning's BP photo: 156/96. Feeling fine, no headache.",
            created_at=now - timedelta(minutes=56),
        ))

        # Story C Variant: CHEUNG Wing-Kit (M 70) — Acute Flu A <24h with Unknown eGFR (Renal Warning Demo)
        elderly_flu_text = (
            "Patient Intake (Portal, 09:12 HKT)\n"
            "Patient: CHEUNG, Wing-Kit (張永傑)  |  DOB: 12/04/1956 (70M)  |  HKID: A819XXX(X)\n\n"
            "「尋晚開始發燒 38.3°C，今朝快速測試甲型流感陽性，全身骨痛同少少乾咳。無胸口痛、無氣促。」\n"
            "Vitals: Temp 38.3°C · SpO2 97% · RAT Influenza A Positive · Onset < 24h · NKDA · No recent eGFR on file."
        )
        pat_elderly = Patient(
            id="pat_cheung_elderly_flu",
            tenant_id=DEMO_TENANT_1,
            corporate_id=None,
            campaign_id=None,
            phone_hash=sha256_hex("+85291000012"),
            phone_masked="****0012",
            name_en="CHEUNG Wing-Kit",
            name_zh="張永傑",
            dob="1956-04-12",
            sex="M",
            hkid_masked="A819XXX(X)",
            preferred_lang="zh-HK",
            g6pd_deficient=False,
            allergies_json=[],
            current_meds_json=[],
            weight_kg=66.0,
            egfr=None,
            ehealth_consent=True,
            referral_code="REF-FLU012",
            status="active",
        )
        s.add(pat_elderly)
        s.flush()
        enc_elderly = Encounter(
            id="enc_elderly_flu",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_elderly.id,
            encounter_type="acute_flu",
            status="pending_review",
            chief_complaint_text=elderly_flu_text,
            icd10_code="J10.1",
            icpc2_code="R80",
            snomed_codes_json=[{"code": "442696006", "display": "Influenza A"}],
            clinical_note="70M Influenza A RAT+, onset <24h, Temp 38.3C, SpO2 97%, no red flags. No recent eGFR on file — confirm Oseltamivir 75mg vs 30mg BD.",
            created_at=now - timedelta(minutes=18),
        )
        s.add(enc_elderly)
        s.flush()
        s.add(Proposal(
            id="prop_elderly_flu",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_elderly.id,
            encounter_id=enc_elderly.id,
            kind="consult_draft",
            urgency=2,
            status="pending_review",
            sla_due_at=now + timedelta(hours=1),
            protocol_id="acute_consult_draft_v1",
            protocol_version="1.0.0",
            payload_json={
                "story_tag": "elderly_flu",
                "title": "Influenza A, RAT positive, onset under 24 h (70M, eGFR unknown).",
                "summary_points": [
                    {"text": "Onset last night (<24h), within 48h antiviral window; Influenza A RAT positive.", "quote": "尋晚開始發燒 38.3°C，今朝快速測試甲型流感陽性"},
                    {"text": "No chest pain or dyspnoea; SpO2 97%.", "quote": "無胸口痛、無氣促"},
                ],
                "case_sites": ["Central GP"],
                "conflicts": [],
                "draft": {
                    "working_diagnosis": {"icd10": "J10.1", "icpc2": "R80", "display": "Influenza A with respiratory manifestations"},
                    "suggested_medications": [
                        {"drug": "oseltamivir", "display": "Oseltamivir 75 mg BD × 5 days", "dose_mg": 75, "frequency": "BD", "days": 5},
                        {"drug": "paracetamol", "display": "Paracetamol 500 mg QDS PRN × 3 days", "dose_mg": 500, "frequency": "QDS", "days": 3},
                    ],
                    "suggested_sick_leave": {"days_off": 2, "start_date": today_hkt.isoformat()},
                    "patient_summary_draft": "張先生你好，你甲型流感快速測試陽性且發病未滿 24 小時，適合盡早開始抗病毒藥特敏福療程。請多休息及補充水份；如出現氣促、胸悶或第 3 日仍持續高燒，請即時聯絡診所或前往急症室。",
                },
            },
            source_quotes_json=[
                {"field": "Onset & RAT", "quote": "尋晚開始發燒 38.3°C，今朝快速測試甲型流感陽性", "source": "Patient Portal"},
                {"field": "Red-flag negatives", "quote": "無胸口痛、無氣促", "source": "Patient Portal"},
            ],
            checks_json=[],
            created_at=now - timedelta(minutes=17),
        ))

        # Story D: LAM Wing-Keung (M 54) — EC Internal GI/Endoscopy Cross-Referral + Existing DOAC+NSAID Guardrail + Structured Summary
        gi_consult_text = (
            "EC HEALTHCARE GP CONSULTATION RECORD (Central Clinic, 10:05 HKT)\n"
            "Patient: LAM, Wing-Keung (林永強)  |  DOB: 09/06/1972 (54M)  |  HKID: C618XXX(X)\n"
            "Panel Coverage: Bupa HK Panel / AIA / Bowtie VHIS Flexi\n\n"
            "「醫生，我近六個星期食完嘢之後成日上腹痛同火燒心，persistent epigastric pain for 6 weeks，"
            "自己買過胃藥食都係唔掂。呢兩個月仲輕咗 3kg（lost ~3kg），冇黑便、冇嘔血。"
            "我爸爸60歲嗰陣有胃癌（father had stomach cancer at 60），想問可唔可以開啲強力消炎止痛藥同安排照胃鏡？」\n\n"
            "PMH: Paroxysmal AF (2024, Queen Mary Hospital) — on Apixaban 5mg BD.\n"
            "Allergies: Penicillin (urticaria).  |  G6PD: Normal.\n"
            "Vitals: BP 126/78 mmHg · HR 74/min · Temp 36.7°C · SpO2 99% · Abdomen: mild epigastric tenderness, no guarding."
        )
        pat_gi = Patient(
            id="pat_lam_gi_referral",
            tenant_id=DEMO_TENANT_1,
            corporate_id=None,
            campaign_id=None,
            phone_hash=sha256_hex("+85291000014"),
            phone_masked="****0014",
            name_en="LAM Wing-Keung",
            name_zh="林永強",
            dob="1972-06-09",
            sex="M",
            hkid_masked="C618XXX(X)",
            preferred_lang="zh-HK",
            g6pd_deficient=False,
            allergies_json=["Penicillin"],
            current_meds_json=["Apixaban 5mg BD (DOAC — paroxysmal AF)"],
            weight_kg=68.0,
            egfr=86.0,
            ehealth_consent=True,
            referral_code="REF-GI014",
            status="active",
        )
        s.add(pat_gi)
        s.flush()
        enc_gi = Encounter(
            id="enc_lam_gi_referral",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_gi.id,
            encounter_type="specialist_cross_referral",
            status="pending_review",
            chief_complaint_text=gi_consult_text,
            icd10_code="K30",
            icpc2_code="D07",
            snomed_codes_json=["79922009", "89362005", "162057007"],
            clinical_note=(
                "STRUCTURED SUMMARY (FHIR R4 / HK PANEL READY)\n"
                "Coding: ICD-10 K30 (Functional dyspepsia / persistent epigastric pain) | ICPC-2 D07 | "
                "SNOMED CT: 79922009 (Epigastric pain), 89362005 (Unintentional weight loss), 162057007 (Family history GI malignancy).\n\n"
                "PRE-DRAFTED EC HEALTHCARE INTERNAL REFERRAL:\n"
                "To: EC Healthcare Gastroenterology & Endoscopy Centre (Central / Mong Kok) — Priority: Urgent 14-day OGD + H. pylori + GI Specialist Consult.\n"
                "Triggers: 6-week persistent epigastric pain + ~3kg unintentional weight loss + first-degree FHx gastric cancer (father at 60).\n"
                "Periprocedural Alert: Active Apixaban 5mg BD (DOAC) — hold/bridge per BSG/ESGE endoscopy guideline; Penicillin allergy.\n\n"
                "HK INSURER CLAIM & PRE-AUTH SUMMARY (Bupa HK / AIA / Bowtie VHIS):\n"
                "Outpatient GP Consult (HK$480) + Day-Case OGD w/ Biopsy Pre-Auth (CPT 43239, Est. HK$14,800, Cashless VHIS Eligible) | "
                "Attending: Dr. Kenneth Lam (MCHK M14829)."
            ),
            created_at=now - timedelta(minutes=8),
        )
        s.add(enc_gi)
        s.flush()
        s.add(Observation(
            id=new_id("obs"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_gi.id,
            encounter_id=enc_gi.id,
            loinc_code="8310-5",
            display="Body temperature",
            value_num=36.7,
            value_text="36.7 Cel",
            ucum_unit="Cel",
            source_quote="Temp 36.7°C",
        ))
        s.add(Observation(
            id=new_id("obs"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_gi.id,
            encounter_id=enc_gi.id,
            loinc_code="8480-6",
            display="Systolic blood pressure",
            value_num=126.0,
            value_text="126/78 mmHg",
            ucum_unit="mm[Hg]",
            source_quote="BP 126/78 mmHg",
        ))
        s.add(Proposal(
            id="prop_lam_gi_referral",
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_gi.id,
            encounter_id=enc_gi.id,
            kind="ec_cross_referral",
            urgency=3,
            status="pending_review",
            sla_due_at=now + timedelta(hours=1),
            protocol_id="acute_consult_draft_v1",
            protocol_version="1.0.0",
            payload_json={
                "story_tag": "ec_gi_referral",
                "title": "Persistent epigastric pain (6w) + gastric Ca FHx: EC GI/OGD referral pre-drafted & DOAC+NSAID flagged.",
                "summary_points": [
                    {"text": "6 weeks persistent post-prandial epigastric pain refractory to OTC antacids.", "quote": "persistent epigastric pain for 6 weeks"},
                    {"text": "First-degree family history of gastric cancer (father at 60) + ~3kg unintentional weight loss — urgent EC GI & Endoscopy Centre (OGD) referral pre-drafted.", "quote": "father had stomach cancer at 60"},
                    {"text": "Patient requested strong anti-inflammatory analgesia while on Apixaban 5mg BD (DOAC) — live DDI guardrail flagged Ibuprofen pre-sign.", "quote": "on Apixaban 5mg BD"},
                ],
                "case_sites": ["EC Central GP", "EC Gastroenterology & Endoscopy Centre", "eHealth"],
                "conflicts": [],
                "draft": {
                    "working_diagnosis": {
                        "icd10": "K30",
                        "icpc2": "D07",
                        "display": "Functional dyspepsia / persistent epigastric pain (OGD evaluation)",
                    },
                    "suggested_medications": [
                        {"drug": "ec_gi_ogd_referral", "display": "Internal Referral: EC Healthcare Gastroenterology & Endoscopy Centre (Urgent 14d OGD + H. pylori; Apixaban hold notice)", "is_non_acute_order": True},
                        {"drug": "bupa_aia_bowtie_preauth", "display": "Structured Pre-Auth Packet: Bupa / AIA / Bowtie VHIS Day-Case OGD (ICD-10 K30 / ICPC-2 D07 / CPT 43239)", "is_non_acute_order": True},
                        {"drug": "ibuprofen", "display": "Patient-requested Ibuprofen 400 mg TDS (HOLD — Major DDI w/ Apixaban DOAC)", "dose_mg": 400, "frequency": "TDS", "days": 5},
                        {"drug": "paracetamol", "display": "Paracetamol 500 mg QDS PRN × 5 days (DOAC-safe analgesia)", "dose_mg": 500, "frequency": "QDS", "days": 5},
                    ],
                    "suggested_sick_leave": {"days_off": 1, "start_date": today_hkt.isoformat()},
                    "patient_summary_draft": (
                        "林先生你好，鑑於你持續上腹痛六星期、體重輕微下降及父親曾患胃癌，我已為你預備醫思健康腸胃肝臟科及內視鏡中心優先轉介信及自願醫保預先批核摘要，安排專科評估及胃鏡檢查（OGD）。"
                        "由於你正服用薄血藥 Apixaban，消炎止痛藥（Ibuprofen）會大幅增加胃出血風險，故不建議處方；現改開較安全的撲熱息痛作暫時止痛。請勿自行停服薄血藥，如出現黑便、嘔血或劇烈腹痛請即往急症室。"
                    ),
                },
            },
            source_quotes_json=[
                {"field": "Epigastric pain (6w)", "quote": "persistent epigastric pain for 6 weeks", "source": "EC GP Consult"},
                {"field": "Gastric Ca FHx", "quote": "father had stomach cancer at 60", "source": "EC GP Consult"},
                {"field": "Weight loss", "quote": "lost ~3kg", "source": "EC GP Consult"},
                {"field": "Active DOAC", "quote": "on Apixaban 5mg BD", "source": "EC GP Consult"},
            ],
            checks_json=[],
            created_at=now - timedelta(minutes=7),
        ))
        s.add(Message(
            id=new_id("msg"),
            tenant_id=DEMO_TENANT_1,
            patient_id=pat_gi.id,
            encounter_id=enc_gi.id,
            direction="inbound",
            sender_role="patient",
            body_text="林醫生，我上腹痛咗六個星期，爸爸以前有胃癌，想問需唔需要喺醫思健康安排照胃鏡？同埋我有保柏同 Bowtie 醫療保險。",
            created_at=now - timedelta(minutes=6),
        ))

        # Seed one clean non-clinical WhatsApp doorbell message for demo inspection
        s.add(WhatsAppOutbox(
            id=new_id("wao"),
            tenant_id=DEMO_TENANT_1,
            phone="+85291000003",
            conversation_id="wa_demo_leung",
            template_id="document_ready",
            body_text="A new clinic document is ready in your secure portal.",
            magic_link_url="/p",
            leakage_passed=True,
            status="sent",
        ))
        record_audit(
            s,
            tenant_id=DEMO_TENANT_1,
            actor_role="system",
            actor_id="seeder",
            action="demo.seeded",
            resource_type="Tenant",
            resource_id=DEMO_TENANT_1,
            metadata={"synthetic_only": True, "patients": len(synthetic_patients) + 4},
        )

    # Seed Tenant 2 with one clinician & one patient (for cross-tenant RLS verification & multi-clinic demo)
    with db.tenant_session(DEMO_TENANT_2) as s:
        s.add(Practitioner(
            id="doc_kowloon_wu",
            tenant_id=DEMO_TENANT_2,
            email="dr.wu@kowloon-gp.tmrw.hk",
            password_hash=pw_hash,
            name_en="Dr. Raymond Wu",
            name_zh="胡志強醫生",
            role="clinician",
            mchk_reg_no="M20411",
            clinic_name="Kowloon East Corporate Care Clinic",
        ))
        s.add(Patient(
            id="pat_kowloon_isolated",
            tenant_id=DEMO_TENANT_2,
            phone_hash=sha256_hex("+85298880001"),
            phone_masked="****0001",
            name_en="KWOK Mei-Ling",
            name_zh="郭美玲",
            dob="1979-09-10",
            sex="F",
            hkid_masked="G223XXX(X)",
            referral_code="REF-KWL001",
        ))

    return {"seeded": True, "tenant_id": DEMO_TENANT_1, "patients_seeded": len(synthetic_patients) + 1}
