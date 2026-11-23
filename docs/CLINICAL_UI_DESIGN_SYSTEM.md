# Clinical UI Design System & Deterministic Presentation Compiler

*Version: `v1.0` · Last updated: 23 Nov 2026*
*Internal specification governing every dynamic clinical surface, widget binding, prose slot, and anti-slop linter check across `harness`.*

---

## 1. Core Architectural Principle: Compiler, Not Free-Text Prompt

Dynamic clinical UIs fail in production when an LLM is asked to invent layout, summarise numbers already visible in charts, and write free-form clinical actions. Our presentation layer is engineered as a **4-Stage Deterministic Presentation Compiler**:

```text
1. Practice Graph Traversal   →  Deterministically selects active issues & fixed Widget Stack
2. Typed Slot Schema          →  LLM fills bounded clinical slots only (never layout, never raw actions)
3. Closed Action Ontology     →  Every + pill & /command instantiates 1 of 8 canonical order primitives
4. Presentation Linter (Gate) →  Automated validator rejects slop before render (runtime + CI)
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
| Safety cue (Amber) | `#B45309` / `#FEF7EC` | Safety-critical unconfirmed gaps, borderline/low labs |
| Hard alert (Crimson) | `#B91C1C` / `#FEF2F2` | Allergies, G6PD blocks, high-risk medication conflicts |

### Typography & Control Discipline
- **Natural clinical sans (`Inter`, system sans)** for all body text, headings, labels, navigation, and **all `+` action pills & buttons**.
- **Zero coding/monospace fonts on buttons or prose.** Tabular numerals (`font-variant-numeric: tabular-nums`) are used cleanly for lab values, vitals, scores, and dates.
- **Calm `+` Action Pills:** Soft light-stone surface (`#F7F5F0` bg, `#D8D3C8` border, `#1E252B` natural sans text). Toggling adds the item idempotently to **`Management Plan`** and shifts the pill to soft botanical jade (`#EBF5F2` bg, `#9ECAC0` border, `#115349` text, `✓` prefix) — never harsh black monospace chips.

---

## 3. Canonical Clinical Widget Catalog (14 Finite Primitives)

Every pathway node in `backend/acute/practice_graph.py` maps deterministically to an ordered subset of these **14 canonical widgets** (9 Canvas + 5 Right Rail). The model never invents custom widget shapes.

### Case Canvas (9 Primitives)
| # | Widget ID | Trigger Condition | Deterministic Display Rule |
| :--- | :--- | :--- | :--- |
| 1 | `VitalsExamStrip` | Clinic/home vitals or discharge exam findings present | Single compact horizontal strip (`BP`, `HR`, `Temp`, `SpO₂`, `BMI`, key exam note). |
| 2 | `ClinicalScoreStrip` | Validated clinical score applies (`CHA₂DS₂-VASc`, `HAS-BLED`, `HK CVD 10-yr`, `CURB-65`) | **Computed deterministically in Python** (never LLM-estimated). Shows score, risk band, and guideline ref. |
| 3 | `MedDiff` | External discharge/specialist meds ≠ EMR active meds | 2-column structured comparison (`External` vs `Clinic EMR`) tagging `Dose change`, `Switched`, `Duplicate`. |
| 4 | `TrendGraph` | ≥ 2 longitudinal numeric points (`INR`, `BP`, `Ferritin`, `HbA1c`, `LDL`) | Light-mode line/spark chart with target reference band. **Prose must not narrate these numbers.** |
| 5 | `ResultTable` | Linked lab report or relevant baseline panel | Tabular/grid panel of abnormal + clinically paired markers only (e.g., `Ferritin + Hb`, `INR + eGFR`). |
| 6 | `ImagingPreview` | Linked ECG, X-ray, ultrasound, or echo report | Compact study preview card (`Modality · Date`, key finding, expandable full report/visual frame). |
| 7 | `SourceClip` | Verified 3-pt ID discharge PDF, lab report, or referral | Clean attachment link + verbatim quote (expandable). Zero raw PDF filenames or `"Linked source"` prefixes. |
| 8 | `IssueCard` | One card per atomic clinical problem | Clean title → crisp factual bullets (no `"History / Clinical reasoning / Assessment"` headers) → optional minimal inline amber cue **only when safety-critical** → calm natural `+` pills. |
| 9 | `ManagementPlan` | Always anchored at bottom of main canvas | Named strictly **`Management Plan`** (not "Checkout"). **Starts empty**; populates idempotently as doctor clicks `+` pills grouped by Issue; `/` opens a **floating overlay popover** (zero page-shift jank, 2–3 placeholder hints); commits via **`Confirm & Sign Plan`** + quiet 10s Undo. |

### Persistent Right Rail (5 Primitives)
| # | Widget ID | Position | Deterministic Display Rule |
| :--- | :--- | :--- | :--- |
| 10 | `SafetyCard` | Pinned top-right | Hard safety alerts only: allergies, G6PD, high-risk drug conflicts. |
| 11 | `PreventiveCard` | Right rail · upper-mid | Always-on age/sex/risk/corporate-benefit GP prevention plan with calm `+` pills (today or deferred). |
| 12 | `ScheduledCard` | Right rail · middle | Booked appointments, pending lab draws, and active automated follow-up loops. |
| 13 | `TimelineCard` | Right rail · lower-mid | Compact chronological case milestones + folded `▸ N agent steps` execution traces (never stranded below Management Plan). |
| 14 | `UnifiedThread` | Right rail · bottom | Single scrollable thread: **Patient ↔ Sam Careline history** above → subtle **`Private · Doctor & Sam only`** divider → **Sam's unread note** (strict high-signal gate) + ephemeral reply box underneath. |

---

## 4. Clinical Writing, Uncertainty & Sam High-Signal Rules

### Prose & Epistemic Discipline
- **One Problem = One `IssueCard`:** Never mash unrelated conditions or drug classes (e.g., AFib anticoagulation vs. Statin lipid switching) into the same issue card or prose sentence.
- **Zero Widget-Prose Duplication ("Show, Don't Narrate"):** Once a lab series, BP trajectory, or medication diff is mounted in a widget, prose may reference the clinical takeaway without re-listing the raw numeric table or timeline.
- **No Essay Subheadings:** Never print `"History:"`, `"Clinical reasoning:"`, `"Assessment:"`, or SOAP headers inside `IssueCard`.
- **Minimal Epistemic Uncertainty:** Do not clutter bullets with provenance tags or routine confirmation checklists. Highlight an unconfirmed clinical gap inline with a **subtle amber cue only when safety-critical** (e.g., *unconfirmed home Warfarin tablet strength*, *old Simvastatin still at home*, or *insufficient intake before prescribing*).

### Strict High-Signal Gate for Sam (Unified Right-Rail Thread)
- Sam's proactive unread message beneath the `Private · Doctor & Sam only` divider fires **only** when at least one objective trigger holds:
  1. A **deterministic clinical score** (`CHA₂DS₂-VASc`, `HAS-BLED`, `HK CVD 10-yr`, `CURB-65`) materially guides today's decision;
  2. A **verified guideline threshold / safety trigger** applies (e.g., BSG non-anaemic iron deficiency `< 15 ng/mL` + active G6PD/allergy blocks);
  3. A **clinically significant lab/vital anomaly** requires doctor awareness.
- **Otherwise Sam stays quietly silent** until the doctor types in the private reply box. Conversational meta-narration (*"Pulled his Sep INR..."*, *"Drag the slider..."*, *"Kept him in Scheduled..."*) is strictly forbidden.

---

## 5. Closed 8-Primitive Clinical Action Ontology

Every `+` pill and `/` order must instantiate one of **8 standardised primitives** with atomic clinical verbs — never free-form LLM slop (e.g., *"Reconcile EMR active list, confirm home pillbox in Cantonese"*):

| Primitive | `/` Command | Required Clinical Verb / Shape | Anti-Slop Rule |
| :--- | :--- | :--- | :--- |
| Medication | `/prescribe` | `Start` / `Update` / `Stop` / `Continue` + **single drug**, dose, freq, duration | Never bundle ≥ 2 drugs into one `rx` line |
| Lab Order | `/order` | Standardised test name + partner lab + timing + fasting flag | Exact catalog test name |
| Imaging | `/imaging` | Modality + anatomical region + indication + urgency | Structured imaging request |
| Certificate | `/cert` | Days off + date range + `omit_employer_diagnosis` (MCHK default) | Pre-structured cert card |
| Referral | `/refer` | Specialty + urgency + clinical question | Structured referral card |
| Nurse Protocol | `/nurse` | Closed clinic protocol menu (`Medication adherence check`, `Vaccine session`, `BP/INR follow-up call`, `Screening kit dispatch`) | Never free-form nurse prose |
| Booking | `/book` | Visit type (`In-clinic review`, `Teleconsult`, `Nurse slot`, `Fasting blood draw`) + timeframe | Actionable calendar slot |
| Goal / Guidance | `/goal` | Quantified target (`BP < 135/85`) or clinic-approved patient leaflet | Deterministic slider or approved sheet |

---

## 6. Automated Clinical Presentation Linter (`validate_case_presentation`)

Enforced in `backend/acute/practice_graph.py` and verified by pytest on every seeded and dynamically generated case:

1. **`R1_NO_WIDGET_DUPLICATION`**: Fails if issue prose repeats ≥ 2 numeric trend points or lab values already displayed in mounted `TrendGraph` / `ResultTable`.
2. **`R2_ATOMIC_ISSUES_AND_ORDERS`**: Fails if a single `rx` order bundles multiple drugs (`+`, ` and `) or if an `IssueCard` title mixes unrelated clinical domains (e.g., `Anticoagulation & Lipid`).
3. **`R3_NO_SLOP_OR_SCAFFOLDING`**: Fails if prose contains forbidden headers (`History:`, `Clinical reasoning:`, `Assessment:`) or slop phrases (`Reconcile EMR active list`, `High-risk medication discrepancy surfaced`, `Pulled his`, `Drag the`, `Checkout`).
4. **`R4_CANONICAL_ACTION_ONTOLOGY`**: Fails if any suggested order lacks a valid canonical `type` (`rx | lab | imaging | cert | refer | nurse | book | goal`) or uses non-atomic drug actions (`action` must be `start | update | stop | continue` on `rx`).
5. **`R5_HIGH_SIGNAL_SAM_GATE`**: Fails if `sam_proactive_insight` is non-empty without an objective guideline/score signal or contains meta-UI/chatty narration (`Pulled`, `Spotted`, `Drag`, `Kept him`, `Checkout`).
