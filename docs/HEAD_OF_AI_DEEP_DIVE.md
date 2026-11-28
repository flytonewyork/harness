# Head-of-AI Technical Deep Dive & Grilling Playbook

*Branch: `feat/live-demo-convergence` · Last updated: 24 Nov 2026*
*Purpose: Everything needed to explain the architecture from 30,000 ft down to exact Python modules, SQL tables, security boundaries, failure modes, and honest limits under tough Head-of-AI questioning.*

---

## 1. The 30,000-Foot Mental Model (4 Non-Negotiable Pillars)

If you remember only four things under grilling, anchor every answer to these:

1. **Deterministic Safety Floor First, Bounded LLM Second**
   - The LLM **never** makes clinical safety, triage, prescribing, ID-matching, or risk-scoring decisions.
   - Pre-LLM deterministic Python gates run **before** any model call:
     - Red-flag & 999 crisis screening (`backend/acute/triage.py`)
     - Drug safety CDS: HK Cap. 134, G6PD, allergy cross-reactivity, DDI, renal eGFR (`backend/acute/rx_rules.py`)
     - 3-point identity verification (`backend/acute/emr_connector.py` + `verification.py`)
     - Validated clinical calculators: `CHA₂DS₂-VASc`, `HAS-BLED`, `HK 10-Yr CVD`, `CURB-65`, `PHQ-9`, `eGFR` (`backend/acute/practice_graph.py`)
   - The LLM is bounded to **drafting, summarizing, conversational intake, and filling 5 structured slots** inside a strict `< 4,096`-token context window.

2. **Asymmetric Authority (Structural, Not Prompt-Based)**
   - Agents can **open items, raise urgency, stage drafts, and alert**. They can **never** close clinical loops, dismiss findings, reassure unilaterally, prescribe, sign MCHK certificates, or write signed orders to the EMR.
   - Enforced in code at two layers:
     - `backend/agentic/tools.py` (`ToolRegistry`: `BLOCKED` and `SIGNED_WRITE` tools fail closed **before** handler invocation).
     - `backend/acute/app.py` (RBAC rejects nurse/non-clinician signing with HTTP 403; `rx_rules.py` rejects unsafe Rx with HTTP 422 before any DB commit).

3. **Sole Clinical EMR Source of Truth + Compliance-Gated Agent Memory**
   - We are **NOT** building a second EMR. The clinic's existing EMR stays the sole legal clinical record via an **additive-only connector** (`emr_connector.py`: read by ID; write only compact doctor-confirmed addenda, bookings, order stubs).
   - Separate PostgreSQL 15 **Agent Memory Store** (`agent_memory_entries`, `FORCE ROW LEVEL SECURITY` under non-superuser `clinic_app NOBYPASSRLS`) stores longitudinal trajectories, verbatim source quotes, and loop state.
   - **Private Doctor ↔ Sam Q&A is strictly ephemeral (RAM-only / never written to EMR):** enforced by `assert_zero_sam_chat_leakage()` (`emr_connector.py`) and `EMRIsolationError` (`backend/agentic/memory.py`).

4. **Deterministic Presentation Compiler (14 Finite Widgets + 5-Rule Linter)**
   - Why not let an LLM stream free-form markdown or generative UI into a doctor's chart? Because unstructured output is untestable, verbose ("AI slop"), and risks hiding safety-critical numbers in prose.
   - Every pathway node in the 3-Layer Practice Knowledge Graph binds deterministically to a subset of **14 canonical Warm-Paper widgets** (9 Canvas + 5 Right Rail) and **8 closed `/slash` order primitives** (`rx`, `cert`, `lab`, `imaging`, `refer`, `nurse`, `book`, `goal`).
   - Every compiled case must pass **`validate_case_presentation` (5/5 rules)**:
     - `R1_NO_WIDGET_DUPLICATION` (don't narrate numbers already in `TrendGraph`/`ResultTable`)
     - `R2_ATOMIC_ISSUES_AND_ORDERS` (1 problem = 1 `IssueCard`; never bundle 2 drugs into 1 `rx`)
     - `R3_NO_SLOP_OR_SCAFFOLDING` (no `History:`/`Assessment:` essay headers or chatty filler)
     - `R4_CANONICAL_ACTION_ONTOLOGY` (strict 8-primitive schema + atomic `start|update|stop|continue` Rx verbs)
     - `R5_HIGH_SIGNAL_SAM_GATE` (Sam posts unread **only** on objective scores/guidelines/anomalies; otherwise silent)
   - If a live LLM fails any rule → **bounded 1-retry diagnostic self-repair** (`PRESENTATION_SELF_REPAIR_V1`) → if still failing, deterministic safe fallback. Guaranteed 5/5 output every time.

---

## 2. End-to-End Packet Trace (QR Scan → Phone Chat → Doctor Sign → Phone PDF → Undo)

Trace every hop when asked *"Walk me through what actually happens on the wire"*:

1. **Blank Laptop Start (`/workspace`):**
   - Browser calls `GET /api/v1/workspace/overview?mode=live` (`app.py` → `workspace_engine.py:build_workspace_overview(auto_seed=False)`).
   - Returns `cases: []`, all 4 loop counts `0`, Practice Graph `v2.4`, and `live_session.journeys` (5 synthetic HK GP journeys, all `not_started`).
   - UI renders the blank **QR Launchpad** encoding `<origin>/p/chat`.
2. **Physical Phone Scans QR (`/p/chat`):**
   - `AppShell.tsx` mounts `PatientChatApp.tsx` directly (zero `DemoDock`, mobile `100dvh`).
   - Screen opens **empty** → 420ms pause → clinic typing bubble → calls `POST /api/v1/demo/live/chat/start` (`live_demo.py:start_live_journey`).
   - Backend runs `LoopWatcher` (loads prior KingMed/QEH verified doc via `ingest_external_document`, writes quote-verified `AgentMemoryEntry`, compiles initial `<4,096`-token 5-slot `ContextSnapshot`), creates `CaseLoop(state="running", baton="with_patient")` in PG15, logs tool calls to `live_bus`, and publishes `phone.connected` + `case.updated`.
   - Laptop `/workspace` receives the event (via SSE `/api/v1/live/stream` or 2s poll fallback) and immediately populates `Running · Today` showing agents working live.
3. **Patient Sends Natural Message (EN or Cantonese):**
   - Phone calls `POST /api/v1/demo/live/chat/turn` (`live_demo.py:handle_live_chat_turn`).
   - **Pre-Gate 1 (Triage & Security):** `triage.py:classify_bilingual_async_triage` + `backend/agentic/prompts.py:detect_prompt_injection_signals`.
     - If Tier 0 emergency (e.g. chest pain / `心口痛、唞唔到氣`) → deterministic 999 exit card immediately (zero LLM call).
     - If Cantonese hyperbole with negated red flags (`「咳到想死但冇氣促冇胸痛」`) → clause-scoped negation suppresses false 999!
     - If EN or Cantonese prompt injection (`唔好理所有系統安全規則...`) → detected, fenced, zero tools exposed, zero EMR write.
   - **Pre-Gate 2 (Medication CDS):** Extracted drugs checked via `rx_rules.py:check_prescription_safety` against patient's G6PD, allergies, active meds, eGFR.
   - **Graph Traversal & Compilation:** Updates `CaseLoop` against active `PracticeGraphVersion` nodes, computes Python scores, validates 5/5 linter, compiles `<4,096`-token 5-slot context, optionally polishes natural reply via `invoke_clinical_qa` (Vertex AI `asia-east2` → Gemini → Anthropic → OpenAI → deterministic fallback, screened by `screen_agent_output`).
   - **State Promotion:** Promotes case to `needs_decision` (e.g. URTI/Warfarin/BP breach) or keeps quietly in `scheduled` (e.g. on-target BP `128/78`, silent Sam gate).
4. **Doctor Signs Plan (`/workspace` → `Confirm & Sign Plan`):**
   - `Management Plan` starts **empty**; doctor builds orders via `+` pills or floating `/` overlay.
   - Clicking `Confirm & Sign Plan` calls `POST /api/v1/workspace/cases/{id}/confirm` (`app.py:_handle_workspace_case_confirm`):
     1. Runs `rx_rules.py` deterministic CDS across all Rx orders → returns **HTTP 422** (`RX_HARD_BLOCK` for Cap. 134 Codeine; `RX_OVERRIDE_REQUIRED` for G6PD Aspirin, Penicillin Amoxicillin, Warfarin+NSAID DDI, renal Metformin/Nitrofurantoin) before any DB write.
     2. Mints real **AES-256-GCM encrypted bilingual MCHK Sick Leave PDF** via `certificates.py` (`DocumentVault`, MCHK `^[ML]\d{5}$`, `omit_employer_diagnosis=True` default, SHA-256 verification hash).
     3. Enforces `assert_zero_sam_chat_leakage()` and writes compact **additive EMR addendum** via `emr_connector.py` (visible immediately in `/emr`).
     4. Persists durable `OutboxEvent` (`workflows.py`) with a **10-second Undo window**.
     5. Appends Doctor-Signed Care Plan bubble + MCHK PDF link to `original_chat` and publishes `patient.care_plan_delivered` over `live_bus` → arrives live on the physical phone!
5. **10-Second Undo (`POST /api/v1/workspace/cases/{id}/undo/{trace_id}`):**
   - Cancels pending `OutboxEvent`, retracts additive EMR addendum, cancels staged bookings, deletes staged `Certificate`/`Prescription` rows, removes doctor-signed chat bubble from phone, and publishes `patient.care_plan_retracted`.

---

## 3. Codebase Deep-Dive Map (Where Every Claim Lives in Code)

| Layer | Exact File | Key Symbols / What to Cite |
| :--- | :--- | :--- |
| **Live Demo Orchestrator** | `backend/acute/live_demo.py` | `LIVE_JOURNEYS` (5 stories), `start_live_journey`, `handle_live_chat_turn`, `reset_live_demo`, `LiveDemoCoordinator` (`session_epoch`) |
| **3-Layer Practice Graph** | `backend/acute/practice_graph.py` | 8 HK GP pathways, `SafetyFloorViolation` (HTTP 422), `validate_case_presentation` (`R1`–`R5`), `compute_cha2ds2_vasc`, `compute_has_bled`, `compute_cvd_10yr_tier`, `compute_curb65`, `compute_phq9`, `compute_egfr_ckdepi` |
| **Clinic Curator** | `backend/acute/curator.py` | `ensure_active_practice_graph`, `record_override_diff`, `decide_curator_proposal` (`v2.4 → v2.5` + rollback) |
| **Deterministic Triage** | `backend/acute/triage.py` | `screen_acute_message`, `classify_bilingual_async_triage` (clause-scoped EN/Cantonese negation + hyperbole suppression + 3-tier async triage) |
| **Deterministic Rx CDS** | `backend/acute/rx_rules.py` | `check_prescription_safety` (Cap. 134 hard block, G6PD, allergy cross-reactivity, Warfarin+NSAID DDI, Levothyroxine–iron 4h spacing, Metformin eGFR<30, Nitrofurantoin eGFR<45, mcg/g dose parsing) |
| **MCHK PDF & Vault** | `backend/acute/certificates.py`, `backend/vault/vault.py` | `issue_sick_leave_pdf` (bilingual EN/繁中, AES-256-GCM envelope encryption, SHA-256 hash, `omit_employer_diagnosis`) |
| **EMR Connector & 3-Pt ID** | `backend/acute/emr_connector.py` | `verify_three_point_id` (`3/3` exact MRN/HKID+Name+DOB or quarantine to Nurse Rachel), `assert_zero_sam_chat_leakage`, `SimulatedPostgresEMRAdapter`, HK EMR capability matrix (EC Healthcare, ClinicSolution X, Apricot/VITA) |
| **Agentic Core** | `backend/agentic/{skills,prompts,tools,context,memory,runner,transports}.py` | 8 `skills/clinical/*/SKILL.md` progressive disclosure, SHA-256 prompt fingerprints, `<untrusted_data nonce=...>` XML fencing, CJK-aware `estimate_tokens` (`<4,096` 5-slot compiler), `PHIScrubber` off-HK boundary, `PostgresAgentMemoryStore` FORCE RLS, `EMRIsolationError` |
| **Database & Tenancy** | `backend/acute/db.py` | 27 tenant-scoped tables in `TENANT_SCOPED_TABLES`, PostgreSQL 15 `ENABLE` + `FORCE ROW LEVEL SECURITY`, `clinic_app NOBYPASSRLS`, append-only `audit_events` |
| **Durable Loops** | `backend/acute/workflows.py`, `live_bus.py` | PostgreSQL transactional `outbox_events` (Temporal-swappable), 10s Undo, 72h grace window, SSE + poll sync |
| **Frontend Surfaces** | `frontend/src/v2/patient/PatientChatApp.tsx`, `workspace/ClinicWorkspaceApp.tsx`, `emr/EmrHarnessApp.tsx` | Mobile native chat + journeys sidebar; blank-start QR Warm-Paper 14-widget workspace + Under-the-Hood X-Ray; Simulated EMR inspector |
| **Decoupled Eval Bed** | `../loopbench/src/loopbench/founder_eval.py`, `scenarios/founder_eval/cases_20.yaml` | 20 synthetic HK GP cases (`FG-01`–`FG-20`), 79 assertions, calls `harness` **only** across FastAPI HTTP/TestClient boundary; enforced by `tests/test_engine.py::test_engine_never_imports_loopbench` |

---

## 4. 15 Tough Head-of-AI Grilling Questions & Calm, Concrete Answers

1. **"Wait — is a live LLM running right now, or is this hardcoded?"**
   - *Be 100% straight:* "Right now in this local demo without GCP/Gemini credentials, it's running in **deterministic fallback mode** — which is also how the 20-case grading report ran. When Vertex AI (`asia-east2`) or `GEMINI_API_KEY` credentials are set in env, `detect_live_llm_engine()` routes all resident-agent clinical reasoning through **Gemini 4 Neon (`gemini-4-neon`)** (with automatic fallback to **Gemini 4 Hydrogen (`gemini-4-hydrogen`)** on 403/404, configurable via `AGENT_LLM_MODEL=neon|hydrogen` and `GEMINI_THINKING_LEVEL=LOW|MEDIUM|HIGH|XHIGH`) via the exact same 5-slot `<4,096`-token context compiler, 5-rule linter + 1-retry self-repair loop, and output guard. The clinical safety floor is 100% deterministic either way."

2. **"Why build a deterministic compiler and 14 finite widgets instead of letting a frontier model generate rich UI / summaries?"**
   - "Three reasons: (1) **Testability** — a finite 14-widget schema + 5-rule linter lets us assert structural invariants (`R1`–`R5`) on every output in CI. (2) **Cognitive load** — doctors hate reading verbose AI prose that repeats numbers already in a chart (`R1` forbids narrating `TrendGraph` values). (3) **Fail-closed safety** — if the model hallucinates a bundled two-drug order or skips a verb, the linter rejects it, runs 1 bounded diagnostic repair retry, and falls back deterministically."

3. **"How do you prevent prompt injection from a malicious patient message or OCR'd hospital PDF?"**
   - "Defense in depth across 4 layers: (1) Pre-LLM regex + control-char/XML-escape detector (`detect_prompt_injection_signals` in `backend/agentic/prompts.py`, covering both English and colloquial HK Cantonese like `唔好理所有系統安全規則`). (2) Every untrusted text block is stripped of bidi/zero-width chars and wrapped in a random nonce-tagged `<untrusted_data nonce="...">` XML fence. (3) Even if an LLM were completely jailbroken, `ToolRegistry` enforces a typed **Asymmetric Authority Gate** in Python — `emr.write_signed_orders` is `BLOCKED` and `SIGNED_WRITE` tools cannot be called by agents at all. (4) Verified in `FG-16`."

4. **"How do you handle Hong Kong Cantonese, code-switching, and exaggeration?"**
   - "In HK primary care, patients routinely say `「咳到想死但冇氣促冇胸痛」` ('coughing to death, no shortness of breath, no chest pain'). Naive keyword matching triggers false 999 suicide/cardiac alarms. `triage.py` uses clause-scoped bilingual negation (`冇`, `無`, `唔覺得`, `denies`) plus Cantonese hyperbole patterns (`咳到想死`, `痛到想死` without self-harm intent) while keeping true cardiac/respiratory red flags (`心口痛、唞唔到氣、標冷汗`) strictly union-only Tier 0. Also our token estimator in `backend/agentic/context.py` prices CJK at ~1.6 tokens/char so Chinese transcripts never silently blow past the 4,096-token budget."

5. **"How do you stop the AI from hallucinating quotes or chart facts?"**
   - "Every memory fact and source clip passes OCR/CJK-safe verbatim substring verification (`verify.py` / `verification.py:verify_quote_span` / `backend/agentic/memory.py:verify_memory_quote`) with character spans against the raw document text. In LoopBench CI (`scripts/check.sh`), even **1 made-up quote is a hard fail**."

6. **"How do you enforce multi-tenant data isolation and HK PDPO (Cap. 486)?"**
   - "All 27 tenant tables in PostgreSQL 15 have `ENABLE` + `FORCE ROW LEVEL SECURITY` and the app connects as non-superuser `clinic_app NOBYPASSRLS` with `SET LOCAL app.tenant_id` inside every transaction (`db.py`). Default inference region is GCP Vertex AI `asia-east2` (Hong Kong) with zero-retention headers. If a fallback provider outside HK is ever used, `ContextCompiler` routes the payload through `PHIScrubber` automatically. DPP6 patient data export excludes ephemeral private Sam chat; DPP2/DPP6 erasure deletes agent memories without mutating the legal clinic EMR."

7. **"Why keep a separate Agent Memory Store if the Clinic EMR is the sole Source of Truth?"**
   - "Because clinic EMRs are legal medical records — you cannot dump noisy agent working state, intermediate tool traces, 72-hour nudge counters, or ephemeral doctor↔Sam Q&A into the EMR. We keep a strict wall: the EMR holds the legal clinical record and receives only compact, doctor-confirmed additive addenda (`assert_zero_sam_chat_leakage`). The compliance-gated Agent Memory Store holds longitudinal trajectories, quote-verified extractions, and loop state under FORCE RLS."

8. **"What happens if a hospital discharge fax has the wrong patient's DOB or name?"**
   - "Fail-closed 3-Point ID Gate (`emr_connector.py:verify_three_point_id`). An external document must match `3/3` (MRN/HKID + Name + DOB) to link to a doctor's `# my-work` case. A `2/3` match (like DOB off by 5 days in `FG-17`) is automatically quarantined to Nurse Rachel in `# clinic-team` and never reaches the doctor until manually verified."

9. **"Can a clinic customize protocols without accidentally weakening safety rules?"**
   - "No — Layer 1 of the 3-Layer Practice Knowledge Graph (`practice_graph.py`) is the **Immutable Safety Floor** (999 crisis exit, G6PD/allergy blocks, Cap. 134, 3-point ID gate, doctor-only authority, no medical AI titles like `Resident`/`Doctor`). Any onboarding patch or Curator proposal attempting to weaken `enforce_before_llm` or rename Sam to `Dr. Sam` / `Resident` is rejected in code with **HTTP 422 `SafetyFloorViolation`**."

10. **"How does the Clinic Curator learn without silently drifting clinical behavior?"**
    - "Propose-only curation (asymmetric authority for knowledge). When doctors edit or override staged orders, `record_override_diff` logs the diff pattern. The Curator clusters recurring overrides and stages a versioned diff (`v2.4 → v2.5`) with a SHA-256 content hash for **Lead GP sign-off** — never auto-deployed, always rollbackable."

11. **"Why did you build an empty `Management Plan` instead of pre-checking recommended orders?"**
    - "Automation bias. Pre-checked orders turn clinicians into rubber-stampers. Starting the `Management Plan` empty forces an explicit doctor tap (`+` pill or `/` command) for every clinical order, followed by a deterministic CDS check and a 10-second Undo window before irreversible dispatch."

12. **"What happens if a doctor signs a plan by mistake?"**
    - "Irreversible side effects (EMR addendum finalization, pharmacy dispatch, WhatsApp doorbell) sit in PostgreSQL `outbox_events` with a 10-second Undo window (`workflows.py`). Clicking Undo within 10s cancels the outbox event, retracts the additive EMR addendum, cancels staged bookings, deletes staged Rx/Cert rows, and retracts the signed bubble from the patient's phone (`FG-20`)."

13. **"How do you prevent alert fatigue on chronic patients?"**
    - "4-state durable loops (`Needs Decision`, `Running`, `Scheduled / Waiting`, `Closed`) + `R5_HIGH_SIGNAL_SAM_GATE`. When WONG Mei Ling logs `128/78` (inside her `<135/85` target), the case routes quietly to `Scheduled / Waiting` with `sam_proactive_insight = null` — zero doctor interruption. It wakes into `# my-work` only when a reading breaches `>140/90`. Low-risk booking stalls route to Nurse Rachel first after 2 nudges / 72h."

14. **"How reliable is your eval suite — aren't you grading your own homework?"**
    - *Be completely candid:* "LoopBench lives in a separate repo and calls the harness only across the HTTP/TestClient boundary (`test_engine_never_imports_loopbench` enforces zero imports). Our core LoopBench suite includes blind sets (`blind: true`) that are never tuned on. Tonight's 20-case Founder Grading Suite (`20/20`, `79/79` assertions) is **synthetic, authored by us, and ran deterministically** — it's a fast regression & safety-contract gate, **not** clinical validation and **not** a live-LLM benchmark yet. That's why the interactive HTML report puts every input, trace, and assertion in front of a human reviewer to grade."

15. **"How do you integrate with messy HK clinic EMRs (EC Healthcare / ClinicSolution X / Apricot)?"**
    - "`emr_connector.py` defines an additive-only `EMRConnector` interface with a capability matrix for EC Healthcare's stack, ClinicSolution X, and Apricot/VITA, plus a Phase-1 zero-friction bridge (structured addendum export + HL7/FHIR/webhook adapter). Real production wiring waits on the Phase-0 IT discovery call with EC Healthcare — we don't pretend we already have write APIs into their proprietary EMR."

---

## 5. Zooming Out (Milestone 3): From Tonight's 12-Case Prototype to the Full AI-Native Agentic Clinic

When the CEO or Head of AI asks *"Okay, this prototype works cleanly on 12 GP episodes — what does this actually look like when it runs an entire multi-doctor, multi-site Hong Kong clinic group at scale?"*, map **every single primitive we built in Milestones 1 & 2** directly to its scaled architecture:

| Pillar | What We Built Tonight (Milestones 1 & 2) | What It Scales Into (Full AI-Native Agentic Clinic) |
| :--- | :--- | :--- |
| **1. Patient–Agent Loop Layer** | Physical phone QR chat (`/p/chat`) with **Sam**, typing cadence, EN/Cantonese natural conversation, **12 realistic HK GP episodes**, deterministic 999 exit, post-sign follow-up loop. | **Always-on Omnichannel Clinic Careline** (WhatsApp Business API + clinic app + QR check-in) managing thousands of concurrent longitudinal patient loops (acute, post-discharge, chronic titration, preventive recall) with durable Temporal/Postgres timers, 48h/72h nudges, and zero doctor noise on on-target patients. |
| **2. Doctor Workspace & Canonical Dynamic UI** | Blank-start Warm-Paper `/workspace` compiling **14 finite widgets** + **8 closed `/slash` order primitives** + empty-by-default `Management Plan` + **5-rule Presentation Linter** (`R1`–`R5`). | **Deterministic Clinical UI Compiler** across specialties (GP, Paediatrics, Women's Health, Chronic Care): any clinic workflow compiles strictly into vetted, versioned UI primitives (never raw AI markdown essays), guaranteeing zero automation bias, sub-15-second doctor scan time, and CI-verifiable clinical presentation. |
| **3. 3-Layer Practice Knowledge Graph + Clinic Curator** | 8 executable HK GP pathways (`practice_graph.py`), Immutable Safety Floor (HTTP 422 `SafetyFloorViolation`), Python calculators (`CHA₂DS₂-VASc`, `HAS-BLED`, `CURB-65`, `PHQ-9`, `eGFR`), and Curator `v2.4 → v2.5` diff approval (`curator.py`). | **Federated Multi-Clinic Practice Graph**: Group-level Immutable Safety Floor (Layer 1) + HK/NICE/CHP EBM Graph (Layer 2) + Clinic-specific formulary/locum/billing norms (Layer 3). Every doctor edit feeds the propose-only Curator so the clinic's institutional taste compounds safely across locums and sites without silent drift. |
| **4. Additive EMR Fabric & Compliance-Gated Memory** | `emr_connector.py` (`3/3` ID gate or quarantine to Nurse Rachel, `assert_zero_sam_chat_leakage`), live `/emr?mode=live` additive addenda, and PostgreSQL 15 **FORCE RLS** (`clinic_app NOBYPASSRLS`) `AgentMemoryEntry` across 27 tenant tables. | **Zero-Rip-and-Replace Clinic Fabric**: Sits cleanly over EC Healthcare / ClinicSolution X / Apricot / eHealth (HK) as an additive intelligence layer — existing EMR stays sole legal clinical record, while the FORCE-RLS Agent Memory Graph maintains quote-verified longitudinal patient trajectories with full HK PDPO DPP1–DPP6 auditability. |
| **5. Clinical Reasoning Runtime** | **Gemini 4 Neon (`gemini-4-neon`)** primary + **Gemini 4 Hydrogen (`gemini-4-hydrogen`)** fast worker/fallback (`thinkingConfig.thinkingLevel`, `thought: true` filtering, `< 4,096`-token 5-slot compiler, Vertex AI `asia-east2` HK + off-HK `PHIScrubber`). | **Tiered Sovereign HK Reasoning Mesh**: Deterministic Python gates run first (`< 10ms`) for safety/CDS/ID/scores; **Gemini 4 Hydrogen** handles high-throughput intake extraction & routine quiet loops; **Gemini 4 Neon** handles complex multi-morbidity synthesis & Curator diffing — all inside `asia-east2` zero-retention enclaves with bounded 1-retry linter self-repair. |
| **6. Internal Eval, Error-Analysis & Taste Flywheel** | Strictly separate from product UI: decoupled `../loopbench/` **20-case Founder Grading Eval** (`20/20`, `79/79`) + **12-Episode Multi-Turn Product Taste & Error-Analysis Workbench** (`12/12`, `112/112`, caught real Turn-2 repetition on `cheung` immediately!). | **Continuous Clinical Safety & Taste Flywheel**: Every anonymized doctor override, Nurse quarantine, and red-flag edge case turns into a regression scenario in `loopbench` (split into dev vs blind holdout sets). Weekly founder/Lead-GP error-analysis sessions tune prompt/graph taste before any model or graph version promotes to production — turning "magical AI product taste" from luck into an engineered CI loop. |

### The One-Sentence Architecture Thesis to Close the Meeting
> *"We don't drop a chatbot on top of an EMR. We put a deterministic clinical safety floor underneath, a versioned Practice Knowledge Graph in the middle, a 14-widget Presentation Compiler in front of the doctor, and a decoupled LoopBench error-analysis flywheel behind the curtain — so the patient gets a warm natural conversation, the doctor gets a 15-second decision surface with zero slop, and the Head of AI gets deterministic guarantees they can actually audit."*

