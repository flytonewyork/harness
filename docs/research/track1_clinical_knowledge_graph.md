# Track 1 · Canonical Clinical Knowledge Graph & Governance Specification

*Locked against `docs/research/SEAM_CONTRACT.md` · Branch `feat/live-demo-convergence`*
*Scope: 8 executable HK GP pathways, live `/slash` bindings (`terminology.py`, `rx_rules.py`, `certificates.py`), deterministic Python clinical calculators, 10 HK preventive rules, hardened 5-rule Presentation Linter, 45-min Lead GP onboarding script, multi-doctor Curator governance, and bilingual HK PDPO consent/retention spec.*

---

## 1. 3-Layer Clinical Practice Knowledge Graph & Immutable Safety Floor

Every agent loop (`Loops 1–5`) traverses the active versioned Practice Graph deterministically **before** spending LLM tokens:

1. **Layer 1 · Gold-Standard EBM Base Tree + Immutable Safety Floor (`EBM_BASE_NODES`)**
   - Immutable Safety Floor nodes (`safety_floor=True`, `overridable=False`) raise structured `SafetyFloorViolation(error_code, node_id, layer="safety_floor", overridable=False)` (HTTP 422):
     - `ebm.safety.crisis_999`: immediate 999 / A&E exit on red flags (`classify_crisis`).
     - `ebm.safety.g6pd_allergy_floor`: deterministic pre-LLM G6PD + allergy + Cap. 134 block (`rx_rules.py`).
     - `ebm.safety.doctor_only_authority`: zero autonomous prescribing, zero autonomous medication holds, exact 3-point ID match required on external documents, and zero clinician impersonation titles (`FORBIDDEN_AI_TITLES`).
2. **Layer 2 · Clinic Practice Overlay (`DEFAULT_PRACTICE_OVERLAY` + `DEFAULT_CARELINE_PROFILE`)**
   - Encodes clinic-specific care standards (`grace_window_hours`, `retry_cap_nudges`, `partner_lab`, `corporate_plans`, `undo_window_seconds`) and per-pathway `node_overrides` moulded during onboarding.
3. **Layer 3 · Continuous Clinic Curator (`curator.py`)**
   - Clusters doctor `override_diff` telemetry via `(target_node_id, diff_key, normalized_value)` `diff_fingerprint`, enforces capture-time Safety Floor validation, flags multi-doctor conflicts, protects against locum-only drift, and supports immutable version rollback.

---

## 2. All 8 Executable HK GP Pathways (Seam A ↔ Seam B)

All 8 pathways bind deterministically to a subset of the **9 Canvas widgets** (the **5 Right-Rail widgets** — `SafetyCard`, `PreventiveCard`, `ScheduledCard`, `TimelineCard`, `UnifiedThread` — always mount in fixed order). `default.thin_intake` mounts `["IssueCard", "ManagementPlan"]` only.

| # | Canonical `GraphNode.id` (Alias) | Guideline Reference | Key Executable Params & Escalation Gates | Deterministic Canvas `widget_stack` |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `ebm.pathway.post_discharge_anticoag` | NICE NG196 AFib / HK HA MedRec | `risk_tier=high`, 3-pt ID gate, 7d INR recall, Cantonese nurse adherence call, stall → doctor | `VitalsExamStrip`, `ClinicalScoreStrip`, `MedDiff`, `IssueCard`, `TrendGraph`, `ResultTable`, `ImagingPreview`, `SourceClip`, `ManagementPlan` |
| 2 | `ebm.pathway.acute_urti_g6pd` | HK CHP IMPACT / MCHK Sick-Leave | G6PD-safe symptomatic Rx (`Paracetamol`, `Cetirizine`), zero routine antibiotics, 14d flu vax deferral, `omit_employer_diagnosis=True` | `VitalsExamStrip`, `IssueCard`, `SourceClip`, `ManagementPlan` |
| 3 | `ebm.pathway.low_ferritin_normal_hb` | BSG Iron Deficiency / RACGP | `ferritin_low_threshold=15 ng/mL`, `hb_normal_floor=11.5 g/dL`, alternate-day `Ferrous bisglycinate 25mg`, 3m repeat Ferritin + FBC | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `TrendGraph`, `SourceClip`, `ManagementPlan` |
| 4 | `ebm.pathway.chronic_htn_home_bp` | HK Ref Framework HTN / NICE NG136 | `target_sys=135`, `target_dia=85`; stays Scheduled if on-target; wakes `#my-work` if 7d avg > `140/90` or single > `160/100` | `VitalsExamStrip`, `IssueCard`, `TrendGraph`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 5 | `ebm.pathway.borderline_ldl` | HK Primary Care CVD Framework | `ldl_upper_normal=3.4 mmol/L`, 8h fasting 3m repeat lipid draw, escalate if repeat > `3.4` or 10-yr CVD ≥ 10% | `ClinicalScoreStrip`, `IssueCard`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 6 | `ebm.pathway.t2dm_hba1c` | HK Ref Framework Diabetes / NICE NG28 | `hba1c_target_pct=7.0`, `hba1c_escalate_pct=7.5`, Metformin eGFR guard (`<30` contraindicated, `30–45` max `1000mg/d`), 3m HbA1c + annual uACR/eye/foot | `VitalsExamStrip`, `ClinicalScoreStrip`, `IssueCard`, `TrendGraph`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 7 | `ebm.pathway.thyroid_fatigue` | NICE NG145 Thyroid / HK GP Guide | TSH (`0.27–4.2 mIU/L`) + Free T4; `Levothyroxine` mcg dosing + 4h iron/calcium spacing + 6–8w TSH recheck; `Carbimazole` agranulocytosis & pregnancy block | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `TrendGraph`, `SourceClip`, `ManagementPlan` |
| 8 | `ebm.pathway.acute_uti` *(alias: `ebm.pathway.acute_uti_stewardship`)* | HK CHP IMPACT / NICE NG109 | Pyelonephritis red-flag gate (fever/flank pain/vomiting); `Nitrofurantoin` blocked if `eGFR < 45` or G6PD-deficient → fallback `Fosfomycin 3g` stat or `Cephalexin`; MSU if complicated/pregnant | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `SourceClip`, `ManagementPlan` |

---

## 3. Live `/slash` Bindings into `terminology.py`, `rx_rules.py`, and `certificates.py`

All 8 canonical order primitives (`ORDER_CATALOG_PRIMITIVES`) pass through `normalize_order_primitive(order, include_camel_aliases=True)` in `backend/acute/practice_graph.py`, accepting either canonical `snake_case` or legacy `camelCase` and emitting canonical fields plus read aliases for backward compatibility.

| Primitive | `/slash` | Execution Binding | Deterministic Safety & Compliance Enforcement |
| :--- | :--- | :--- | :--- |
| `rx` | `/prescribe` | `terminology.lookup_drug(name, include_chronic=True)` → `rx_rules.evaluate_rx_order_primitives(...)` | `HK_ACUTE_FORMULARY` preserved intact; `HK_CHRONIC_GP_FORMULARY` + `HK_PRIMARY_CARE_FORMULARY` cover all 8 pathways (`warfarin`, `atorvastatin`, `simvastatin`, `amlodipine`, `ferrous bisglycinate`, `metformin`, `levothyroxine`, `carbimazole`, `nitrofurantoin`, `fosfomycin`, `cephalexin`, etc.); unlisted drugs (`ivermectin`) fail closed (`hard_block`). Unit-aware `_parse_numeric_mg` (`mcg`/`ug`/`µg` ÷ 1000, `g` × 1000) prevents `Levothyroxine 50mcg` false blocks. `action="stop"` reconciliation removes stopped drugs from active chart before duplicate-class checks (so `stop Simvastatin` + `start Atorvastatin` passes cleanly). |
| `cert` | `/cert` | `certificates.issue_certificate_from_order_primitive(...)` → `issue_encrypted_certificate(...)` | Validates MCHK `^[ML]\d{5}$`, `1 <= days_off <= 7`, dual ICD-10/ICPC-2 against `HK_DIAGNOSES`. Defaults `omit_employer_diagnosis=True` on `/cert` orders: retains dual ICD-10/ICPC-2 in `db.Certificate` while omitting diagnosis on the employer PDF and computing SHA-256 verification hash with `"diagnosis_omitted_for_employer": true`. |
| `lab` | `/order` | `terminology.LOINC_OBSERVATIONS` & `validate_observation_coding` | Validates LOINC/UCUM (`INR 6301-6`, `Ferritin 2276-4`, `HbA1c 4548-4`, `eGFR 33914-3`, `uACR 14959-1`, `TSH 3016-3`, `Free T4 3024-7`, `Urine Culture 630-4`, `FIT 29771-3`) + partner lab & fasting flag. |
| `imaging` | `/imaging` | Structured imaging request (`modality`, `region`, `indication`, `urgency`) | Normalizes `priority` → lowercased `urgency` (`routine\|soon\|urgent`). |
| `refer` | `/refer` | Specialist referral (`specialty`, `urgency`, `clinical_question`, `attach_labs`) | `requires_sign=True`; attaches verified 3-pt ID labs by default. |
| `goal` | `/goal` | Quantified chronic target (`metric`, `target_sys/dia`, `target_value`, `unit`, `leaflet_id`) | Updates compliance-gated longitudinal goal in `CaseLoop.goal_json` / `AgentMemoryEntry`. |
| `nurse` | `/nurse` | Closed 6-protocol menu (`CANONICAL_NURSE_PROTOCOLS`) | Only `medication_adherence_check`, `vaccine_session`, `bp_inr_followup_call`, `screening_kit_dispatch`, `booking_followup_call`, `pre_visit_questionnaire` permitted. Free-form clinical prose rejected by Linter `R4`. |
| `book` | `/book` | `terminology.CVX_VACCINES` (`150`, `158`, `171`, `185`, `216`, `187`, `165`, `43`) | Actionable visit slot (`visit_type`, `when`, `duration_min`, `cvx_code`). |

---

## 4. Deterministic Python Clinical Score Calculators & 10 HK Preventive Care Rules

### 4.1 Deterministic Calculators (`practice_graph.py`)
Every calculator returns the locked Seam A contract:
`{id, label, value, band, detail, guideline, numeric_value, unit, safety_flag, dosing_flags, computed_by: "deterministic_python"}`.

1. **`compute_cha2ds2_vasc`** (NICE NG196): Female sex alone (`sex="F"`, `score == 1`, zero non-sex risk factors) explicitly returns `band="Low"` (never over-recommends anticoagulation). Male `>=2` / Female `>=3` → `"High · Anticoag indicated"`; Male `1` / Female `2` → `"Consider anticoag"`.
2. **`compute_has_bled`** (NICE NG196): Supports combined or granular renal/liver/drugs/alcohol flags; `score >= 3` sets `safety_flag="HIGH_BLEEDING_RISK"`.
3. **`compute_cvd_10yr_tier`** (HK Primary Care CVD): Deterministic risk tier (`< 2%`, `< 5%`, `5–10%`, `≥ 10%`) incorporating age, smoking, SBP, LDL-C, and diabetes.
4. **`compute_curb65`** (NICE NG138 / HK CAP): Confusion, Urea > 7.0 mmol/L, RR ≥ 30, BP < 90/60, Age ≥ 65; sets `safety_flag="CURB65_URGENT_AE"` at `>= 3`.
5. **`compute_phq9`** (NICE NG222 / HK Primary Care Mental Health): Accepts 9 item scores (`0–3`) or `total_score + item9_score`. **Mandatory Safety Gate:** any `item9 > 0` sets `safety_flag="PHQ9_ITEM9_SELF_HARM"` and `dosing_flags=["urgent_suicide_risk_assessment", "activate_crisis_999_if_imminent"]`.
6. **`compute_renal_dosing`** *(alias `compute_egfr_crcl`)* (KDIGO / CKD-EPI 2021 + Cockcroft-Gault): Computes race-free CKD-EPI 2021 eGFR (`mL/min/1.73m²`) and optional Cockcroft-Gault CrCl (`mL/min` when `weight_kg` supplied), emitting deterministic `dosing_flags` (`oseltamivir_reduce_75mg_od`, `nitrofurantoin_contraindicated`, `metformin_max_1000mg_day`, `metformin_contraindicated`, `oseltamivir_reduce_30mg_od`).

### 4.2 Ten Always-On HK Preventive Care Rules (`PREVENTIVE_CARE_RULES` + `evaluate_preventive_care`)
1. `prev.hk.flu_annual` — Seasonal Influenza Vaccine (All adults annually; G6PD-safe inactivated; HK CHP SCVPD; 14d deferral if febrile).
2. `prev.hk.cervical_25_64` — Cervical Screening HPV/Pap (Females 25–64 every 3–5y; HK DH CSP).
3. `prev.hk.colorectal_fit_50_75` — HK Colorectal FIT Screening (Adults 50–75 biennial, Govt-subsidized; HK CRC Programme).
4. `prev.hk.mammogram_44_69` — Biennial Screening Mammogram (Females 44–69; HK CEWG Breast Cancer).
5. `prev.hk.pcv20_60plus` — Pneumococcal Vaccine PCV20 (Adults 60+ or chronic cardiopulmonary/DM; HK CHP SCVPD; CVX `216`).
6. `prev.hk.cardiometabolic` — Annual HbA1c & Fasting Lipids (Age 45+ or HTN/DM/BMI ≥ 23; HK Reference Framework).
7. `prev.hk.hbv_screening` — Hepatitis B Surface Antigen (HBsAg) One-Time Adult Screening (HK adults without documented HBV status; HK DH Viral Hepatitis Action Plan).
8. `prev.hk.shingrix_50plus` — Recombinant Zoster Vaccine (Shingrix RZV, 2-dose; Adults 50+; CVX `187`).
9. `prev.hk.dexa_osteoporosis` — DEXA Bone Mineral Density (Postmenopausal females 65+ or 50+ with hyperthyroidism/steroid/fracture risk; Osteoporosis Society of HK).
10. `prev.hk.dm_annual_complications` — Annual T2DM Microvascular Bundle (uACR, diabetic retinopathy screening, foot exam; HK DM Reference Framework).

---

## 5. Hardened Semantic Presentation Linter (`validate_case_presentation`)

Preserves `rules_total == 5` (`R1`–`R5`) with zero brittle word-count caps, returning both Seam B keys (`rule_id`, `sub_rule`, `severity`, `field_path`, `message`, `repair_hint`) and legacy keys (`rule`, `issue`, `detail`):

- **R1 (`R1_NO_WIDGET_DUPLICATION`)**: `R1.1` blocks repeating ≥ 2 mounted numeric values from `TrendGraph`/`ResultTable`; `R1.2` blocks narrating side-by-side `MedDiff` dose transitions or superseded+new drug pairs in issue prose.
- **R2 (`R2_ATOMIC_ISSUES_AND_ORDERS`)**: `R2.1` blocks mixing unrelated clinical domains across all 8 pathways in one `IssueCard`; `R2.2` blocks bundled multi-drug `rx` primitives; `R2.3` blocks duplicate active drugs/classes across issues while explicitly permitting `stop` + `start` regimen switches.
- **R3 (`R3_NO_SLOP_OR_SCAFFOLDING`)**: `R3.1` blocks legacy SOAP essay fields; `R3.2` blocks banned essay headers & slop phrases; `R3.3` blocks forbidden clinician titles on AI persona (`Resident`, `Doctor`, `Clinician`); `R3.4` blocks premature `rx` (`start`/`update`) or `cert` orders when `intake_sufficiency == "insufficient"`; `R3.5` enforces verbatim substring fidelity of `attachments[].quote` against verified `source_documents`.
- **R4 (`R4_CANONICAL_ACTION_ONTOLOGY`)**: Validates all 8 order primitives across **both** `issues[].suggested_orders` and `preventive_care[].order` — enforcing canonical `type`, atomic `rx` verbs (`start|update|stop|continue`), `cert` `1..7` day bounds, closed 6-protocol `nurse` menu, and required fields per primitive.
- **R5 (`R5_HIGH_SIGNAL_SAM_GATE`)**: Enforces objective score/guideline/safety marker presence (`OBJECTIVE_SAM_MARKERS` covering all 8 pathways + calculators) and bans chatty/meta-UI narration (`Pulled`, `Spotted`, `Drag`, `Kept him`, `Checkout`, etc.).

---

## 6. 45-Minute Lead GP Onboarding Interview Script (Layer 2 Moulding)

Designed to mould Layer 1 EBM defaults into a clinic's signed `PracticeGraphVersion v1.0` in a single 45-minute session with the Lead GP & Head Nurse:

| Block | Time | Interview Prompts for Lead GP / Head Nurse | Direct Layer-2 Overlay Binding |
| :--- | :--- | :--- | :--- |
| **1. Identity & Safety Floor Walkthrough** | 0–8 min | Confirm clinic name, Lead GP MCHK reg no., regular/locum roster, lead nurse, and Careline AI display name (e.g., `Sam · Clinic Careline AI`). Explicitly review the 3 Immutable Safety Floor nodes (999 exit, G6PD/Allergy/Cap. 134 block, Doctor-Only Authority). | `careline_profile.{name, role_label, clinic_name, lead_gp, lead_nurse, disclosure_en, disclosure_zh}` |
| **2. Async Triage & Commercial Boundaries** | 8–18 min | What post-visit/discharge grace window should route clarifications to `#my-work` (default `72h`)? Which intents stay self-serve Tier 1? Confirm zero fake reply SLAs and zero unilateral AI billing. Confirm retry cap (`2 nudges / 72h`) before routing stalls to Nurse vs. Doctor. | `clinic_standards.{grace_window_hours, retry_cap_nudges, retry_cap_hours, undo_window_seconds}` |
| **3. Partner Labs, Corporate Plans & Cert Privacy** | 18–26 min | Primary pathology lab (e.g., KingMed, PHML, Diagnostix)? Active corporate panels (`$0` flu co-pay, visit duration, default employer diagnosis omission)? Confirm `omit_employer_diagnosis: true` MCHK privacy default. | `clinic_standards.{partner_lab, corporate_plans}`, `node_overrides.ebm.pathway.acute_urti_g6pd.omit_employer_diagnosis` |
| **4. Pathway Calibration (Top 8 HK GP Flows)** | 26–38 min | Walk through clinic norms across the 8 pathways: Warfarin post-discharge INR recall (`7d`) & Cantonese nurse call; non-anaemic ferritin iron salt (`Ferrous bisglycinate` alternate days vs daily); HTN home BP target (`<135/85`) & wake threshold (`>140/90`); Borderline LDL repeat (`3m`, fasting `8h`, auto-book GP review?); T2DM HbA1c target (`7.0%`); Thyroid recheck (`6w`); UTI 1st-line stewardship (`Nitrofurantoin` vs `Fosfomycin`). | `node_overrides["ebm.pathway.*"]` |
| **5. Nurse Scope, Curator & Sign-Off** | 38–45 min | Confirm closed 6-protocol nurse delegation menu. Designate Lead GP as sole Curator approver (locum diffs require regular GP corroboration). Dry-run 1 live case preview + 10s Undo, then sign `PracticeGraphVersion`. | `curator.mould_practice_graph(..., approved_by=lead_gp)` |

---

## 7. Multi-Doctor Clinic Curator Governance (`curator.py`)

1. **Deterministic Diff Fingerprinting**: Every `record_override_diff(...)` extracts `(target_node_id, diff_key, normalized_value)` and hashes it into `diff_fingerprint` (SHA-256[:16]). Identical clinic patterns cluster cleanly; semantically distinct changes on the same node never collapse into one noisy bucket.
2. **Capture-Time Safety Floor Guard**: Every proposed override diff is validated against `validate_overlay_against_safety_floor(...)` **at capture time**. Attempts to weaken any Safety Floor node (or enable autonomous Rx/med holds / disable 3-pt ID) raise `SafetyFloorViolation` immediately and write an immutable `curator_safety_floor_blocked` audit event — never entering the Lead GP approval queue.
3. **Multi-Doctor Conflict Detection**: If Dr. A and Dr. B submit different `normalized_value` diffs on the same `(target_node_id, diff_key)` (e.g., BP target `<130/80` vs `<140/90`, or `daily` vs `alternate-day` iron), Curator creates distinct proposals and marks **both** with `conflict_detected: true` and cross-linked `conflicting_proposal_ids` for explicit Lead GP adjudication.
4. **Locum Corroboration Safeguard**: Proposals sourced solely from covering locums carry `locum_only: true` and `needs_regular_corroboration: true`. Locums cannot self-approve Curator proposals (`LEAD_GP_APPROVAL_REQUIRED`).
5. **Immutable Version History & Rollback**: `rollback_practice_graph(...)` never mutates or deletes prior `PracticeGraphVersion` rows; it supersedes the active version and publishes a new bumped version restoring the target snapshot's overlay/profile after Safety Floor re-verification, logging `practice_graph_rolled_back` to `audit_events`.

---

## 8. Bilingual HK PDPO Compliance Spec for `AgentMemoryEntry` (DPP1 / DPP3 / DPP6)

`AgentMemoryEntry` is a **compliance-gated "EMR for Agents"** store isolated by PostgreSQL 15 `FORCE ROW LEVEL SECURITY` (`clinic_app NOBYPASSRLS`) and strictly separate from the clinic's Sole Legal Clinical EMR Source of Truth. Ephemeral doctor–Sam private Q&A is **never** written to the legal EMR.

### 8.1 Data Protection Principles & Retention Policy
- **DPP1 (Purpose & Minimisation)**: Collect only clinically necessary longitudinal trajectories, 3-pt-ID-verified quotes, preference flags, and active loop states under explicit `consent_scope` (`ai_assisted_intake` | `clinical_care` | `practice_quality_improvement`).
- **DPP3 (Use Limitation)**:
  - `AgentMemoryEntry` rows are scoped strictly to `tenant_id` and `patient_id` and used solely for care coordination within the patient's treating clinic.
  - Zero cross-clinic pooling, zero foundation-model training on patient PHI, and zero employer/insurer disclosure (corporate dashboards remain aggregate-only with k-anonymity `< 5` suppression).
  - Inference runs strictly in GCP Vertex AI **`asia-east2` (Hong Kong)** (`allow_cross_border_inference = False` default).
- **DPP2 / Retention & Statutory Hold**:
  - **Clinical EMR SoT records** (encounters, signed addenda, prescriptions, certificates): retained per HK MCHK / HA standard — minimum **7 years** from last adult attendance, or until **age 21** (whichever is later) for minors, enforced via `patients.status = "deleted_statutory_hold"` and `retention_until`.
  - **Compliance-gated `AgentMemoryEntry` rows**:
    - `episodic` & `preference` tiers: purged or tombstoned immediately upon patient withdrawal of `ai_assisted_intake` consent (DPP6), or auto-expired **24 months** after loop closure if no active care episode.
    - `longitudinal` tier: retained while patient maintains active care + consent, purged on consent withdrawal (clinical source values remain safely inside the clinic's legal EMR SoT).
    - `override_diff` tier: de-identified of patient PII when promoted to Practice Graph Curator proposals.
- **DPP6 (Data Access, Correction & Withdrawal)**:
  - Patients may submit Data Access Requests (`dar_export`), Data Correction Requests (`dcr_correction`), or `consent_withdrawal` via the immutable `consents` + `data_requests` ledger; withdrawal immediately disables `AgentMemoryEntry` injection into LLM context windows while preserving statutory EMR records.

### 8.2 Bilingual Patient Personal Information Collection Statement (PICS) Excerpt

> **English (`HK-PICS-AGENT-v1.1`):**
> *With your consent, our clinic's automated careline assistant (`Sam · Clinic Careline AI`) stores brief care-coordination summaries (such as your home blood-pressure readings, verified lab quotes, and follow-up dates) in an encrypted Hong Kong (`asia-east2`) memory store so our doctors and nurses — including covering doctors — can keep track of your ongoing care. All medical decisions and prescriptions are made solely by our Hong Kong Registered Medical Practitioners. Your data is never shared with your employer or used to train external AI models. You may access, correct, or withdraw AI care-coordination memory consent at any time via the clinic portal or our Data Protection Officer (`dpo@tmrw-clinic.hk`); withdrawing consent stops AI memory use immediately while statutory clinical records remain retained in our clinic EMR under Hong Kong medical regulations (minimum 7 years for adults / age 21 for minors).*

> **繁體中文（`HK-PICS-AGENT-v1.1`）：**
> *經閣下同意，本診所之自動診所助理（`Sam · Clinic Careline AI`）會將簡要的跟進摘要（例如家居血壓紀錄、已核實化驗報告摘錄及覆診日期）加密儲存於香港區域（`asia-east2`）之專用記憶庫，以便本診所醫生（包括替假醫生）及護士持續跟進閣下的健康狀況。所有臨床診斷及處方均由香港註冊西醫全權決定。閣下的個人資料絕不會向僱主披露，亦不會用於訓練外部人工智能模型。閣下可隨時透過診所平台或聯絡保障資料主任（`dpo@tmrw-clinic.hk`）查閱、更正或撤回人工智能記憶同意；撤回同意後系統將即時停止使用人工智能記憶，而法定病歷紀錄將按香港醫療規例保留於診所正式病歷系統（成人最少七年，未成年人保留至年滿二十一歲）。*
