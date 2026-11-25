# Forward-Deployment Vision & Config-Driven Clinical OS Playbook

> **Core Thesis:** We are not building one-off clinic chatbots or an EMR replacement. We are building a single **AI-Native Clinical Loop OS (Sidecar Harness)** whose **deterministic rails + canonical primitives** stay constant, while each forward deployment (Corporate Flu & Preventive Campaigns, Multi-Doctor GP Practices, Longevity & Preventive Clinics) is composed rapidly via **declarative configuration (`PracticeGraph` overlays & Pathway Specs)**.

---

## 1. The Architectural Separation (What Never Changes vs. What Changes Per Deployment)

### A. The Shared Base OS (Constant Across All Forward Deployments)
1. **Deterministic Safety & Authority Harness:**
   - Pre-LLM red-flag / 999 emergency exit & prompt-injection fence (`safety.screen_red_flags`).
   - Deterministic Rx/clinical safety gate (drug allergies, G6PD, NSAID/anticoagulant interactions, pregnancy, vaccine contraindications).
   - **Asymmetric Clinical Authority:** AI Resident never signs prescriptions, issues MCHK sick-leave certificates, or closes clinical judgement loops—only MCHK-registered doctors sign.
   - 3-point ID verification & document quarantine (`DocumentIngestion`), encrypted Vault PDFs, 10-second Undo durable outbox, and additive-only EMR write-back (zero private Sam chat leakage).
2. **Canonical Clinical Grammar:**
   - **8 Order Primitives:** `rx`, `cert`, `lab`, `imaging`, `refer`, `goal` (numeric targets + personalised patient care leaflets), `nurse`, `book`.
   - **14-Widget Clinical Canvas:** 9 canvas widgets (`MedDiff`, `TrendChart`, `LabPanel`, `ImagingStudies`, `ClinicalScoreStrip`, `VitalsExam`, `Issues & Orders`, `PreventiveCare`, `AgentThread`) + 5 right-rail widgets (`Alerts`, `ScheduledTasks`, `DocumentVault`, `PatientChat`, `Timeline`), enforced by the 5-rule Presentation Compiler linter.
   - **Patient Deliverables:** Structured Signed e-Prescription PDFs, MCHK Sick-Leave PDFs, Patient-Friendly Investigation Order Slips, Personalised Patient Care Leaflets, Live Slot Booking Cards (`BookingOfferCard`), and Visual File/Photo Upload Cards.
3. **The Three-Channel Operating Model (Universal Outpatient UX):**
   - **`# my-work` (`Needs Decision`):** Strictly clinical exceptions & sign-offs requiring a doctor's decision *right now* (goal: **Inbox Zero**). Signed patients immediately move to `PATIENTS` with **`✓`** (doctor done for the shift).
   - **`# clinic-team`:** Nurse & front-desk execution queue (vaccination bays, phlebotomy, Cantonese adherence calls, document ID verification).
   - **`# ai-team`:** Clinic-owned background loops across days, weeks, and months (Day +2 post-vax watch, Day +3 acute recovery, Day +7 home BP titration check, 7-day INR lab watch, 3-month lipid/longevity recalls) with explicit **wake-up triggers** that alert the *on-duty* GP only on exceptions.
   - **`@ Clinic Playbook`:** Clean Quick Guideline Lookup + chat for locum/on-duty doctors; **`Clinic Curator`** stays strictly inside **`Internal · Lead GP`** governance.

---

### B. The Deployment Config Layer ("Config-per-Forward-Deployment")
Each new clinic, specialty, or corporate rollout should be assembled by swapping **4 declarative config blocks**—not rewriting the platform:

| Config Block | What It Defines | Example: Corporate Flu Campaign (Central Health) | Example: HK GP Group (EC Healthcare) | Example: Longevity / Preventive Clinic |
| :--- | :--- | :--- | :--- | :--- |
| **1. `PracticeGraph` Overlay** | Clinic standards, local formulary, preventive triggers, safety overrides | HK CHP flu contraindications, recombinant egg-free vaccine rule, age/sex upsell triggers (Shingrix, Mammogram, FIT) | HK GP antibiotic stewardship (Augmentin 375mg TDS for G6PD UTI, no URTI Abx), MCHK MC rules, QEH Warfarin MedDiff | Optimal vs standard biomarker ranges (ApoB, Lp(a), hs-CRP, HbA1c), DEXA & CGM protocols |
| **2. Pathway Intake Specs** | Required facts/docs to collect (1 question at a time) before routing | Fever >38°C, egg/vaccine anaphylaxis, GBS, anticoagulants, pregnancy | Red flags, home vitals, pregnancy status, QEH discharge photo upload, CVD risk inputs | Family history, wearables/blood PDF upload, lifestyle & supplement adherence |
| **3. Canvas & Order Presets** | Which of the 14 widgets mount & which of the 8 orders are pre-staged | `Alerts` + `PreventiveCare` + `book` (10-min Nurse Bay) + `lab` + `leaflet` | `MedDiff` + `TrendChart` + `ClinicalScoreStrip` + `rx`/`cert`/`lab`/`leaflet` | Multi-marker `LabPanel` + `TrendChart` (ApoB/HbA1c/BP) + `goal` + `lab` + `imaging` (DEXA) |
| **4. `# ai-team` Loop & Wake-Up Rules** | Async timers, autonomous check-ins, and exception escalation rules | Day +2 post-vax check; Day +5 fever-free auto-rebook; 8w Shingrix Dose 2 recall | Day +3 acute recovery; Day +7 home BP loop (wake if >140/90); 7d INR watch | Day +30 adherence nudge; Day +90 biomarker retest slip; auto-close if in optimal zone |

---

## 2. Operationalising Forward Deployments (Our Repeat Playbook)

### Step 1 · Land with a Narrow, High-ROI Wedge (Shadow / Sidecar First)
Never try to replace the clinic's EMR on Day 1. Pick **one narrow workflow** with immediate operational pain and bounded clinical risk:
1. **Corporate Flu & Preventive Health Campaigns** (e.g., Central Health): high volume, massive front-desk savings, zero doctor interruption on routine jabs, instant preventive screening revenue capture, PDPO-safe `/corp` HR portal (`k-anonymity < 5`).
2. **Longevity / Chronic Preventive Loops:** high margin, multi-month `# ai-team` retention loops, tech-forward patients, rich longitudinal biomarker canvas.
3. **High-Volume GP Acute + Post-Discharge Reconciliation:** proves full clinical safety & locum alignment once EMR additive sync is connected.

### Step 2 · Run the 4-Step Forward-Deployment Sprint
1. **Discovery (Day 1–2):** Capture the clinic's top 3–5 clinical standards, formulary preferences, nurse vs doctor split, and patient communication tone (EN / Cantonese).
2. **Configure Pack (Day 3–5):** Author a dedicated **Deployment Pack** (declarative `PracticeGraph` overlay + intake specs + nurse/doctor slot profiles + bilingual patient leaflets + `# ai-team` wake-up triggers) without touching core harness code.
3. **Shadow Mode Live Pilot (Weeks 2–4):** Connect real WhatsApp Business / QR MagicLink; AI Resident drafts into `# my-work`, `# clinic-team`, and `# ai-team` side-by-side with the existing EMR. Measure:
   - `% of episodes closed with zero doctor interruption`
   - `Median doctor sign-off latency (< 45s)`
   - `Preventive screening / follow-up conversion rate`
   - `Zero safety floor / PDPO violations`
4. **Harden & Expand (Month 2+):** Capture recurring clinician edits via **`Clinic Curator`** (Lead GP approved) to evolve the clinic's `PracticeGraph`, then compose the next clinical workflow (e.g., expanding from Corporate Flu → Annual Executive Checkups → Chronic BP/Lipids) in days rather than months.

---

## 3. Immediate Engineering North Star (Post-Demo)
- **Decouple Pathway Specs from Python:** Migrate hardcoded demo journey branches (`live_demo.py`, `live_journeys_extra.py`) into a **declarative Pathway & Loop Runner** executed from `PracticeGraphVersion` JSON/YAML specs so composing a new forward deployment is pure configuration.
- **Keep Deployment Packs Isolated & Composable:** Support `?pack=gp`, `?pack=corporate_health`, `?pack=longevity` on top of the exact same harness, UI primitives, and test suite.
