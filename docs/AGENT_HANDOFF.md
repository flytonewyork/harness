# Multi-Agent Handoff & 4-Track Deep Research / Build Plan

*Last updated: 23 Nov 2026 · Branch: `feat/live-emr-agent-phone-demo` (PR #65)*
*Canonical orientation for parallel Antigravity / `/teamwork` sessions.*

---

## 1. Operating Mandate & Required Reading

**Directive:** **STOP building net-new speculative features or personas.** Hone the existing working live demo (`/workspace`, `/emr`, `/p/qr/pat_lam_urti_demo`) into a production-grade, model-backed, deterministically governed clinical system with an interactive founder grading suite.

Read these 3 files before doing any work:
1. **`docs/PRODUCT_DECISIONS.md`** — Locked product/architecture decisions, non-goals (DO NOT re-litigate), and open research questions.
2. **`docs/CLINICAL_UI_DESIGN_SYSTEM.md`** — Warm Editorial Paper (Light Mode) spec, **14 finite clinical widgets** (9 Canvas + 5 Right Rail including `TimelineCard`), **8 canonical order primitives**, and **5-rule Presentation Linter**.
3. **`NOW.md`** — Active priority queue (**strictly `< 2 KB`**).

---

## 2. Non-Negotiable Engineering & Safety Invariants

- **Git discipline:** Never commit to `main`. Branch from `feat/live-emr-agent-phone-demo` (or commit cleanly on it if coordinated); Thomas merges.
- **Strict Repo Boundary (`tests/test_engine.py`):** `harness/backend/` **NEVER** imports `loopbench`.
- **Sole Clinical SoT:** Clinic EMR is the sole legal clinical record via additive-only writebacks. Never duplicate charting or write ephemeral Sam chat to the EMR.
- **FORCE RLS (`tests/test_observability_security_and_guidelines.py`):** All tenant-scoped tables in `backend/acute/db.py` (currently **27**) must be in `TENANT_SCOPED_TABLES` with `ENABLE` + `FORCE ROW LEVEL SECURITY` (`clinic_app NOBYPASSRLS`).
- **Zero Slop & Light Mode Only:** Every rendered clinical case must pass `validate_case_presentation` (`5/5` rules) — no essay headers (`History:`, `Clinical reasoning:`, `Assessment:`), no prose repeating widget numbers, no bundled multi-drug Rx lines, empty-by-default **`Management Plan`**, natural-font calm `+` pills (zero monospace buttons), floating overlay `/` popover, right-rail `Timeline`, strict high-signal `Sam` gate, and zero forbidden clinician titles (`Resident`, `Doctor`).
- **Synthetic data only** — zero real PHI.

### Verification Commands
```bash
python3 -m pytest tests/test_practice_graph_loops.py tests/test_engine.py -v   # fast backend (~10s)
npm --prefix frontend run build                                                # frontend tsc + vite (~2s)
bash scripts/check.sh                                                          # full dual-repo CI gate (~2.5m; 459 + 66 tests)
```

---

## 3. Shared Cross-Track Interface Contracts (Prevent Parallel Drift)

To run all 4 tracks concurrently without incompatible schemas or merge collisions, every track respects **3 frozen contract seams**:

1. **Seam A (`Track 1 → Track 3`): Graph & Clinical Rules Contract**
   - `GraphNode`, `ORDER_CATALOG_PRIMITIVES` (8 canonical types + atomic `rx` verbs `start|update|stop|continue`), `PREVENTIVE_CARE_RULES`, deterministic score outputs (`id`, `label`, `value`, `band`, `detail`, `guideline`), and `SafetyFloorViolation` (HTTP 422) in `backend/acute/practice_graph.py`.
2. **Seam B (`Track 2 → Track 3`): Canonical Presentation & Linter Contract**
   - `CLINICAL_WIDGET_CATALOG` (14 finite widgets), `PATHWAY_WIDGET_STACKS`, typed `CaseSummaryPayload` (`vitals_exam`, `clinical_scores`, `med_diff`, `imaging_studies`, `issues[].{id, name, bullets, safety_gap, suggested_orders}`, `lab_panel`, `trend_chart`, `attachments`, `alerts`, `preventive_care`, `scheduled_tasks`, `sam_proactive_insight`, `original_chat`), and `validate_case_presentation(summary)` (`5/5` invariants).
3. **Seam C (`Track 3 → Track 4`): Runtime API, EMR Writeback & Eval Export Contract**
   - Workspace endpoints (`/api/v1/workspace/*`, `/api/v1/graph/*`, `/api/v1/curator/*`), additive EMR addendum schema (`emr_connector.py`), `ExecutionTrace` + `ContextSnapshot` JSON, and eval JSONL output format consumed by the Founder Grading Eval Suite in `loopbench/`.

---

## 4. The 4 Parallel Deep-Research & Build Subagent Tracks

Execute **Phase 1 (Deep Research & Exact Spec)** into isolated `docs/research/track*.md` files first, validate seams A–C, then execute **Phase 2 (Implementation & Live Demo Integration)** in disjoint file boundaries.

### Track 1 · Canonical Clinical Knowledge Graph
- **Research Output:** `docs/research/track1_clinical_knowledge_graph.md`
- **Code Ownership (Phase 2):** `backend/acute/practice_graph.py`, `backend/acute/curator.py`, `backend/acute/rx_rules.py`
- **Subtasks:**
  1. **Top-15 HK Primary Care Pathways:** Formalise node/edge trees grounded in HK Primary Care Reference Frameworks (HTN, DM, Lipids, AFib/Anticoag, Non-Anaemic Iron Deficiency, URTI/Antibiotic Stewardship, Thyroid, Gout, Asthma, GERD, UTI, Mental Health PHQ-9/GAD-7, Post-Discharge MedRec, Preventive Screening, Abnormal Labs) with explicit Immutable Safety Floor guards.
  2. **HK Formulary, Dosing & Certificate Ontology:** Structured drug safety & renal/G6PD/allergy dosing rules + formalised MCHK sick-leave/fitness/referral certificate schemas tied directly to the 8 canonical `/slash` order primitives.
  3. **Deterministic Clinical Score Library:** Exact Python spec for validated GP calculators (`CHA₂DS₂-VASc`, `HAS-BLED`, `HK 10-Yr CVD`, `CURB-65`, `PHQ-9`, `eGFR/CrCl dosing`).
  4. **Always-On Preventive Care Engine:** Age/sex/risk/corporate-benefit rule table (HK CHP vaccines, CRC FIT, Cervical, Mammogram, Cardiometabolic) distinguishing *add today* vs *defer to future booking*.
  5. **Onboarding Moulding & Multi-Doctor Curator Governance:** 45-min Lead GP onboarding interview script + override clustering thresholds, inter-doctor divergence handling, rollback semantics, and bilingual HK PDPO (DPP1/DPP3/DPP6) consent & retention rules for `AgentMemoryEntry`.

### Track 2 · Design, UX & Canonical Clinical Widget Catalog
- **Research Output:** `docs/research/track2_design_ux_widget_catalog.md`
- **Code Ownership (Phase 2):** `docs/CLINICAL_UI_DESIGN_SYSTEM.md`, `frontend/src/v2/workspace/ClinicWorkspaceApp.tsx`, `frontend/src/v2/workspace/workspace.css`
- **Subtasks:**
  1. **14-Widget Formal Component Spec:** Exact JSON prop schemas, empty/error/overflow states, responsive rules, and clinical typography/color tokens for every widget (`VitalsExamStrip`, `ClinicalScoreStrip`, `MedDiff`, `TrendGraph`, `ResultTable`, `ImagingPreview`, `SourceClip`, `IssueCard`, `ManagementPlan`, `SafetyCard`, `PreventiveCard`, `ScheduledCard`, `TimelineCard`, `UnifiedThread`).
  2. **Deterministic Pathway → Widget Composition Matrix:** Exact deterministic rules deciding which widgets mount per clinical scenario (max above-the-fold density, zero clutter) and exact `Management Plan` polymorphic inline form specs for all 8 `/slash` commands.
  3. **Edge-Case Clinical UX (Thin Intake, Cantonese/Code-Switching, Family Proxies):** Minimalist UX for incomplete/contradictory intakes (when to show subtle amber `safety_gap` vs explicit `Insufficient intake` + 1-click pre-visit intake form via Sam), zero fake SLAs, and clear visual separation between patient-visible Careline messages and private Doctor ↔ Sam notes.
  4. **Anti-Slop Linter Spec Refinement:** Tighten semantic rules `R1`–`R5` (and any new high-leverage deterministic checks) without brittle word-count handcuffs, including exact positive/negative golden examples.

### Track 3 · Agentic Harness Layer (Live Model Calls + Graph + Widgets)
- **Research Output:** `docs/research/track3_agentic_harness_runtime.md`
- **Code Ownership (Phase 2):** `backend/acute/workspace_engine.py`, `backend/acute/workflows.py`, `backend/acute/llm/` (or model adapter modules)
- **Subtasks:**
  1. **Exact Agent Topology & Model Call Architecture:** Map every live agent loop (Loop 1 Inbound Triage, Loop 2 Doc Ingestion & Structured Diff, Loop 3 Dynamic Case Presentation Compiler & Plan Instruction Compiler, Loop 4 Multi-Day Loop Wake/Stall Router, Private Doctor ↔ Sam Ephemeral Registrar) to exact Vertex AI `asia-east2` (`gemini-2.5-flash`, `temperature=0.1`) structured JSON / tool-calling contracts + deterministic fallback when offline/demo.
  2. **5-Slot Bounded Context Compiler (`< 4,096` tokens):** Exact prompt templates, subgraph pruning algorithm (2–4 active nodes), compliance-gated Agent Memory retrieval (`agent_memory_entries`), and verbatim source quote verification.
  3. **Linter-in-the-Loop Self-Repair Pipeline:** Wire `validate_case_presentation` directly after LLM slot-fill: if any invariant (`R1`–`R5`) fails, feed exact violation diagnostics back into a bounded 1-retry repair call (or fail closed to deterministic template) and record telemetry — guaranteeing ~100% zero-slop UI outputs in production.
  4. **Durable Execution, Undo & Grace-Window Calibration:** Harden PostgreSQL `outbox_events` handlers (`EVENT_ACTION_COMMIT_DISPATCH`, `EVENT_LOOP_CHECKPOINT_WAKE`, `EVENT_LOOP_NUDGE_RETRY`), 10-second Undo atomicity, and 48h vs 72h grace-window / retry-cap execution semantics.

### Track 4 · End-to-End Demo Integration, EMR Connector & Founder Grading Eval Suite
- **Research Output:** `docs/research/track4_demo_emr_and_eval_suite.md`
- **Code Ownership (Phase 2):** `backend/acute/emr_connector.py`, `backend/acute/app.py`, `tests/test_practice_graph_loops.py`, and `../loopbench/` eval pack (keeping `harness/backend/` strictly isolated from `loopbench`!)
- **Subtasks:**
  1. **HK EMR Additive Connector Blueprint & Live Demo Wiring:** Research practical read + additive writeback paths for **EC Healthcare stack**, **ClinicSolution X**, and **Apricot/VITA** (FHIR/HL7, encounter addendum APIs, PDF attachment drops, calendar hooks, Phase-1 bridge fallback) AND wire live end-to-end sync between `/workspace` `Confirm & Sign Plan` → additive addendum visible immediately in `/emr` + patient updates in `/p/qr/...`.
  2. **Deterministic Practice Graph Management & Live Demo Polish:** Wire clean end-to-end flows for `@ Clinic Playbook` Q&A, Clinic Curator approval (`v2.4 → v2.5`), 3-pt ID quarantine → Nurse Rachel `# clinic-team` resolution, and live `Architecture X-Ray` inspection (real compiled Context Window tokens, traversed nodes, live 5/5 linter badge).
  3. **Founder Grading Eval Suite (Built for Human Review):** Build a reproducible synthetic evaluation set (~15–20 realistic HK primary care cases spanning post-discharge medrec, URTI/G6PD, iron deficiency, HTN, lipids, messy Cantonese/code-switched messages, family proxies, thin intakes, red-flag 999 traps, and 2/3 ID mismatches) producing both automated pass/fail scores (Safety Floor, 3-pt ID, Linter `R1`–`R5`, zero made-up quotes) AND a **clean side-by-side HTML/Markdown Grading Sheet** so the founder can quickly grade clinical judgment, UI clarity, and slop-freeness on every single output!
  4. **CEO Commercial ROI Proof Points:** Compact, grounded ROI sheet linking live demo workflows to clinic metrics (doctor/nurse minutes saved, recovered follow-up/preventive bookings, Day-1 locum parity) aligned with `docs/EC_DISCOVERY_SCOPE.md`.
