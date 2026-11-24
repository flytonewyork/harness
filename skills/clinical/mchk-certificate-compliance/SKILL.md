---
name: mchk-certificate-compliance
version: "1.0.0"
autonomy_level: L1
authority_ceiling: DRAFT
priority: 75
max_tokens: 300
triggers:
  always_on: false
  intents: [sick_leave_request, intake, instruction_compile, workspace_compile]
  pathways:
    - ebm.pathway.acute_urti_g6pd
    - ebm.pathway.acute_uti
  keywords_en: [sick leave, medical certificate, MC, cert, doctor note, employer, days off]
  keywords_zh: [病假, 病假紙, 醫生紙, 假紙, 公司請假]
allowed_tools:
  - emr.get_patient_chart
  - harness.stage_staff_proposal
  - memory.upsert_fact
safety_floor_refs:
  - ebm.safety.doctor_only_authority
banned_claims:
  - sick leave has been issued
  - sick leave is approved
  - certificate issued
  - 已發出病假
  - 已批出病假
  - 已經出咗病假紙
---
# Hong Kong MCHK Medical Certificate Compliance

1. **Doctor-Only Signature Boundary:** Under MCHK Telemedicine Guidelines, only a Hong Kong registered medical practitioner (`^[ML]\d{5}$`) may sign and issue a medical certificate. AI agents may ONLY stage a draft `/cert` proposal (`days_off` 1–7, `start_date`) for clinician review.
2. **Employer Privacy Default (HK PDPO DPP3):** Always default `omit_employer_diagnosis: true` on `/cert` orders so the employer-facing PDF omits clinical diagnoses while preserving coded dual ICD-10 + ICPC-2 inside the clinic medical record.
3. **Bounds & Backdating Guard:** Standard acute teleconsult draft is 1–2 days (hard schema bounds 1–7 days). Flag any request for backdated dates or > 3 days explicitly for doctor verification.
4. **Zero Premature Confirmation:** Never tell a patient their sick leave is approved or issued until the doctor's MCHK signature event is confirmed.
