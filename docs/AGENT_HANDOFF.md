# Multi-Agent Handoff & Overnight Research Pack

*Last updated: 23 Nov 2026 · Branch: `feat/live-emr-agent-phone-demo` (PR #65)*
*Canonical orientation for joining Antigravity agents and parallel `/teamwork` sessions.*

---

## 1. Read These First (Source of Truth)

Before proposing architecture changes, UI edits, or research outputs, read these 3 files in order:
1. **`docs/PRODUCT_DECISIONS.md`** — Locked product bets, safety invariants, and **Explicitly Ruled Out / Non-Goals (DO NOT re-litigate!)**.
2. **`docs/CLINICAL_UI_DESIGN_SYSTEM.md`** — Canonical **Warm Editorial Paper (Light Mode)** design system, **14 finite clinical widgets** (9 Canvas + 5 Right Rail including `TimelineCard`), **8 canonical order primitives**, and **5-rule Presentation Linter**.
3. **`NOW.md`** — Current priority queue (**strictly keep `< 2 KB`**).

---

## 2. Current Codebase State (PR #65 · `feat/live-emr-agent-phone-demo`)

Everything below is built, tested (`bash scripts/check.sh` green: **459 harness + 66 loopbench tests**), and pushed on `feat/live-emr-agent-phone-demo`:

| Component | File(s) | Status |
| :--- | :--- | :--- |
| **3-Layer Practice Knowledge Graph + Deterministic Calculators + 5-Rule Presentation Linter** | `backend/acute/practice_graph.py` | Complete (`EBM_BASE_NODES`, `SafetyFloorViolation` HTTP 422, `CLINICAL_WIDGET_CATALOG` [14], `ORDER_CATALOG_PRIMITIVES` [8], `PREVENTIVE_CARE_RULES`, `compute_cha2ds2_vasc` / `compute_has_bled` / `compute_cvd_10yr_tier`, `validate_case_presentation`). |
| **Clinic Curator & Onboarding Moulding** | `backend/acute/curator.py` | Complete (versioned overlays, `override_diff` clustering, Lead GP approval, `@ Clinic Playbook` Q&A). |
| **Additive-Only EMR Connector & Fail-Closed 3-Pt ID Gate** | `backend/acute/emr_connector.py` | Complete (`3/3` MRN/HKID + Name + DOB match; mismatches quarantine to Nurse Rachel). |
| **PostgreSQL 15 FORCE RLS Schema** | `backend/acute/db.py` | **27** tenant-scoped FORCE RLS tables (`clinic_app NOBYPASSRLS`) + transactional `outbox_events` loop wake/undo handlers in `workflows.py`. |
| **Workspace Orchestrator & 4 Seeded Cases** | `backend/acute/workspace_engine.py`, `backend/acute/app.py` | Complete (`CHAN Tai Man`, `LAM Wing-Yan`, `WONG Mei Ling`, `LEE Ming Kit` — all pass `5/5` Presentation Linter checks; `/api/v1/workspace/*`, `/api/v1/graph/*`, `/api/v1/curator/*`). |
| **Warm-Paper Light-Mode Clinical Workspace (`/workspace`)** | `frontend/src/v2/workspace/ClinicWorkspaceApp.tsx`, `workspace.css` | Complete (14 canonical widgets, `Timeline` in right rail, empty-by-default **`Management Plan`** with zero-jank floating overlay `/` popover, **Unified Careline + Sam Thread** with strict high-signal gate, hidden `Architecture X-Ray`). |
| **Tests** | `tests/test_practice_graph_loops.py` | Complete (Safety Floor 422, 3-tier triage, 3-pt ID quarantine, commit/10s undo, loop progression, Curator, `5/5` Presentation Linter pass + slop regression checks). |

---

## 3. Non-Negotiable Engineering & Product Invariants

Any joining agent **must preserve** every invariant below:
- **Never commit directly to `main`** — work on `feat/live-emr-agent-phone-demo` or a dedicated feature/research branch off it; Thomas merges.
- **Strict Repo Boundary (`tests/test_engine.py`):** `harness/backend/` **NEVER** imports `loopbench`.
- **Sole Clinical SoT:** Clinic EMR remains the sole legal/clinical Source of Truth via additive-only writebacks. Never create duplicate charting or write ephemeral Sam chat to the legal EMR.
- **FORCE RLS Count (`tests/test_observability_security_and_guidelines.py`):** Any new tenant table in `db.py` must be registered in `TENANT_SCOPED_TABLES` with `ENABLE` + `FORCE ROW LEVEL SECURITY`.
- **Zero Slop & Light Mode Only:** Every new or edited clinical case payload must pass `validate_case_presentation` (`5/5` rules) — no essay headers (`History:`, `Clinical reasoning:`, `Assessment:`), no prose repeating widget numbers, no bundled multi-drug `rx` orders, no pre-populated Management Plan, no coding monospace buttons, no chatty Sam filler, and no clinician titles (`Resident`, `Doctor`) on Careline AI.
- **Keep `NOW.md` strictly `< 2 KB`.**
- **Synthetic data only** — zero real patient PHI anywhere in repo or research docs.

### Verification Commands
```bash
# Backend targeted tests (~10s)
python3 -m pytest tests/test_practice_graph_loops.py tests/test_engine.py -v

# Frontend typecheck + production bundle (~2s)
npm --prefix frontend run build

# Full dual-repo CI gate (~2.5 min; requires local Postgres running via /home/antigravity/env-setup.sh)
bash scripts/check.sh
```

---

## 4. Parallel Overnight `/teamwork` Research Workstreams

Each workstream writes to its own isolated file under `docs/research/` so multiple agents can execute concurrently with zero merge conflicts.

### Workstream A · HK EMR Connectors & Additive Writeback Blueprint
- **Output file:** `docs/research/emr_connectors_hk.md`
- **Objective:** Map practical read + **additive-only writeback** interfaces across target HK private clinic EMRs (**EC Healthcare stack**, **ClinicSolution X**, **Apricot/VITA**) — covering encounter addendum APIs, HL7/FHIR endpoints, PDF attachment drops, calendar hooks, and a clean Phase-1 bridge fallback (zero dual-SoT overhead).
- **Done criteria:** Concrete capability matrix per EMR, auth/deployment topology (HK data residency), Phase-1 vs Phase-2 integration plan, and exact schema mapping to `backend/acute/emr_connector.py`.

### Workstream B · Clinical Practice Knowledge Graph & HK GP Ontology Expansion
- **Output file:** `docs/research/clinical_knowledge_graph_hk.md`
- **Objective:** Design the scalable schema and top-15 HK primary care pathways grounded in **HK Primary Care Reference Frameworks** (HTN, DM, Preventive Care for Older Adults/Children, Lipids, AFib/Anticoag, Iron Deficiency, URTI/Antibiotic Stewardship, Thyroid, Gout, Asthma, Mental Health PHQ-9/GAD-7), linked to HK formulary dosing, G6PD/renal CDS rules, MCHK certificate standards, and a **30–60 min guided Lead GP onboarding moulding format**.
- **Done criteria:** Typed node/edge schema extension plan for `practice_graph.py`, top-15 pathway table with Immutable Safety Floor rules, deterministic Python score calculator inventory, and an actionable 45-minute clinic onboarding script.

### Workstream C · Deterministic Clinical UI Compiler, Cantonese/Proxy Intake & Linter Evals
- **Output file:** `docs/research/clinical_ui_compiler_evals.md`
- **Objective:** Stress-test and harden the **4-stage Deterministic Presentation Compiler** (`docs/CLINICAL_UI_DESIGN_SYSTEM.md`) for real-world messy inputs: colloquial HK Cantonese, English/Cantonese code-switching, family-member proxies messaging for elderly patients, thin/ambiguous intakes (designing the exact minimal insufficient-intake UX + dynamic pre-visit questionnaire trigger), and expanding `validate_case_presentation` + `loopbench` eval metrics without violating the `harness` ≠ `loopbench` boundary.
- **Done criteria:** Expanded deterministic linter ruleset (with zero brittle word-count hacks), 10 tricky Cantonese/proxy synthetic edge cases with expected compiler outputs, and an eval rubric runnable in `loopbench`.

### Workstream D · Multi-Doctor Clinic Curator Governance & HK PDPO Compliance
- **Output file:** `docs/research/curator_governance_and_pdpo.md`
- **Objective:** Specify production governance for Layer 3 (**Continuous Clinic Curator**) in a 5–20 GP group practice (override clustering thresholds, handling doctor-to-doctor clinical style divergence, Medical Director review cadence, rollback semantics) alongside **HK PDPO (DPP1/DPP3/DPP6)** consent wording, retention windows, and opt-out behavior for the compliance-gated **Agent Memory Store** (`agent_memory_entries`).
- **Done criteria:** Concrete Curator state machine & disagreement policy, draft bilingual (EN + 繁體中文) patient PDPO disclosure/consent copy, and retention/deletion matrix mapping to `db.py` tables.

### Workstream E · Commercial Triage Calibration & Clinic ROI Model (EC Healthcare CEO Pitch)
- **Output file:** `docs/research/commercial_triage_and_roi.md`
- **Objective:** Calibrate the **3-tier async-first triage model** (48h vs 72h post-visit/discharge grace window, question cap, teleconsult vs in-clinic conversion mechanics without unilateral AI billing) and model concrete clinic ROI from (1) doctor/nurse minutes saved per day, (2) recovered paid follow-up & preventive care visits (flu/PCV20/FIT/mammogram/HbA1c), and (3) Day-1 locum continuity value.
- **Done criteria:** Sensitivity table (solo GP vs 10-GP group vs EC Healthcare clinic network), grace-window policy recommendations, and CEO-ready KPI assumptions consistent with `docs/EC_DISCOVERY_SCOPE.md`.
