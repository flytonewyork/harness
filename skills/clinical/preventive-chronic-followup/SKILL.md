---
name: preventive-chronic-followup
version: "1.0.0"
autonomy_level: L1
authority_ceiling: DRAFT
priority: 70
max_tokens: 340
triggers:
  always_on: false
  intents: [preventive_check, chronic_followup, booking, patient_chat, workspace_compile]
  pathways:
    - ebm.pathway.low_ferritin_normal_hb
    - ebm.pathway.chronic_htn_home_bp
    - ebm.pathway.borderline_ldl
    - ebm.pathway.t2dm_hba1c
    - ebm.pathway.thyroid_fatigue
  keywords_en: [flu shot, flu vaccine, ferritin, iron, blood pressure, home BP, LDL, cholesterol, HbA1c, diabetes, thyroid, TSH, checkup, follow up, booking]
  keywords_zh: [流感針, 疫苗, 鐵蛋白, 血壓, 膽固醇, 糖尿, 血糖, 甲狀腺, 覆診, 預約]
allowed_tools:
  - emr.get_patient_chart
  - emr.get_clinical_records
  - emr.check_preventive_gaps
  - clinic.get_available_slots
  - clinic.book_slot
  - memory.upsert_fact
safety_floor_refs:
  - ebm.safety.doctor_only_authority
banned_claims:
  - reply by 4pm
  - guaranteed reply
  - Waiting on Dr.
  - discount
  - special offer
  - 優惠價
  - 免費診症
---
# Always-On Preventive Care & Longitudinal Chronic Follow-Up

1. **Acute Febrile Flu-Vax Deferral:** If a patient presents with acute febrile URTI (`T > 37.5°C`) or acute illness, pause seasonal flu vaccination offers and schedule an automatic 14-day re-offer loop (preserving corporate `$0` copay eligibility).
2. **Evidence-Based HK GP Prevention:** Surface age/sex/risk/corporate-benefit preventive gaps cleanly in `PreventiveCard` (seasonal flu, colorectal FIT >= 50y, cervical screening, pneumococcal >= 65y, lipid/HbA1c screening, bone health) mapped strictly to canonical `/book`, `/order`, or `/nurse` primitives.
3. **Closed-Loop Escalation Cadence:** For low-risk chronic follow-up stalls (e.g., missed home BP log, overdue repeat Ferritin/HbA1c/TSH), send up to `2 nudges / 72h` via Careline AI, then route to **Nurse Rachel** first before escalating to the GP.
4. **MCHK Non-Inducement & Zero Fake SLAs:** Mention corporate benefit coverage factually (`Harbour FinTech $0 copay`) without promotional superlatives or discount inducement language, and never fabricate clinician reply-time guarantees.
