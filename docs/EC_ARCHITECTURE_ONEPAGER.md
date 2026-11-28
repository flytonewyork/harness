# tmrw Clinical Harness — One-Page Architecture (EC Healthcare)

> Print-ready A4 landscape visual version: [`docs/EC_ARCHITECTURE_ONEPAGER.html`](EC_ARCHITECTURE_ONEPAGER.html) (open in Chrome/Safari → Print → Save as PDF).

```mermaid
flowchart LR
  subgraph IN["1. Raw Clinical & Patient Inputs"]
    I1["Cantonese / EN Consults\n(Code-switched notes & intake)"]
    I2["EC Clinic EMR\n(Meds, Allergies, G6PD, eGFR)"]
    I3["HA Discharge PDFs & Labs\n(QEH/QMH, PathLab, eHealth)"]
    I4["WhatsApp Doorbell & Monitors\n(0 PHI in chat; Home BP / RAT)"]
  end

  subgraph HARNESS["2. tmrw Clinical Harness (Model-Agnostic HK Control Plane)"]
    N["A. Cantonese Normalization & Verbatim Quote Anchoring\n(Idiom/negation safety, HKID collision check, char-span quotes)"]
    S["B. Deterministic Safety Floor (<2ms, 0 LLM)\n• Union-only 999 triage\n• HK Cap. 134 hard-blocks\n• DDI (Omeprazole+Clopidogrel, Warfarin/DOAC+NSAID)\n• Allergy, G6PD, eGFR & dose ceilings"]
    R["C. EC Ecosystem Cross-Referral Engine\n• Epigastric pain + Gastric Ca FHx -> EC GI / OGD\n• Refractory BP / AF INR -> EC Cardiology\n• Periprocedural antithrombotic alerts"]
    G["D. Pluggable HK LLM Gateway (asia-east2)\nVertex / VPC / Claude · Git-pinned YAML · Kill switch"]
    A["E. Asymmetric MCHK Authority Gate & LoopBench Eval\nAgent drafts/raises only · MCHK Doctor signs · PG15 Forced RLS · Immutable Audit"]
    N --> S
    N --> R
    N --> G
    S --> A
    R --> A
    G --> A
  end

  subgraph OUT["3. Structured Clinic & EC Network Outputs"]
    O1["EC Internal Referrals\n(GI / Endoscopy / Cardiology\nFHIR ServiceRequest + Summary)"]
    O2["HL7 FHIR R4 / EMR Write-Back\n(ICD-10, ICPC-2, SNOMED, LOINC, RxNorm)"]
    O3["HK Insurer Claim & Pre-Auth\n(Bupa HK, AIA HK, Bowtie VHIS)"]
    O4["MCHK Sick-Leave PDF & Follow-Up\n(AES-256-GCM Vault + SHA-256)"]
  end

  I1 --> N
  I2 --> N
  I3 --> N
  I4 --> N
  A --> O1
  A --> O2
  A --> O3
  A --> O4
```

## Core Architectural Invariants
- **Asymmetric Authority:** The harness and LLM agents can only *open*, *draft*, or *raise urgency*. Only an authenticated Hong Kong MCHK-registered clinician can sign prescriptions, sick-leave certificates, or internal EC specialist referrals.
- **Deterministic Floor First:** Clinical red flags, Hong Kong Cap. 134 Dangerous Drugs, DDIs, allergy cross-reactivity, G6PD, renal dosing, and EC cross-referral triggers execute deterministically in <2 ms with zero LLM involvement.
- **Hong Kong Residency & PDPO (Cap. 486):** Runs in `asia-east2` (Hong Kong) on Cloud Run + Cloud SQL PostgreSQL 15 with Forced Row-Level Security across all 21 tenant-scoped tables and per-document AES-256-GCM Vault encryption.
- **Model-Agnostic & Continuously Gated:** Swap underlying foundation models without rewriting clinical safety rules; every change is scored against `LoopBench` (0 hard fails, 0 made-up quotes).
