# Product Decisions · Clinical Execution Layer & Live Demo Freeze

*Last updated: 23 Nov 2026 · Branch `feat/live-emr-agent-phone-demo` (PR #65)*
*Canonical ~1-page lock: stop adding speculative features; hone Tracks 1–4 into a rock-solid, real-model working live demo.*

---

## 1. Operating Mandate: Feature Freeze → Hone the Working Live Demo
- **Stop building net-new product features or personas.** Everything needed for the CEO + Head-of-AI demo is scoped.
- All engineering & research now converges on **honing the existing live demo** (`/workspace`, `/emr`, `/p/qr/pat_lam_urti_demo`) so real model calls, the Knowledge Graph, the 14-widget Presentation Compiler, additive EMR writeback, and a founder-gradable eval suite work reliably ~100% of the time.

---

## 2. Locked Decisions (Product, Architecture, UX & Safety)
- **Execution Layer + Sole EMR SoT:** Clinic EMR stays the **sole legal/clinical Source of Truth** via an **additive-only EMR connector** (read by ID; write only compact doctor-confirmed addenda, bookings, order stubs). Separate compliance-gated **Agent Memory Store** (`PostgreSQL 15 FORCE RLS`, 27 tenant tables, `clinic_app NOBYPASSRLS`) stores longitudinal trajectories, verified quotes, and loop state.
- **3-Layer Clinical Practice Knowledge Graph & Clinic Curator:** (1) EBM Base Tree + **Immutable Safety Floor** (`SafetyFloorViolation` HTTP 422); (2) Clinic Onboarding Overlay (triage grace windows, corporate benefits, partner labs, nurse scope, configurable Careline AI e.g. `Sam · Clinic Careline AI`); (3) Continuous Clinic Curator clustering doctor `override_diff` telemetry for Lead GP sign-off — delivering Day-1 locum continuity.
- **Deterministic First, Bounded LLM Second:** Pre-LLM Python gates (`classify_crisis`, `rx_rules`, 3-point ID verification, deterministic score calculators `CHA₂DS₂-VASc` / `HAS-BLED` / `HK CVD 10-yr`) run before any model call. Runtime prunes context to 2–4 active graph nodes (`< 4,096` tokens) on GCP Vertex AI **`asia-east2` (Hong Kong)**; durable multi-day loops run on transactional `outbox_events`. Product code (`harness/backend/`) **never** imports `loopbench`.
- **Deterministic Presentation Compiler & Warm-Paper Light UI (`docs/CLINICAL_UI_DESIGN_SYSTEM.md`):** Pathway nodes bind deterministically to **14 canonical light-mode widgets** (9 Canvas: `VitalsExamStrip`, `ClinicalScoreStrip`, `MedDiff`, `TrendGraph`, `ResultTable`, `ImagingPreview`, `SourceClip`, atomic `IssueCard`, empty-by-default `ManagementPlan`; 5 Right Rail: `SafetyCard`, always-on `PreventiveCard`, `ScheduledCard`, `TimelineCard`, `UnifiedThread`) gated by the **5-rule Presentation Linter** (`validate_case_presentation`).
- **Empty Management Plan, Overlay `/` & High-Signal Unified Thread:** **`Management Plan`** starts empty and builds via calm natural-font `+` pills + zero-jank floating overlay `/` popover (8 canonical order primitives) → **`Confirm & Sign Plan`** + 10s Undo + folded `▸ N agent steps`. Right-rail **Unified Thread** puts Patient Careline history above a `Private · Doctor & Sam only` divider; Sam posts unread **only** on objective scores/guidelines/anomalies (otherwise silent). Safety-critical gaps show inline subtly in amber only.
- **3-Tier Async Triage & Fail-Closed Authority:** (1) Logistics/rebookings → Careline AI; (2) Post-visit/discharge grace window (`72h`) → `# my-work` with zero fake SLA; (3) New episodes/Rx/sick notes → safety reminder + booking options (never unilateral AI billing); Red flags → 999 exit. External docs require exact `3/3` ID match or quarantine to Nurse Rachel; low-risk stalls (`2 nudges / 72h`) route to Nurse first.

---

## 3. Explicitly Ruled Out / Non-Goals (Do Not Re-Litigate)
- **No Second EMR / Duplicate Charting** and **never write ephemeral Sam chat** to the legal clinical record.
- **No New MVP Personas** (Doctor regular/locum + Nurse only; no Clinic Admin).
- **No Unpaid Live Doctor Q&A Chat, Fake SLAs** (*"reply by 4pm"*), `"Waiting on Dr. X"`, or **Unilateral AI Billing**.
- **No Clinician Impersonation** (`"Resident"`, `"Doctor"`, `"Clinician"`) or autonomous prescribing / medication holds.
- **No Slop or Coding-Font UI:** No dark mode, no monospace `+` buttons, no pre-populated `"Management Plan Checkout"`, no `"History / Clinical reasoning / Assessment"` headers, no prose repeating chart numbers, no bundled multi-drug Rx lines, no chatty Sam filler, no awkward bespoke top CTA buttons (`"Adopt QEH Regimen"`).

---

## 4. Open Questions Research Must Answer
1. **Knowledge Graph Depth & Onboarding:** Executable schemas for **8 deep HK GP pathways** (5 live-demo + T2DM/HbA1c, Thyroid/Fatigue, Acute UTI) wired directly into existing `rx_rules.py` / `terminology.py` / `certificates.py`, plus a 45-min guided Lead GP onboarding script.
2. **Messy Intake & Compiler Robustness:** How the 14-widget compiler + linter handles thin/vague intakes, colloquial HK Cantonese/code-switching, and family proxies without slop or false certainty.
3. **Live Model-Backed Agentic Reliability:** Unifying `/workspace` with existing `agent_harness.py` + `triage.py` + `verification.py`, adding Vertex AI `asia-east2` (`gemini-2.5-flash`) with deterministic fallback, bounded `< 4,096`-token context compilation, and 1-retry linter self-repair.
4. **EMR Integration & Curator/PDPO Governance:** Practical additive writeback paths for HK EMRs (EC Healthcare stack, ClinicSolution X, Apricot/VITA + Phase-1 bridge), live `/workspace` → `/emr` → `/p/qr/...` sync, multi-doctor Curator conflict rules, and bilingual HK PDPO consent.
5. **Founder Grading Eval Suite:** 20-case synthetic HK GP benchmark producing automated safety/linter scores plus an interactive HTML/Markdown grading sheet for founder review.

---

## 5. Parallel Deep-Research & Build Subagent Tracks (See `docs/AGENT_HANDOFF.md`)
*Protocol: Phase 1 specs (`docs/research/track*.md`) → Mandatory Schema Sync Barrier (Seams A/B/C) → Phase 2 disjoint coding, live demo wiring & Founder Grading Report on a branch off `feat/live-emr-agent-phone-demo`.*

| Track | Scope (Converge & Wire — Zero Re-Invention) | Existing Modules & Isolated Boundaries |
| :--- | :--- | :--- |
| **1. Canonical Clinical Knowledge Graph** | 8 deep executable HK GP pathways, bind `/slash` primitives to `rx_rules.py` + `terminology.py` + `certificates.py`, Python calculators, preventive rules, 45-min onboarding, Curator governance & concise HK PDPO consent | `docs/research/track1_clinical_knowledge_graph.md` → `practice_graph.py`, `curator.py`, `rx_rules.py`, `terminology.py`, `certificates.py` |
| **2. Design, UX & 14-Widget Catalog** | Formal prop/empty/overflow specs for all 14 warm-paper widgets, inline editors for all 8 `/slash` orders, thin/Cantonese/proxy intake UX & hardened semantic linter `R1`–`R5` | `docs/research/track2_design_ux_widget_catalog.md` → `docs/CLINICAL_UI_DESIGN_SYSTEM.md`, `frontend/src/v2/workspace/*` |
| **3. Agentic Harness Layer (Live Models + Graph + Widgets)** | Unify `/workspace` with existing `agent_harness.py` (add Vertex AI `asia-east2` + fallback), wire Loop 1 → `triage.py` & Loop 2 → `verification.py`, 5-slot Presentation Compiler + 1-retry linter self-repair & durable `outbox_events` | `docs/research/track3_agentic_harness_runtime.md` → `workspace_engine.py`, `agent_harness.py`, `triage.py`, `verification.py`, `workflows.py` |
| **4. Demo Wiring, EMR Connector & Founder Eval Suite** | Wire live `/workspace` **`Confirm & Sign Plan`** → `rx_rules` + `certificates.py` PDF + `/emr` addendum + `/p/qr/...` live push, HK EMR blueprint, graph management UI & 20-case Founder Grading Eval Sheet | `docs/research/track4_demo_emr_and_eval_suite.md` → `emr_connector.py`, `adapters.py`, `app.py`, `tests/`, `../loopbench/` eval pack *(strictly decoupled)* |
