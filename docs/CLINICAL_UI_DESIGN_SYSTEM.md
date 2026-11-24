# Clinical UI Design System & Deterministic Presentation Compiler

*Version: `v1.1` · Last updated: 24 Nov 2026*
*Internal specification governing every dynamic clinical surface, widget binding, prose slot, inline order primitive, and anti-slop linter check across `harness` (aligned with `docs/research/SEAM_CONTRACT.md`).*

---

## 1. Core Architectural Principle: Compiler, Not Free-Text Prompt

Dynamic clinical UIs fail in production when an LLM is asked to invent layout, summarise numbers already visible in charts, and write free-form clinical actions. Our presentation layer is engineered as a **4-Stage Deterministic Presentation Compiler**:

```text
1. Practice Graph Traversal   →  Deterministically selects active issues & fixed Widget Stack
2. Typed Slot Schema          →  LLM fills bounded clinical slots only (<4,096 token budget, never layout)
3. Closed Action Ontology     →  Every + pill & /command instantiates 1 of 8 canonical order primitives
4. Presentation Linter (Gate) →  Automated semantic validator (R1–R5+, zero word-count caps) before render
```

---

## 2. Visual System: Warm Editorial Paper (Light Mode Only)

The clinical workspace renders strictly in **calm light mode** inspired by editorial paper — never dark developer dashboards or coding-font chrome.

### Color Tokens
| Token | Hex | Clinical Role |
| :--- | :--- | :--- |
| Canvas ground | `#F8F7F4` | Warm off-white workspace background |
| Card surface | `#FFFFFF` | Elevated clinical paper cards |
| Subtle surface | `#F3F1EC` | Sidebar, table headers, quiet secondary pills |
| Hairline border | `#E6E2D8` | Primary card & section dividers (`1px solid`) |
| Strong hairline | `#D4CEC2` | Hover borders, inputs, active plan container |
| Primary ink | `#1E252B` | Headings, clinical facts, primary text |
| Secondary ink | `#4C555E` | Supporting details, timestamps, labels |
| Muted ink | `#767E87` | Reference ranges, quiet metadata |
| Clinical accent (Jade) | `#16695D` / `#EBF5F2` | Selected `+` action pills (`✓ Added`), target bands, verified badges |
| Safety cue (Amber) | `#B45309` / `#FEF7EC` | Safety-critical unconfirmed gaps, thin-intake warnings, borderline/low labs, overridable CDS |
| Hard alert (Crimson) | `#B91C1C` / `#FEF2F2` | Allergies, G6PD blocks, high-risk medication conflicts, non-overridable CDS hard blocks |

### Typography & Control Discipline
- **Natural clinical sans (`Inter`, system sans, `PingFang HK`, `Noto Sans HK`)** for all body text, headings, labels, navigation, and **all `+` action pills & buttons**.
- **Zero coding/monospace fonts on buttons, `+` pills, or prose.** Tabular numerals (`font-variant-numeric: tabular-nums`) are used cleanly for lab values, vitals, scores, and dates only.
- **Calm `+` Action Pills:** Soft light-stone surface (`#F7F5F0` bg, `#D4CEC2` border, `#1E252B` natural sans text). Toggling adds the item idempotently to **`Management Plan`** and shifts the pill to soft botanical jade (`#EBF5F2` bg, `#B3D7CE` border, `#115349` text, `✓` prefix) — never harsh black monospace chips.

---

## 3. Canonical 8 HK GP Pathways + `default.thin_intake` (Seam A ↔ Seam B)

Every pathway node in `backend/acute/practice_graph.py` maps deterministically to an ordered subset of the **9 Canvas widgets**, while the **5 Right-Rail widgets** always mount in fixed order (`SafetyCard → PreventiveCard → ScheduledCard → TimelineCard → UnifiedThread`):

| # | Canonical `GraphNode.id` | Accepted Alias | Deterministic Canvas `widget_stack` |
| :--- | :--- | :--- | :--- |
| 1 | `ebm.pathway.post_discharge_anticoag` | — | `VitalsExamStrip`, `ClinicalScoreStrip`, `MedDiff`, `IssueCard`, `TrendGraph`, `ResultTable`, `ImagingPreview`, `SourceClip`, `ManagementPlan` |
| 2 | `ebm.pathway.acute_urti_g6pd` | — | `VitalsExamStrip`, `IssueCard`, `SourceClip`, `ManagementPlan` |
| 3 | `ebm.pathway.low_ferritin_normal_hb` | — | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `TrendGraph`, `SourceClip`, `ManagementPlan` |
| 4 | `ebm.pathway.chronic_htn_home_bp` | — | `VitalsExamStrip`, `IssueCard`, `TrendGraph`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 5 | `ebm.pathway.borderline_ldl` | — | `ClinicalScoreStrip`, `IssueCard`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 6 | `ebm.pathway.t2dm_hba1c` | — | `VitalsExamStrip`, `ClinicalScoreStrip`, `IssueCard`, `TrendGraph`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| 7 | `ebm.pathway.thyroid_fatigue` | — | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `TrendGraph`, `SourceClip`, `ManagementPlan` |
| 8 | `ebm.pathway.acute_uti` | `ebm.pathway.acute_uti_stewardship` | `VitalsExamStrip`, `IssueCard`, `ResultTable`, `SourceClip`, `ManagementPlan` |
| — | `default.thin_intake` | — | `IssueCard`, `ManagementPlan` |

---

## 4. Canonical Clinical Widget Catalog (14 Finite Primitives)

The model never invents custom widget shapes. Detailed JSON props, empty/overflow states, and responsive breakpoints are catalogued in `docs/research/track2_design_ux_widget_catalog.md`.

### Case Canvas (9 Primitives)
| # | Widget ID | Trigger Condition | Deterministic Display Rule |
| :--- | :--- | :--- | :--- |
| 1 | `VitalsExamStrip` | Clinic/home vitals or discharge exam findings present | Single compact horizontal strip (`BP`, `HR`, `Temp`, `SpO₂`, `BMI`, key exam note). Wraps cleanly on narrow screens. |
| 2 | `ClinicalScoreStrip` | Validated clinical score applies (`CHA₂DS₂-VASc`, `HAS-BLED`, `HK CVD 10-yr`, `CURB-65`, `PHQ-9`, `eGFR/CrCl`) | **Computed deterministically in Python** (never LLM-estimated). Shows score, risk band, guideline ref, and crimson `safety_flag` cue when triggered (e.g., `PHQ9_ITEM9_SELF_HARM`). |
| 3 | `MedDiff` | External discharge/specialist meds ≠ EMR active meds | Generalized 2-column comparison using `external_label` / `emr_label` and row keys `external` / `emr` (with backward-compatible `qeh` / `clinic` fallback), tagging `Dose reduced`, `Switched`, `Duplicate`. |
| 4 | `TrendGraph` | ≥ 2 longitudinal numeric points (`inr`, `bp`, `ferritin`, `hba1c`, `ldl`, `tsh`) | Light-mode SVG line chart with dynamic N-point horizontal scaling, auto-fitted Y domain, and target band/threshold. **Prose must not narrate these numbers.** |
| 5 | `ResultTable` | Linked lab report or relevant baseline panel | Tabular/grid panel of abnormal + clinically paired markers only (e.g., `Ferritin + Hb`, `INR + eGFR`, `HbA1c + eGFR`, `TSH + Free T4`). |
| 6 | `ImagingPreview` | Linked ECG, X-ray, ultrasound, mammogram, or echo report | Modality-aware badge/thumbnail (`Modality · Date`, key finding) + inline expandable `full_report` when present. |
| 7 | `SourceClip` | Verified 3-pt ID discharge PDF, lab report, or referral | Generalized across **all cases** (zero hardcoded patient gates). Clean attachment label, subtle `3-pt ID verified` jade pill, expandable verbatim quote. Zero raw PDF filenames or `"Linked source"` prefixes. |
| 8 | `IssueCard` | One card per atomic clinical problem | Clean title → crisp factual bullets (zero `"History / Clinical reasoning / Assessment"` headers) → optional minimal inline amber `safety_gap` **only when safety-critical** → calm natural `+` pills. |
| 9 | `ManagementPlan` | Always anchored at bottom of main canvas | Named strictly **`Management Plan`** (not "Checkout"). **Starts empty**; builds idempotently from `+` pills grouped by Issue; floating `/` overlay popover with keyboard navigation (`↑`/`↓`/`Enter`/`Esc`, zero layout-shift jank); calm light-mode inline editors for all 8 order primitives; commits via **`Confirm & Sign Plan`** (`POST /api/v1/workspace/cases/{case_id}/confirm`) with HTTP 422 CDS hard-block / override-rationale feedback, MCHK PDF verification hash badge, additive EMR addendum confirmation, and 10s Undo. |

### Persistent Right Rail (5 Primitives)
| # | Widget ID | Position | Deterministic Display Rule |
| :--- | :--- | :--- | :--- |
| 10 | `SafetyCard` | Pinned top-right *(promoted above canvas on `<1024px` when high-alert)* | Hard safety alerts only: allergies, G6PD, high-risk drug conflicts. Calm empty state when clear. |
| 11 | `PreventiveCard` | Right rail · upper-mid | Always-on age/sex/risk/corporate-benefit GP prevention plan with calm `+` pills (today or deferred). |
| 12 | `ScheduledCard` | Right rail · middle | Booked appointments, pending lab draws, and active automated follow-up loops. Calm empty state when none. |
| 13 | `TimelineCard` | Right rail · lower-mid | Compact chronological case milestones + folded `▸ N agent steps` execution traces (never stranded below Management Plan). |
| 14 | `UnifiedThread` | Right rail · bottom | Single scrollable thread: **Patient ↔ Sam Careline history** (with verbatim HK Cantonese `lang="zh-HK"` auto-detection) → subtle **`Private · Doctor & Sam only`** divider → **Sam's unread note** (strict high-signal gate) + ephemeral private reply box (never written to EMR). |

---

## 5. Thin Intake, Cantonese & Family-Proxy UX Discipline

- **Thin Intake (`intake_sufficiency === "insufficient"`):** Displays a calm amber `safety_gap` warning against premature prescribing or certification plus a 1-click **`+ Send pre-visit questionnaire via Sam`** pill staging a structured `nurse` order (`protocol: "pre_visit_questionnaire"`).
- **Verbatim HK Cantonese (`UnifiedThread`):** Any turn containing CJK characters automatically sets `lang="zh-HK"` with a quiet `廣東話` tag and renders verbatim in `PingFang HK` / `Noto Sans HK` so clinical nuance is never lost to paraphrase.
- **Family-Proxy Context (`proxy_context`):** Renders a single calm 1-line strip below the case title displaying proxy relationship/name, consent context, and a 3-point ID status pill (`3-Pt ID Verified` in jade or `3-Pt ID Pending` in amber).

---

## 6. Clinical Writing, Uncertainty & Sam High-Signal Rules

### Prose & Epistemic Discipline
- **One Problem = One `IssueCard`:** Never mash unrelated conditions or drug classes across any of the 8 pathways (e.g., AFib anticoagulation vs. Statin lipid switching, URTI vs. Iron deficiency, T2DM vs. Thyroid).
- **Zero Widget-Prose Duplication ("Show, Don't Narrate"):** Once a lab series, BP trajectory, or medication diff is mounted in a widget, prose may state the clinical takeaway without re-listing raw numeric values or copying `MedDiff` rows.
- **No Essay Subheadings:** Never print `"History:"`, `"Clinical reasoning:"`, `"Assessment:"`, or SOAP headers inside `IssueCard`.
- **Minimal Epistemic Uncertainty:** Highlight an unconfirmed clinical gap inline with a **subtle amber cue only when safety-critical** (e.g., *unconfirmed home Warfarin tablet strength*, *old Simvastatin still at home*, or *insufficient intake before prescribing*).

### Strict High-Signal Gate for Sam (Unified Right-Rail Thread)
- Sam's proactive unread message beneath the `Private · Doctor & Sam only` divider fires **only** when at least one objective trigger holds:
  1. A **deterministic clinical score** (`CHA₂DS₂-VASc`, `HAS-BLED`, `HK CVD 10-yr`, `CURB-65`, `PHQ-9`, `eGFR/CrCl`) materially guides today's decision;
  2. A **verified guideline threshold / safety trigger** applies (e.g., BSG non-anaemic iron deficiency `< 15 ng/mL`, active G6PD/allergy blocks, renal dosing bounds);
  3. A **clinically significant lab/vital anomaly** requires doctor awareness.
- **Otherwise Sam stays quietly silent** until the doctor types in the private reply box. Conversational meta-narration (*"Pulled his Sep INR..."*, *"Drag the slider..."*, *"Kept him in Scheduled..."*) is strictly forbidden.

---

## 7. Closed 8-Primitive Clinical Action Ontology & Inline Editors

Every `+` pill and `/` order instantiates one of **8 standardised primitives** normalized bidirectionally (`snake_case` ↔ `camelCase`) via `normalize_order_primitive` (backend) and `normalizeOrder` (frontend) per `SEAM_CONTRACT.md` §2:

| Primitive | `/` Command | Inline Editor Fields & Clinical Shape | Anti-Slop Rule |
| :--- | :--- | :--- | :--- |
| Medication (`rx`) | `/prescribe` | `action` (`start\|update\|stop\|continue`) + **single drug**, `freq`, `days`, `note` | Never bundle ≥ 2 drugs into one `rx` line |
| Certificate (`cert`) | `/cert` | `days_off` (`1..7` bounds), `start_date`, `dates`, `omit_employer_diagnosis` (MCHK default `true`) | Pre-structured cert card; strict 1–7d bounds |
| Lab Order (`lab`) | `/order` | `test_name`, `lab`, `when`, `fasting` (`bool`), optional `loinc_code` | Exact catalog test name |
| Imaging (`imaging`) | `/imaging` | `modality`, `region`, `indication`, `urgency` (`routine\|soon\|urgent`) | Structured imaging request |
| Referral (`refer`) | `/refer` | `specialty`, `urgency` (`routine\|soon\|urgent`), `clinical_question`, `attach_labs` (default `true`) | Structured referral card |
| Nurse Protocol (`nurse`) | `/nurse` | Closed 6-protocol dropdown (`medication_adherence_check \| vaccine_session \| bp_inr_followup_call \| screening_kit_dispatch \| booking_followup_call \| pre_visit_questionnaire`), `assignee`, `language` (`en\|zh-HK\|bilingual`), `task`, `due` | Never free-form nurse prose |
| Booking (`book`) | `/book` | `visit_type`, `when`, `duration_min`, optional `cvx_code` & `note` | Actionable calendar slot |
| Goal / Guidance (`goal`) | `/goal` | Quantified target (`Blood Pressure` slider live-syncing `TrendGraph`, or non-BP `HbA1c` / `LDL` / `TSH` target value + unit, or clinic-approved `leaflet_id`) | Deterministic slider or approved sheet |

---

## 8. Hardened Semantic Presentation Linter (`validate_case_presentation`)

Enforced in `backend/acute/practice_graph.py`, surfaced live in `Architecture X-Ray`, and verified by pytest on every seeded and dynamically generated case. Every violation includes both rich keys (`rule_id`, `sub_rule`, `severity`, `field_path`, `message`, `repair_hint`) and legacy keys (`rule`, `issue`, `detail`). **Zero brittle word-count caps.**

1. **`R1_NO_WIDGET_DUPLICATION`**: Fails if issue prose repeats ≥ 2 numeric trend points or lab values already displayed in mounted `TrendGraph` / `ResultTable`, or narrates `MedDiff` entries already shown in `MedDiff`.
2. **`R2_ATOMIC_ISSUES_AND_ORDERS`**: Fails if a single `rx` order bundles multiple drugs (`+`, ` and `), if an `IssueCard` title mixes unrelated clinical domains across any of the 8 pathways, or if duplicate active drugs appear across issues (explicitly permitting atomic `stop` + `start` switches).
3. **`R3_NO_SLOP_OR_SCAFFOLDING`**: Fails if prose contains essay headers (`History:`, `Clinical reasoning:`, `Assessment:`), slop phrases, forbidden clinician titles on Careline AI (`Resident`, `Doctor`, `Clinician`), premature `rx`/`cert` suggestions under `intake_sufficiency === "insufficient"`, or fabricated source quotes.
4. **`R4_CANONICAL_ACTION_ONTOLOGY`**: Validates every order in BOTH `issues[].suggested_orders` AND `preventive_care[].order` against the 8 canonical types, atomic `rx` verbs (`start | update | stop | continue`), `cert` `days_off` bounds (`1..7`), and closed 6-protocol `nurse` menu.
5. **`R5_HIGH_SIGNAL_SAM_GATE`**: Fails if `sam_proactive_insight` is non-empty without an objective guideline/score/safety signal or contains meta-UI/chatty narration (`Pulled`, `Spotted`, `Drag`, `Kept him`, `Checkout`).

