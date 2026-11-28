# Mandatory Schema Sync Barrier — Unified Seam Contract (Seams A, B & C)

*Locked: 23 Nov 2026 · Branch `feat/live-demo-convergence` (off `feat/live-emr-agent-phone-demo` + `feat/agentic-skills-prompts-memory`)*
*Unified JSON contract reconciling Tracks 1–4 across Seams A, B, and C with 100% backward compatibility for existing UI and pytest suites.*

---

## 1. Canonical 8 HK GP Pathway IDs & 14-Widget Stacks (Seam A ↔ Seam B)

| # | Canonical `GraphNode.id` | Accepted Alias | Deterministic Canvas `widget_stack` (Right Rail always mounts 5 widgets in fixed order) |
| :--- | :--- | :--- | :--- |
| 1 | `ebm.pathway.post_discharge_anticoag` | — | `["VitalsExamStrip", "ClinicalScoreStrip", "MedDiff", "IssueCard", "TrendGraph", "ResultTable", "ImagingPreview", "SourceClip", "ManagementPlan"]` |
| 2 | `ebm.pathway.acute_urti_g6pd` | — | `["VitalsExamStrip", "IssueCard", "SourceClip", "ManagementPlan"]` |
| 3 | `ebm.pathway.low_ferritin_normal_hb` | — | `["VitalsExamStrip", "IssueCard", "ResultTable", "TrendGraph", "SourceClip", "ManagementPlan"]` |
| 4 | `ebm.pathway.chronic_htn_home_bp` | — | `["VitalsExamStrip", "IssueCard", "TrendGraph", "ResultTable", "SourceClip", "ManagementPlan"]` |
| 5 | `ebm.pathway.borderline_ldl` | — | `["ClinicalScoreStrip", "IssueCard", "ResultTable", "SourceClip", "ManagementPlan"]` |
| 6 | `ebm.pathway.t2dm_hba1c` | — | `["VitalsExamStrip", "ClinicalScoreStrip", "IssueCard", "TrendGraph", "ResultTable", "SourceClip", "ManagementPlan"]` |
| 7 | `ebm.pathway.thyroid_fatigue` | — | `["VitalsExamStrip", "IssueCard", "ResultTable", "TrendGraph", "SourceClip", "ManagementPlan"]` |
| 8 | `ebm.pathway.acute_uti` | `ebm.pathway.acute_uti_stewardship` | `["VitalsExamStrip", "IssueCard", "ResultTable", "SourceClip", "ManagementPlan"]` |
| — | `default.thin_intake` | — | `["IssueCard", "ManagementPlan"]` |

**Fixed 5 Right-Rail Widgets:** `["SafetyCard", "PreventiveCard", "ScheduledCard", "TimelineCard", "UnifiedThread"]`.

---

## 2. Canonical 8 Order Primitives (`ORDER_CATALOG_PRIMITIVES` — Dual-Alias Reconciliation)

Shared normalizer `normalize_order_primitive(order: dict, *, include_camel_aliases: bool = True) -> dict` in `backend/acute/practice_graph.py`:
- Accepts EITHER canonical `snake_case` OR legacy `camelCase` on input.
- Emits canonical `snake_case` PLUS legacy `camelCase` read aliases when `include_camel_aliases=True`.

| `type` | `/slash` | Canonical `snake_case` Fields | Accepted Input Aliases | Legacy Read Aliases Emitted | `requires_sign` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `rx` | `/prescribe` | `id`, `type`, `issue`, `action` (`start\|update\|stop\|continue`), `drug`, `dose_mg` (opt `dose_mcg`), `freq`, `days`, `note` | `frequency` → `freq`, `doseMg` → `dose_mg`, `doseMcg` → `dose_mcg` | `doseMg` (if present) | `true` |
| `cert` | `/cert` | `id`, `type`, `issue`, `days_off` (`1..7`), `start_date`, `end_date` (opt), `dates`, `omit_employer_diagnosis` (default `true`), `diagnosis_icd10` (opt), `diagnosis_icpc2` (opt) | `daysOff` → `days_off`, `startDate` → `start_date`, `omitDx`/`omit_dx` → `omit_employer_diagnosis` | `daysOff`, `startDate`, `omitDx` | `true` |
| `lab` | `/order` | `id`, `type`, `issue`, `test_name`, `loinc_code` (opt), `lab`, `fasting` (`bool`), `when` | `testName` → `test_name`, `loincCode` → `loinc_code` | `testName`, `loincCode` | `false` |
| `imaging` | `/imaging` | `id`, `type`, `issue`, `modality`, `region`, `indication`, `urgency` (`routine\|soon\|urgent`) | `priority` → `urgency` (lowercased) | `priority` | `false` |
| `refer` | `/refer` | `id`, `type`, `issue`, `specialty`, `urgency` (`routine\|soon\|urgent`), `clinical_question`, `attach_labs` (`bool`, default `true`) | `priority` → `urgency`, `reason` → `clinical_question`, `attachLabs` → `attach_labs` | `priority`, `reason`, `attachLabs` | `true` |
| `goal` | `/goal` | `id`, `type`, `issue`, `metric`, `target_sys` (opt), `target_dia` (opt), `target_value` (opt), `target_text` (opt), `unit` (opt), `leaflet_id` (opt) | `targetSys` → `target_sys`, `targetDia` → `target_dia`, `targetValue` → `target_value`, `leafletId` → `leaflet_id` | `targetSys`, `targetDia`, `targetValue`, `leafletId` | `false` |
| `nurse` | `/nurse` | `id`, `type`, `issue`, `protocol` (`medication_adherence_check\|vaccine_session\|bp_inr_followup_call\|screening_kit_dispatch\|booking_followup_call\|pre_visit_questionnaire`), `assignee`, `task`, `language` (`en\|zh-HK\|bilingual`), `due` | — | — | `false` |
| `book` | `/book` | `id`, `type`, `issue`, `visit_type`, `when`, `duration_min` (opt), `cvx_code` (opt), `note` (opt) | `visitType` → `visit_type`, `durationMin` → `duration_min`, `cvxCode` → `cvx_code` | `visitType`, `durationMin`, `cvxCode` | `false` |

---

## 3. Seam A (`Track 1 → Track 3 & Track 4`)

- **Python Clinical Score Calculators (`practice_graph.py`):** `compute_cha2ds2_vasc` (female-sex-alone `score==1` → `band="Low"`), `compute_has_bled`, `compute_cvd_10yr_tier`, `compute_curb65`, `compute_phq9` (Item 9 `>0` sets `safety_flag="PHQ9_ITEM9_SELF_HARM"`), `compute_renal_dosing` (alias `compute_egfr_crcl`). Every calculator returns `{id, label, value, band, detail, guideline, numeric_value, unit, safety_flag, dosing_flags, computed_by: "deterministic_python"}`.
- **`terminology.py`:** Keep `HK_ACUTE_FORMULARY` untouched; add `HK_CHRONIC_GP_FORMULARY` + `HK_PRIMARY_CARE_FORMULARY` (`warfarin`, `atorvastatin`, `simvastatin`, `amlodipine`, `ferrous bisglycinate`, `metformin`, `levothyroxine`, `carbimazole`, `nitrofurantoin`, `fosfomycin`, `cephalexin`) and `lookup_drug(name, include_chronic=True)`; add ICD-10/ICPC-2, LOINC, SNOMED, CVX codes for Pathways 6–8.
- **`rx_rules.py`:** Add `qds`, `alternate days`, `alt days`, `eod` to `_FREQ_MAP`; fix `mcg`/`ug`/`g` dose parsing; add Metformin/Nitrofurantoin eGFR, Carbimazole agranulocytosis/pregnancy, Levothyroxine–iron separation rules; expose `evaluate_rx_order_primitives(orders, patient_context, active_meds=None, override_reason=None)` honoring `action="stop"` so switching `Simvastatin -> Atorvastatin` does not false-trigger duplicate statin.
- **`certificates.py`:** Add `omit_employer_diagnosis: bool = False` on `generate_sick_leave_pdf` / `issue_encrypted_certificate` / `compute_certificate_verification_hash` + `issue_certificate_from_order_primitive(...)`.
- **`curator.py`:** Add `(target_node_id, diff_key, normalized_value)` `diff_fingerprint` clustering, multi-doctor conflict flags, locum corroboration safeguard, capture-time Safety Floor guard, and `rollback_practice_graph(...)`.
- **`SafetyFloorViolation`:** Structured `.error_code`, `.node_id`, `.layer="safety_floor"`, `.overridable=False` while keeping `str(exc)` backward-compatible (`"Safety Floor"` / `"medical title"`).

---

## 4. Seam B (`Track 2 ↔ Track 1 & Track 3`)

- **Hardened `validate_case_presentation(summary, *, source_documents=None, careline_profile=None)` (`practice_graph.py`):**
  - Preserves `passed`, `rules_passed`, `rules_total == 5` (`R1`–`R5`).
  - Each violation dict includes BOTH rich keys (`rule_id`, `sub_rule`, `severity`, `field_path`, `message`, `repair_hint`) AND legacy keys (`rule`, `issue`, `detail`).
  - Enforces hardened sub-rules (zero brittle word-count caps): `R1` numeric + MedDiff narration; `R2` bundled Rx + mixed clinical domains across all 8 pathways + cross-issue duplicate drugs (allowing `stop`+`start` switches); `R3` essay headers, slop phrases, forbidden clinician titles (`Resident`/`Doctor`/`Clinician`), thin-intake premature Rx/cert guard, verbatim quote fidelity; `R4` all 8 primitives across `issues[].suggested_orders` AND `preventive_care[].order`, atomic `rx` verbs, `cert` 1–7d bounds, closed `nurse` protocol menu; `R5` objective score/guideline gate + zero chatty UI narration.
- **`CaseSummaryPayload` additive keys:** `intake_sufficiency` (`sufficient|insufficient`), `proxy_context`, generalized `med_diff` (`external_label`/`emr_label`, `external`/`emr` with `qeh`/`clinic` fallback), dynamic N-point `trend_chart` (`inr|bp|ferritin|hba1c|ldl|tsh`), `imaging_studies[].full_report`.

---

## 5. Seam C (`Track 3 ↔ Track 4 & Track 2`)

- **Endpoints:**
  - `GET /api/v1/workspace/overview` → existing keys + `xray.llm_runtime` (alias `xray.model_runtime`), `xray.linter_summary`, always non-null `xray.latest_context_window` (`<4,096` tokens), `xray.emr_bridge`, per-case `compilation_telemetry`.
  - `POST /api/v1/workspace/cases/{case_id}/preview` → accepts `{instruction_text?, orders?, structured_orders?, actor_name?}`, returns preview + `parsed_orders` + `rx_safety_report`.
  - `POST /api/v1/workspace/cases/{case_id}/confirm` *(and alias `/commit`)* → runs real `rx_rules` CDS (HTTP 422 on hard block or un-overridden conflict), mints real encrypted MCHK PDF via `certificates.py` + `DocumentVault` on `/cert` (`omit_employer_diagnosis: true` default), writes compact additive EMR addendum (strictly excluding ephemeral private Sam chat via `assert_zero_sam_chat_leakage`), invokes `adapters.py` hooks, enqueues 10s Undo outbox event via `DurableWorkflowEngine.enqueue()`, and publishes patient-scoped `live_bus` events (`workspace.committed`, `emr.mutated`, `patient.care_plan_delivered`).
  - `POST /api/v1/workspace/cases/{case_id}/undo/{trace_id}` → cancels pending outbox event, retracts staged EMR addendum, cancels provisional bookings, restores `needs_decision`/`with_doctor`, publishes `workspace.undone`, `emr.mutated`, `patient.care_plan_retracted`.
  - `POST /api/v1/workspace/cases/{case_id}/ask` → unified `agent_harness.py` + `backend.agentic` private Sam Q&A (`ephemeral_private: true`, NEVER written to EMR).
  - `POST /api/v1/workspace/cases/{case_id}/compile` → on-demand 5-slot `<4,096`-token compiler + 1-retry linter self-repair helper (used by UI and decoupled `loopbench` HTTP eval suite).
  - `POST /api/v1/workspace/triage/simulate` → wired to `triage.screen_acute_message()` + `triage.extract_intake()` + `rx_rules` + 3-tier async triage.
  - `POST /api/v1/workspace/documents/ingest` → wired to `emr_connector.verify_three_point_id()` + `verification.verify_external_document_payload()` (fail-closed quarantine to Nurse Rachel on `<3/3` or multi-page ID collision).
  - `POST /api/v1/workspace/documents/{doc_id}/resolve` → Nurse Rachel quarantine resolution + `document.resolved` live bus event.
  - `GET /api/v1/graph/versions` & `POST /api/v1/graph/rollback` → Practice Graph version history & immutable rollback.
- **Seeding FK safety:** `seed_workspace_cases` idempotently ensures `pat_chan_warfarin`, `pat_wong_htn`, `pat_lee_ldl` exist in `patients` and `doc_locum_sarah` exists in `practitioners`.
- **Strict Repo Boundary:** `harness/backend/` NEVER imports `loopbench`. All 20 Founder Grading Eval cases live in `../loopbench/` calling `harness` strictly over FastAPI `TestClient` / HTTP and generating `docs/research/founder_grading_report.html` + `.md`.
