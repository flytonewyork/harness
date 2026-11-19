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
        s.add_all([doc1, doc2, admin1])
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
            metadata={"synthetic_only": True, "patients": len(synthetic_patients)},
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
