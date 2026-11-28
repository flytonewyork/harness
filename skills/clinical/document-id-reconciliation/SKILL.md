---
name: document-id-reconciliation
version: "1.0.0"
autonomy_level: L1
authority_ceiling: MEMORY
priority: 90
max_tokens: 340
triggers:
  always_on: false
  intents: [document_ingest, discharge_reconcile, lab_ingest]
  pathways:
    - ebm.pathway.post_discharge_anticoag
    - ebm.pathway.low_ferritin_normal_hb
    - ebm.pathway.t2dm_hba1c
    - ebm.pathway.thyroid_fatigue
  keywords_en: [discharge, QEH, PWH, KingMed, lab report, referral, HKID, warfarin, statin]
  keywords_zh: [出院, 化驗報告, 驗血, 轉介信, 身份證, 薄血藥]
allowed_tools:
  - emr.get_patient_chart
  - emr.get_clinical_records
  - memory.upsert_fact
  - cds.check_medication_safety
safety_floor_refs:
  - ebm.safety.three_point_id_gate
  - ebm.safety.doctor_only_authority
banned_claims:
  - stop taking your warfarin
  - hold your medication
  - chart updated automatically
  - 自行停藥
---
# Document 3-Point ID Gate & Cross-Site Reconciliation

1. **Fail-Closed 3/3 Identity Gate:** External hospital discharges, lab reports, and specialist letters MUST match **3/3 identifiers** (Full Name + DOB + HKID) AND pass multi-page ID collision checks before linking to any patient chart. Any `< 3/3` match or page mismatch quarantines immediately to Nurse Rachel in `# clinic-team`.
2. **Verbatim Quote Grounding:** Every extracted lab result, medication change, or follow-up commitment MUST cite an exact character-for-character verbatim quote from the source document. Never paraphrase numbers or invent dates.
3. **Structured MedDiff Without Autonomous Holds:** Surface cross-site discrepancies (`External` vs `Clinic EMR`) tagged as `Dose change`, `Switched`, or `Duplicate` (e.g., QEH Warfarin 2mg vs EMR 3mg; Atorvastatin vs Simvastatin). Never instruct the patient to hold or change prescription doses before MCHK doctor confirmation.
4. **Safety-Critical Gaps Only:** Highlight unconfirmed home pillbox strength or duplicate home stock with a single subtle inline amber gap cue; omit routine confirmation boilerplate.
