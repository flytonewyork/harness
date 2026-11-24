# Track 2 Spec — Clinical UI Design, UX & Canonical 14-Widget Catalog

*Version: `v1.1` · Branch: `feat/live-demo-convergence` · Locked to `docs/research/SEAM_CONTRACT.md`*
*Scope: Canonical 14-Widget Catalog (JSON props, empty/loading/overflow, `<1024px`), 8 `/slash` inline editors & bidirectional `normalizeOrder()`, thin/Cantonese/family-proxy intake UX, and hardened `R1`–`R5`+ semantic linter rules.*

---

## 1. Non-Negotiable Visual & Structural Invariants

1. **Warm Editorial Paper Light Mode Only:** `#F8F7F4` canvas ground, `#FFFFFF` paper cards, `#F3F1EC` subtle surface, `#E6E2D8` hairlines, `#1E252B` primary ink, `#16695D` botanical jade accent, `#B45309` amber safety cue, `#B91C1C` crimson hard alert.
2. **Typography Discipline:** Natural clinical sans (`Inter`, system sans, `PingFang HK`, `Noto Sans HK`) on all prose, labels, buttons, and `+` pills. **Zero coding/monospace fonts on buttons, `+` pills, or prose.** Tabular numerals (`font-variant-numeric: tabular-nums`) on numbers, vitals, lab values, scores, and dates only.
3. **Fixed Right-Rail Order:** `SafetyCard` → `PreventiveCard` → `ScheduledCard` → `TimelineCard` → `UnifiedThread`. `TimelineCard` never sits below `ManagementPlan`.
4. **Management Plan Discipline:** Named strictly **`Management Plan`** (never "Checkout"), starts **empty** on case open, populates idempotently from clicked `+` pills grouped by issue, exposes a **floating `/` overlay popover** with zero page-shift jank + full keyboard navigation, and commits via **`Confirm & Sign Plan`** with a 10-second Undo bar.
5. **Strict Feature Freeze:** Zero net-new personas or surfaces beyond the existing workspace channels (`# my-work`, `# clinic-team`, `@ Clinic Playbook`) and optional `Architecture X-Ray` drawer.

---

## 2. Canonical 8 HK GP Pathways + `default.thin_intake`

Deterministic canvas stacks (right rail always mounts the fixed 5 widgets):

| # | Pathway ID | Deterministic Canvas `widget_stack` |
| :--- | :--- | :--- |
| 1 | `ebm.pathway.post_discharge_anticoag` | `VitalsExamStrip`, `ClinicalScoreStrip`, `MedDiff`, `IssueCard`, `TrendGraph`, `ResultTable`, `ImagingPreview`, `SourceClip`, `ManagementPlan` |
| 2 | `ebm.pathway.acute_urti_g6pd` | `VitalsExamStrip`, `IssueCard`, `SourceClip`, `ManagementPlan` |
| 3 | `ebm.pathway.low_ferritin_normal_hb` | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `TrendGraph`, `SourceClip`, `ManagementPlan` |
| 4 | `ebm.pathway.chronic_htn_home_bp` | `VitalsExamStrip`, `IssueCard`, `TrendGraph`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 5 | `ebm.pathway.borderline_ldl` | `ClinicalScoreStrip`, `IssueCard`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 6 | `ebm.pathway.t2dm_hba1c` | `VitalsExamStrip`, `ClinicalScoreStrip`, `IssueCard`, `TrendGraph`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 7 | `ebm.pathway.thyroid_fatigue` | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `TrendGraph`, `SourceClip`, `ManagementPlan` |
| 8 | `ebm.pathway.acute_uti` *(alias `acute_uti_stewardship`)* | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| — | `default.thin_intake` | `IssueCard`, `ManagementPlan` |

---

## 3. Canonical 14-Widget Catalog (JSON Props, States & Responsive Rules)

### A. Case Canvas (Widgets 1–9)

| # | Widget | JSON Props (`case.summary.*`) | Empty / Loading State | Overflow Handling |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **`VitalsExamStrip`** | `vitals_exam: { source: string, items: Array<{ label: string, val: string }> }` | Omitted cleanly when `items` is empty. | `flex-wrap: wrap` with `gap: 10px 18px`; tabular numerals on values; wraps cleanly to 2 rows without horizontal scroll. |
| 2 | **`ClinicalScoreStrip`** | `clinical_scores: Array<{ id, label, value, band, detail, guideline, numeric_value?, unit?, safety_flag?, dosing_flags?, computed_by? }>` | Omitted when array empty. | `flex-wrap: wrap` cards (`flex: 1 1 210px`); `safety_flag` (e.g., `PHQ9_ITEM9_SELF_HARM`) renders a crimson badge. |
| 3 | **`MedDiff`** | `med_diff: { external_label?: string, emr_label?: string, rows: Array<{ drug: string, external?: string, emr?: string, qeh?: string, clinic?: string, tag: string }> }` | If `med_diff` mounted with 0 rows: calm muted line *"No active medication discrepancies detected."* | Wrapped in horizontal scroll container (`overflow-x: auto`) for narrow screens; column headers fall back `external_label → "External Record"` (or `"QEH Discharge"` when legacy `qeh` key used) and `emr_label → "Clinic EMR"`. |
| 4 | **`TrendGraph`** | `trend_chart: { kind: "inr"\|"bp"\|"ferritin"\|"hba1c"\|"ldl"\|"tsh", title, unit?, target_label?, target_min?, target_max?, target_value?, target_sys?, target_dia?, points: Array<{ label, v?, value?, sys?, dia? }> }` | If `points` empty: calm box *"Insufficient longitudinal readings to plot trend."* | Dynamic N-point X scaling (`1..N` points) and dynamic Y domain spanning both data and target band/threshold; never hardcodes 3 points; sparse X-axis labels thin cleanly when `N > 6`. |
| 5 | **`ResultTable`** | `lab_panel: { source: string, rows: Array<{ test: string, val: string, ref: string, flag?: "Normal"\|"LOW"\|"HIGH"\|string }> }` | Omitted when `lab_panel` absent; muted empty line if `rows` empty. | Responsive CSS grid (`repeat(auto-fill, minmax(150px, 1fr))`); `LOW`/`HIGH` cells highlight in warm amber. |
| 6 | **`ImagingPreview`** | `imaging_studies: Array<{ id, modality, date, summary, status, full_report?: string }>` | Omitted when array empty. | Modality-aware left icon/badge (`ECG` waveform, `CXR`, `US`, `Echo`, `Mammo`, `CT/MRI`); optional `full_report` toggles inline via calm ghost button (`Show full report` / `Hide full report`) with bounded scroll (`max-height: 220px`). |
| 7 | **`SourceClip`** | `attachments: Array<{ id, label, quote, verified_3pt?: boolean }>` | Omitted when array empty (no `case_key === "chan"` gate — mounts for all verified cases). | Quotes `> 220` chars collapse with `Expand quote` / `Collapse quote`; shows subtle jade `3-pt ID verified` pill when `verified_3pt` is true; zero raw PDF filenames or `"Linked source"` prefixes. |
| 8 | **`IssueCard`** | `issues: Array<{ id, name, bullets: string[], safety_gap?: string, suggested_orders: PlanOrder[] }>` plus `intake_sufficiency` & `proxy_context` | If `issues` empty: calm placeholder *"No discrete clinical issues staged yet."* | Zero essay headers; bullets wrap cleanly; optional amber `safety_gap` only for safety-critical gaps; `+` pills wrap across lines with `gap: 7px`. |
| 9 | **`ManagementPlan`** | Staged `plans[caseId]` (`PlanOrder[]`) + `activeCase.traces` + commit receipt + CDS 422 state | Always mounted at bottom of canvas. Empty dashed warm-paper slot *"No items staged yet. Click + on any clinical issue above or type / below to build today's plan."* | Grouped by issue; inline editors wrap gracefully; floating `/` popover positioned `bottom: calc(100% + 6px)` with `max-height: 290px; overflow-y: auto` (zero layout shift). |

### B. Persistent Right Rail (Widgets 10–14)

| # | Widget | JSON Props | Empty / Loading State | Overflow Handling |
| :--- | :--- | :--- | :--- | :--- |
| 10 | **`SafetyCard`** | `summary.alerts: Array<{ level: "high"\|"medium"\|"info", label, detail }>` | Calm muted state: *"No active drug allergies, G6PD flags, or high-risk medication conflicts on file."* | High alerts get crimson left-accented card; promoted above canvas on `<1024px`. |
| 11 | **`PreventiveCard`** | `summary.preventive_care: Array<{ id, title, badge, detail, order: PlanOrder }>` | Omitted when no eligible items (or calm *"Up to date"* note). | Vertical card stack with idempotent `+ Add to Plan` / `✓ In Management Plan` pills. |
| 12 | **`ScheduledCard`** | `summary.scheduled_tasks: Array<{ status: "booked"\|"pending", label }>` | Calm muted state: *"No upcoming visits or pending lab draws."* | Vertical list with subtle jade bullets (`•` booked, `◦` pending). |
| 13 | **`TimelineCard`** | `case.timeline: Array<{ date, label, trace_id? }>` + `case.traces` | Calm muted state: *"No case milestones logged yet."* | Vertical hairline timeline; folded `▸ N agent steps` disclosure expands inline without shifting canvas. |
| 14 | **`UnifiedThread`** | `summary.patient_chat_label`, `summary.original_chat`, `summary.sam_proactive_insight`, `summary.agent_thread` | Empty chat shows muted note; silent Sam shows *"No new objective flags from Sam. Ask below anytime."* | Bounded scroll container (`max-height: 380px; overflow-y: auto`); verbatim Cantonese auto-tagged `lang="zh-HK"`. |

### C. Responsive `<1024px` Narrow-Screen Rules

- `.ws-case-grid` collapses cleanly from 2-column (`1.72fr / 0.98fr`) to 1-column (`1fr`) at `max-width: 1024px`.
- **High-Alert Safety Promotion:** Any case with `alerts.some(a => a.level === "high")` renders a compact crimson high-alert `SafetyCard` banner at the very top of the main column on `≤1024px` (`.ws-narrow-safety-promote`, `display: none` on desktop `>1024px`), ensuring critical allergies, G6PD blocks, and anticoagulation warnings are never buried beneath the canvas.
- Topbar wraps gracefully with compact spacing; tables (`MedDiff`) scroll horizontally inside their card without causing page-wide horizontal overflow.

---

## 4. All 8 `/slash` Inline Editors & Bidirectional `normalizeOrder()`

### Bidirectional Normalizer (`normalizeOrder`)

Per `SEAM_CONTRACT.md` §2, `ClinicWorkspaceApp.tsx` normalizes every incoming/outgoing order through `normalizeOrder()`, accepting either canonical `snake_case` or legacy `camelCase` and preserving both read aliases so all 8 pathways and legacy fixtures interoperate seamlessly:

| Primitive | `/` Cmd | Inline Editor Controls (Calm Light Mode) | Canonical Fields (`snake_case` ↔ `camelCase`) |
| :--- | :--- | :--- | :--- |
| `rx` | `/prescribe` | Verb selector (`Start\|Update\|Stop\|Continue`), drug input, frequency input, duration (`days`, hidden on `stop`), note display | `action`, `drug`, `dose_mg`/`doseMg`, `dose_mcg`/`doseMcg`, `freq` (`frequency`), `days`, `note` |
| `cert` | `/cert` | Quick day pills (`1d`–`7d`) + bounded numeric input (`1..7`), `start_date` date picker (auto-formats range), `Omit employer diagnosis` checkbox (default `true`) | `days_off`/`daysOff`, `start_date`/`startDate`, `end_date`, `dates`, `omit_employer_diagnosis`/`omitDx`, `diagnosis_icd10`, `diagnosis_icpc2` |
| `lab` | `/order` | Test name input, partner lab input, timing (`when`) input, `8h Fasting` checkbox | `test_name`/`testName`, `loinc_code`/`loincCode`, `lab`, `fasting`, `when` |
| `imaging` | `/imaging` | Modality input, anatomical region input, clinical indication input, urgency select (`routine\|soon\|urgent`) | `modality`, `region`, `indication`, `urgency` (`priority`) |
| `refer` | `/refer` | Specialty input, urgency select (`routine\|soon\|urgent`), clinical question input, `Attach labs & summary` checkbox (default `true`) | `specialty`, `urgency` (`priority`), `clinical_question` (`reason`), `attach_labs`/`attachLabs` |
| `nurse` | `/nurse` | Closed 6-protocol dropdown (`medication_adherence_check \| vaccine_session \| bp_inr_followup_call \| screening_kit_dispatch \| booking_followup_call \| pre_visit_questionnaire`), assignee input, language select (`en\|zh-HK\|bilingual`), task detail input, due input | `protocol`, `assignee`, `language`, `task`, `due` |
| `book` | `/book` | Visit type input/suggestions, timeframe (`when`), duration select (`10\|15\|20\|30 min`), optional note | `visit_type`/`visitType`, `when`, `duration_min`/`durationMin`, `cvx_code`/`cvxCode`, `note` |
| `goal` | `/goal` | Metric switcher (`Blood Pressure` slider `120–145` mmHg live-syncing `TrendGraph` OR non-BP `HbA1c` / `LDL` / `TSH` numeric target + unit OR clinic-approved patient leaflet selector) | `metric`, `target_sys`/`targetSys`, `target_dia`/`targetDia`, `target_value`/`targetValue`, `target_text`, `unit`, `leaflet_id`/`leafletId` |

### Floating `/` Popover Keyboard Navigation & Structured Commit

- **Keyboard Controls:** `ArrowDown` / `ArrowUp` cycle highlighted command (`aria-selected`), `Enter` stages the highlighted order and resets input, `Escape` closes the popover without moving scroll or shifting page layout.
- **Structured Commit (`POST /api/v1/workspace/cases/{case_id}/confirm` with 404 fallback to `/commit`):**
  - Sends `{ instruction_text, orders, structured_orders, actor_name, mchk_reg_no, override_reason }`.
  - **HTTP 422 CDS Handling:**
    - Hard blocks (G6PD contraindication, severe drug allergy, renal block) display a crimson CDS hard-block banner explaining why signing is blocked (no override bypass).
    - Overridable conflicts (e.g., duplicate class, mild interaction) display an amber CDS review card with an inline `Override rationale` field and `Confirm with Rationale` button.
  - **Signed Receipt:** Displays MCHK encrypted PDF certificate badge + verification hash (`sha256` prefix) + `Employer Dx omitted` status, additive EMR addendum confirmation, and the 10-second Undo bar.

---

## 5. Thin Intake, Verbatim Cantonese & Family-Proxy UX

1. **Thin Intake (`intake_sufficiency === "insufficient"`):**
   - Renders a calm amber `safety_gap` callout warning that clinical history is insufficient for safe prescribing or certification.
   - Provides a 1-click **`+ Send pre-visit questionnaire via Sam`** pill that stages a structured `nurse` order (`protocol: "pre_visit_questionnaire"`, `language: "bilingual"`).
2. **Verbatim HK Cantonese (`UnifiedThread`):**
   - Any patient/Careline turn containing CJK characters (`/[\u4e00-\u9fff\u3400-\u4dbf]/`) automatically receives `lang="zh-HK"` and a subtle `廣東話` metadata tag, rendered verbatim in `PingFang HK` / `Noto Sans HK` without silent paraphrasing.
3. **Family-Proxy Strip (`proxy_context`):**
   - Renders a single calm 1-line strip below the case header displaying relationship & speaker name (e.g., *Family proxy · Daughter (Ms. Chan)*), consent note, and a 3-point ID pill (`3-Pt ID Verified` in jade vs `3-Pt ID Pending` in amber).

---

## 6. Hardened Semantic Presentation Linter Rules (`R1`–`R5`+, Zero Word-Count Caps)

Enforced in `backend/acute/practice_graph.py::validate_case_presentation` and surfaced live in `Architecture X-Ray`. Every violation emits BOTH rich keys (`rule_id`, `sub_rule`, `severity`, `field_path`, `message`, `repair_hint`) and legacy keys (`rule`, `issue`, `detail`). **Zero brittle word-count caps are used.**

| Rule ID | Hardened Semantic Sub-Rules |
| :--- | :--- |
| **`R1_NO_WIDGET_DUPLICATION`** | Rejects issue prose that repeats ≥ 2 numeric points/lab values already plotted in `TrendGraph` or `ResultTable`, or narrates `MedDiff` dose/switch rows verbatim when `MedDiff` is mounted. |
| **`R2_ATOMIC_ISSUES_AND_ORDERS`** | Rejects bundled drugs (`+`, ` and `) inside a single `rx` order, mixed clinical domains in a single `IssueCard` across all 8 pathways (e.g., Anticoagulation + Lipid, URTI + Iron, DM + Thyroid), and duplicate active drug orders across issues (while explicitly permitting atomic `stop` + `start` switches such as Simvastatin → Atorvastatin). |
| **`R3_NO_SLOP_OR_SCAFFOLDING`** | Rejects SOAP/essay headers (`History:`, `Clinical reasoning:`, `Assessment:`), canned slop phrases, forbidden clinician titles on Careline AI (`Doctor`, `Resident`, `Clinician`), premature `rx`/`cert` suggestions when `intake_sufficiency === "insufficient"`, and ungrounded/fabricated source quotes. |
| **`R4_CANONICAL_ACTION_ONTOLOGY`** | Validates every order in BOTH `issues[].suggested_orders` AND `preventive_care[].order` against the 8 canonical primitives, atomic `rx` verbs (`start\|update\|stop\|continue`), `cert` `days_off` bounds (`1..7`), and closed 6-protocol `nurse` menu. |
| **`R5_HIGH_SIGNAL_SAM_GATE`** | Requires an objective clinical score, guideline threshold, or safety trigger whenever `sam_proactive_insight` is non-empty, and rejects meta-UI/chatty narration (`Pulled his`, `Spotted`, `Drag the`, `Kept him`, `Checkout`). |

---

## 7. Live Event Stream, Nurse Quarantine Resolution & X-Ray Telemetry

- **Live SSE (`/api/v1/live/stream`):** Subscribes via `EventSource` to refresh workspace state on `workspace.committed`, `workspace.undone`, `emr.mutated`, `document.resolved`, and `case.updated`; fails silently on network disconnect without interrupting clinical flow.
- **Nurse Rachel 1-Click Quarantine Resolution:** In `# clinic-team`, `quarantined_nurse` items expose 1-click **`Confirm Match & Release`** and **`Reject & Hold`** actions posting to `POST /api/v1/workspace/documents/{doc_id}/resolve`.
- **Architecture X-Ray Drawer:** Displays live `llm_runtime` (provider, model, region, live/deterministic mode), `linter_summary` (fleet pass rate + active-case violations), and `latest_context_window` token budget meter enforcing `< 4,096` tokens.
