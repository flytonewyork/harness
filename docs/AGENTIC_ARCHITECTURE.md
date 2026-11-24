# Agentic Core Architecture: Skills, Prompts, Tools, Context & Memory

*Branch: `feat/agentic-skills-prompts-memory` (zero file overlap with `feat/live-demo-convergence` Tracks 1–4)*

This document describes the standalone production agentic runtime package at `backend/agentic/` and modular clinical runtime skills at `skills/clinical/*/SKILL.md`. It is engineered to drop cleanly into `backend/acute/agent_harness.py` and `backend/acute/workspace_engine.py` once `feat/live-demo-convergence` merges, with **zero merge conflicts** and **zero new database tables**.

---

## 1. Module Inventory & Responsibilities

| Module / Directory | Production Responsibility |
| :--- | :--- |
| `skills/clinical/*/SKILL.md` (8 skills) | Progressive-disclosure clinical runtime skills with strict YAML frontmatter (`autonomy_level`, `authority_ceiling`, `priority`, `max_tokens`, `triggers`, `allowed_tools`, `safety_floor_refs`, `banned_claims`). |
| `backend/agentic/skills.py` | Parses & validates `SKILL.md` files (rejecting `SIGNED_WRITE` escalation or token-cap overflow), computes SHA-256 hashes, selects 1–3 active skills per turn (`select_skills`), and intersects `allowed_tools`. |
| `backend/agentic/prompts.py` | 6 versioned, SHA-256 fingerprinted `PromptTemplate`s + JSON output schemas, nonce-tagged `<untrusted_data>` XML prompt-injection fencing, bilingual EN/ZH jailbreak detector, and post-generation `screen_agent_output` guard. |
| `backend/agentic/tools.py` | Typed `ToolSpec` + `ToolRegistry` with strict runtime JSON-schema argument validation, fail-closed Asymmetric Authority Gate (`BLOCKED`/`SIGNED_WRITE` denied before handler execution), skill allowlist check, idempotent dedup & loop circuit breaker, hallucinated-tool recovery, and Vertex/Gemini + Anthropic + OpenAI native tool adapters. |
| `backend/agentic/context.py` | CJK-aware `estimate_tokens()` (handling HK Traditional Chinese ~1.6 tok/char accurately), deterministic 5-slot `< 4,096`-token `ContextCompiler` (Slot 1 Safety Floor never truncated; priority-ordered pruning across Slots 2–5), and automatic HK PDPO `PHIScrubber` boundary when routed outside `asia-east2`. |
| `backend/agentic/memory.py` | 3-tier `ClinicalMemoryManager` (`emr_memory` read-only cache, verified `episodic`/`longitudinal`/`preference` memory, strictly isolated RAM-only `ephemeral_private` Sam thread), OCR/CJK-safe `verify_memory_quote`, `superseded_by` trajectory + contradiction detection, clinical staleness & PDPO TTL/DPP6 export/erasure, `EMRIsolationError` guard, and `PostgresAgentMemoryStore` reusing existing `agent_memory_entries` FORCE RLS table. |
| `backend/agentic/runner.py` | End-to-end `GovernedAgentRunner`: Deterministic Pre-Gate → Progressive Skills → Memory + `<4,096`-Token Context Compile → Bounded Multi-Step Tool Loop → Output Guard + Bounded 1-Retry Diagnostic Self-Repair → Safe Deterministic Fallback → `AgentRunTrace`. |
| `backend/agentic/transports.py` | `LiveHTTPModelTransport` (Vertex AI `asia-east2` HK ADC + metadata fallback with zero-retention headers, Gemini, Anthropic, OpenAI) + `ScriptedReplayTransport` for deterministic CI & eval testing. |

---

## 2. Clinical Skills Catalog (`skills/clinical/`)

| Skill (`name`) | Always-On | Ceiling | Purpose |
| :--- | :---: | :---: | :--- |
| `safety-triage-sentinel` | **Yes** | `CDS` | Union-only HK 999 / Samaritans crisis floor, Cantonese hyperbole vs red flag, 3-tier async triage. |
| `rx-cds-stewardship` | No | `CDS` | Cap. 134 hard blocks, G6PD/Penicillin allergy blocks, renal eGFR/CrCl, DDI & antibiotic stewardship. |
| `document-id-reconciliation` | No | `MEMORY` | Fail-closed 3/3 HKID+DOB+Name gate, multi-page collision quarantine, verbatim quote MedDiff. |
| `clinical-presentation-compiler` | No | `DRAFT` | 4-stage deterministic 14-widget compiler & `R1`–`R5` anti-slop rules. |
| `sam-private-registrar` | No | `READ` | Ephemeral private Doctor ↔ Sam Q&A, `R5` high-signal gate, strict EMR isolation. |
| `bilingual-hk-intake-proxy` | No | `MEMORY` | Verbatim HK Cantonese preservation, family proxy clarity, thin-intake safety cue (zero premature Rx/cert). |
| `mchk-certificate-compliance` | No | `DRAFT` | MCHK `^[ML]\d{5}$` doctor-only sign-off, 1–7d bounds, `omit_employer_diagnosis: true` default. |
| `preventive-chronic-followup` | No | `DRAFT` | 8 HK GP pathways, acute febrile 14d flu-vax deferral, 2 nudges / 72h → Nurse Rachel escalation. |

---

## 3. Zero-Conflict Wiring Guide (Post-Merge into `agent_harness.py` / `workspace_engine.py`)

Once `feat/live-demo-convergence` merges, wiring `backend/agentic/` into the live runtime takes ~15 lines with zero schema changes:

```python
from backend.agentic import (
    CARE_LINE_PATIENT_TURN_V1,
    CLINICAL_PRESENTATION_SLOT_FILL_V1,
    ClinicalMemoryManager,
    ContextCompiler,
    GovernedAgentRunner,
    PostgresAgentMemoryStore,
    SkillRegistry,
    build_default_clinic_tool_registry,
)
from backend.acute.practice_graph import validate_case_presentation

runner = GovernedAgentRunner(
    skill_registry=SkillRegistry.load_from_directory(),
    tool_registry=build_default_clinic_tool_registry(handlers=live_handlers),
    context_compiler=ContextCompiler(token_ceiling=4096),
    memory_manager=ClinicalMemoryManager(store=PostgresAgentMemoryStore(db)),
)

trace = runner.run_turn(
    template=CLINICAL_PRESENTATION_SLOT_FILL_V1,
    transport=live_transport_or_none,
    tenant_id=tenant_id,
    patient_id=patient_id,
    intent="workspace_compile",
    pathway_node_id=case.pathway_node_id,
    active_graph_nodes=active_nodes,
    clinical_state=deterministic_widgets,
    current_input=latest_turn_text,
    expect_json=True,
    structured_validator=validate_case_presentation,
    deterministic_fallback=lambda: ("", deterministic_summary),
)
```

---

## 4. Security & Regulatory Invariants Verified by `tests/test_agentic_core.py`

1. **Asymmetric Authority:** `emr.write_signed_orders` (`BLOCKED`) and any `SIGNED_WRITE` skill/tool are rejected fail-closed before handler invocation.
2. **Progressive Disclosure & `<4,096` CJK Token Budget:** Stress-tested against massive HK Cantonese + English multi-document transcripts — Slot 1 Safety Floor is never truncated and `total_tokens_est <= 4096` always holds.
3. **Prompt Injection Fencing:** XML breakout tags, zero-width/bidi characters, and EN/ZH jailbreak attempts are detected and defanged inside nonce-tagged `<untrusted_data>` tags.
4. **Verbatim Quote & FORCE RLS Memory:** Unverified/paraphrased quotes are rejected; OCR full-width HK punctuation quotes verify with character spans; PostgreSQL `AgentMemoryEntry` enforces cross-tenant isolation under `clinic_app NOBYPASSRLS`.
5. **Ephemeral Sam EMR Isolation & PDPO:** Private Sam chat raises `EMRIsolationError` if injected into EMR/patient context or writebacks; DPP6 export excludes private Sam notes; DPP2/DPP6 erasure removes agent memories without touching the Clinic EMR.
6. **Zero `loopbench` Import:** Enforced repo-wide by `tests/test_engine.py::test_engine_never_imports_loopbench`.
