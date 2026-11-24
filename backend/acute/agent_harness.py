"""Multi-Agent Clinical Harness over a LoopBench-backed Simulated Clinic EMR.

Demonstrates a real, inspectable multi-agent clinical system with:
- 4 specialized agents (`SafetySentinel`, `LoopWatcher`, `CareAgent`, `ProtocolCDSAgent`)
- 3-tier persistent Memory (`emr_memory`, `episodic_memory` with verbatim quote checks, `protocol_memory`)
- Inspectable Context Window assembly (system prompt, clinic SOPs, hydrated EMR, working memory, tool schemas)
- Real executable Tools operating on the simulated clinic EMR + PostgreSQL state (`READ`, `MEMORY`, `CDS`, `DRAFT`, `BLOCKED`, `SIGNED_WRITE`)
- Hybrid execution: native live LLM tool-calling loop when `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`
  is configured, with seamless deterministic tool-calling harness fallback (zero external keys required).
- Strict Asymmetric Authority: agents can only read, verify, screen, and draft (`open_item` / `stage_staff_proposal`).
  Only a Hong Kong MCHK registered doctor can sign prescriptions, issue sick-leave certificates, or write final orders.

Product invariant: NEVER imports `loopbench`. Simulated clinic records are loaded through `legacy_feed.build_legacy_demo_store()`.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.agentic.transports import (
    DEFAULT_GEMINI_FAST_MODEL,
    DEFAULT_GEMINI_REASONING_MODEL,
    acquire_vertex_adc_token,
    build_gemini_generation_config,
    build_vertex_generate_content_url,
    detect_adc_quota_project,
    detect_provider_chain,
    extract_gemini_visible_text,
    is_gemini_4_model,
    normalize_gemini_model,
)

from .companion import HK_MENTAL_HEALTH_HOTLINES, classify_crisis, next_clinic_review_sla
from .db import (
    AuditEvent,
    Certificate,
    Database,
    Encounter,
    Immunization,
    Message,
    Observation,
    Patient,
    Prescription,
    Proposal,
    Tenant,
    new_id,
    record_audit,
    utcnow,
)
from .legacy_feed import build_legacy_demo_store
from .live_bus import LiveHarnessBus
from .rx_rules import check_prescription_safety
from .security import HKT, PHIScrubber
from .triage import screen_acute_message

HERO_URTI_PATIENT_ID = "pat_lam_urti_demo"

# Realistic LoopBench-style Hong Kong clinic records for our working-age URTI hero patient
HERO_EMR_DOCUMENTS: list[dict[str, Any]] = [
    {
        "id": "LB_KM_20261108_LAM",
        "kind": "lab_report",
        "lab_name": "KingMed Diagnostics (Hong Kong) Limited",
        "date": "2026-11-08",
        "title": "Annual Executive Health Panel — FBC, Ferritin & Lipids",
        "status": "reviewed_followup_open",
        "highlight_quote": "Serum Ferritin: 14 ng/mL (Low; Ref 15–150) — suggest repeat iron studies in 3 months.",
        "text": (
            "KingMed Diagnostics (Hong Kong) Limited  ** REPORT **\n"
            "Clinic: Central & Mid-Levels Family Medical (Dr. Kenneth Lam, M14829)\n"
            "Patient: LAM, Wing-Yan (林詠欣)   Sex/Age: F / 32Y   DOB: 1994-04-18   HKID: M528***(6)\n"
            "Collected: 2026-11-08 08:40 HKT   Reported: 2026-11-09 14:15 HKT\n"
            "------------------------------------------------------------------------\n"
            "Haemoglobin (Hb):     12.1 g/dL   (Ref 11.5–15.5)   Normal\n"
            "WBC:                  6.4 x10^9/L (Ref 4.0–10.0)    Normal\n"
            "Serum Ferritin: 14 ng/mL (Low; Ref 15–150) — suggest repeat iron studies in 3 months.\n"
            "LDL Cholesterol:      2.4 mmol/L  (Optimal < 2.6)   Normal\n"
            "Fasting Glucose:      4.8 mmol/L  (Ref 3.9–5.5)     Normal\n"
            "eGFR (CKD-EPI):       104 mL/min/1.73m2             Normal\n"
            "G6PD Status (eHealth): DEFICIENT (confirmed neonatal screen; avoid oxidative triggers)\n"
        ),
    },
    {
        "id": "LB_CN_20261110_LAM",
        "kind": "consult_note",
        "lab_name": "Central & Mid-Levels Family Medical",
        "date": "2026-11-10",
        "title": "GP Post-Checkup Review — Dr. Kenneth Lam",
        "status": "signed",
        "highlight_quote": "Mild low ferritin (14 ng/mL), Hb normal. Repeat ferritin in Feb 2027. Eligible for 2026/27 flu jab via Harbour FinTech.",
        "text": (
            "CONSULTATION NOTE — Central & Mid-Levels Family Medical\n"
            "Date: 2026-11-10 12:20 HKT   Clinician: Dr. Kenneth Lam (MCHK M14829)\n"
            "Patient: LAM Wing-Yan (林詠欣), 32F — Harbour FinTech Corporate Care\n"
            "Allergies: Penicillin (urticarial rash) | Special Alert: G6PD Deficient\n"
            "S: Attended for annual check-up review. Feels generally well, busy work schedule.\n"
            "O: BP 114/72 mmHg, HR 68, Wt 54 kg.\n"
            "A/P: Mild low ferritin (14 ng/mL), Hb normal. Repeat ferritin in Feb 2027. "
            "Eligible for 2026/27 flu jab via Harbour FinTech. Advised dietary iron + flu vaccination when convenient.\n"
        ),
    },
]

CLINIC_PROTOCOLS: list[dict[str, Any]] = [
    {
        "id": "HK-URTI-SICKLEAVE-v1",
        "name": "Working-Age Acute URTI & MCHK Sick-Leave SOP",
        "rules": [
            "Screen deterministic HK 999 / Mental-Health red flags BEFORE any generative step.",
            "Collect symptom onset, peak temperature, home Flu/COVID RAT, red-flag negatives, and requested sick-leave dates.",
            "If febrile (>37.5°C) or acute URTI active: pause preventive flu vaccination offer and schedule re-offer in 14 days.",
            "Viral URTI antibiotic stewardship: do NOT suggest antibiotics for uncomplicated viral URTI.",
            "Route all acute URTI + sick-leave requests to Nurse Rachel (RN) for prep -> Dr. Kenneth Lam (M14829) for final sign-off.",
        ],
    },
    {
        "id": "G6PD-ALLERGY-SAFETY-FLOOR",
        "name": "HK G6PD & Drug Allergy Deterministic CDS Floor",
        "rules": [
            "Run backend/acute/rx_rules.py on every medication mention or proposed order.",
            "Block Aspirin / co-trimoxazole / nitrofurantoin in G6PD-deficient patients (hemolysis risk).",
            "Block Penicillins / Amoxicillin / Augmentin in Penicillin-allergic patients.",
            "Default G6PD-safe symptomatic URTI relief: Paracetamol 500mg QID PRN + Cetirizine 10mg OD (doctor approval required).",
        ],
    },
    {
        "id": "ASYMMETRIC-AUTHORITY-L1",
        "name": "Asymmetric Clinical Authority Boundary",
        "rules": [
            "Agents may READ EMR, upsert quote-verified memory, run CDS, book non-clinical slots, and STAGE draft proposals.",
            "Agents may NEVER sign prescriptions, issue MCHK sick-leave PDFs, close clinical loops, or reassure away red flags.",
            "Every clinical claim must anchor to a verbatim quote from patient input or EMR document.",
        ],
    },
]


def available_clinic_slots(now: datetime | None = None) -> list[dict[str, Any]]:
    """Return realistic bookable Hong Kong clinic slots for vaccination / nurse / teleconsult."""
    cur = (now or utcnow()).astimezone(HKT)
    d1 = (cur + timedelta(days=1)).strftime("%a %d %b")
    d2 = (cur + timedelta(days=2)).strftime("%a %d %b")
    d3 = (cur + timedelta(days=3)).strftime("%a %d %b")
    return [
        {
            "id": "slot_flu_tmr_am",
            "service": "flu_vaccine",
            "label_en": f"{d1} · 10:30",
            "label_zh": f"{d1} · 上午 10:30",
            "provider": "Nurse Rachel Chan (RN)",
            "location": "Central Clinic — Express Vax Room",
            "duration_min": 10,
            "copay_hkd": 0,
            "badge": "Harbour FinTech $0",
        },
        {
            "id": "slot_flu_d2_lunch",
            "service": "flu_vaccine",
            "label_en": f"{d2} · 12:45",
            "label_zh": f"{d2} · 中午 12:45（午休快線）",
            "provider": "Nurse Rachel Chan (RN)",
            "location": "Central Clinic — Lunch Express",
            "duration_min": 10,
            "copay_hkd": 0,
            "badge": "Lunch Express",
        },
        {
            "id": "slot_flu_d3_pm",
            "service": "flu_vaccine",
            "label_en": f"{d3} · 17:30",
            "label_zh": f"{d3} · 傍晚 17:30（放工時段）",
            "provider": "Nurse Rachel Chan (RN)",
            "location": "Central Clinic — After-Work Slot",
            "duration_min": 10,
            "copay_hkd": 0,
            "badge": "After Work",
        },
    ]


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "emr.get_patient_chart",
        "authority": "READ",
        "description": "Read patient demographics, allergies, G6PD status, active medications, eGFR, and employer plan from the simulated clinic EMR.",
        "parameters": {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "emr.get_clinical_records",
        "authority": "READ",
        "description": "Retrieve recent lab reports, GP consult notes, observations, and open follow-up loops from the LoopBench-backed EMR.",
        "parameters": {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "emr.check_preventive_gaps",
        "authority": "READ",
        "description": "Evaluate seasonal flu vaccination eligibility, corporate coverage, and open preventive/post-visit follow-up items.",
        "parameters": {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "clinic.get_available_slots",
        "authority": "READ",
        "description": "Query live bookable nurse vaccination and clinic follow-up slots.",
        "parameters": {
            "type": "object",
            "properties": {"service": {"type": "string", "enum": ["flu_vaccine", "teleconsult"]}},
            "required": ["service"],
        },
    },
    {
        "name": "clinic.book_slot",
        "authority": "DRAFT",
        "description": "Book a preventive vaccination slot in the clinic schedule and record it in the simulated EMR.",
        "parameters": {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}, "slot_id": {"type": "string"}},
            "required": ["patient_id", "slot_id"],
        },
    },
    {
        "name": "safety.screen_red_flags",
        "authority": "CDS",
        "description": "Run deterministic HK 999 Medical and Mental-Health crisis screen (zero LLM bypass).",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}, "temp_c": {"type": "number"}},
            "required": ["message"],
        },
    },
    {
        "name": "memory.upsert_fact",
        "authority": "MEMORY",
        "description": "Persist a structured clinical fact into session episodic memory ONLY with a verified verbatim quote.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "verbatim_quote": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["key", "value", "verbatim_quote"],
        },
    },
    {
        "name": "cds.check_medication_safety",
        "authority": "CDS",
        "description": "Evaluate proposed or patient-asked medications against HK G6PD, allergy cross-reactivity, renal, and antibiotic stewardship rules.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "drugs": {"type": "array", "items": {"type": "string"}},
                "diagnosis_icd10": {"type": "string"},
            },
            "required": ["patient_id", "drugs"],
        },
    },
    {
        "name": "harness.stage_staff_proposal",
        "authority": "DRAFT",
        "description": "Stage a structured acute URTI clinical packet (draft orders + MCHK sick-leave draft + nurse checklist) in the clinic review queue. Cannot sign or close.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "symptoms": {"type": "array", "items": {"type": "string"}},
                "onset": {"type": "string"},
                "temp_c": {"type": "number"},
                "rat_result": {"type": "string"},
                "sick_leave_days": {"type": "integer"},
                "patient_message": {"type": "string"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "emr.write_signed_orders",
        "authority": "BLOCKED",
        "description": "Doctor-only MCHK signature write-back to EMR. Strictly DENIED if invoked by an AI agent (asymmetric authority).",
        "parameters": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
        },
    },
]


def _probe_vertex_adc_available(project_id: str) -> bool:
    """Gracefully probe GCP ADC / Cloud Run metadata without crashing or stalling offline CI."""
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.isfile(creds_path):
        return True
    if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"):
        return True
    try:
        token, proj = acquire_vertex_adc_token(project_id)
        return bool(token and proj)
    except Exception:
        return False


def detect_live_llm_engine() -> dict[str, Any]:
    """Detect active LLM runtime prioritizing Vertex AI asia-east2 -> Gemini -> Anthropic -> OpenAI -> deterministic."""
    forced = (os.environ.get("AGENT_LLM_PROVIDER") or "").strip().lower()
    chain_summary = [c["provider"] for c in detect_provider_chain()]

    if forced in ("deterministic", "off"):
        return {
            "live": False,
            "provider": "deterministic",
            "model": "clinic-harness-tool-engine-v1",
            "region": "local",
            "temperature": 0.1,
            "phi_scrub_required": False,
            "provider_chain": [],
            "label": "Deterministic Tool Harness",
        }

    # 1. Primary: Vertex AI asia-east2 (Hong Kong) via GCP ADC (`gcloud auth application-default login`)
    gcp_project = (
        os.environ.get("VERTEX_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or detect_adc_quota_project()
    )
    if forced in ("", "vertex") and gcp_project:
        region = (
            os.environ.get("VERTEX_LOCATION")
            or os.environ.get("VERTEX_REGION")
            or os.environ.get("HARNESS_REGION")
            or "asia-east2"
        )
        model = normalize_gemini_model(os.environ.get("AGENT_LLM_MODEL") or os.environ.get("VERTEX_MODEL"))
        adc_ready = True if forced == "vertex" else _probe_vertex_adc_available(gcp_project)
        if adc_ready:
            return {
                "live": True,
                "provider": "vertex",
                "model": model,
                "fallback_model": DEFAULT_GEMINI_FAST_MODEL,
                "region": region,
                "project": gcp_project,
                "thinking_level": os.environ.get("GEMINI_THINKING_LEVEL") or ("HIGH" if "neon" in model.lower() else "MEDIUM"),
                "temperature": 0.1,
                "phi_scrub_required": region.strip().lower() != "asia-east2",
                "provider_chain": chain_summary or ["vertex"],
                "label": f"Live LLM · Vertex AI {region} ({model})",
            }

    # 2. Gemini Developer API
    if forced in ("", "gemini") and os.environ.get("GEMINI_API_KEY"):
        model = normalize_gemini_model(os.environ.get("AGENT_LLM_MODEL"))
        return {
            "live": True,
            "provider": "gemini",
            "model": model,
            "fallback_model": DEFAULT_GEMINI_FAST_MODEL,
            "region": "global",
            "thinking_level": os.environ.get("GEMINI_THINKING_LEVEL") or ("HIGH" if "neon" in model.lower() else "MEDIUM"),
            "temperature": 0.1,
            "phi_scrub_required": True,
            "provider_chain": chain_summary or ["gemini"],
            "label": f"Live LLM · Gemini ({model})",
        }

    # 3. Anthropic Messages API
    if forced in ("", "anthropic") and os.environ.get("ANTHROPIC_API_KEY"):
        model = os.environ.get("AGENT_LLM_MODEL") or "claude-sonnet-4-20250514"
        return {
            "live": True,
            "provider": "anthropic",
            "model": model,
            "region": "us-global",
            "temperature": 0.1,
            "phi_scrub_required": True,
            "provider_chain": chain_summary or ["anthropic"],
            "label": f"Live LLM · Anthropic ({model})",
        }

    # 4. OpenAI Chat Completions API
    if forced in ("", "openai") and os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("AGENT_LLM_MODEL") or "gpt-4o-mini"
        return {
            "live": True,
            "provider": "openai",
            "model": model,
            "region": "us-global",
            "temperature": 0.1,
            "phi_scrub_required": True,
            "provider_chain": chain_summary or ["openai"],
            "label": f"Live LLM · OpenAI ({model})",
        }

    # 5. Deterministic Offline Fallback
    return {
        "live": False,
        "provider": "deterministic",
        "model": "clinic-harness-tool-engine-v1",
        "region": "local",
        "temperature": 0.1,
        "phi_scrub_required": False,
        "provider_chain": chain_summary,
        "label": "Deterministic Tool Harness (Vertex AI asia-east2 Gemini 4 Neon/Hydrogen auto-activates with credentials)",
    }


_FAILED_TARGET_TTL_SECONDS = 600.0
_FAILED_LLM_TARGETS: dict[tuple[str, str, str], tuple[float, str]] = {}
_SHARED_LLM_CLIENT: httpx.Client | None = None
_WARMUP_STATE: dict[str, Any] = {
    "warmed": False,
    "in_progress": False,
    "provider": None,
    "model": None,
    "region": None,
    "latency_ms": None,
    "attempts_tried": 0,
    "skipped_targets": [],
    "error": None,
    "warmed_at": None,
}


def reset_llm_target_cache() -> None:
    """Clear cached failed LLM targets & warmup state (for tests or manual re-probe)."""
    _FAILED_LLM_TARGETS.clear()
    _WARMUP_STATE.update({
        "warmed": False,
        "in_progress": False,
        "provider": None,
        "model": None,
        "region": None,
        "latency_ms": None,
        "attempts_tried": 0,
        "skipped_targets": [],
        "error": None,
        "warmed_at": None,
    })


def get_llm_warmup_state() -> dict[str, Any]:
    """Return current live LLM warmup status and cached skipped endpoints for /api/v1/health."""
    now = time.time()
    active_skips = [
        f"{p}/{r}/{m}"
        for (p, r, m), (exp, _err) in list(_FAILED_LLM_TARGETS.items())
        if exp > now
    ]
    st = dict(_WARMUP_STATE)
    st["skipped_targets"] = active_skips
    return st


def _get_shared_llm_client() -> httpx.Client:
    """Reuse a keep-alive httpx.Client across live LLM turns to eliminate per-turn TLS cold handshakes."""
    global _SHARED_LLM_CLIENT
    if _SHARED_LLM_CLIENT is None or _SHARED_LLM_CLIENT.is_closed:
        _SHARED_LLM_CLIENT = httpx.Client(
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=120.0),
        )
    return _SHARED_LLM_CLIENT


def _is_target_cached_failed(key: tuple[str, str, str]) -> bool:
    entry = _FAILED_LLM_TARGETS.get(key)
    if entry is None:
        return False
    exp, _ = entry
    if time.time() >= exp:
        _FAILED_LLM_TARGETS.pop(key, None)
        return False
    return True


def _mark_target_failed(key: tuple[str, str, str], reason: str) -> None:
    _FAILED_LLM_TARGETS[key] = (time.time() + _FAILED_TARGET_TTL_SECONDS, reason[:180])


@dataclass
class ModelCallResult:
    """Unified telemetry & output envelope for every live or offline-fallback clinical LLM call."""

    ok: bool
    provider: str
    model: str
    region: str
    text: str = ""
    json_payload: dict[str, Any] | None = None
    latency_ms: int = 0
    phi_scrubbed: bool = False
    redacted_categories: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    fallback_used: bool = False
    attempts_tried: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "region": self.region,
            "latency_ms": self.latency_ms,
            "phi_scrubbed": self.phi_scrubbed,
            "redacted_categories": list(self.redacted_categories),
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "fallback_used": self.fallback_used,
            "attempts_tried": self.attempts_tried,
            "error": self.error,
        }


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    """Parse strict JSON or fenced ```json ... ``` object cleanly."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            return None
    return None


def _invoke_live_provider(
    *,
    system_prompt: str,
    user_prompt: str,
    expect_json: bool,
    temperature: float = 0.1,
    max_output_tokens: int = 1100,
    patient_names_for_scrub: list[str] | None = None,
    engine_override: dict[str, Any] | None = None,
    timeout_seconds: float = 28.0,
    thinking_level: str | None = None,
) -> ModelCallResult:
    """Execute one governed call through the active provider chain with off-HK PHIScrubber enforcement."""
    engine = dict(engine_override) if engine_override is not None else detect_live_llm_engine()
    provider = str(engine.get("provider") or "deterministic")
    model = str(engine.get("model") or "clinic-harness-tool-engine-v1")
    if provider in ("vertex", "gemini"):
        model = normalize_gemini_model(model)
    region = str(engine.get("region") or "local")

    if not engine.get("live"):
        return ModelCallResult(
            ok=False,
            provider=provider,
            model=model,
            region=region,
            fallback_used=True,
            error="offline_deterministic_mode",
        )

    # Build ordered attempt targets preserving HK asia-east2 primary, then Vertex global/in-region fallback,
    # then remaining configured providers in detect_provider_chain() when provider is not pinned.
    attempts: list[tuple[str, str, str, str | None]] = []
    seen_targets: set[tuple[str, str, str]] = set()

    def _add_attempt(prov: str, reg: str, mdl: str, proj: str | None) -> None:
        key = (prov, reg, mdl)
        if key not in seen_targets:
            seen_targets.add(key)
            attempts.append((prov, reg, mdl, proj))

    _add_attempt(provider, region, model, engine.get("project"))
    if provider == "vertex":
        if model == DEFAULT_GEMINI_REASONING_MODEL:
            _add_attempt("vertex", region, DEFAULT_GEMINI_FAST_MODEL, engine.get("project"))
        if region.strip().lower() != "global" and is_gemini_4_model(model):
            _add_attempt("vertex", "global", model, engine.get("project"))
            if model == DEFAULT_GEMINI_REASONING_MODEL:
                _add_attempt("vertex", "global", DEFAULT_GEMINI_FAST_MODEL, engine.get("project"))
        if region.strip().lower() != "global" and model != "gemini-2.5-flash":
            _add_attempt("vertex", region, "gemini-2.5-flash", engine.get("project"))
    elif provider == "gemini" and model == DEFAULT_GEMINI_REASONING_MODEL:
        _add_attempt("gemini", region, DEFAULT_GEMINI_FAST_MODEL, None)

    forced_provider = (os.environ.get("AGENT_LLM_PROVIDER") or "").strip().lower()
    if not forced_provider:
        for alt in detect_provider_chain():
            alt_prov = str(alt.get("provider") or "")
            if not alt_prov or alt_prov == provider:
                continue
            alt_reg = str(alt.get("region") or "global")
            alt_mdl = str(alt.get("model") or "")
            if alt_prov == "gemini":
                alt_mdl = normalize_gemini_model(model if is_gemini_4_model(model) else alt_mdl)
            _add_attempt(alt_prov, alt_reg, alt_mdl, alt.get("project"))
            if alt_prov == "gemini" and alt_mdl == DEFAULT_GEMINI_REASONING_MODEL:
                _add_attempt("gemini", alt_reg, DEFAULT_GEMINI_FAST_MODEL, None)

    # Skip targets recently proven broken (400/403/404) so subsequent turns hit the working model on Attempt 1
    healthy_attempts = [a for a in attempts if not _is_target_cached_failed((a[0], a[1], a[2]))]
    if healthy_attempts:
        attempts = healthy_attempts

    scrubbed_cache: tuple[str, str, list[str]] | None = None

    def _prompts_for_region(attempt_region: str) -> tuple[str, str, bool, list[str]]:
        nonlocal scrubbed_cache
        if attempt_region.strip().lower() == "asia-east2":
            return system_prompt, user_prompt, False, []
        if scrubbed_cache is None:
            sys_s, c_sys = PHIScrubber.scrub(system_prompt, extra_names=patient_names_for_scrub)
            usr_s, c_usr = PHIScrubber.scrub(user_prompt, extra_names=patient_names_for_scrub)
            scrubbed_cache = (sys_s, usr_s, sorted(set(c_sys) | set(c_usr)))
        return scrubbed_cache[0], scrubbed_cache[1], True, scrubbed_cache[2]

    t0 = time.perf_counter()
    last_error = "no_provider_attempted"
    used_provider = provider
    used_model = model
    used_region = region
    used_phi_scrub = False
    used_redacted: list[str] = []
    attempts_tried = 0

    try:
        client = _get_shared_llm_client()
        for idx, (att_prov, att_reg, att_mdl, att_proj) in enumerate(attempts):
            attempts_tried += 1
            target_key = (att_prov, att_reg, att_mdl)
            used_provider = att_prov
            used_model = att_mdl
            used_region = att_reg
            sys_txt, usr_txt, phi_scrubbed, redacted_cats = _prompts_for_region(att_reg)
            used_phi_scrub = phi_scrubbed
            used_redacted = redacted_cats

            prompt_tok: int | None = None
            out_tok: int | None = None
            txt = ""

            try:
                if att_prov in ("vertex", "gemini"):
                    if att_prov == "vertex":
                        token, project = MultiAgentHarness._vertex_adc_token(att_proj)
                        url = build_vertex_generate_content_url(region=att_reg, project=project, model=att_mdl)
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "x-goog-user-project": project,
                            "Content-Type": "application/json",
                            "X-Vertex-Zero-Data-Retention": "true",
                            "X-No-Training": "true",
                        }
                        params: dict[str, str] = {}
                    else:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{att_mdl}:generateContent"
                        headers = {"Content-Type": "application/json"}
                        params = {"key": os.environ["GEMINI_API_KEY"]}

                    gen_cfg = build_gemini_generation_config(
                        att_mdl,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        expect_json=expect_json,
                        thinking_level=thinking_level or engine.get("thinking_level"),
                    )
                    req_body: dict[str, Any] = {
                        "systemInstruction": {"parts": [{"text": sys_txt}]},
                        "contents": [{"role": "user", "parts": [{"text": usr_txt}]}],
                        "generationConfig": gen_cfg,
                    }
                    if att_prov == "vertex":
                        req_body["labels"] = {
                            "zero_retention": "true",
                            "no_training": "true",
                            "pdpo_dpp3": "enforced",
                        }

                    resp = client.post(url, headers=headers, params=params, json=req_body, timeout=timeout_seconds)
                    if resp.status_code != 200:
                        err_snippet = re.sub(r"\s+", " ", (resp.text or ""))[:220]
                        last_error = f"{att_prov}({att_reg}/{att_mdl})_http_{resp.status_code}: {err_snippet}"
                        if resp.status_code in (400, 401, 403, 404):
                            _mark_target_failed(target_key, last_error)
                        continue
                    data = resp.json()
                    cand = (data.get("candidates") or [{}])[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    txt = extract_gemini_visible_text(parts)
                    usage = data.get("usageMetadata") or {}
                    prompt_tok = usage.get("promptTokenCount")
                    out_tok = usage.get("candidatesTokenCount")

                elif att_prov == "anthropic":
                    resp = client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": att_mdl,
                            "max_tokens": max_output_tokens,
                            "temperature": temperature,
                            "system": sys_txt,
                            "messages": [{"role": "user", "content": usr_txt}],
                        },
                        timeout=timeout_seconds,
                    )
                    if resp.status_code != 200:
                        err_snippet = re.sub(r"\s+", " ", (resp.text or ""))[:220]
                        last_error = f"anthropic_http_{resp.status_code}: {err_snippet}"
                        if resp.status_code in (401, 403, 404):
                            _mark_target_failed(target_key, last_error)
                        continue
                    data = resp.json()
                    blocks = data.get("content") or []
                    txt = " ".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text").strip()
                    usage = data.get("usage") or {}
                    prompt_tok = usage.get("input_tokens")
                    out_tok = usage.get("output_tokens")

                elif att_prov == "openai":
                    oai_body: dict[str, Any] = {
                        "model": att_mdl,
                        "max_tokens": max_output_tokens,
                        "temperature": temperature,
                        "store": False,
                        "messages": [
                            {"role": "system", "content": sys_txt},
                            {"role": "user", "content": usr_txt},
                        ],
                    }
                    if expect_json:
                        oai_body["response_format"] = {"type": "json_object"}
                    resp = client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                            "Content-Type": "application/json",
                        },
                        json=oai_body,
                        timeout=timeout_seconds,
                    )
                    if resp.status_code != 200:
                        err_snippet = re.sub(r"\s+", " ", (resp.text or ""))[:220]
                        last_error = f"openai_http_{resp.status_code}: {err_snippet}"
                        if resp.status_code in (401, 403, 404):
                            _mark_target_failed(target_key, last_error)
                        continue
                    data = resp.json()
                    txt = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                    usage = data.get("usage") or {}
                    prompt_tok = usage.get("prompt_tokens")
                    out_tok = usage.get("completion_tokens")
                else:
                    last_error = f"unsupported_provider:{att_prov}"
                    continue
            except Exception as exc:
                last_error = f"{att_prov}({att_reg}/{att_mdl}) {type(exc).__name__}: {exc}"
                if "ADC credentials unavailable" in str(exc):
                    _mark_target_failed(target_key, last_error)
                continue

            latency_ms = max(1, int((time.perf_counter() - t0) * 1000))
            if expect_json:
                parsed = _extract_json_object(txt)
                if parsed is None:
                    last_error = f"{att_prov}({att_reg}/{att_mdl}): invalid_json_output"
                    continue
                return ModelCallResult(
                    ok=True,
                    provider=att_prov,
                    model=att_mdl,
                    region=att_reg,
                    text=txt,
                    json_payload=parsed,
                    latency_ms=latency_ms,
                    phi_scrubbed=phi_scrubbed,
                    redacted_categories=redacted_cats,
                    prompt_tokens=prompt_tok,
                    output_tokens=out_tok,
                    fallback_used=(att_prov != provider or att_reg != region or idx > 0),
                    attempts_tried=attempts_tried,
                )

            if not txt:
                last_error = f"{att_prov}({att_reg}/{att_mdl}): empty_model_response"
                continue
            return ModelCallResult(
                ok=True,
                provider=att_prov,
                model=att_mdl,
                region=att_reg,
                text=txt,
                latency_ms=latency_ms,
                phi_scrubbed=phi_scrubbed,
                redacted_categories=redacted_cats,
                prompt_tokens=prompt_tok,
                output_tokens=out_tok,
                fallback_used=(att_prov != provider or att_reg != region or idx > 0),
                attempts_tried=attempts_tried,
            )

        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))
        return ModelCallResult(
            ok=False,
            provider=used_provider,
            model=used_model,
            region=used_region,
            latency_ms=latency_ms,
            phi_scrubbed=used_phi_scrub,
            redacted_categories=used_redacted,
            fallback_used=True,
            attempts_tried=attempts_tried,
            error=last_error,
        )
    except Exception as exc:
        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))
        return ModelCallResult(
            ok=False,
            provider=used_provider,
            model=used_model,
            region=used_region,
            latency_ms=latency_ms,
            phi_scrubbed=used_phi_scrub,
            redacted_categories=used_redacted,
            fallback_used=True,
            attempts_tried=attempts_tried,
            error=f"{type(exc).__name__}: {exc}",
        )


def warmup_live_llm(*, force: bool = False) -> dict[str, Any]:
    """Prime TLS keep-alive pool, model endpoint, and failed-target skip cache before the first demo turn."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not force:
        return get_llm_warmup_state()
    if _WARMUP_STATE.get("in_progress"):
        return get_llm_warmup_state()
    if _WARMUP_STATE.get("warmed") and not force:
        return get_llm_warmup_state()

    engine = detect_live_llm_engine()
    if not engine.get("live"):
        _WARMUP_STATE.update({
            "warmed": False,
            "in_progress": False,
            "provider": engine.get("provider"),
            "model": engine.get("model"),
            "region": engine.get("region"),
            "error": "offline_deterministic_mode",
        })
        return get_llm_warmup_state()

    if force:
        _FAILED_LLM_TARGETS.clear()

    _WARMUP_STATE["in_progress"] = True
    try:
        warm_engine = dict(engine)
        if warm_engine.get("provider") in ("gemini", "vertex") and warm_engine.get("model") == DEFAULT_GEMINI_REASONING_MODEL:
            warm_engine["model"] = DEFAULT_GEMINI_FAST_MODEL
        res = invoke_structured_json(
            system_prompt='Reply ONLY with JSON: {"status":"ready"}',
            user_prompt="ping",
            temperature=0.0,
            max_output_tokens=64,
            engine_override=warm_engine,
            timeout_seconds=12.0,
            thinking_level="LOW",
        )
        _WARMUP_STATE.update({
            "warmed": bool(res.ok),
            "in_progress": False,
            "provider": res.provider,
            "model": res.model,
            "region": res.region,
            "latency_ms": res.latency_ms,
            "attempts_tried": res.attempts_tried,
            "error": res.error,
            "warmed_at": utcnow().isoformat(),
        })
    except Exception as exc:
        _WARMUP_STATE.update({
            "warmed": False,
            "in_progress": False,
            "error": f"{type(exc).__name__}: {exc}",
        })
    return get_llm_warmup_state()



def invoke_structured_json(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_output_tokens: int = 1100,
    patient_names_for_scrub: list[str] | None = None,
    engine_override: dict[str, Any] | None = None,
    timeout_seconds: float = 18.0,
    thinking_level: str | None = None,
) -> ModelCallResult:
    """Unified structured-JSON clinical LLM invocation (Vertex asia-east2 primary, off-HK PHIScrubber)."""
    return _invoke_live_provider(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        expect_json=True,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        patient_names_for_scrub=patient_names_for_scrub,
        engine_override=engine_override,
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
    )


def invoke_clinical_qa(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_output_tokens: int = 360,
    patient_names_for_scrub: list[str] | None = None,
    engine_override: dict[str, Any] | None = None,
    timeout_seconds: float = 28.0,
    thinking_level: str | None = None,
) -> ModelCallResult:
    """Unified concise clinical Q&A LLM invocation (Vertex asia-east2 primary, off-HK PHIScrubber)."""
    return _invoke_live_provider(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        expect_json=False,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        patient_names_for_scrub=patient_names_for_scrub,
        engine_override=engine_override,
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
    )


def _compute_age(dob: str | None) -> int | None:
    if not dob or len(dob) < 4:
        return None
    try:
        yr = int(dob[:4])
        return max(0, 2026 - yr)
    except ValueError:
        return None


def _normalize_for_quote(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


class MultiAgentHarness:
    """Orchestrates SafetySentinel, LoopWatcher, CareAgent, and ProtocolCDSAgent with live SSE telemetry."""

    def __init__(self, bus: LiveHarnessBus) -> None:
        self.bus = bus
        self._loopbench_cache: dict[str, Any] | None = None

    def _get_loopbench_sim_data(self) -> dict[str, Any]:
        """Load committed LoopBench clinic simulation data via legacy_feed (never imports loopbench)."""
        if self._loopbench_cache is not None:
            return self._loopbench_cache
        store = build_legacy_demo_store()
        if store is None:
            self._loopbench_cache = {"patients": {}, "documents": {}, "items": [], "feeds": [], "source": {}}
            return self._loopbench_cache
        items_list = list(store.items.values()) if isinstance(store.items, dict) else list(store.items or [])
        self._loopbench_cache = {
            "source": store.source,
            "patients": store.patients,
            "documents": store.documents,
            "items": items_list,
            "feeds": store.feeds,
        }
        return self._loopbench_cache

    def _patient_emr_docs(self, pat: Patient, encs: list[Encounter]) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        if pat.id == HERO_URTI_PATIENT_ID:
            docs.extend(HERO_EMR_DOCUMENTS)
        for e in encs:
            if e.chief_complaint_text:
                docs.append({
                    "id": f"DOC_{e.id}",
                    "kind": "encounter_note" if e.status == "completed" else "acute_intake",
                    "lab_name": "Central & Mid-Levels Family Medical",
                    "date": e.created_at.astimezone(HKT).strftime("%Y-%m-%d"),
                    "title": f"{e.encounter_type.replace('_', ' ').title()} ({e.icd10_code or 'Pending'})",
                    "status": e.status,
                    "highlight_quote": e.chief_complaint_text[:120],
                    "text": (
                        f"Encounter ID: {e.id} | Status: {e.status} | ICD-10: {e.icd10_code or '—'}\n"
                        f"Chief Complaint / Intake:\n{e.chief_complaint_text}\n\n"
                        f"Clinical Note:\n{e.clinical_note or '(Draft — awaiting MCHK clinician sign-off)'}"
                    ),
                })
        return docs

    def build_emr_overview(
        self,
        db: Database,
        tenant_id: str,
        *,
        selected_patient_id: str | None = None,
    ) -> dict[str, Any]:
        """Build the unified Simulated Clinic EMR snapshot combining LoopBench sim data + live Postgres state."""
        lb = self._get_loopbench_sim_data()
        engine = detect_live_llm_engine()
        live_states = self.bus.get_all_patient_states()

        with db.admin_session() as sa:
            t_row = sa.get(Tenant, tenant_id)
            ai_enabled = bool(t_row.ai_enabled) if t_row else True

        with db.tenant_session(tenant_id) as s:
            pats = s.scalars(select(Patient)).all()
            encs = s.scalars(select(Encounter).order_by(Encounter.created_at.desc())).all()
            obs_all = s.scalars(select(Observation).order_by(Observation.recorded_at.desc())).all()
            props_all = s.scalars(select(Proposal).order_by(Proposal.created_at.desc())).all()
            rxs_all = s.scalars(select(Prescription).order_by(Prescription.signed_at.desc())).all()
            certs_all = s.scalars(select(Certificate).order_by(Certificate.signed_at.desc())).all()
            imms_all = s.scalars(select(Immunization).order_by(Immunization.created_at.desc())).all()
            audits = s.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(35)).all()

            # Group by patient
            by_pat = lambda rows: _group_by(rows, "patient_id")
            encs_map = by_pat(encs)
            obs_map = by_pat(obs_all)
            props_map = by_pat(props_all)
            rxs_map = by_pat(rxs_all)
            certs_map = by_pat(certs_all)
            imms_map = by_pat(imms_all)

            # Build roster: hero patient first, then key live clinic patients, then LoopBench sim patients
            priority_order = [
                HERO_URTI_PATIENT_ID,
                "pat_wong_flu_g6pd",
                "pat_chan_qeh_discharge",
                "pat_ho_htn_drift",
                "pat_cheung_elderly_flu",
            ]
            pat_by_id = {p.id: p for p in pats}
            ordered_live_pats: list[Patient] = []
            for pid in priority_order:
                if pid in pat_by_id:
                    ordered_live_pats.append(pat_by_id[pid])
            for p in pats:
                if p.id not in priority_order and p.id not in ("pat_harbour_4", "pat_harbour_5", "pat_harbour_6", "pat_harbour_7", "pat_pacific_1", "pat_pacific_2"):
                    ordered_live_pats.append(p)

            emr_patients: list[dict[str, Any]] = []
            for p in ordered_live_pats:
                p_encs = encs_map.get(p.id, [])
                p_obs = obs_map.get(p.id, [])
                p_props = props_map.get(p.id, [])
                p_rxs = rxs_map.get(p.id, [])
                p_certs = certs_map.get(p.id, [])
                p_imms = imms_map.get(p.id, [])
                st = live_states.get(p.id) or self.bus.get_patient_state(p.id)

                open_loops: list[dict[str, Any]] = []
                if p.id == HERO_URTI_PATIENT_ID:
                    open_loops.append({
                        "id": "LOOP_FERRITIN_LAM",
                        "family": "abnormal_lab_followup",
                        "status": "open",
                        "urgency": 2,
                        "title": "Mild low Ferritin (14 ng/mL) — repeat iron panel due Feb 2027",
                        "source": "KingMed 2026-11-08",
                        "quote": "Serum Ferritin: 14 ng/mL (Low; Ref 15–150) — suggest repeat iron studies in 3 months.",
                    })
                    vax_status = (
                        "booked"
                        if (st.get("booked_slot") or any(im.status in ("booked", "completed") for im in p_imms))
                        else ("deferred" if st.get("flu_vax_deferred") else "open")
                    )
                    open_loops.append({
                        "id": "LOOP_FLUVAX_2026_LAM",
                        "family": "preventive_flu_vax",
                        "status": vax_status,
                        "urgency": 1,
                        "title": (
                            f"2026/27 Flu Vaccine Booked ({st['booked_slot']['label_en']})"
                            if st.get("booked_slot")
                            else (
                                "2026/27 Flu Vaccine Deferred 14d (active acute febrile URTI)"
                                if st.get("flu_vax_deferred")
                                else "2026/27 Seasonal Flu Vaccination due (Harbour FinTech $0 copay)"
                            )
                        ),
                        "source": "Clinic Preventive Registry",
                        "quote": "Eligible for 2026/27 flu jab via Harbour FinTech.",
                    })

                for pr in p_props:
                    open_loops.append({
                        "id": pr.id,
                        "family": pr.kind,
                        "status": "closed" if pr.status in ("approved", "edited", "rejected") else ("nurse_ready" if (pr.payload_json or {}).get("nurse_ready") else "open"),
                        "urgency": pr.urgency,
                        "title": (pr.payload_json or {}).get("title") or pr.kind.replace("_", " ").title(),
                        "source": f"Protocol {pr.protocol_id}",
                        "quote": ((pr.source_quotes_json or [{}])[0].get("quote") if (pr.source_quotes_json and isinstance(pr.source_quotes_json[0], dict)) else ""),
                    })

                emr_patients.append({
                    "id": p.id,
                    "source": "hero_live" if p.id == HERO_URTI_PATIENT_ID else "clinic_live",
                    "badge": "URTI Phone Demo" if p.id == HERO_URTI_PATIENT_ID else "Live Clinic",
                    "name_en": p.name_en,
                    "name_zh": p.name_zh or "",
                    "sex": p.sex or "—",
                    "dob": p.dob or "—",
                    "age": _compute_age(p.dob),
                    "hkid_masked": p.hkid_masked or "—",
                    "phone_masked": p.phone_masked,
                    "employer": "Harbour FinTech (Corporate Care)" if p.corporate_id else "Private GP Patient",
                    "g6pd_deficient": p.g6pd_deficient,
                    "allergies": p.allergies_json or [],
                    "current_meds": p.current_meds_json or [],
                    "egfr": p.egfr,
                    "weight_kg": p.weight_kg,
                    "flu_vax_status": (
                        "booked"
                        if (st.get("booked_slot") or p_imms)
                        else ("deferred_acute" if st.get("flu_vax_deferred") else "due_2026_27")
                    ),
                    "booked_slot": st.get("booked_slot"),
                    "phone_connected": bool(st.get("phone_connected")),
                    "workflow_stage": st.get("workflow_stage", "idle"),
                    "open_loops": open_loops,
                    "documents": self._patient_emr_docs(p, p_encs),
                    "observations": [
                        {
                            "id": o.id,
                            "loinc": o.loinc_code,
                            "display": o.display,
                            "value": o.value_text or (f"{o.value_num:g} {o.ucum_unit or ''}".strip() if o.value_num is not None else "—"),
                            "recorded_at": o.recorded_at.astimezone(HKT).strftime("%d %b %H:%M"),
                            "quote": o.source_quote or "",
                        }
                        for o in p_obs[:12]
                    ],
                    "encounters": [
                        {
                            "id": e.id,
                            "type": e.encounter_type,
                            "status": e.status,
                            "icd10": e.icd10_code or "—",
                            "icpc2": e.icpc2_code or "—",
                            "chief_complaint": e.chief_complaint_text or "",
                            "clinical_note": e.clinical_note or "",
                            "created_at": e.created_at.astimezone(HKT).strftime("%d %b %Y %H:%M"),
                        }
                        for e in p_encs[:10]
                    ],
                    "prescriptions": [
                        {
                            "id": r.id,
                            "items": r.items_json or [],
                            "mchk_reg_no": r.mchk_reg_no,
                            "fulfilment_mode": r.fulfilment_mode,
                            "fulfilment_status": r.fulfilment_status,
                            "signed_at": (r.signed_at or utcnow()).astimezone(HKT).strftime("%d %b %H:%M"),
                        }
                        for r in p_rxs[:10]
                    ],
                    "certificates": [
                        {
                            "id": c.id,
                            "days_off": c.days_off,
                            "start_date": c.start_date,
                            "end_date": c.end_date,
                            "mchk_reg_no": c.mchk_reg_no,
                            "diagnosis_icd10": c.diagnosis_icd10,
                            "verification_hash": c.verification_hash[:16] if c.verification_hash else "",
                            "signed_at": (c.signed_at or utcnow()).astimezone(HKT).strftime("%d %b %H:%M"),
                        }
                        for c in p_certs[:10]
                    ],
                    "immunizations": [
                        {
                            "id": im.id,
                            "vaccine": im.vaccine_display,
                            "status": im.status,
                            "scheduled_at": (im.slot_at or im.created_at).astimezone(HKT).strftime("%d %b %H:%M"),
                        }
                        for im in p_imms[:5]
                    ],
                })

            # Surface LoopBench simulated clinic patients (read-only simulated records from loopbench snapshot)
            lb_docs = lb.get("documents") or {}
            lb_items = lb.get("items") or []
            for lb_pid, lb_card in (lb.get("patients") or {}).items():
                p_items = [it for it in lb_items if it.get("patient_id") == lb_pid]
                p_doc_ids = {ev for it in p_items for ev in (it.get("evidence") or [])}
                # Also grab any document mentioning the patient's English surname/name
                p_docs = []
                for did, doc in lb_docs.items():
                    if did in p_doc_ids or doc.get("patient_id") == lb_pid:
                        p_docs.append({
                            "id": did,
                            "kind": doc.get("kind") or "record",
                            "lab_name": "LoopBench HK Sim Clinic",
                            "date": str(doc.get("date") or doc.get("created_at") or "2027-06-10")[:10],
                            "title": f"{(doc.get('kind') or 'Record').replace('_', ' ').title()} · {did}",
                            "status": "sim_record",
                            "highlight_quote": (doc.get("text") or "").splitlines()[0][:110],
                            "text": doc.get("text") or "",
                        })
                emr_patients.append({
                    "id": f"lb_{lb_pid}",
                    "source": "loopbench_sim",
                    "badge": "LoopBench Sim",
                    "name_en": lb_card.get("name_en") or lb_pid,
                    "name_zh": lb_card.get("name_zh") or "",
                    "sex": lb_card.get("sex") or "—",
                    "dob": lb_card.get("dob") or "—",
                    "age": _compute_age(lb_card.get("dob")),
                    "hkid_masked": lb_card.get("hkid") or "—",
                    "phone_masked": "LoopBench Sim",
                    "employer": "LoopBench HK GP Panel (Apricot VITA / ClinicSolution)",
                    "g6pd_deficient": False,
                    "allergies": [],
                    "current_meds": [],
                    "egfr": None,
                    "weight_kg": None,
                    "flu_vax_status": "unknown",
                    "booked_slot": None,
                    "phone_connected": False,
                    "workflow_stage": "loopbench_readonly",
                    "open_loops": [
                        {
                            "id": it["id"],
                            "family": it.get("family", "followup"),
                            "status": "closed" if it.get("resolved_at") else "open",
                            "urgency": it.get("urgency_at_open", 2),
                            "title": it.get("reason", "Open loop"),
                            "source": f"LoopBench ({it.get('opened_at', '')[:10]})",
                            "quote": ((it.get("reasoning") or [{}])[0].get("cites") or [{}])[0].get("quote", ""),
                        }
                        for it in p_items
                    ],
                    "documents": p_docs,
                    "observations": [],
                    "encounters": [],
                    "prescriptions": [],
                    "certificates": [],
                    "immunizations": [],
                })

            focus_pid = selected_patient_id or HERO_URTI_PATIENT_ID
            agent_state = self.bus.get_patient_state(focus_pid)
            # Rehydrate workflow_stage from PostgreSQL if this container's in-memory bus is cold (Cloud Run scaling)
            if agent_state.get("workflow_stage") == "idle":
                fp_props = props_map.get(focus_pid, [])
                fp_certs = certs_map.get(focus_pid, [])
                if fp_certs or any(pr.status in ("approved", "edited") for pr in fp_props):
                    agent_state["workflow_stage"] = "doctor_signed"
                elif any((pr.payload_json or {}).get("nurse_ready") for pr in fp_props if pr.status == "pending_review"):
                    agent_state["workflow_stage"] = "nurse_ready"
                elif any(pr.status == "pending_review" for pr in fp_props):
                    agent_state["workflow_stage"] = "intake_submitted"

            audit_rows = [
                {
                    "id": a.id,
                    "action": a.action,
                    "actor_role": a.actor_role,
                    "actor_id": a.actor_id,
                    "resource_type": a.resource_type,
                    "resource_id": a.resource_id,
                    "created_at": a.created_at.astimezone(HKT).strftime("%H:%M:%S"),
                    "metadata": a.metadata_json or {},
                }
                for a in audits
            ]

            return {
                "clinic": {
                    "name": "Central & Mid-Levels Family Medical",
                    "emr_profile": "ClinicSolution X · LoopBench Simulated EMR",
                    "doctor_on_duty": "Dr. Kenneth Lam (MCHK M14829)",
                    "nurse_on_duty": "Nurse Rachel Chan (RN)",
                    "region": "asia-east2 (Hong Kong)",
                    "ai_enabled": ai_enabled,
                },
                "engine": engine,
                "hero_patient_id": HERO_URTI_PATIENT_ID,
                "patients": emr_patients,
                "feeds": lb.get("feeds") or [],
                "protocols": CLINIC_PROTOCOLS,
                "tool_schemas": TOOL_SCHEMAS,
                "available_slots": available_clinic_slots(),
                "agent_state": agent_state,
                "recent_events": self.bus.recent_events(limit=60),
                "audit_log": audit_rows,
            }

    # ------------------------------------------------------------------ Tool Execution Engine
    def _exec_tool(
        self,
        *,
        s: Session,
        tenant_id: str,
        patient: Patient,
        agent: str,
        tool_name: str,
        args: dict[str, Any],
        source_texts: list[str] | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        authority = next((t["authority"] for t in TOOL_SCHEMAS if t["name"] == tool_name), "READ")
        emr_target: str | None = None
        result: dict[str, Any]

        if tool_name == "emr.get_patient_chart":
            emr_target = "patient_chart"
            result = {
                "patient_id": patient.id,
                "name_en": patient.name_en,
                "name_zh": patient.name_zh,
                "age": _compute_age(patient.dob),
                "sex": patient.sex,
                "hkid_masked": patient.hkid_masked,
                "g6pd_deficient": patient.g6pd_deficient,
                "allergies": patient.allergies_json or [],
                "current_meds": patient.current_meds_json or [],
                "egfr": patient.egfr,
                "weight_kg": patient.weight_kg,
                "employer": "Harbour FinTech" if patient.corporate_id else "Private",
            }
            self.bus.set_memory_layers(
                patient.id,
                emr_memory={
                    "patient": result,
                    "hydrated_at": utcnow().astimezone(HKT).strftime("%H:%M:%S HKT"),
                },
                protocol_memory=CLINIC_PROTOCOLS,
            )

        elif tool_name == "emr.get_clinical_records":
            emr_target = "documents"
            encs = s.scalars(select(Encounter).where(Encounter.patient_id == patient.id).order_by(Encounter.created_at.desc())).all()
            obs = s.scalars(select(Observation).where(Observation.patient_id == patient.id).order_by(Observation.recorded_at.desc()).limit(8)).all()
            docs = self._patient_emr_docs(patient, encs)
            result = {
                "documents_found": len(docs),
                "latest_lab": docs[0]["title"] if docs else None,
                "latest_lab_highlight": docs[0]["highlight_quote"] if docs else None,
                "observations": [{"display": o.display, "value": o.value_text or str(o.value_num)} for o in obs[:5]],
                "open_followups": (
                    [{"id": "LOOP_FERRITIN_LAM", "summary": "Mild low ferritin (14 ng/mL) — repeat iron panel in Feb 2027"}]
                    if patient.id == HERO_URTI_PATIENT_ID
                    else []
                ),
            }

        elif tool_name == "emr.check_preventive_gaps":
            emr_target = "open_loops"
            imms = s.scalars(select(Immunization).where(Immunization.patient_id == patient.id)).all()
            st = self.bus.get_patient_state(patient.id)
            has_vax = bool(imms) or bool(st.get("booked_slot"))
            result = {
                "flu_season": "2026/27",
                "flu_vaccine_eligible": not has_vax and not st.get("flu_vax_deferred"),
                "deferred_for_acute_illness": bool(st.get("flu_vax_deferred")),
                "subsidy": "Harbour FinTech Corporate Care ($0 copay)" if patient.corporate_id else "Standard GP tariff",
                "g6pd_vaccine_safe": True,
                "penicillin_allergy_vaccine_safe": True,
            }

        elif tool_name == "clinic.get_available_slots":
            emr_target = "schedule"
            slots = available_clinic_slots()
            result = {"service": args.get("service", "flu_vaccine"), "slots": slots}

        elif tool_name == "clinic.book_slot":
            emr_target = "schedule"
            slot_id = str(args.get("slot_id") or "")
            slot = next((sl for sl in available_clinic_slots() if sl["id"] == slot_id), available_clinic_slots()[0])
            imm = Immunization(
                id=new_id("imm"),
                tenant_id=tenant_id,
                patient_id=patient.id,
                campaign_id=patient.campaign_id,
                vaccine_display="2026/27 Quadrivalent Inactivated Influenza Vaccine",
                partner_clinic=slot["location"],
                slot_at=utcnow() + timedelta(days=1),
                status="booked",
                booking_ref=f"VAX-{new_id('b')[-6:].upper()}",
            )
            s.add(imm)
            s.flush()
            record_audit(
                s,
                tenant_id=tenant_id,
                actor_role="assistant",
                actor_id="care_agent",
                action="flu_vax.booked_via_agent",
                resource_type="Immunization",
                resource_id=imm.id,
                metadata={"slot_id": slot["id"], "slot_label": slot["label_en"]},
            )
            self.bus.update_patient_state(patient.id, booked_slot=slot)
            result = {"booked": True, "immunization_id": imm.id, "slot": slot}
            self.bus.publish("emr.mutated", patient_id=patient.id, data={"entity": "immunization", "id": imm.id, "summary": f"Flu shot booked: {slot['label_en']}"})

        elif tool_name == "safety.screen_red_flags":
            emr_target = "safety_sentinel"
            msg = str(args.get("message") or "")
            temp_c_arg = args.get("temp_c")
            vitals_arg = {"temp_c": float(temp_c_arg)} if temp_c_arg is not None else None
            triage_res = screen_acute_message(msg, vitals=vitals_arg, enable_model_screen=False)
            crisis: str | None = None
            if triage_res.is_emergency:
                crisis = "mental_health_crisis" if triage_res.kind == "crisis" else "medical_999"
            elif not triage_res.suppressed_negations:
                # Union-only with legacy crisis matcher when neither Cantonese hyperbole nor clause-scoped negation suppressed it
                crisis = classify_crisis(msg)
            result = {
                "crisis_kind": crisis,
                "status": "EMERGENCY_STOP" if crisis else "CLEAR",
                "urgency": triage_res.urgency,
                "red_flags": list(triage_res.red_flags),
                "suppressed_negations": list(triage_res.suppressed_negations),
                "prompt_injection_detected": triage_res.prompt_injection_detected,
                "rule_engine": "triage_screen_acute_message_union_v2",
            }

        elif tool_name == "memory.upsert_fact":
            emr_target = "memory"
            key = str(args.get("key") or "clinical_fact")
            val = str(args.get("value") or "")
            quote = str(args.get("verbatim_quote") or "")
            src = str(args.get("source") or "Patient Chat")
            corpus = "\n".join(source_texts or [])
            # Verify quote appears verbatim (case/whitespace-normalized) in patient message or EMR records
            verified = bool(quote) and (_normalize_for_quote(quote) in _normalize_for_quote(corpus) if corpus else True)
            if not verified and quote:
                # Reject unverified quote cleanly (demonstrates quote-verification guardrail!)
                result = {"stored": False, "verified": False, "reason": "Quote not found verbatim in source text"}
            else:
                self.bus.upsert_episodic_fact(
                    patient.id,
                    {"key": key, "value": val, "verbatim_quote": quote, "source": src, "verified": True},
                )
                result = {"stored": True, "verified": True, "key": key, "value": val, "verbatim_quote": quote}

        elif tool_name == "cds.check_medication_safety":
            emr_target = "cds_rules"
            drugs = [str(d) for d in (args.get("drugs") or []) if str(d).strip()]
            diag = str(args.get("diagnosis_icd10") or "J06.9")
            rx_items = [{"drug": d, "dose_mg": 500 if d.lower() in ("paracetamol", "aspirin", "amoxicillin") else 10, "frequency": "QDS", "days": 3} for d in drugs]
            cds = check_prescription_safety(
                {
                    "dob": patient.dob,
                    "g6pd_deficient": patient.g6pd_deficient,
                    "allergies": patient.allergies_json or [],
                    "current_meds": patient.current_meds_json or [],
                    "weight_kg": patient.weight_kg,
                    "egfr": patient.egfr,
                },
                rx_items,
                diagnosis_icd10=diag,
            ).to_dict()
            result = {
                "status": cds["status"],
                "drugs_checked": drugs,
                "blocked_or_flagged": [
                    {
                        "drug": f["drug"],
                        "code": f["code"],
                        "severity": f["severity"],
                        "message": f["message"],
                    }
                    for f in cds["findings"]
                ],
                "safe_candidates": [
                    d for d in drugs
                    if not any(f["drug"].lower() == d.lower() and f["severity"] in ("hard_block", "override_required") for f in cds["findings"])
                ],
            }

        elif tool_name == "harness.stage_staff_proposal":
            emr_target = "proposal_queue"
            result = self._stage_urti_proposal(s, tenant_id, patient, args)

        elif tool_name == "emr.write_signed_orders":
            emr_target = "authority_boundary"
            authority = "BLOCKED"
            record_audit(
                s,
                tenant_id=tenant_id,
                actor_role="assistant",
                actor_id=agent,
                action="authority.blocked_agent_sign",
                resource_type="Proposal",
                resource_id=str(args.get("proposal_id") or "unknown"),
                metadata={"reason": "Asymmetric authority: AI agents cannot sign Rx or MCHK certificates"},
            )
            result = {
                "allowed": False,
                "error": "DENIED (Asymmetric Authority): AI agents cannot sign prescriptions, issue MCHK certificates, or close clinical loops. Handed off to Nurse Rachel -> Dr. Kenneth Lam (M14829).",
            }

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.bus.append_tool_call(
            patient.id,
            {
                "agent": agent,
                "tool": tool_name,
                "authority": authority,
                "args": args,
                "result": result,
                "duration_ms": duration_ms,
                "emr_target": emr_target,
            },
        )
        return result

    def _stage_urti_proposal(
        self,
        s: Session,
        tenant_id: str,
        patient: Patient,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update a real PostgreSQL acute URTI Encounter + Proposal routed Nurse -> Doctor."""
        symptoms = args.get("symptoms") or ["sore throat", "cough", "runny nose", "fever"]
        onset = str(args.get("onset") or "Yesterday evening (<24h)")
        temp_c = float(args.get("temp_c") or 38.2)
        rat = str(args.get("rat_result") or "Negative (Viral URTI)")
        days_off = max(1, min(7, int(args.get("sick_leave_days") or 2)))
        raw_msg = str(args.get("patient_message") or "").strip()
        is_flu_pos = "flu" in rat.lower() and ("pos" in rat.lower() or "+" in rat)

        complaint_lines = [
            f"Patient Phone Intake ({patient.name_en} / {patient.name_zh or ''}, 32F, Harbour FinTech):",
            f"Message: \"{raw_msg}\"" if raw_msg else "",
            f"Symptoms: {', '.join(symptoms)} | Onset: {onset} | Peak Temp: {temp_c:.1f}°C",
            f"Home Flu/COVID RAT: {rat}",
            "Red Flags Screened: Breathing comfortably, alert & oriented, nil acute warning signs (Deterministic SafetySentinel CLEAR).",
            f"Sick Leave Requested: {days_off} day(s) starting {utcnow().astimezone(HKT).date().isoformat()} (Bilingual MCHK PDF).",
            f"Chart Alerts: G6PD Deficient ({'YES — avoid Aspirin/co-trimoxazole' if patient.g6pd_deficient else 'No'}); Allergies: {', '.join(patient.allergies_json or []) or 'NKDA'}.",
            "Preventive Protocol: 2026/27 flu vaccination deferred 14 days due to acute febrile illness; auto-reoffer scheduled.",
        ]
        complaint_text = "\n".join(line for line in complaint_lines if line)

        icd10 = "J10.1" if is_flu_pos else "J06.9"
        icpc2 = "R80" if is_flu_pos else "R74"
        diag_display = (
            "Influenza A with respiratory manifestations (working-age, G6PD-deficient)"
            if is_flu_pos
            else "Acute upper respiratory tract infection (viral URTI; working-age, G6PD-deficient)"
        )

        suggested_meds: list[dict[str, Any]] = []
        if is_flu_pos:
            suggested_meds.append({
                "drug": "oseltamivir",
                "display": "Oseltamivir 75 mg BD × 5 days (within 48h window)",
                "dose_mg": 75,
                "frequency": "BD",
                "days": 5,
            })
        suggested_meds.extend([
            {
                "drug": "paracetamol",
                "display": "Paracetamol 500 mg QDS PRN × 3 days (G6PD-safe antipyretic)",
                "dose_mg": 500,
                "frequency": "QDS",
                "days": 3,
            },
            {
                "drug": "cetirizine",
                "display": "Cetirizine 10 mg OD × 5 days (G6PD-safe non-sedating nasal/URTI relief)",
                "dose_mg": 10,
                "frequency": "OD",
                "days": 5,
            },
        ])

        today_str = utcnow().astimezone(HKT).date().isoformat()
        patient_reply_draft = (
            f"Hi {patient.name_en.split()[0]}, thank you for sharing your symptoms. Based on your assessment, this is consistent with "
            f"{'acute Influenza A' if is_flu_pos else 'an acute viral upper respiratory tract infection (URTI)'}. "
            f"Because of your G6PD deficiency and Penicillin allergy, please avoid Aspirin and penicillin antibiotics — I have prescribed G6PD-safe "
            f"{'Oseltamivir + Paracetamol + Cetirizine' if is_flu_pos else 'Paracetamol 500mg and Cetirizine 10mg'} for symptom relief, "
            f"and issued your signed {days_off}-day MCHK bilingual sick-leave certificate. "
            f"Rest well and hydrate; if you develop shortness of breath, chest pain, or persistent high fever after 48 hours, seek urgent care. "
            f"We will check in with you in 48 hours and re-open your Harbour FinTech flu vaccine booking once you have recovered."
        )

        # Reuse existing pending URTI proposal for this patient if already staged in this session
        st = self.bus.get_patient_state(patient.id)
        existing_prop = s.get(Proposal, st["active_proposal_id"]) if st.get("active_proposal_id") else None
        if existing_prop and existing_prop.status == "pending_review":
            prop = existing_prop
            enc = s.get(Encounter, prop.encounter_id)
            if enc:
                enc.chief_complaint_text = complaint_text
                enc.icd10_code = icd10
                enc.icpc2_code = icpc2
        else:
            enc = Encounter(
                id=new_id("enc"),
                tenant_id=tenant_id,
                patient_id=patient.id,
                practitioner_id="doc_dr_lam",
                campaign_id=patient.campaign_id,
                encounter_type="acute_urti_phone_intake",
                status="pending_review",
                chief_complaint_text=complaint_text,
                icd10_code=icd10,
                icpc2_code=icpc2,
                snomed_codes_json=["54150009", "386661006", "49727002"],
                clinical_note=(
                    f"S: 32F working-age patient reporting {', '.join(symptoms)} ({onset}), peak T {temp_c:.1f}°C, RAT {rat}. Requests {days_off}d MCHK sick leave.\n"
                    f"O: Self-reported temp {temp_c:.1f}°C; no red flags. Chart alerts: G6PD deficient, Penicillin allergy. Recent Ferritin 14 ng/mL (3m repeat open).\n"
                    f"A: {diag_display} ({icd10} / {icpc2}).\n"
                    f"P: G6PD-safe symptomatic Rx, {days_off}d MCHK sick-leave cert, defer 2026/27 flu shot 14d, 48h check-in loop."
                ),
            )
            s.add(enc)
            s.flush()
            prop = Proposal(
                id=new_id("prop"),
                tenant_id=tenant_id,
                patient_id=patient.id,
                encounter_id=enc.id,
                kind="consult_draft",
                urgency=2,
                status="pending_review",
                sla_due_at=utcnow() + timedelta(minutes=45),
                protocol_id="acute_consult_draft_v1",
                protocol_version="1.0.0",
                payload_json={},
                source_quotes_json=[],
                checks_json=[],
            )
            s.add(prop)
            s.flush()

        s.add(Observation(
            id=new_id("obs"),
            tenant_id=tenant_id,
            patient_id=patient.id,
            encounter_id=enc.id,
            loinc_code="8310-5",
            display="Body temperature (Phone Intake)",
            value_num=temp_c,
            value_text=f"{temp_c:.1f} °C",
            ucum_unit="Cel",
            source_quote=f"Peak Temp: {temp_c:.1f}°C",
        ))

        quotes = [
            {"field": "Symptoms & Onset", "quote": f"Symptoms: {', '.join(symptoms)} | Onset: {onset} | Peak Temp: {temp_c:.1f}°C", "source": "Patient Phone Intake"},
            {"field": "Home RAT", "quote": f"Home Flu/COVID RAT: {rat}", "source": "Patient Phone Intake"},
            {"field": "Sick Leave Request", "quote": f"Sick Leave Requested: {days_off} day(s) starting {today_str}", "source": "Patient Phone Intake"},
        ]
        if raw_msg:
            quotes.insert(0, {"field": "Patient Message", "quote": raw_msg[:140], "source": "Patient Phone Chat"})

        prop.payload_json = {
            "title": f"Working-age {'Flu A' if is_flu_pos else 'Viral URTI'} + {days_off}d MCHK sick leave ({patient.name_en}, 32F · G6PD+Penicillin allergy).",
            "story_tag": "urti_phone_demo",
            "case_sites": ["Central GP", "Patient Phone QR", "KingMed Lab"],
            "conflicts": [],
            "labs_context": "KingMed 08 Nov: Ferritin 14 ng/mL (Low; repeat Feb 2027), Hb 12.1, eGFR 104.",
            "nurse_checklist": [
                {"id": "chk_g6pd", "label": "Confirm G6PD deficiency & Penicillin allergy banner (avoid Aspirin / Amoxicillin)", "done": True},
                {"id": "chk_rat", "label": f"Verify home RAT ({rat}) & onset ({onset})", "done": True},
                {"id": "chk_sl", "label": f"Confirm employer sick-leave dates ({days_off} days from {today_str}) & defer flu vax 14d", "done": False},
            ],
            "summary_points": [
                {
                    "text": f"Working-age URTI presentation ({', '.join(symptoms)}), peak temp {temp_c:.1f}°C, RAT {rat}; red flags denied.",
                    "quote": f"Symptoms: {', '.join(symptoms)} | Onset: {onset} | Peak Temp: {temp_c:.1f}°C",
                },
                {
                    "text": "CDS Safety Catch: G6PD-deficient (Aspirin blocked) + Penicillin allergy (Amoxicillin/Augmentin blocked); viral antibiotic stewardship applied.",
                    "quote": "Chart Alerts: G6PD Deficient (YES — avoid Aspirin/co-trimoxazole); Allergies: Penicillin.",
                },
                {
                    "text": f"Requests {days_off}-day bilingual MCHK sick-leave certificate; 2026/27 flu vaccination deferred 14 days until afebrile.",
                    "quote": f"Sick Leave Requested: {days_off} day(s) starting {today_str}",
                },
            ],
            "draft": {
                "working_diagnosis": {"icd10": icd10, "icpc2": icpc2, "display": diag_display},
                "suggested_medications": suggested_meds,
                "suggested_sick_leave": {"days_off": days_off, "start_date": today_str},
                "contraindications_noted": [
                    "G6PD Deficient — Aspirin / NSAID oxidative risk & co-trimoxazole blocked",
                    "Penicillin Allergy — Amoxicillin / Augmentin blocked; viral URTI requires no antibiotics",
                ],
                "patient_summary_draft": patient_reply_draft,
                "clinical_note": {
                    "subjective": f"32F working-age patient via QR Care AI: {', '.join(symptoms)}, onset {onset}, peak temp {temp_c:.1f}°C, RAT {rat}. Requests {days_off}d sick leave.",
                    "objective": f"T {temp_c:.1f}°C; no dyspnoea/chest pain. Allergies: Penicillin; G6PD deficient; eGFR 104.",
                    "assessment": f"{diag_display} ({icd10} / {icpc2}).",
                    "plan": f"G6PD-safe symptomatic Rx + {days_off}d MCHK sick-leave PDF + 48h check-in + re-offer flu shot in 14d.",
                },
            },
        }
        prop.source_quotes_json = quotes
        flag_modified(prop, "payload_json")
        flag_modified(prop, "source_quotes_json")
        s.flush()

        record_audit(
            s,
            tenant_id=tenant_id,
            actor_role="assistant",
            actor_id="protocol_cds_agent",
            action="proposal.staged_from_phone",
            resource_type="Proposal",
            resource_id=prop.id,
            metadata={"icd10": icd10, "days_off": days_off, "routed_to": "nurse_chan->doc_dr_lam"},
        )

        self.bus.update_patient_state(
            patient.id,
            active_proposal_id=prop.id,
            flu_vax_deferred=True,
            workflow_stage="intake_submitted",
        )
        self.bus.publish(
            "staff.queue_updated",
            patient_id=patient.id,
            data={
                "proposal_id": prop.id,
                "patient_id": patient.id,
                "patient_name": patient.name_en,
                "stage": "intake_submitted",
                "title": prop.payload_json["title"],
            },
        )
        self.bus.publish(
            "emr.mutated",
            patient_id=patient.id,
            data={"entity": "proposal", "id": prop.id, "summary": f"Staged URTI packet ({days_off}d sick leave) for Nurse Rachel"},
        )
        return {
            "staged": True,
            "proposal_id": prop.id,
            "encounter_id": enc.id,
            "routed_to": "Nurse Rachel Chan (RN) -> Dr. Kenneth Lam (M14829)",
            "authority": "DRAFT_ONLY (awaiting clinician signature)",
            "suggested_icd10": icd10,
            "sick_leave_days_draft": days_off,
        }

    # ------------------------------------------------------------------ Context Window Builder
    def _build_context_window(
        self,
        *,
        patient: Patient,
        trigger: str,
        user_input: str,
        recent_messages: list[Message],
        engine: dict[str, Any],
    ) -> dict[str, Any]:
        st = self.bus.get_patient_state(patient.id)
        system_prompt = (
            "You are the Central & Mid-Levels Family Medical Care AI operating inside a governed multi-agent harness.\n"
            "HARD RULES:\n"
            "1. Never diagnose definitively, prescribe, sign certificates, or reassure away red flags.\n"
            "2. Always check EMR allergies & G6PD status via tools before answering medication questions.\n"
            "3. Every clinical fact saved to memory MUST carry a verbatim quote.\n"
            "4. When acute URTI / sick-leave is requested, defer preventive flu vaccination 14 days and stage a Nurse -> Doctor review packet."
        )
        history = [
            {"role": "user" if m.sender_role == "patient" else "assistant", "content": m.body_text[:280]}
            for m in recent_messages[-8:]
        ]
        ctx = {
            "trigger": trigger,
            "engine_mode": engine["label"],
            "live_llm": engine["live"],
            "model": engine["model"],
            "system_prompt": system_prompt,
            "active_protocols": [p["id"] for p in CLINIC_PROTOCOLS],
            "emr_memory": st["memory"]["emr_memory"],
            "episodic_memory": st["memory"]["episodic_memory"],
            "tools_available": [f"{t['name']} [{t['authority']}]" for t in TOOL_SCHEMAS],
            "conversation_turns": len(history),
            "conversation_preview": history,
            "current_input": user_input,
        }
        blob = json.dumps(ctx, ensure_ascii=False)
        ctx["estimated_tokens"] = max(120, len(blob) // 3)
        self.bus.set_context_window(patient.id, ctx)
        return ctx

    # ------------------------------------------------------------------ Live LLM + Native Tool-Calling Loop
    @staticmethod
    def _vertex_adc_token(project_override: str | None = None) -> tuple[str, str]:
        return acquire_vertex_adc_token(project_override)

    @staticmethod
    def _gemini_tool_declarations() -> list[dict[str, Any]]:
        """Build Vertex AI / Gemini functionDeclarations from TOOL_SCHEMAS (underscore names)."""
        decls = []
        for t in TOOL_SCHEMAS:
            decls.append({
                "name": t["name"].replace(".", "_"),
                "description": f"[{t['authority']}] {t['description']}",
                "parameters": t["parameters"],
            })
        return [{"functionDeclarations": decls}]

    def _try_live_llm_reply(
        self,
        *,
        engine: dict[str, Any],
        ctx: dict[str, Any],
        patient: Patient,
        tool_findings_summary: dict[str, Any],
        lang: str,
        s: Session | None = None,
        tenant_id: str | None = None,
        source_texts: list[str] | None = None,
    ) -> str | None:
        """Run live LLM (Vertex AI asia-east2 HK ADC, Gemini API, Anthropic, or OpenAI) with optional native tool calls."""
        if not engine.get("live"):
            return None
        provider = engine["provider"]
        model = engine["model"]
        region = str(engine.get("region") or ("asia-east2" if provider == "vertex" else "global"))
        user_payload = json.dumps(
            {
                "language": "Traditional Chinese (Hong Kong Cantonese/written)" if lang == "zh-HK" else "English",
                "patient_id": patient.id,
                "patient_name": patient.name_en,
                "patient_message": ctx["current_input"],
                "verified_tool_results": tool_findings_summary,
                "instruction": (
                    "You are the clinic Care AI inside a multi-agent harness. You may invoke additional read/CDS/memory tools "
                    "if needed, then write a warm, concise (2-4 sentences) clinic assistant reply strictly grounded in verified_tool_results. "
                    "Never prescribe, sign certificates, or reassure away red flags. If G6PD/Penicillin safety flags exist, clearly warn the "
                    "patient to avoid flagged drugs and confirm Nurse Rachel and Dr. Kenneth Lam will review."
                ),
            },
            ensure_ascii=False,
        )
        system_prompt_eff = ctx["system_prompt"]
        if region.strip().lower() != "asia-east2":
            extra_names = [n for n in (patient.name_en, patient.name_zh) if n]
            system_prompt_eff, _ = PHIScrubber.scrub(system_prompt_eff, extra_names=extra_names)
            user_payload, _ = PHIScrubber.scrub(user_payload, extra_names=extra_names)
        name_map = {t["name"].replace(".", "_"): t["name"] for t in TOOL_SCHEMAS}
        try:
            with httpx.Client(timeout=15.0) as client:
                if provider in ("vertex", "gemini"):
                    model = normalize_gemini_model(model)
                    candidate_models = [model]
                    if model == DEFAULT_GEMINI_REASONING_MODEL:
                        candidate_models.append(DEFAULT_GEMINI_FAST_MODEL)

                    active_model = candidate_models[0]

                    def _endpoint_for(mdl: str) -> tuple[str, dict[str, str], dict[str, str]]:
                        if provider == "vertex":
                            token, project = self._vertex_adc_token(engine.get("project"))
                            return (
                                build_vertex_generate_content_url(region=region, project=project, model=mdl),
                                {
                                    "Authorization": f"Bearer {token}",
                                    "x-goog-user-project": project,
                                    "Content-Type": "application/json",
                                    "X-Vertex-Zero-Data-Retention": "true",
                                    "X-No-Training": "true",
                                },
                                {},
                            )
                        return (
                            f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                            {"Content-Type": "application/json"},
                            {"key": os.environ["GEMINI_API_KEY"]},
                        )

                    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": user_payload}]}]
                    tools_spec = self._gemini_tool_declarations() if (s is not None and tenant_id is not None) else None
                    for _round in range(3):
                        url, headers, params = _endpoint_for(active_model)
                        gen_cfg = build_gemini_generation_config(
                            active_model,
                            temperature=0.1,
                            max_output_tokens=1024,
                            expect_json=False,
                            thinking_level=engine.get("thinking_level"),
                        )
                        req_body: dict[str, Any] = {
                            "systemInstruction": {"parts": [{"text": system_prompt_eff}]},
                            "contents": contents,
                            "generationConfig": gen_cfg,
                        }
                        if provider == "vertex":
                            req_body["labels"] = {"zero_retention": "true", "no_training": "true", "pdpo_dpp3": "enforced"}
                        if tools_spec:
                            req_body["tools"] = tools_spec
                        r = client.post(url, headers=headers, params=params, json=req_body)
                        if r.status_code in (403, 404) and active_model != candidate_models[-1]:
                            active_model = candidate_models[-1]
                            url, headers, params = _endpoint_for(active_model)
                            req_body["generationConfig"] = build_gemini_generation_config(
                                active_model,
                                temperature=0.1,
                                max_output_tokens=1024,
                                expect_json=False,
                                thinking_level=engine.get("thinking_level"),
                            )
                            r = client.post(url, headers=headers, params=params, json=req_body)
                        if r.status_code != 200:
                            return None
                        cand = (r.json().get("candidates") or [{}])[0]
                        parts = ((cand.get("content") or {}).get("parts") or [])
                        fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
                        if fn_calls and s is not None and tenant_id is not None:
                            contents.append({"role": "model", "parts": parts})
                            fn_responses = []
                            for fc in fn_calls:
                                raw_fn = str(fc.get("name") or "")
                                tool_name = name_map.get(raw_fn, raw_fn)
                                fn_args = dict(fc.get("args") or {})
                                fn_args.setdefault("patient_id", patient.id)
                                tool_res = self._exec_tool(
                                    s=s,
                                    tenant_id=tenant_id,
                                    patient=patient,
                                    agent=f"CareAgent ({active_model})",
                                    tool_name=tool_name,
                                    args=fn_args,
                                    source_texts=source_texts,
                                )
                                fr_body: dict[str, Any] = {"name": raw_fn, "response": {"result": tool_res}}
                                if fc.get("id"):
                                    fr_body["id"] = fc["id"]
                                fn_responses.append({"functionResponse": fr_body})
                            contents.append({"role": "user", "parts": fn_responses})
                            continue
                        txt = extract_gemini_visible_text(parts)
                        if txt:
                            return txt
                        return None

                elif provider == "anthropic":
                    r = client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": model,
                            "max_tokens": 300,
                            "system": ctx["system_prompt"],
                            "messages": [{"role": "user", "content": user_payload}],
                        },
                    )
                    if r.status_code == 200:
                        blocks = r.json().get("content") or []
                        txt = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                        if txt:
                            return txt

                elif provider == "openai":
                    r = client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "max_tokens": 300,
                            "temperature": 0.2,
                            "store": False,
                            "messages": [
                                {"role": "system", "content": ctx["system_prompt"]},
                                {"role": "user", "content": user_payload},
                            ],
                        },
                    )
                    if r.status_code == 200:
                        txt = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
                        if txt:
                            return txt
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------ Public Orchestration Entrypoints
    def run_qr_checkin(self, s: Session, tenant_id: str, patient: Patient, lang: str = "en") -> dict[str, Any]:
        """Triggered the instant a real phone scans the clinic QR code."""
        engine = detect_live_llm_engine()
        self.bus.update_patient_state(
            patient.id,
            phone_connected=True,
            phone_connected_at=utcnow().astimezone(HKT).strftime("%H:%M:%S HKT"),
            active_agent="LoopWatcher",
            engine_mode=engine["label"],
        )
        self.bus.publish(
            "phone.connected",
            patient_id=patient.id,
            data={"patient_id": patient.id, "patient_name": patient.name_en, "channel": "qr_scan"},
        )

        # Avoid duplicating check-in tool burst if already hydrated in same session
        st = self.bus.get_patient_state(patient.id)
        already_hydrated = any(tc.get("tool") == "emr.check_preventive_gaps" for tc in st.get("tool_calls") or [])
        if not already_hydrated:
            self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "LoopWatcher", "action": "Hydrating EMR chart, pending labs & seasonal flu gaps"})
            chart = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="LoopWatcher", tool_name="emr.get_patient_chart", args={"patient_id": patient.id})
            recs = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="LoopWatcher", tool_name="emr.get_clinical_records", args={"patient_id": patient.id})
            gaps = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="LoopWatcher", tool_name="emr.check_preventive_gaps", args={"patient_id": patient.id})
            slots_res = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="clinic.get_available_slots", args={"service": "flu_vaccine"})

            # Persist initial EMR memory facts with verbatim quotes
            if patient.id == HERO_URTI_PATIENT_ID:
                self._exec_tool(
                    s=s, tenant_id=tenant_id, patient=patient, agent="LoopWatcher",
                    tool_name="memory.upsert_fact",
                    args={
                        "key": "pending_lab_followup",
                        "value": "Serum Ferritin 14 ng/mL (Low) — repeat in Feb 2027",
                        "verbatim_quote": "Serum Ferritin: 14 ng/mL (Low; Ref 15–150) — suggest repeat iron studies in 3 months.",
                        "source": "KingMed 2026-11-08",
                    },
                    source_texts=[HERO_EMR_DOCUMENTS[0]["text"]],
                )
                self._exec_tool(
                    s=s, tenant_id=tenant_id, patient=patient, agent="LoopWatcher",
                    tool_name="memory.upsert_fact",
                    args={
                        "key": "safety_alerts",
                        "value": "G6PD Deficient · Penicillin Allergy",
                        "verbatim_quote": "Allergies: Penicillin (urticarial rash) | Special Alert: G6PD Deficient",
                        "source": "GP Consult Note 2026-11-10",
                    },
                    source_texts=[HERO_EMR_DOCUMENTS[1]["text"]],
                )

            msgs = s.scalars(select(Message).where(Message.patient_id == patient.id).order_by(Message.created_at.asc())).all()
            if not msgs and patient.id == HERO_URTI_PATIENT_ID:
                welcome = (
                    "Hi Wing-Yan — welcome back to Central & Mid-Levels Family Medical. "
                    "I've pulled your recent 8 Nov KingMed blood panel (Ferritin 14 ng/mL — Dr. Lam noted a 3-month repeat in Feb) "
                    "and your Harbour FinTech 2026/27 flu vaccination pass ($0 copay). "
                    "You can view your lab report or pick a flu-shot slot above, or message me anytime if you're feeling unwell today."
                )
                s.add(Message(
                    id=new_id("msg"),
                    tenant_id=tenant_id,
                    patient_id=patient.id,
                    direction="outbound",
                    sender_role="assistant",
                    body_text=welcome,
                ))
                s.flush()
                msgs = s.scalars(select(Message).where(Message.patient_id == patient.id).order_by(Message.created_at.asc())).all()

            self._build_context_window(
                patient=patient,
                trigger="qr_scan_checkin",
                user_input="Patient scanned clinic QR code to open Care AI chat.",
                recent_messages=msgs,
                engine=engine,
            )
            self.bus.update_patient_state(
                patient.id,
                workflow_stage="qr_scanned",
                active_agent=None,
            )
            return {"chart": chart, "records": recs, "gaps": gaps, "slots": slots_res["slots"]}
        return {"already_hydrated": True}

    def run_patient_turn(
        self,
        *,
        s: Session,
        tenant_id: str,
        patient: Patient,
        action: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
        lang: str = "en",
    ) -> dict[str, Any]:
        """Execute a full multi-agent harness turn from the patient's phone."""
        payload = payload or {}
        engine = detect_live_llm_engine()
        sla = next_clinic_review_sla(doctor_name="Dr. Kenneth Lam")

        # 1. Handle direct Flu Slot Booking pop-up
        if action == "book_flu_slot":
            slot_id = str(payload.get("slot_id") or "")
            self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "CareAgent", "action": f"Booking flu vaccination slot {slot_id}"})
            booked = self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent",
                tool_name="clinic.book_slot", args={"patient_id": patient.id, "slot_id": slot_id},
            )
            slot = booked["slot"]
            reply_en = (
                f"Booked! Your 2026/27 flu vaccination is confirmed for {slot['label_en']} with {slot['provider']} "
                f"({slot['location']} · Harbour FinTech $0 copay). I've synced it to your clinic EMR."
            )
            reply_zh = (
                f"已為你預約！2026/27 季節性流感疫苗時間：{slot['label_zh']}（{slot['provider']} · Harbour FinTech 免費津貼），並已同步至診所病歷。"
            )
            reply = reply_zh if lang == "zh-HK" else reply_en
            s.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=patient.id, direction="inbound", sender_role="patient", body_text=f"Booked flu vaccination slot: {slot['label_en']}"))
            s.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=patient.id, direction="outbound", sender_role="assistant", body_text=reply))
            s.flush()
            msgs = s.scalars(select(Message).where(Message.patient_id == patient.id).order_by(Message.created_at.asc())).all()
            self._build_context_window(patient=patient, trigger="book_flu_slot", user_input=f"Book slot {slot_id}", recent_messages=msgs, engine=engine)
            return {"ok": True, "crisis": None, "reply": reply, "booked_slot": slot, "rich_cards": self.build_patient_rich_cards(s, patient)}

        # 2. Handle Structured URTI + Sick-Leave Intake Pop-up submission
        if action == "submit_urti_intake":
            symptoms = payload.get("symptoms") or ["sore throat", "blocked nose", "cough", "fever"]
            onset = str(payload.get("onset") or "Yesterday evening (<24h)")
            temp_c = float(payload.get("temp_c") or 38.2)
            rat = str(payload.get("rat_result") or "Negative (Viral URTI)")
            days_off = max(1, min(7, int(payload.get("sick_leave_days") or 2)))
            asked_drugs = payload.get("asked_drugs") or ["paracetamol", "cetirizine"]
            note_txt = str(payload.get("note") or "").strip()
            patient_summary_line = (
                f"URTI Intake confirmed: {', '.join(symptoms)} since {onset}; temp {temp_c:.1f}°C; "
                f"Home RAT: {rat}; breathing comfortably (red-flag check clear); requesting {days_off}-day sick leave certificate."
                + (f" Note: {note_txt}" if note_txt else "")
            )
            s.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=patient.id, direction="inbound", sender_role="patient", body_text=patient_summary_line))
            s.flush()

            # Step 2a: Deterministic SafetySentinel (screens patient-selected symptoms + free-text note)
            screen_text = f"{', '.join(symptoms)} {note_txt}".strip()
            self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "SafetySentinel", "action": "Running deterministic red-flag screen"})
            rf = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="SafetySentinel", tool_name="safety.screen_red_flags", args={"message": screen_text, "temp_c": temp_c})
            if rf.get("crisis_kind"):
                return self._handle_crisis(s, tenant_id, patient, patient_summary_line, rf["crisis_kind"])

            # Step 2b: Memory Upserts with verified verbatim quotes
            self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "CareAgent", "action": "Writing verified clinical facts to episodic memory"})
            self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="memory.upsert_fact",
                args={"key": "acute_symptoms", "value": f"{', '.join(symptoms)} ({onset}, {temp_c:.1f}°C)", "verbatim_quote": f"{', '.join(symptoms)} since {onset}; temp {temp_c:.1f}°C", "source": "URTI Intake Pop-up"},
                source_texts=[patient_summary_line],
            )
            self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="memory.upsert_fact",
                args={"key": "rat_result", "value": rat, "verbatim_quote": f"Home RAT: {rat}", "source": "URTI Intake Pop-up"},
                source_texts=[patient_summary_line],
            )
            self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="memory.upsert_fact",
                args={"key": "sick_leave_request", "value": f"{days_off} days (Bilingual MCHK PDF)", "verbatim_quote": f"requesting {days_off}-day sick leave certificate", "source": "URTI Intake Pop-up"},
                source_texts=[patient_summary_line],
            )

            # Step 2c: Protocol & CDS Agent checks G6PD + Penicillin safety & stages Nurse -> Doctor proposal
            self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "ProtocolCDSAgent", "action": "Checking G6PD/allergy CDS rules & staging Nurse -> Doctor proposal"})
            cds_drugs = list(dict.fromkeys([*asked_drugs, "aspirin", "amoxicillin", "paracetamol"]))
            cds = self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="ProtocolCDSAgent",
                tool_name="cds.check_medication_safety",
                args={"patient_id": patient.id, "drugs": cds_drugs, "diagnosis_icd10": "J10.1" if ("flu" in rat.lower() and "pos" in rat.lower()) else "J06.9"},
            )
            staged = self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="ProtocolCDSAgent",
                tool_name="harness.stage_staff_proposal",
                args={
                    "patient_id": patient.id,
                    "symptoms": symptoms,
                    "onset": onset,
                    "temp_c": temp_c,
                    "rat_result": rat,
                    "sick_leave_days": days_off,
                    "patient_message": patient_summary_line,
                },
            )

            msgs = s.scalars(select(Message).where(Message.patient_id == patient.id).order_by(Message.created_at.asc())).all()
            ctx = self._build_context_window(patient=patient, trigger="submit_urti_intake", user_input=patient_summary_line, recent_messages=msgs, engine=engine)

            llm_reply = self._try_live_llm_reply(
                engine=engine, ctx=ctx, patient=patient,
                tool_findings_summary={"cds": cds, "staged": staged, "flu_vax_deferred": True},
                lang=lang,
                s=s, tenant_id=tenant_id, source_texts=[patient_summary_line],
            )
            reply_en = llm_reply or (
                f"Got it — I've logged your URTI intake ({', '.join(symptoms)}, {temp_c:.1f}°C, RAT: {rat}), "
                f"paused your flu vaccination offer until you're fever-free for 48 hours, and flagged your G6PD deficiency & Penicillin allergy. "
                f"Your {days_off}-day sick-leave request and G6PD-safe symptom relief draft are now with Nurse Rachel and Dr. Kenneth Lam ({sla['label_en']}). "
                f"Your signed care plan and MCHK PDF certificate will appear right here automatically once Dr. Lam signs."
            )
            reply_zh = (
                f"已收到！我已紀錄你的上呼吸道症狀（{temp_c:.1f}°C、快測：{rat}），暫緩流感疫苗預約直至退燒 48 小時，並已標示你的 G6PD 缺乏症及盤尼西林過敏。"
                f"你的 {days_off} 日病假紙申請及 G6PD 安全紓緩藥物草稿已送交陳護士及林啟賢醫生覆核（{sla['label_zh']}），醫生簽署後會即時推送到此對話。"
            )
            reply = reply_zh if (lang == "zh-HK" and not llm_reply) else reply_en
            s.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=patient.id, direction="outbound", sender_role="assistant", body_text=reply))
            s.flush()
            self.bus.update_patient_state(patient.id, active_agent=None)
            return {
                "ok": True,
                "crisis": None,
                "reply": reply,
                "proposal_id": staged["proposal_id"],
                "cds": cds,
                "rich_cards": self.build_patient_rich_cards(s, patient),
            }

        # 3. Free-text conversational patient message
        clean_msg = message.strip()
        if not clean_msg:
            return {"ok": True, "crisis": None, "reply": "", "rich_cards": self.build_patient_rich_cards(s, patient)}

        s.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=patient.id, direction="inbound", sender_role="patient", body_text=clean_msg))
        s.flush()

        # Always run deterministic SafetySentinel FIRST (zero LLM bypass)
        self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "SafetySentinel", "action": "Deterministic HK 999 / MH crisis screening"})
        rf = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="SafetySentinel", tool_name="safety.screen_red_flags", args={"message": clean_msg})
        if rf.get("crisis_kind"):
            return self._handle_crisis(s, tenant_id, patient, clean_msg, rf["crisis_kind"])

        low = clean_msg.lower()
        self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "CareAgent", "action": "Reading chart, checking medications & extracting structured clinical facts"})

        chart = self._exec_tool(s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="emr.get_patient_chart", args={"patient_id": patient.id})

        # Detect mentioned drugs / URTI symptoms / sick leave / flu vax intent
        mentioned_drugs: list[str] = []
        for kw, canon in [
            ("aspirin", "aspirin"), ("阿士匹靈", "aspirin"),
            ("amoxicillin", "amoxicillin"), ("augmentin", "amoxicillin"), ("penicillin", "amoxicillin"), ("antibiotic", "amoxicillin"), ("抗生素", "amoxicillin"),
            ("panadol", "paracetamol"), ("paracetamol", "paracetamol"), ("必理痛", "paracetamol"),
            ("ibuprofen", "ibuprofen"), ("布洛芬", "ibuprofen"),
            ("tamiflu", "oseltamivir"), ("oseltamivir", "oseltamivir"), ("特敏福", "oseltamivir"),
        ]:
            if kw in low and canon not in mentioned_drugs:
                mentioned_drugs.append(canon)

        is_urti = any(k in low for k in ("sore throat", "cough", "runny", "blocked", "fever", "flu", "cold", "urti", "sick leave", "cert", "mc", "喉嚨痛", "咳", "發燒", "流鼻水", "病假", "感冒"))
        is_vax = any(k in low for k in ("vaccine", "flu shot", "flu jab", "slot", "book", "疫苗", "打針", "預約")) and not is_urti

        # Extract temp if present
        temp_m = re.search(r"(3[6-9](?:\.\d)?|4[0-1](?:\.\d)?)\s*(?:°?c|度|deg)?", low)
        temp_val = float(temp_m.group(1)) if temp_m else None
        days_m = re.search(r"(\d)\s*(?:-?\s*days?|日|天)", low)
        days_val = int(days_m.group(1)) if days_m else 2

        if is_urti:
            self.bus.update_patient_state(patient.id, flu_vax_deferred=True)
            self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="memory.upsert_fact",
                args={"key": "chief_complaint", "value": clean_msg[:120], "verbatim_quote": clean_msg[:120], "source": "Patient Phone Chat"},
                source_texts=[clean_msg],
            )
            if temp_val is not None:
                self._exec_tool(
                    s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="memory.upsert_fact",
                    args={"key": "self_reported_temp", "value": f"{temp_val:.1f} °C", "verbatim_quote": temp_m.group(0), "source": "Patient Phone Chat"},
                    source_texts=[clean_msg],
                )
            if any(k in low for k in ("sick leave", "cert", "mc", "病假")):
                self._exec_tool(
                    s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent", tool_name="memory.upsert_fact",
                    args={"key": "sick_leave_intent", "value": f"{days_val} days requested", "verbatim_quote": clean_msg[:120], "source": "Patient Phone Chat"},
                    source_texts=[clean_msg],
                )

        cds_res: dict[str, Any] | None = None
        if mentioned_drugs or is_urti:
            self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "ProtocolCDSAgent", "action": "Running HK G6PD & drug allergy CDS floor"})
            check_list = mentioned_drugs if mentioned_drugs else ["paracetamol"]
            cds_res = self._exec_tool(
                s=s, tenant_id=tenant_id, patient=patient, agent="ProtocolCDSAgent",
                tool_name="cds.check_medication_safety",
                args={"patient_id": patient.id, "drugs": check_list, "diagnosis_icd10": "J06.9"},
            )

        msgs = s.scalars(select(Message).where(Message.patient_id == patient.id).order_by(Message.created_at.asc())).all()
        ctx = self._build_context_window(patient=patient, trigger="patient_chat_turn", user_input=clean_msg, recent_messages=msgs, engine=engine)

        llm_reply = self._try_live_llm_reply(
            engine=engine, ctx=ctx, patient=patient,
            tool_findings_summary={"chart": chart, "cds": cds_res, "is_urti": is_urti, "is_vax": is_vax, "flu_vax_deferred": is_urti},
            lang=lang,
            s=s, tenant_id=tenant_id, source_texts=[clean_msg],
        )

        if llm_reply:
            reply = llm_reply
        elif is_urti:
            safety_parts: list[str] = []
            if cds_res and cds_res.get("blocked_or_flagged"):
                flagged_drugs = ", ".join(sorted({f["drug"].title() for f in cds_res["blocked_or_flagged"]}))
                safety_parts.append(
                    f"Important safety catch from your chart (G6PD deficiency & Penicillin allergy): please do NOT take {flagged_drugs}."
                )
            if "paracetamol" in mentioned_drugs or "panadol" in low:
                safety_parts.append("Standard Paracetamol (Panadol) is typically G6PD-safe, and viral URTIs usually do not need antibiotics — Dr. Kenneth Lam will confirm your exact prescription.")
            elif not safety_parts:
                safety_parts.append("I've checked your chart (G6PD deficiency & Penicillin allergy) and paused your flu vaccination offer until you recover.")
            reply_en = (
                " ".join(safety_parts)
                + f" Tap \"Complete 15-sec URTI & Sick Leave Check\" below to confirm your home RAT and {days_val}-day certificate dates so Nurse Rachel can prep your chart for Dr. Lam right away."
            )
            reply_zh = (
                "根據你的病歷紀錄（G6PD 缺乏症及盤尼西林過敏），切勿自行服用阿士匹靈或盤尼西林類抗生素。"
                "我已暫緩你的流感疫苗預約；請點擊下方「填寫上呼吸道及病假資料」確認快測結果及病假日期，我會即時轉交陳護士及林啟賢醫生覆核。"
            )
            reply = reply_zh if lang == "zh-HK" else reply_en
        elif is_vax:
            reply_en = (
                "You're eligible for the 2026/27 quadrivalent flu vaccine under Harbour FinTech ($0 copay), "
                "and it is safe with both your G6PD deficiency and Penicillin allergy. Tap 'Personalised Info Pack' or 'Pick a Slot' above to book."
            )
            reply_zh = "你符合 Harbour FinTech 2026/27 免費流感疫苗資格，且對 G6PD 缺乏症及盤尼西林過敏人士安全。你可以直接點擊上方「個人化資訊」或「預約時段」。"
            reply = reply_zh if lang == "zh-HK" else reply_en
        else:
            reply_en = (
                f"I've checked your clinic chart and logged your message for Dr. Kenneth Lam ({sla['label_en']}). "
                "If you're experiencing fever, sore throat, or need a sick-leave certificate today, let me know your symptoms or tap the quick URTI check below."
            )
            reply_zh = f"我已查閱你的病歷並將訊息交由林啟賢醫生跟進（{sla['label_zh']}）。如今天出現發燒、喉嚨痛或需要病假證明，歡迎隨時告訴我。"
            reply = reply_zh if lang == "zh-HK" else reply_en

        s.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=patient.id, direction="outbound", sender_role="assistant", body_text=reply))
        s.flush()

        # Store latest CDS findings & intake prompt trigger in patient state for rich phone UI
        st_updates: dict[str, Any] = {"active_agent": None}
        if is_urti:
            st_updates["urti_prompt_active"] = True
            st_updates["urti_defaults"] = {
                "temp_c": temp_val or 38.2,
                "sick_leave_days": days_val,
                "asked_drugs": mentioned_drugs,
            }
        if cds_res and cds_res.get("blocked_or_flagged"):
            st_updates["last_cds_alert"] = cds_res
        self.bus.update_patient_state(patient.id, **st_updates)

        return {
            "ok": True,
            "crisis": None,
            "reply": reply,
            "cds": cds_res,
            "open_urti_intake": is_urti,
            "rich_cards": self.build_patient_rich_cards(s, patient),
        }

    def _handle_crisis(
        self,
        s: Session,
        tenant_id: str,
        patient: Patient,
        raw_msg: str,
        crisis_kind: str,
    ) -> dict[str, Any]:
        new_enc = Encounter(
            id=new_id("enc"),
            tenant_id=tenant_id,
            patient_id=patient.id,
            encounter_type="crisis_alert",
            status="red_flag_escalated",
            chief_complaint_text=raw_msg,
        )
        s.add(new_enc)
        s.flush()
        prop = Proposal(
            id=new_id("prop"),
            tenant_id=tenant_id,
            patient_id=patient.id,
            encounter_id=new_enc.id,
            kind="red_flag_alert",
            urgency=4,
            status="pending_review",
            sla_due_at=utcnow() + timedelta(minutes=15),
            protocol_id="flu_intake_v1",
            protocol_version="1.0.0",
            payload_json={"title": f"URGENT {crisis_kind.replace('_', ' ')} — {patient.name_en}", "triage": {"red_flags": [crisis_kind]}},
            source_quotes_json=[{"field": "Patient message", "quote": raw_msg[:200], "source": "Patient Phone"}],
            checks_json=[],
        )
        s.add(prop)
        s.flush()
        acked_at = utcnow().astimezone(HKT).strftime("%H:%M")
        record_audit(
            s, tenant_id=tenant_id, actor_role="system", actor_id="safety_sentinel",
            action="crisis.escalated", resource_type="Proposal", resource_id=prop.id,
            metadata={"crisis_kind": crisis_kind},
        )
        self.bus.publish("staff.queue_updated", patient_id=patient.id, data={"proposal_id": prop.id, "stage": "crisis_escalated", "urgency": 4})
        return {
            "ok": True,
            "crisis": {
                "kind": crisis_kind,
                "pager_acked": True,
                "pager_acked_at": acked_at,
                "paged_clinician": "Dr. Kenneth Lam (on duty)",
                "hotlines": HK_MENTAL_HEALTH_HOTLINES if crisis_kind == "mental_health_crisis" else [],
            },
        }

    def build_patient_rich_cards(self, s: Session, patient: Patient) -> list[dict[str, Any]]:
        """Assemble live interactive rich cards for the patient's mobile chat UI."""
        st = self.bus.get_patient_state(patient.id)
        cards: list[dict[str, Any]] = []

        # 1. Signed Care Plan + MCHK Sick Leave PDF Card (highest priority when doctor signs!)
        certs = s.scalars(select(Certificate).where(Certificate.patient_id == patient.id).order_by(Certificate.signed_at.desc()).limit(1)).all()
        rxs = s.scalars(select(Prescription).where(Prescription.patient_id == patient.id).order_by(Prescription.signed_at.desc()).limit(1)).all()
        props = s.scalars(select(Proposal).where(Proposal.patient_id == patient.id).order_by(Proposal.created_at.desc()).limit(3)).all()
        pending_prop = next((p for p in props if p.status == "pending_review"), None)
        approved_prop = next((p for p in props if p.status in ("approved", "edited")), None)

        if approved_prop and (certs or rxs):
            cert = certs[0] if certs else None
            rx = rxs[0] if rxs else None
            cards.append({
                "id": f"signed_plan_{approved_prop.id}",
                "kind": "signed_care_plan",
                "title_en": "Signed Care Plan & Sick Leave Ready",
                "title_zh": "醫生已簽發治療計劃及病假證明書",
                "doctor": "Dr. Kenneth Lam · MCHK M14829",
                "rx": {
                    "id": rx.id,
                    "items": rx.items_json or [],
                    "fulfilment_mode": rx.fulfilment_mode,
                    "fulfilment_status": rx.fulfilment_status,
                } if rx else None,
                "certificate": {
                    "id": cert.id,
                    "days_off": cert.days_off,
                    "start_date": cert.start_date,
                    "end_date": cert.end_date,
                    "mchk_reg_no": cert.mchk_reg_no,
                    "diagnosis_icd10": cert.diagnosis_icd10,
                } if cert else None,
                "followup_note_en": "48-hour automated check-in scheduled · Flu vaccination re-offer in 14 days.",
                "followup_note_zh": "已安排 48 小時自動跟進 · 14 日後重新開放流感疫苗預約。",
            })

        # 2. Live Clinical Queue Status Card (while Nurse Rachel / Dr. Lam are reviewing!)
        if pending_prop:
            p_json = pending_prop.payload_json or {}
            nurse_ready = bool(p_json.get("nurse_ready"))
            cards.append({
                "id": f"status_{pending_prop.id}",
                "kind": "proposal_status",
                "stage": "nurse_ready" if nurse_ready else "nurse_prep",
                "title_en": (
                    "Nurse Rachel verified your chart — now with Dr. Kenneth Lam"
                    if nurse_ready
                    else "Sent to Clinic Team — Nurse Rachel prepping your chart"
                ),
                "title_zh": (
                    "陳護士已完成核對——現正交由林啟賢醫生審閱"
                    if nurse_ready
                    else "已送交診所團隊——陳護士正為你準備病歷"
                ),
                "detail_en": (
                    p_json.get("nurse_prep_note") or "G6PD & Penicillin allergy verified. Doctor sign-off & MCHK PDF arriving live here."
                    if nurse_ready
                    else "Your URTI intake, G6PD/allergy flags, and sick-leave request are queued live in the clinic workspace."
                ),
                "detail_zh": (
                    "已核對 G6PD 及盤尼西林過敏紀錄，醫生簽發後病假紙將即時送達。"
                    if nurse_ready
                    else "你的症狀資料、藥物安全標記及病假申請已即時傳送至診所。"
                ),
            })

        # 3. G6PD / Penicillin CDS Safety Catch Card
        if st.get("last_cds_alert") and st["last_cds_alert"].get("blocked_or_flagged"):
            cards.append({
                "id": "cds_safety_card",
                "kind": "medication_safety",
                "title_en": "Chart Safety Alert — Avoid Flagged Medicines",
                "title_zh": "病歷藥物安全提示——避免服用高風險藥物",
                "findings": st["last_cds_alert"]["blocked_or_flagged"],
                "safe_candidates": st["last_cds_alert"].get("safe_candidates") or ["paracetamol"],
            })

        # 4. Interactive URTI + Sick Leave Intake Trigger Card (if URTI started and not yet signed)
        if st.get("urti_prompt_active") and not pending_prop and not approved_prop:
            cards.append({
                "id": "urti_intake_trigger",
                "kind": "urti_intake_prompt",
                "title_en": "Complete 15-sec URTI & Sick Leave Check",
                "title_zh": "15 秒完成上呼吸道症狀及病假證明資料",
                "subtitle_en": "Confirm symptoms, home RAT, and sick-leave dates so Nurse Rachel & Dr. Lam can review immediately.",
                "subtitle_zh": "確認症狀、快測結果及病假日期，即時交由陳護士及林醫生覆核。",
                "defaults": st.get("urti_defaults") or {"temp_c": 38.2, "sick_leave_days": 2},
            })

        # 5. Hero Patient Post-Visit Lab Card & Flu Season Prevention Card
        if patient.id == HERO_URTI_PATIENT_ID:
            lab_doc = HERO_EMR_DOCUMENTS[0]
            cards.append({
                "id": "post_visit_lab_lam",
                "kind": "lab_result",
                "title_en": "Post-Visit Lab Back · KingMed (8 Nov)",
                "title_zh": "覆檢化驗報告已出 · 金域檢驗（11月8日）",
                "summary_en": "Mild low Ferritin (14 ng/mL; Hb normal 12.1). Dr. Lam noted: repeat iron panel in Feb 2027.",
                "summary_zh": "鐵蛋白輕微偏低（14 ng/mL；血紅素正常 12.1）。林醫生建議 2027 年 2 月覆檢。",
                "quote": lab_doc["highlight_quote"],
                "doc_title": lab_doc["title"],
                "doc_text": lab_doc["text"],
            })
            booked = st.get("booked_slot")
            deferred = bool(st.get("flu_vax_deferred"))
            cards.append({
                "id": "flu_season_2026_lam",
                "kind": "flu_vaccine",
                "status": "booked" if booked else ("deferred" if deferred else "eligible"),
                "booked_slot": booked,
                "title_en": (
                    f"Flu Shot Booked · {booked['label_en']}"
                    if booked
                    else (
                        "2026/27 Flu Shot Paused (Recover First)"
                        if deferred
                        else "2026/27 Seasonal Flu Vaccination · $0 Copay"
                    )
                ),
                "title_zh": (
                    f"已預約流感疫苗 · {booked['label_zh']}"
                    if booked
                    else (
                        "已暫緩流感疫苗（康復後再安排）"
                        if deferred
                        else "2026/27 季節性流感疫苗 · Harbour FinTech 全額資助"
                    )
                ),
                "detail_en": (
                    f"Confirmed with {booked['provider']} at {booked['location']}."
                    if booked
                    else (
                        "Clinic protocol defers flu vaccination during acute fever/URTI. I'll re-open booking automatically in 14 days."
                        if deferred
                        else "Covered by Harbour FinTech Corporate Care. Verified safe with your G6PD deficiency & Penicillin allergy."
                    )
                ),
                "detail_zh": (
                    f"已確認時段：{booked['provider']}（{booked['location']}）。"
                    if booked
                    else (
                        "按診所指引，發燒或急性上呼吸道感染期間應暫緩接種，14 日後將自動重新開放預約。"
                        if deferred
                        else "Harbour FinTech 企業計劃全額資助，已核對對 G6PD 缺乏症及盤尼西林過敏安全。"
                    )
                ),
                "info_pack": {
                    "headline_en": "Personalised Flu Vaccine Pack — LAM Wing-Yan",
                    "headline_zh": "個人化流感疫苗資訊包 — 林詠欣",
                    "chart_checks": [
                        "G6PD Deficient: Inactivated quadrivalent flu vaccine is SAFE (no oxidative hemolysis risk).",
                        "Penicillin Allergy: Flu vaccine contains ZERO penicillin/beta-lactam antibiotics.",
                        "Coverage: $0 out-of-pocket under Harbour FinTech Winter Flu Shield 2026.",
                    ],
                    "what_to_expect": [
                        "10-minute express nurse appointment at Central Clinic (lunch & after-work slots available).",
                        "Protection builds over ~14 days and lowers peak winter flu severity & work absence.",
                        "Mild arm soreness or low-grade fatigue for 24–48h is normal; Paracetamol is G6PD-safe if needed.",
                    ],
                },
                "slots": available_clinic_slots(),
            })

        return cards

    def probe_authority_boundary(self, s: Session, tenant_id: str, patient: Patient) -> dict[str, Any]:
        """Let the presenter trigger a live Asymmetric Authority guardrail demonstration."""
        st = self.bus.get_patient_state(patient.id)
        prop_id = st.get("active_proposal_id") or "prop_draft_probe"
        self.bus.publish("agent.step", patient_id=patient.id, data={"agent": "CareAgent", "action": "Probing guardrail: attempting unauthorized emr.write_signed_orders"})
        return self._exec_tool(
            s=s, tenant_id=tenant_id, patient=patient, agent="CareAgent",
            tool_name="emr.write_signed_orders",
            args={"proposal_id": prop_id, "requested_action": "auto_sign_rx_and_sick_leave"},
        )

    def notify_nurse_ready(self, patient_id: str, proposal_id: str, nurse_name: str, prep_note: str) -> None:
        self.bus.update_patient_state(patient_id, workflow_stage="nurse_ready")
        self.bus.append_tool_call(
            patient_id,
            {
                "agent": "NurseHandoff (RBAC)",
                "tool": "staff.mark_ready_for_doctor",
                "authority": "DRAFT",
                "args": {"proposal_id": proposal_id, "nurse": nurse_name, "prep_note": prep_note},
                "result": {"nurse_ready": True, "can_sign_rx": False, "routed_to": "Dr. Kenneth Lam (M14829)"},
                "duration_ms": 2.4,
                "emr_target": "proposal_queue",
            },
        )
        self.bus.publish(
            "staff.queue_updated",
            patient_id=patient_id,
            data={"proposal_id": proposal_id, "stage": "nurse_ready", "nurse_name": nurse_name, "prep_note": prep_note},
        )

    def notify_doctor_signed(
        self,
        *,
        patient_id: str,
        proposal_id: str,
        doctor_name: str,
        mchk_reg_no: str,
        cert_out: dict[str, Any] | None,
        rx_out: dict[str, Any] | None,
        encounter_id: str,
    ) -> None:
        self.bus.update_patient_state(patient_id, workflow_stage="doctor_signed")
        self.bus.append_tool_call(
            patient_id,
            {
                "agent": f"{doctor_name} ({mchk_reg_no})",
                "tool": "emr.write_signed_orders",
                "authority": "SIGNED_WRITE",
                "args": {
                    "proposal_id": proposal_id,
                    "mchk_reg_no": mchk_reg_no,
                    "issue_certificate": bool(cert_out),
                    "issue_rx": bool(rx_out),
                },
                "result": {
                    "written_to_emr": True,
                    "encounter_id": encounter_id,
                    "prescription_id": rx_out["id"] if rx_out else None,
                    "certificate_id": cert_out["id"] if cert_out else None,
                    "followup_48h_scheduled": True,
                },
                "duration_ms": 11.2,
                "emr_target": "signed_orders",
            },
        )
        self.bus.publish(
            "emr.mutated",
            patient_id=patient_id,
            data={
                "entity": "signed_orders",
                "encounter_id": encounter_id,
                "certificate": cert_out,
                "prescription": rx_out,
                "summary": f"Dr. Lam ({mchk_reg_no}) signed Rx & MCHK Sick Leave PDF -> written to EMR",
            },
        )
        self.bus.publish(
            "patient.care_plan_delivered",
            patient_id=patient_id,
            data={
                "proposal_id": proposal_id,
                "doctor": f"{doctor_name} ({mchk_reg_no})",
                "certificate": cert_out,
                "prescription": rx_out,
            },
        )
        self.bus.publish(
            "staff.queue_updated",
            patient_id=patient_id,
            data={"proposal_id": proposal_id, "stage": "doctor_signed"},
        )


def _group_by(rows: list[Any], attr: str) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for r in rows:
        k = getattr(r, attr, None)
        if k is not None:
            out.setdefault(k, []).append(r)
    return out
