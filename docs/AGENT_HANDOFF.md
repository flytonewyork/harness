# Multi-Agent Handoff & 4-Track Overnight Execution Plan

*Last updated: 23 Nov 2026 · Base Branch: `feat/live-emr-agent-phone-demo` (PR #65)*
*Canonical orientation for parallel Antigravity / `/teamwork` sessions executing Phase 1 (Research + Schema Sync) and Phase 2 (Implementation + Live Demo Wiring + Founder Grading Eval Suite).*

---

## 1. Operating Mandate & Required Reading

**Directive:** **STOP building net-new speculative features or personas.** Converge and wire the ~350 KB of existing `backend/acute/` clinical engines into the working live demo (`/workspace`, `/emr`, `/p/qr/pat_lam_urti_demo`) and generate a 20-case Founder Grading Eval Sheet.

Read these 3 files first:
1. **`docs/PRODUCT_DECISIONS.md`** — Locked product/architecture decisions, non-goals (DO NOT re-litigate), and Track 1–4 overview.
2. **`docs/CLINICAL_UI_DESIGN_SYSTEM.md`** — Warm Editorial Paper (Light Mode) spec, **14 finite widgets** (9 Canvas + 5 Right Rail including `TimelineCard`), **8 canonical order primitives**, and **5-rule Presentation Linter**.
3. **`NOW.md`** — Active queue (**strictly keep `< 2 KB`**).

---

## 2. Existing Production Clinical Infrastructure — WIRE, DO NOT REBUILD!

Before writing any new backend module, inspect and reuse what already exists & passes 459 pytest tests in `backend/acute/`:
- **`agent_harness.py` (105 KB):** Hybrid live LLM tool-calling loop (`GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` + seamless deterministic fallback) with `READ / MEMORY / CDS / DRAFT / SIGNED_WRITE` tools.
- **`triage.py` (88 KB):** Bilingual EN + Cantonese + code-switched union-only red-flag triage (clause-scoped negation & Cantonese hyperbole safe), verbatim symptom extraction, and prompt-injection neutralization.
- **`rx_rules.py` (48 KB):** Deterministic HK formulary safety engine (Cap. 134 hard blocks, G6PD, allergy cross-reactivity, major Warfarin/Statin/NSAID DDIs, renal eGFR & pediatric weight ceilings).
- **`adapters.py` (33 KB):** `WhatsAppDoorbellAdapter` (with `WhatsAppLeakageGuard`), `BookingAdapter`, `PharmacyFulfilmentAdapter`, `FluVaxAdapter`, `CorporateBillingAdapter`.
- **`certificates.py` (16 KB):** MCHK-compliant encrypted PDF sick-leave generator (`^[ML]\d{5}$`, 1–7d, dual ICD-10/ICPC-2, SHA-256 hash, `DocumentVault`).
- **`terminology.py` (15 KB):** Official HK SNOMED CT, ICD-10, ICPC-2, RxNorm, ATC, LOINC, UCUM, CVX coding validators.
- **`verification.py` (14 KB):** Bilingual OCR-safe `verify_quote_span`, `doc_identifiers` multi-page ID collision check, cross-site `DoseConflict`, elderly unknown-eGFR checks.
- **`live_bus.py` (8 KB) & `workflows.py` (18 KB):** Real-time event bus & PostgreSQL `outbox_events` durable wake/undo loop engine.

---

## 3. Non-Negotiable Engineering & Safety Invariants

- **Branching:** Create a clean working branch off `feat/live-emr-agent-phone-demo` (e.g. `feat/live-demo-convergence`); NEVER commit to `main`; Thomas merges.
- **Strict Repo Boundary (`tests/test_engine.py`):** `harness/backend/` **NEVER** imports `loopbench`.
- **Sole Clinical SoT:** Clinic EMR stays sole clinical record via additive-only writebacks. Never write ephemeral Sam chat to the legal EMR.
- **FORCE RLS (`tests/test_observability_security_and_guidelines.py`):** Every tenant table in `db.py` (currently **27**) must be registered in `TENANT_SCOPED_TABLES` with `ENABLE` + `FORCE ROW LEVEL SECURITY` (`clinic_app NOBYPASSRLS`).
- **Zero Slop & Light Mode Only:** Every rendered case passes `validate_case_presentation` (`5/5` rules) — warm editorial paper light mode, no monospace buttons, empty-by-default **`Management Plan`**, floating overlay `/` popover, right-rail `Timeline`, strict high-signal `Sam` gate, minimal amber safety-gap cues only, zero essay headers, zero forbidden clinician titles (`Resident`, `Doctor`).
- **Keep `NOW.md` strictly `< 2 KB`** and use **synthetic data only** (zero real PHI).

### Verification Gate (Must Pass Before Finishing)
```bash
python3 -m pytest tests/test_practice_graph_loops.py tests/test_engine.py -v   # fast backend (~10s)
npm --prefix frontend run build                                                # frontend tsc + vite (~2s)
bash scripts/check.sh                                                          # full dual-repo CI gate (~2.5m; 459 + 66 tests)
```

---

## 4. Mandatory 3-Stage Execution Protocol (Zero Drift)

1. **Phase 1 — Parallel Deep Research & Spec (All 4 Tracks Concurrent):**
   - Each track writes its concrete architecture/implementation spec to its isolated `docs/research/track{1..4}_*.md` file.
2. **Mandatory Schema Sync Barrier (Before Any Phase 2 Code):**
   - Orchestrator reconciles **Seams A, B, and C** into one unified JSON schema (eliminating minor camelCase/snake_case drift such as `testName`/`daysOff` vs `safety_gap`/`lab_panel` while keeping backward compatibility for existing UI/tests):
     - **Seam A (`Track 1 → Track 3`):** `GraphNode`, 8 `ORDER_CATALOG_PRIMITIVES` + atomic `rx` verbs (`start|update|stop|continue`), `PREVENTIVE_CARE_RULES`, Python score outputs, `SafetyFloorViolation` (HTTP 422).
     - **Seam B (`Track 2 → Track 3`):** 14 widgets (`CLINICAL_WIDGET_CATALOG`), `CaseSummaryPayload`, and `validate_case_presentation` (`R1`–`R5`).
     - **Seam C (`Track 3 → Track 4`):** `/api/v1/workspace/*` contract, live additive EMR addendum payload, `ExecutionTrace` + `ContextSnapshot`, and eval JSONL schema.
3. **Phase 2 — Disjoint Implementation, Live Demo Wiring & Eval Run:**
   - Each track implements ONLY inside its assigned file boundary, followed by full `npm --prefix frontend run build` + `bash scripts/check.sh` (`== all green`) and generating the **Founder Grading Eval Report**.

---

## 5. The 4 Parallel Tracks

### Track 1 · Canonical Clinical Knowledge Graph
- **Phase 1 Spec:** `docs/research/track1_clinical_knowledge_graph.md`
- **Phase 2 Files:** `backend/acute/practice_graph.py`, `backend/acute/curator.py`, `backend/acute/rx_rules.py`, `backend/acute/terminology.py`, `backend/acute/certificates.py`
- **Deliverables:**
  1. **8 Deep Executable HK GP Pathways:** 5 live-demo pathways (Post-Discharge AFib/Warfarin + Statin, Acute URTI + G6PD/Allergy, Non-Anaemic Iron Deficiency, Chronic HTN Home BP, Borderline LDL) + 3 high-frequency HK GP expansions (**Type 2 DM / HbA1c**, **Thyroid / Fatigue**, **Acute UTI / Antibiotic Stewardship**) grounded in HK Primary Care Reference Frameworks with Immutable Safety Floor guards.
  2. **Live Bindings to Existing Engines:** Bind every `/prescribe` order to `rx_rules.py` deterministic CDS, every `/cert` order to `certificates.py` (real MCHK PDF generation), and coded diagnoses/labs/vaccines to `terminology.py` (ICD-10/ICPC-2/SNOMED/RxNorm/LOINC/CVX).
  3. **Deterministic Python Clinical Score Library & Always-On Preventive Rules:** Complete `CHA₂DS₂-VASc`, `HAS-BLED`, `HK 10-Yr CVD`, `CURB-65`, `PHQ-9`, and `eGFR/CrCl` dosing calculators + HK age/sex/risk preventive rules.
  4. **Onboarding Moulding, Multi-Doctor Curator Governance & Concise HK PDPO:** 45-min Lead GP onboarding interview protocol, override clustering/conflict/rollback rules, and concise bilingual (EN + 繁體中文) HK PDPO DPP1/DPP3/DPP6 consent & retention spec for `AgentMemoryEntry`.

### Track 2 · Design, UX & Canonical 14-Widget Catalog
- **Phase 1 Spec:** `docs/research/track2_design_ux_widget_catalog.md`
- **Phase 2 Files:** `docs/CLINICAL_UI_DESIGN_SYSTEM.md`, `frontend/src/v2/workspace/ClinicWorkspaceApp.tsx`, `frontend/src/v2/workspace/workspace.css`
- **Deliverables:**
  1. **Formal 14-Widget Component Contract:** Exact JSON prop schemas, empty/loading/overflow states, and narrow-screen behavior for all 9 Canvas + 5 Right Rail widgets (keeping Warm Editorial Paper Light Mode, right-rail `Timeline`, empty `Management Plan`, and floating `/` overlay strictly intact).
  2. **Complete All 8 Polymorphic `/slash` Order Editors:** Build clean calm light-mode inline editors for `imaging`, `refer`, `nurse`, and `book` (matching the existing `rx`, `cert`, `lab`, `goal` editors — zero coding fonts).
  3. **Messy/Ambiguous Intake UX:** Exact minimalist UI patterns for thin intakes (`Insufficient intake` cue + 1-click pre-visit questionnaire trigger via Sam), Cantonese/code-switched threads, and family proxy clarity.
  4. **Hardened Semantic Anti-Slop Linter Spec (`R1`–`R5`+):** Expand deterministic linter rules (e.g., cross-issue duplicate drugs, quote fidelity) with golden pass/fail test fixtures and zero brittle word-count limits.

### Track 3 · Agentic Harness Layer (Live Model Calls + Graph + Widgets)
- **Phase 1 Spec:** `docs/research/track3_agentic_harness_runtime.md`
- **Phase 2 Files:** `backend/acute/workspace_engine.py`, `backend/acute/agent_harness.py`, `backend/acute/triage.py`, `backend/acute/verification.py`, `backend/acute/workflows.py`
- **Deliverables:**
  1. **Unified Hybrid Model Runtime (Zero Parallel `llm/` Duplication):** Extend `agent_harness.py` to support GCP Vertex AI `asia-east2` (`gemini-2.5-flash`, `temperature=0.1`) alongside `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` and seamless deterministic offline fallback.
  2. **Wire Real Clinical Pre-Gates:** Loop 1 inbound triage calls `triage.py` (`screen_acute_message`, `extract_intake`); Loop 2 doc ingestion calls `verification.py` (`verify_quote_span`, `doc_identifiers`) + `emr_connector.py` (`verify_three_point_id`).
  3. **Model-Backed Presentation Compiler + 1-Retry Linter Self-Repair Loop:** Bounded 5-slot `< 4,096`-token context compiler → structured JSON slot-fill → `validate_case_presentation` (`5/5`) → bounded 1-retry diagnostic repair on violation → deterministic safe fallback with telemetry logged to `ExecutionTrace`.
  4. **Live Private Sam Assistant & Durable Outbox Execution:** Wire `ask_case_agent` and `compile_instruction_plan` cleanly to the unified harness + harden `workflows.py` `outbox_events` wake/undo handlers & 48h/72h grace-window semantics.

### Track 4 · End-to-End Live Demo Wiring, EMR Connector & Founder Grading Eval Suite
- **Phase 1 Spec:** `docs/research/track4_demo_emr_and_eval_suite.md`
- **Phase 2 Files:** `backend/acute/emr_connector.py`, `backend/acute/adapters.py`, `backend/acute/app.py`, `tests/test_practice_graph_loops.py`, and `../loopbench/` eval pack *(strictly decoupled from `harness/backend/`)*
- **Deliverables:**
  1. **True End-to-End Live Demo Closure (`/workspace` ↔ `/emr` ↔ `/p/qr/...`):** Clicking **`Confirm & Sign Plan`** in `/workspace` runs real `rx_rules.py` CDS, mints a real encrypted PDF via `certificates.py` (when `/cert` is included), writes the compact additive addendum visible immediately in `/emr`, invokes `adapters.py` booking/WhatsApp doorbell stubs, and pushes real-time updates to `/p/qr/pat_lam_urti_demo` via `live_bus.py`.
  2. **Deterministic Practice Graph Management & Live X-Ray Polish:** End-to-end `@ Clinic Playbook` Q&A, Curator approval (`v2.4 → v2.5`), 3-pt ID quarantine → Nurse resolution in `# clinic-team`, and live `Architecture X-Ray` metrics (real token counts, traversed nodes, live `5/5` linter status).
  3. **HK EMR Additive Integration Blueprint:** Concrete read + additive writeback capability matrix for **EC Healthcare stack**, **ClinicSolution X**, and **Apricot/VITA** + Phase-1 zero-friction bridge mapped directly to `emr_connector.py` & `adapters.py`.
  4. **20-Case Founder Grading Eval Suite:** Reproducible synthetic eval pack in `../loopbench/` (calling `harness` via clean test client / HTTP boundary — zero `harness`→`loopbench` imports!) spanning all 8 pathways, Cantonese/code-switching, family proxies, thin intakes, 999 traps, prompt injections, and 2/3 ID mismatches — generating both automated pass/fail metrics AND a clean interactive **HTML + Markdown Founder Grading Sheet** (`docs/research/founder_grading_report.html` & `.md`) ready to review and score in the morning.

---

## 6. Live Convergence Checkpoint (`feat/live-demo-convergence`)

*Updated: 24 Nov 2026 · Base: `feat/live-emr-agent-phone-demo` + `feat/agentic-skills-prompts-memory`*

| Stage / Track | Status | Files & Verification |
| :--- | :---: | :--- |
| **Agentic Core (`feat/agentic-skills-prompts-memory`)** | **PUSHED** (`8781cf4`) | 8 `skills/clinical/*/SKILL.md`, `backend/agentic/*` (skills, prompts, tools, CJK `<4,096` context, Postgres FORCE RLS memory, runner, transports), `tests/test_agentic_core.py` (14/14 pass). |
| **Mandatory Schema Sync Barrier** | **COMMITTED** | `docs/research/SEAM_CONTRACT.md` locking Seams A/B/C across all 4 tracks. |
| **Track 1 · Clinical Knowledge Graph** | **COMMITTED** | `docs/research/track1_clinical_knowledge_graph.md`, `terminology.py`, `rx_rules.py`, `certificates.py`, `practice_graph.py`, `curator.py` (73/73 pytest pass). |
| **Track 2 · Design & 14-Widget UX** | **COMMITTED** | `docs/research/track2_design_ux_widget_catalog.md`, `docs/CLINICAL_UI_DESIGN_SYSTEM.md` (`v1.1`), `ClinicWorkspaceApp.tsx`, `workspace.css` (`npm --prefix frontend run build` pass, 0 TS errors). |
| **Track 3 · Agentic Harness Runtime** | **COMMITTED** | `docs/research/track3_agentic_harness_runtime.md`, `agent_harness.py`, `triage.py`, `verification.py`, `workflows.py`, `workspace_engine.py` (51/51 pytest pass). |
| **Track 4 · Demo Wiring, EMR & 20-Case Eval** | **COMPLETE** | `docs/research/track4_demo_emr_and_eval_suite.md`, `emr_connector.py`, `adapters.py`, `app.py`, `tests/test_practice_graph_loops.py` (77/77 pass), `../loopbench/` 20-case Founder Eval (`20/20` cases, `79/79` assertions, peak `2,534/4,096` tokens), `docs/research/founder_grading_report.{html,md}`. |

---

## 7. Live Phone-to-Workspace Chat Demo Checkpoint (24 Nov 2026)

*Branch: `feat/live-demo-convergence` (harness + loopbench)*

### Completed in This Checkpoint
1. **Blank-Start Live Workspace (`/workspace`):** Defaults to `mode=live` (`GET /api/v1/workspace/overview?mode=live`), starting completely blank (`0` cases) with a centered, scannable Warm-Paper **QR Launchpad** (`<origin>/p/chat`, editable host URL for LAN / Cloudflare tunnel, copy link, live connection status). Seeded mode remains 100% backward-compatible at `GET /api/v1/workspace/overview` (`?mode=seeded` in UI) so all existing tests & 20-case Founder Eval stay green.
2. **Pure Natural Chat Phone App (`/p/chat`, `/p/chat/<journey>`, `/p/qr/<patient_id>`):** Built `frontend/src/v2/patient/PatientChatApp.tsx` + `patientChat.css` — opens completely empty → animated clinic typing bubble → warm Clinic AI welcome & introduction → natural EN/Cantonese conversation with optional subtle demo reply chips.
3. **Slide-Over Patient Journeys Sidebar (5 Demo Stories):** Switch patient perspectives anytime from the phone sidebar (`LAM Wing-Yan` URTI/G6PD/Ferritin, `CHAN Tai Man` Post-QEH Warfarin/Statin, `WONG Mei Ling` Home BP quiet vs wake-up, `LEE Ming Kit` LDL booking vs thin-intake guard, `CHEUNG Kwok-Wai` T2DM + Cantonese hyperbole vs 999 red-flag). Each new journey starts from an empty chat and spawns an independent live workflow thread on the laptop `/workspace`.
4. **Live Agent Activity, Doctor Sign-Off → Phone PDF, 10s Undo & Under-the-Hood X-Ray:** Orchestrated in `backend/acute/live_demo.py` + `app.py` (`/api/v1/demo/live/{journeys,chat/start,chat/state/{key},chat/turn,reset}`). Doctor sign-offs push structured care plan cards + MCHK PDF download buttons directly into the phone chat; 10s Undo cleanly retracts them; **`Under the Hood`** drawer reveals live agent tool calls, 1-click Asymmetric Authority probe (`BLOCKED`), `<4,096`-token budget, and 5/5 linter; **`Reset Demo`** wipes everything back to blank and syncs across laptop + physical phone via `session_epoch` + `demo.reset`.
5. **Hardened Cantonese Prompt Injection Detection:** Extended `backend/agentic/prompts.py` (`detect_prompt_injection_signals`) to catch colloquial HK Cantonese jailbreaks (`唔好理`, `假裝自己係`, `即刻開`, `跳過審批`), fixing `FG-16` detection.
6. **Tests & Build:** `tests/test_live_phone_workspace_demo.py` + `tests/test_engine.py` passing; `npm --prefix frontend run build` clean (0 TS errors).

### Ready for Next Agent Pickup (Disjoint Queue)
1. **Live Browser / Mobile Viewport E2E Verification:** Spin up local server (`uvicorn backend.acute.app:app_from_env --factory --port 8080`) + Playwright/headless Chromium to visually verify the 2-screen choreography (`/workspace` blank QR → `/p/chat` empty → typing bubble → welcome → send message → `/workspace` live thread + sign-off → phone PDF → `Under the Hood` → `Reset Demo`) and fix any visual/UX rough edges.
2. **Rebase / Merge `main` (PR #64 `/demo`) into `feat/live-demo-convergence` & Open Clean PRs:** PR #65 currently has a merge conflict with `main` (PR #64 touched `AppShell.tsx`, `DemoDock.tsx`, `api.ts`). Merge `origin/main` cleanly into `feat/live-demo-convergence`, re-run `bash scripts/check.sh`, and open clean PRs for both `harness` and `loopbench` (never push to `main`).
3. **Optional Live LLM Polish & GCP `asia-east2` Staging Deploy:** Test natural multi-turn chat quality with live Vertex AI `gemini-2.5-flash` (`asia-east2`) if credentials are available, and verify `deploy/deploy_gcp_asia_east2.sh` readiness.


