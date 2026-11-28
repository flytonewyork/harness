# EC Healthcare Head of AI — Design-Partner Pilot Brief & Live Demo Runbook

**Goal of tomorrow's meeting:** Do **not** pitch an enterprise software contract. Ask for a **zero-risk 2-week Design-Partner Pilot** shadowing **2–3 EC Healthcare GPs** (synthetic or inside-VPC shadow mode; zero patient risk, zero PHI leaving EC).

---

## 1. The Design-Partner Pilot Ask (2 Weeks, Zero-Risk)

| Dimension | Pilot Design |
|---|---|
| **Scope** | Shadow 2–3 EC Healthcare GPs across 1 flagship GP clinic + 1 specialist handoff path (e.g., EC Gastroenterology & Endoscopy Centre). |
| **Data & Safety Posture** | Zero autonomous actions. Runs on synthetic/replayed consults or read-only shadow mode inside EC's Hong Kong environment (`asia-east2`, PDPO Cap. 486). |
| **3 Measurable Pilot KPIs** | 1. **EC Ecosystem Cross-Referral Capture:** % of clinically indicated GP cases auto-drafted for EC Specialists / Endoscopy / Imaging.<br>2. **Closed-Loop Medication Safety:** Real-time pre-sign catches on DDIs, allergies, G6PD, HK Cap. 134, and dose ceilings.<br>3. **Admin Time Eliminated:** Clicks & minutes saved per consult via auto-coded ICD-10/ICPC-2/SNOMED, MCHK sick-leave PDFs, FHIR R4 export, and Bupa/AIA/Bowtie claim summaries. |

---

## 2. Pre-Meeting Checklist (30 Minutes Before Walking In)

1. **Kill Cloud Run Latency (`asia-east2`):**
   ```bash
   bash scripts/prewarm_demo.sh --ensure-warm-instance --reset https://<YOUR_CLOUD_RUN_URL>
   ```
   Confirm every endpoint reports `<150 ms` on the warm pass. Scale back to `--min-instances=0` after the meeting.
2. **Open two browser tabs:**
   - **Tab 1 (Live Demo):** `https://<YOUR_CLOUD_RUN_URL>/c`
   - **Tab 2 (Architecture One-Pager):** `docs/EC_ARCHITECTURE_ONEPAGER.html` (print 2 A4 landscape copies for the table).

---

## 3. 10-Minute Live Demo Script (Frozen Clinician Console `/c`)

### Minute 0–2 · Frame the Harness (One-Page Architecture Diagram)
- **Line:** *"LLMs are becoming a commodity. The moat in Hong Kong healthcare is the deterministic clinical harness between messy Cantonese consults/EMR records and the model—enforcing MCHK medicolegal safety, routing patients across EC Healthcare's own specialist network, and emitting structured data instead of walls of text."*

### Minute 2–5 · Pillar 1: EC Healthcare Cross-Referral & Ecosystem Routing
- Open **LAM Wing-Keung (林永強, 54M)** in `/c` (top of Dr. Kenneth Lam's queue via `‹ N left` or `⌘K`).
- Point to the right-hand **Source Paper**: messy Cantonese/English code-switched note (*"上腹痛同火燒心，persistent epigastric pain for 6 weeks ... lost ~3kg ... father had stomach cancer at 60 ... on Apixaban 5mg BD ... Penicillin allergy"*).
- Click any highlighted verbatim quote → exact span locks in the original record (**0 hallucinated quotes**).
- Point to **Orders** (`Internal Referral: EC Healthcare Gastroenterology & Endoscopy Centre — Urgent 14d OGD + H. pylori; Apixaban hold notice`) and click **"View compiled note"** to show the pre-drafted referral letter with periprocedural DOAC alert.

### Minute 5–7 · Pillar 2: Closed-Loop Safety & HK Medication Guardrails
- Right under **Orders**, point out the live deterministic guardrail banner on **Ibuprofen 400mg TDS**:
  - `Major DDI / DOAC + NSAID` triggered automatically against **Apixaban 5mg BD** (0 LLM calls, `<2 ms`)!
  - Show the mandatory MCHK override rationale input (never pre-filled; backend rejects unsigned/blank overrides with HTTP `422`).
- Optionally flip to **CHAN Tai Man (QEH Discharge)** (Warfarin 3mg vs 5mg dose conflict + duplicate Statin) or **CHEUNG Wing-Kit (70M Flu)** (unknown eGFR renal Oseltamivir choice).

### Minute 7–9 · Pillar 3: Structured Data Extraction (Zero-Click Admin)
- Click **"View compiled note"** on LAM Wing-Keung → show structured dual coding (**ICD-10 `K30` + ICPC-2 `D07` + 3 SNOMED CT codes**) and the **Bupa HK / AIA / Bowtie VHIS** day-case OGD pre-authorisation summary (`CPT 43239`, cashless eligible, `MCHK M14829`).
- Sign & Send (`⌘↵`) → click **MCHK Sick Leave PDF (1d) ↗** to open the encrypted bilingual PDF with SHA-256 verification hash, and note that the full **HL7 FHIR R4 Bundle** (`Patient`, `AllergyIntolerance`, `Encounter`, `Condition`, `Observation`, `MedicationRequest`, `DocumentReference`) is available via `/api/v1/patient/export`.

### Minute 9–10 · Close with the Design-Partner Pilot Ask
- *"We aren't asking for an enterprise contract today. Let us shadow 2–3 EC Healthcare GPs for 2 weeks—zero patient risk, zero PHI leaving EC—and report back with measured referral capture, guardrail catches, and admin clicks saved."*
