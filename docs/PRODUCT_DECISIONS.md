# Product Decisions · Clinical Execution Layer & Practice Knowledge Graph

*Last updated: 23 Nov 2026 · Branch `feat/live-emr-agent-phone-demo` (PR #65)*
*Canonical reference for locked product/architecture bets, settled non-goals, and open research questions.*

---

## 1. Locked Decisions

### Product & Commercial Bets
- **Execution Layer for Clinic ROI & Locum Continuity:** Focus on reducing clinical/reception friction, converting post-visit follow-ups into revenue-generating bookings/rebookings, and giving covering locum doctors Day-1 parity with the practice's care standards.
- **3-Layer Clinical Practice Knowledge Graph & Clinic Curator (Core Moat):** Every agent loop traverses a versioned, auditable decision graph rather than flat prompts:
  1. **EBM Base Tree** — Pareto-optimal gold-standard GP pathways + an **Immutable Safety Floor** enforced in code (`SafetyFloorViolation` HTTP 422; cannot be weakened by any clinic overlay).
  2. **Clinic Onboarding Overlay** — Guided onboarding moulds the base tree into a tenant Practice Graph (triage grace windows, corporate benefits, partner labs, nurse scope, Careline AI profile).
  3. **Continuous Clinic Curator** — Clusters recurring doctor `override_diff` telemetry into versioned **Proposed Practice Standards** requiring Lead GP / Medical Director sign-off.
- **Dual-Store Architecture (Sole Clinical SoT):** The Clinic EMR remains the **sole clinical/legal Source of Truth**, accessed via an **additive-only EMR connector** (reads by ID; writes only compact doctor-confirmed addenda, bookings, and order stubs). A separate compliance-gated **Agent Memory Store ("EMR for Agents")** under PostgreSQL 15 FORCE RLS (`clinic_app NOBYPASSRLS`) stores longitudinal trajectories, verified quotes, and loop state.

### Architecture & Runtime Engineering
- **Deterministic First, Bounded LLM Second:** Pre-LLM Python gates (`classify_crisis`, `rx_rules`, 3-point ID verification) run before any model call. Runtime traversal prunes context to only the 2–4 active graph nodes (`< 4,096` tokens) routed to GCP Vertex AI **`asia-east2` (Hong Kong)** via ADC.
- **Durable Multi-Day Loops:** Case lifecycles, stall timers, and irreversible commit Undo windows run on PostgreSQL transactional outbox (`outbox_events`, Temporal-swappable). Product code under `harness/backend/` **never** imports `loopbench`.

### UX & Clinical Safety Invariants
- **One Consistent Workspace (Doctor + Nurse Only):** Fixed workspace (`# my-work`, `# clinic-team`, `@ Clinic Playbook`) + **4-state case queue** with a **Today relevance window**: `Needs Decision` ↔ `Running · Today` (active today only) ↔ `Scheduled / Waiting` (quiet multi-day waits with origin/elapsed strip & future bookings) ↔ `Closed`. Technical inspection sits behind one hidden `Architecture X-Ray` toggle.
- **Deterministic Presentation Compiler & Clinical Design Guide (`docs/CLINICAL_UI_DESIGN_SYSTEM.md`):** Pathway nodes deterministically bind to **13 canonical warm editorial paper (light-mode) widgets** (9 Canvas: `VitalsExamStrip`, `ClinicalScoreStrip`, `MedDiff`, `TrendGraph`, `ResultTable`, `ImagingPreview`, `SourceClip`, atomic `IssueCard`, `ManagementPlan`; 4 Fixed Right Rail: `SafetyCard`, always-on `PreventiveCard`, `ScheduledCard`, `UnifiedThread`) gated by a **5-rule Presentation Linter** (`validate_case_presentation`) that rejects prose duplicating widget data, mixed issues/bundled Rx, essay headers, and non-catalog actions.
- **Empty-by-Default Management Plan & Overlay `/` Primitives:** **`Management Plan`** starts empty on every case and populates idempotently via calm natural-font `+` pills on atomic Issue cards or Preventive Care (never pre-populated, zero coding-font buttons). Typing `/` opens a **floating overlay popover** (zero layout shift, 2–3 concise hints) for the 8 canonical primitives (`/prescribe`, `/order`, `/cert`, `/imaging`, `/refer`, `/nurse`, `/goal`, `/book`), committing via **`Confirm & Sign Plan`** + quiet **10s Undo** + folded `▸ N agent steps`.
- **Unified Right-Rail Careline + Sam Thread (Strict High-Signal Gate):** Single scrollable right-rail thread showing Patient ↔ Sam Careline history above a subtle **`Private · Doctor & Sam only`** divider, with Sam's ephemeral unread note and reply box below. Sam only posts proactively when a deterministic Python clinical score (e.g., `CHA₂DS₂-VASc`, `HAS-BLED`, `HK CVD 10-yr`), notable trend, or accepted GP guideline applies — otherwise stays quietly silent until asked. Unconfirmed clinical gaps appear inline with a minimal amber cue **only when safety-critical**.
- **3-Tier Inbound Triage (Async-First):** (1) Self-serve logistics, approved summaries, and rebookings → Careline AI directly; (2) Small clarifying questions within a clinic-set post-visit/discharge grace window (e.g., `72h`) → `# my-work` with zero artificial reply SLA; (3) New clinical episodes, Rx, or sick notes → share file safety reminder + offer booking options (teleconsult / in-clinic). Urgent red flags immediately exit to clinic urgent phone / 999.
- **Fail-Closed 3-Point ID Gate & Asymmetric Authority:** Incoming lab/discharge PDFs require exact `3/3` match (`MRN/HKID + Name + DOB`); mismatches quarantine strictly to Nurse. Careline AI is configurable (e.g., `Sam · Clinic Careline AI`), never uses medical titles, and never prescribes, signs certificates, withholds medications autonomously, or alarms patients on unreviewed discrepancies. Low-risk stalled loops (`2 nudges / 72h`) route to Nurse first; only high-risk items (Warfarin/INR, abnormal labs) escalate to Doctor.

---

## 2. Explicitly Ruled Out / Non-Goals (Do Not Re-Litigate)

- **No Second EMR / Duplicate Charting:** Never maintain a parallel clinical SoT or write ephemeral chat into the legal chart; consolidate strictly onto the current EMR via additive sync (or our own EMR if ever deployed as primary SoT).
- **No Unpaid Doctor Chat or Real-Time Waiting Stress:** Never display `"Waiting on Dr. X"`, promise non-urgent reply SLAs (e.g., *"reply by 4pm"*), or turn Careline into an uncompensated live-chat clinical Q&A channel.
- **No Unilateral AI Billing or Clinical Autonomy:** AI never decides whether a consult is billed/waived, never holds/alters drugs without doctor confirmation, and never silently mutates Practice Graph clinical standards.
- **No Clinician Impersonation:** Never refer to the AI as `"Resident"`, `"Doctor"`, or `"Clinician"`.
- **No Slop, Essay Headers, Pre-Populated Plans, or Coding-Font UI:** No dark mode or monospace `+` buttons, no pre-populated `"Management Plan Checkout"`, no `"History / Clinical reasoning / Assessment"` essay headers, no prose narrating numbers already in charts, no bundled multi-drug Rx lines, no chatty Sam filler, no bespoke top CTA buttons (`"Adopt QEH Regimen"`), and no jargon (`"working memory"`, `"tacit knowledge"`, raw filenames).
- **No Clinic Admin Persona in MVP Workspace:** Scope stays strictly **Doctor (regular + locum) and Nurse** for now.

---

## 3. Open Questions for Research

1. **Onboarding Moulding Speed:** What is the shortest, lowest-friction guided onboarding format (30–60 min) to mould the EBM Base Tree into a HK clinic's `v1.0` Practice Graph without overwhelming Lead GPs?
2. **HK EMR Connector Interfaces:** Which additive writeback paths (FHIR/HL7, encounter addendum APIs, PDF attachment drops, calendar hooks) are practically exposed in target HK clinic systems (EC Healthcare stack, ClinicSolution, Apricot/VITA) vs. requiring a Phase-1 clipboard/bridge fallback?
3. **Post-Visit Grace Window Calibration:** What grace window (48h vs. 72h, question cap) balances patient goodwill with paid teleconsult/clinic conversion for private HK GP practices?
4. **Multi-Doctor Curator Governance:** In a 5–20 GP group, what override clustering threshold, inter-doctor disagreement resolution UX, and Medical Director review cadence keep the Practice Graph clean and trusted?
5. **HK PDPO & Patient Consent for Agent Memory:** What exact DPP1/DPP3 consent wording, retention lifecycle, and opt-out behavior satisfy HK clinic DPOs for storing longitudinal agent memory alongside the clinic EMR?
6. **Cantonese / Mixed-Code & Proxy Caregiver Safety:** How well do deterministic red-flag gates and triage tiers perform on colloquial HK Cantonese, English/Cantonese code-switching, and family-member proxies messaging on behalf of elderly chronic patients?
7. **Chronic Goal Dashboards (Phase 2):** Which chronic cohorts (HTN, DM, lipids) drive measurable review-booking retention from patient-facing goal targets without introducing clinician alert fatigue?
