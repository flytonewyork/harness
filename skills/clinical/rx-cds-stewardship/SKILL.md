---
name: rx-cds-stewardship
version: "1.0.0"
autonomy_level: L1
authority_ceiling: CDS
priority: 95
max_tokens: 400
triggers:
  always_on: false
  intents: [medication_question, rx_draft, intake, workspace_compile, instruction_compile]
  pathways:
    - ebm.pathway.post_discharge_anticoag
    - ebm.pathway.acute_urti_g6pd
    - ebm.pathway.t2dm_hba1c
    - ebm.pathway.thyroid_fatigue
    - ebm.pathway.acute_uti
  keywords_en: [aspirin, panadol, paracetamol, ibuprofen, antibiotic, amoxicillin, augmentin, warfarin, statin, metformin, nitrofurantoin, fosfomycin, carbimazole, levothyroxine, G6PD, allergy, codeine]
  keywords_zh: [阿士匹靈, 必理痛, 撲熱息痛, 布洛芬, 抗生素, 盤尼西林, 薄血藥, 蠶豆症, 藥物過敏, 止痛藥, 咳藥水]
allowed_tools:
  - emr.get_patient_chart
  - cds.check_medication_safety
  - memory.upsert_fact
safety_floor_refs:
  - ebm.safety.g6pd_allergy_floor
  - ebm.safety.doctor_only_authority
banned_claims:
  - prescription has been issued
  - prescription is approved
  - safe to take aspirin
  - 已處方
  - 已批出處方
  - 可以食阿士匹靈
---
# HK Formulary CDS & Antibiotic Stewardship Floor

1. **Mandatory Deterministic CDS:** Always call `emr.get_patient_chart` and `cds.check_medication_safety` BEFORE answering any patient medication question or drafting a `/prescribe` order. Never estimate drug safety from memory alone.
2. **Hard Blocks (Non-Overridable):**
   - **Cap. 134 Dangerous Drugs** (e.g., codeine, tramadol, benzodiazepines, zopiclone) are unconditionally blocked in acute teleconsultation drafts.
   - **G6PD Deficiency:** Block Aspirin, co-trimoxazole, and nitrofurantoin (acute hemolysis risk). Default G6PD-safe URTI relief: Paracetamol 500mg QDS PRN + Cetirizine 10mg OD (doctor sign-off required).
   - **Penicillin Allergy:** Block Amoxicillin / Augmentin / Ampicillin and flag beta-lactam cross-reactivity.
3. **Renal, Endocrine & DDI Guardrails:** Check eGFR/CrCl before Metformin (< 30 contraindicated, 30–45 dose cap) or Nitrofurantoin (< 45 contraindicated); flag Warfarin + NSAID/Macrolide/Azole bleeding DDIs; warn Carbimazole sore-throat agranulocytosis; separate Levothyroxine from oral iron/calcium by >= 4 hours.
4. **Atomic Drug Switching & Stewardship:** Emit one single-drug `rx` order per line (`start | update | stop | continue`). For statin/antihypertensive switches, pair explicit `stop` + `start` lines. Do NOT suggest antibiotics for uncomplicated viral URTI.
