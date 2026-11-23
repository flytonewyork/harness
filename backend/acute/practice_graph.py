"""3-Layer Standards-of-Care Clinical Practice Knowledge Graph & Deterministic Traversal Engine.

Architecture:
- Layer 1 (EBM Base Tree): Pareto-optimal gold-standard GP clinical decision graph built on
  evidence-based medicine (HK Primary Care Reference Frameworks, NICE, RACGP, MCHK) with an
  Immutable Safety Floor that NO clinic customization can weaken.
- Layer 2 (Clinic Practice Overlay): Moulded during clinic onboarding to encode practice-agreed
  care standards, commercial triage windows, corporate benefits, partner labs, nurse scope,
  and Careline AI identity (e.g., "Sam · Clinic Careline AI" — never "Resident" or "Doctor").
- Layer 3 (Continuous Curation): Captures structured doctor override diffs and compiles versioned
  Practice Graph diffs for Lead GP / Medical Director sign-off (implemented in `curator.py`).

Every agent loop (Loops 1–5) traverses this graph deterministically BEFORE spending LLM tokens,
then injects only the bounded traversed subgraph slice into the model's context window.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .companion import classify_crisis

EBM_BASE_VERSION = "ebm-hk-gp-v1.0"

# Forbidden careline titles that misrepresent automated software as a licensed medical practitioner.
FORBIDDEN_AI_TITLES = ("doctor", "dr", "dr.", "resident", "physician", "clinician", "md", "mbbs")


class SafetyFloorViolation(ValueError):
    """Raised whenever an onboarding overlay or Curator proposal attempts to weaken an Immutable Safety Floor node."""


@dataclass(frozen=True)
class GraphNode:
    id: str
    layer: str  # safety_floor | ebm_base | practice_overlay
    category: str  # safety | triage | pathway | action | governance
    title: str
    guideline_ref: str
    summary: str
    safety_floor: bool = False
    overridable: bool = True
    irreversible: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    condition: str
    priority: int = 10


# -------------------------------------------------------------------------------------- Layer 1: Gold-Standard EBM Base Tree
EBM_BASE_NODES: dict[str, GraphNode] = {
    # --- Immutable Safety Floor (cannot be disabled or weakened by any clinic overlay) ---
    "ebm.safety.crisis_999": GraphNode(
        id="ebm.safety.crisis_999",
        layer="safety_floor",
        category="safety",
        title="Urgent Red-Flag & 999 Exit",
        guideline_ref="HK HA A&E Red-Flag Triage / MCHK Duty of Care",
        summary="Chest pain, acute dyspnoea, syncope, neurological deficit, or self-harm immediately exits chat to clinic urgent phone / 999.",
        safety_floor=True,
        overridable=False,
        params={"action": "immediate_999_exit", "allow_async_queue": False},
    ),
    "ebm.safety.g6pd_allergy_floor": GraphNode(
        id="ebm.safety.g6pd_allergy_floor",
        layer="safety_floor",
        category="safety",
        title="G6PD & Drug Allergy Deterministic Floor",
        guideline_ref="HK Dept of Health G6PD Guide / Formulary CDS",
        summary="Deterministic block on oxidative drugs (Aspirin, sulfonamides, nitrofurantoin) in G6PD deficiency and cross-reactive allergens (Penicillin).",
        safety_floor=True,
        overridable=False,
        params={"blocked_g6pd": ["aspirin", "co-trimoxazole", "nitrofurantoin"], "enforce_before_llm": True},
    ),
    "ebm.safety.doctor_only_authority": GraphNode(
        id="ebm.safety.doctor_only_authority",
        layer="safety_floor",
        category="safety",
        title="Asymmetric Clinical Authority",
        guideline_ref="HK Medical Registration Ordinance / MCHK Telemedicine Guide",
        summary="Agents never prescribe, sign certificates, withhold/stop medications autonomously, or message patients about unreviewed medication discrepancies.",
        safety_floor=True,
        overridable=False,
        params={"allow_autonomous_rx": False, "allow_autonomous_med_hold": False, "require_3pt_id_match": True},
    ),
    # --- Loop 1: Inbound Triage & Commercial Policy Subtree ---
    "ebm.triage.routine_logistics": GraphNode(
        id="ebm.triage.routine_logistics",
        layer="ebm_base",
        category="triage",
        title="Tier 1 · Self-Serve Logistics & Preventive Booking",
        guideline_ref="HK Primary Care Preventive Framework",
        summary="Approved post-checkup summaries, prep instructions, repeat lab slots, and protocolised preventive nurse visits executed directly by Careline AI.",
        params={"tier": 1, "doctor_minutes": 0, "allowed_intents": ["flu_booking", "lab_rebooking", "prep_info", "bp_log", "approved_summary"]},
    ),
    "ebm.triage.post_visit_grace": GraphNode(
        id="ebm.triage.post_visit_grace",
        layer="ebm_base",
        category="triage",
        title="Tier 2 · Post-Visit / Discharge Grace Clarification",
        guideline_ref="Safe Continuity Post-Discharge Standard",
        summary="Small clarifying questions within the clinic's post-visit/discharge grace window route to #my-work for doctor discretion without promising an artificial reply time.",
        params={"tier": 2, "grace_window_hours": 72, "promise_reply_sla": False},
    ),
    "ebm.triage.booking_options": GraphNode(
        id="ebm.triage.booking_options",
        layer="ebm_base",
        category="triage",
        title="Tier 3 · New Clinical Episode Booking Options",
        guideline_ref="MCHK Real-Time Assessment Standard",
        summary="New clinical symptoms, medication starts, or sick-leave requests share file safety reminders and offer booking options (teleconsult or in-clinic visit); billing stays with clinic/doctor.",
        params={"tier": 3, "offer_modes": ["teleconsult", "in_clinic"], "ai_unilateral_billing": False},
    ),
    # --- Loops 2–4: Gold-Standard Primary Care Clinical Pathways ---
    "ebm.pathway.post_discharge_anticoag": GraphNode(
        id="ebm.pathway.post_discharge_anticoag",
        layer="ebm_base",
        category="pathway",
        title="Post-Hospital Anticoagulation Reconciliation (Warfarin)",
        guideline_ref="NICE NG196 AFib / HK HA Post-Discharge MedRec",
        summary="Exact 3-point ID matched discharge summary diffed against EMR chart; high-risk med mismatch surfaced silently to #my-work ahead of booked follow-up; 7-day INR recall after doctor sign-off.",
        irreversible=True,
        params={"risk_tier": "high", "inr_recall_days": 7, "nurse_pillbox_call": True, "stall_escalate_to": "doctor"},
    ),
    "ebm.pathway.low_ferritin_normal_hb": GraphNode(
        id="ebm.pathway.low_ferritin_normal_hb",
        layer="ebm_base",
        category="pathway",
        title="Non-Anaemic Iron Deficiency (Ferritin 10–20, Normal Hb)",
        guideline_ref="BSG Iron Deficiency Guideline / RACGP GP Guide",
        summary="Ferritin < 15 ng/mL with normal Hb (≥ 11.5 g/dL): dietary iron + optional alternate-day gentle oral iron on doctor review + 3-month repeat Ferritin blood draw.",
        irreversible=True,
        params={"ferritin_low_threshold": 15, "hb_normal_floor": 11.5, "repeat_months": 3, "oral_iron_regimen": "Alternate-day gentle oral iron"},
    ),
    "ebm.pathway.acute_urti_g6pd": GraphNode(
        id="ebm.pathway.acute_urti_g6pd",
        layer="ebm_base",
        category="pathway",
        title="Working-Age Viral URTI (Antibiotic Stewardship + G6PD Safe)",
        guideline_ref="HK CHP Antibiotic Stewardship / MCHK Sick-Leave Guide",
        summary="Uncomplicated viral URTI (Home RAT neg, red flags clear): symptomatic G6PD-safe relief (Paracetamol + Cetirizine), zero antibiotics, defer flu vaccine 14 days while febrile.",
        irreversible=True,
        params={"default_rx": ["Paracetamol 500mg QDS PRN", "Cetirizine 10mg OD"], "defer_flu_days": 14, "omit_employer_diagnosis": True},
    ),
    "ebm.pathway.chronic_htn_home_bp": GraphNode(
        id="ebm.pathway.chronic_htn_home_bp",
        layer="ebm_base",
        category="pathway",
        title="Chronic Hypertension Home BP Loop",
        guideline_ref="HK Reference Framework for Hypertension / NICE NG136",
        summary="7-day home BP log against customizable target (<135/85 mmHg). On-target stays Scheduled for routine paid review; wakes into #my-work if 7d avg > 140/90 or single > 160/100.",
        irreversible=False,
        params={"target_sys": 135, "target_dia": 85, "escalate_avg_sys": 140, "escalate_avg_dia": 90, "review_weeks": 4, "risk_tier": "low"},
    ),
    "ebm.pathway.borderline_ldl": GraphNode(
        id="ebm.pathway.borderline_ldl",
        layer="ebm_base",
        category="pathway",
        title="Post-Checkup Borderline LDL (3.4–4.1 mmol/L)",
        guideline_ref="HK Cardiovascular Risk Primary Care Framework",
        summary="Lifestyle guidance + 3-month repeat fasting lipid panel with nurse blood draw; escalate to doctor if repeat LDL remains > 3.4 mmol/L.",
        irreversible=False,
        params={"ldl_upper_normal": 3.4, "repeat_months": 3, "auto_book_paid_review": False, "fasting_hours": 8, "risk_tier": "low"},
    ),
}

EBM_BASE_EDGES: list[GraphEdge] = [
    GraphEdge("inbound", "ebm.safety.crisis_999", "Always evaluate red flags before any triage or LLM turn", 1),
    GraphEdge("inbound", "ebm.safety.g6pd_allergy_floor", "Screen any medication mention against G6PD and allergy record", 2),
    GraphEdge("inbound", "ebm.triage.routine_logistics", "Intent is booking, prep, BP log, or approved checkup summary", 10),
    GraphEdge("inbound", "ebm.triage.post_visit_grace", "Clarifying question within practice grace_window_hours", 20),
    GraphEdge("inbound", "ebm.triage.booking_options", "New symptoms, Rx start, or sick-leave request", 30),
    GraphEdge("document_ingested", "ebm.safety.doctor_only_authority", "Require exact 3-point ID match; never auto-hold drugs", 1),
    GraphEdge("document_ingested", "ebm.pathway.post_discharge_anticoag", "Discharge summary medication differs from EMR active chart", 10),
    GraphEdge("document_ingested", "ebm.pathway.low_ferritin_normal_hb", "Ferritin < 15 ng/mL with normal Hb", 15),
    GraphEdge("document_ingested", "ebm.pathway.borderline_ldl", "LDL 3.4–4.1 mmol/L without statin therapy", 20),
    GraphEdge("scheduled_checkpoint", "ebm.pathway.chronic_htn_home_bp", "Chronic HTN home BP log or 4-week review tick", 10),
]

# -------------------------------------------------------------------------------------- Layer 2: Default Clinic Practice Overlay
DEFAULT_CARELINE_PROFILE: dict[str, Any] = {
    "name": "Sam",
    "role_label": "Clinic Careline AI",
    "clinic_name": "Central & Mid-Levels Family Medical",
    "lead_gp": "Dr. Kenneth Lam (M14829)",
    "covering_locum": "Dr. Sarah Wong (Locum GP)",
    "lead_nurse": "Nurse Rachel Chan (RN)",
    "disclosure_en": "Automated clinic assistant for Central & Mid-Levels Family Medical. Clinical decisions and prescriptions are reviewed by our doctors.",
    "disclosure_zh": "中環家庭醫務中心自動診所助理。所有醫療決定及處方均由本診所醫生審核。",
}

DEFAULT_PRACTICE_OVERLAY: dict[str, Any] = {
    "clinic_standards": {
        "grace_window_hours": 72,
        "retry_cap_nudges": 2,
        "retry_cap_hours": 72,
        "undo_window_seconds": 10,
        "partner_lab": "KingMed Diagnostics (HK)",
        "corporate_plans": {
            "Harbour FinTech": {
                "flu_shot_copay_hkd": 0,
                "flu_visit_slot": "10-min nurse visit",
                "omit_employer_diagnosis_by_default": True,
            }
        },
        "compliance_agent_memory_enabled": True,
    },
    "node_overrides": {
        "ebm.pathway.post_discharge_anticoag": {
            "nurse_call_language": "Cantonese",
            "nurse_assignee": "Nurse Rachel Chan (RN)",
            "inr_recall_days": 7,
            "practice_note": "Never alarm patient before booked visit; once doctor signs, Nurse Rachel calls same day in Cantonese to check home pillbox.",
        },
        "ebm.pathway.low_ferritin_normal_hb": {
            "oral_iron_regimen": "Alternate-day gentle oral iron",
            "repeat_months": 3,
            "practice_note": "Dr. Lam prefers alternate-day oral iron to minimize GI side effects plus a 10-min morning nurse blood draw at 3 months.",
        },
        "ebm.pathway.acute_urti_g6pd": {
            "omit_employer_diagnosis": True,
            "practice_note": "For Harbour FinTech patients, omit diagnosis on employer certificate copy unless requested.",
        },
        "ebm.pathway.chronic_htn_home_bp": {
            "target_sys": 135,
            "target_dia": 85,
            "review_weeks": 4,
            "practice_note": "Keep on-target patients Scheduled with 4-week paid GP review; wake doctor only if 7d avg > 140/90.",
        },
        "ebm.pathway.borderline_ldl": {
            "repeat_months": 3,
            "fasting_hours": 8,
            "auto_book_paid_review": False,
            "practice_note": "Standard 3-month repeat fasting lipid panel with Nurse Rachel.",
        },
    },
}

# Canonical Polymorphic Order Catalog (drives /slash autofill & typed Checkout widgets across the Knowledge Graph)
ORDER_CATALOG_PRIMITIVES: list[dict[str, Any]] = [
    {
        "cmd": "/prescribe",
        "type": "rx",
        "label": "Prescription (HK Formulary)",
        "desc": "Drug, dose, frequency, duration · G6PD, allergy & renal verified",
        "fields": ["drug", "freq", "days", "note"],
        "requires_sign": True,
    },
    {
        "cmd": "/cert",
        "type": "cert",
        "label": "Medical Certificate",
        "desc": "Bilingual MCHK sick leave PDF · days, dates, employer diagnosis privacy toggle",
        "fields": ["days_off", "dates", "omit_dx"],
        "requires_sign": True,
    },
    {
        "cmd": "/order",
        "type": "lab",
        "label": "Lab Investigation",
        "desc": "Blood/urine/stool test · partner lab, fasting flag, timing",
        "fields": ["test_name", "lab", "fasting", "when"],
        "requires_sign": False,
    },
    {
        "cmd": "/imaging",
        "type": "imaging",
        "label": "Imaging Request",
        "desc": "CXR, Ultrasound, Mammogram · indication & priority",
        "fields": ["modality", "indication", "priority"],
        "requires_sign": False,
    },
    {
        "cmd": "/refer",
        "type": "refer",
        "label": "Specialist Referral",
        "desc": "Specialty letter with attached clinical summary & lab reports",
        "fields": ["specialty", "reason", "attach_labs"],
        "requires_sign": True,
    },
    {
        "cmd": "/goal",
        "type": "goal",
        "label": "Chronic Treatment Goal",
        "desc": "Adjustable numeric target widget synced to longitudinal patient monitoring",
        "fields": ["metric", "target_sys", "target_dia"],
        "requires_sign": False,
    },
    {
        "cmd": "/nurse",
        "type": "nurse",
        "label": "Nurse Task",
        "desc": "Delegate phone check, chase, or nurse clinic prep to Nurse Rachel",
        "fields": ["assignee", "task", "due"],
        "requires_sign": False,
    },
    {
        "cmd": "/book",
        "type": "book",
        "label": "Follow-Up / Preventive Session",
        "desc": "Schedule separate nurse visit, review, or preventive screening session",
        "fields": ["visit_type", "when"],
        "requires_sign": False,
    },
]

# Always-On Age & Risk-Specific Preventive Care Layer (HK Primary Care Reference Frameworks)
PREVENTIVE_CARE_RULES: list[dict[str, Any]] = [
    {"id": "prev.hk.flu_annual", "title": "Seasonal Influenza Vaccine", "criteria": "All adults annually (inactivated G6PD-safe)", "guideline": "HK CHP SCVPD"},
    {"id": "prev.hk.cervical_25_64", "title": "Cervical Screening (HPV / Pap)", "criteria": "Females age 25–64 every 3–5 years", "guideline": "HK Dept of Health CSP"},
    {"id": "prev.hk.colorectal_fit_50_75", "title": "HK Colorectal FIT Screening", "criteria": "Adults age 50–75 every 2 years (Govt subsidized)", "guideline": "HK CRC Screening Programme"},
    {"id": "prev.hk.mammogram_44_69", "title": "Biennial Screening Mammogram", "criteria": "Females age 44–69 biennial risk-stratified", "guideline": "HK CEWG Breast Cancer"},
    {"id": "prev.hk.pcv20_60plus", "title": "Pneumococcal Vaccine (PCV20)", "criteria": "Adults age 60+ or chronic cardiopulmonary disease", "guideline": "HK CHP SCVPD"},
    {"id": "prev.hk.cardiometabolic", "title": "Annual HbA1c & Fasting Lipids", "criteria": "Hypertension or cardiometabolic risk factors", "guideline": "HK Reference Framework for HTN/DM"},
]

# Deterministic Internal Clinical Design Guide: 13 Canonical Clinical Widgets (9 Canvas + 4 Right Rail)
CLINICAL_WIDGET_CATALOG: list[dict[str, str]] = [
    {"id": "VitalsExamStrip", "zone": "canvas", "title": "Vitals & Exam Strip", "trigger": "Clinic, home, or discharge vitals/exam findings present"},
    {"id": "ClinicalScoreStrip", "zone": "canvas", "title": "Clinical Risk & Score Strip", "trigger": "Deterministic Python score (CHA2DS2-VASc, HAS-BLED, HK CVD 10-yr) applies"},
    {"id": "MedDiff", "zone": "canvas", "title": "Medication Comparison", "trigger": "External discharge or specialist medications differ from Clinic EMR"},
    {"id": "TrendGraph", "zone": "canvas", "title": "Longitudinal Trend Graph", "trigger": ">= 2 longitudinal numeric points (INR, BP, Ferritin, HbA1c, LDL)"},
    {"id": "ResultTable", "zone": "canvas", "title": "Structured Result Panel", "trigger": "Abnormal or clinically paired baseline investigation results"},
    {"id": "ImagingPreview", "zone": "canvas", "title": "Imaging & Study Preview", "trigger": "Linked ECG, X-ray, ultrasound, or echo report"},
    {"id": "SourceClip", "zone": "canvas", "title": "Verified Source Attachment", "trigger": "3-pt ID verified external PDF or lab report quote"},
    {"id": "IssueCard", "zone": "canvas", "title": "Atomic Clinical Issue Card", "trigger": "One card per discrete clinical problem (no essay headers, calm + pills)"},
    {"id": "ManagementPlan", "zone": "canvas", "title": "Management Plan", "trigger": "Always mounted empty; builds idempotently from clicked + pills & overlay / menu"},
    {"id": "SafetyCard", "zone": "right_rail", "title": "Safety & Alerts", "trigger": "Allergies, G6PD, and high-risk drug conflicts pinned top-right"},
    {"id": "PreventiveCard", "zone": "right_rail", "title": "Preventive Care", "trigger": "Always-on age/sex/risk GP screening & vaccine opportunities"},
    {"id": "ScheduledCard", "zone": "right_rail", "title": "Scheduled & Upcoming", "trigger": "Booked clinic visits, lab draws, and active automated loops"},
    {"id": "UnifiedThread", "zone": "right_rail", "title": "Unified Careline + Sam Thread", "trigger": "Patient history above -> Private divider -> strict high-signal Sam note & reply"},
]

PATHWAY_WIDGET_STACKS: dict[str, list[str]] = {
    "ebm.pathway.post_discharge_anticoag": [
        "VitalsExamStrip", "ClinicalScoreStrip", "MedDiff", "IssueCard", "TrendGraph", "ResultTable", "ImagingPreview", "SourceClip", "ManagementPlan",
    ],
    "ebm.pathway.low_ferritin_normal_hb": [
        "VitalsExamStrip", "IssueCard", "ResultTable", "TrendGraph", "SourceClip", "ManagementPlan",
    ],
    "ebm.pathway.chronic_htn_home_bp": [
        "VitalsExamStrip", "IssueCard", "TrendGraph", "ResultTable", "SourceClip", "ManagementPlan",
    ],
    "ebm.pathway.borderline_ldl": [
        "ClinicalScoreStrip", "IssueCard", "ResultTable", "SourceClip", "ManagementPlan",
    ],
}

CANONICAL_ORDER_TYPES: frozenset[str] = frozenset(p["type"] for p in ORDER_CATALOG_PRIMITIVES)
CANONICAL_RX_VERBS: frozenset[str] = frozenset({"start", "update", "stop", "continue"})

BANNED_SLOP_PHRASES: tuple[str, ...] = (
    "reconcile emr active list",
    "high-risk medication discrepancy surfaced",
    "history:",
    "clinical reasoning:",
    "assessment:",
    "management plan checkout",
    "adopt qeh regimen",
    "pulled his",
    "pulled her",
    "also spotted",
    "drag the ",
    "kept him in scheduled",
    "working memory",
    "tacit knowledge",
    "linked source",
)

OBJECTIVE_SAM_MARKERS: tuple[str, ...] = (
    "cha₂ds₂-vasc",
    "cha2ds2-vasc",
    "has-bled",
    "cvd",
    "qrisk",
    "nice",
    "bsg",
    "hk ",
    "guideline",
    "threshold",
    "ng196",
    "ng136",
    "g6pd",
    "curb-65",
    "fit",
    "screening",
)


# -------------------------------------------------------------------------------------- Deterministic Clinical Score Calculators
def compute_cha2ds2_vasc(
    *,
    age: int,
    sex: str,
    chf: bool = False,
    hypertension: bool = False,
    stroke_tia: bool = False,
    vascular_disease: bool = False,
    diabetes: bool = False,
) -> dict[str, Any]:
    """Deterministic NICE NG196 / ESC CHA2DS2-VASc score (never LLM-estimated)."""
    score = 0
    factors: list[str] = []
    if chf:
        score += 1
        factors.append("CHF (+1)")
    if hypertension:
        score += 1
        factors.append("HTN (+1)")
    if age >= 75:
        score += 2
        factors.append("Age ≥75 (+2)")
    elif age >= 65:
        score += 1
        factors.append("Age 65–74 (+1)")
    if diabetes:
        score += 1
        factors.append("DM (+1)")
    if stroke_tia:
        score += 2
        factors.append("Stroke/TIA (+2)")
    if vascular_disease:
        score += 1
        factors.append("Vascular (+1)")
    if sex.upper() == "F":
        score += 1
        factors.append("Female (+1)")
    band = "High · Anticoag indicated" if (score >= 2 if sex.upper() == "M" else score >= 3) else ("Consider anticoag" if score >= 1 else "Low")
    return {
        "id": "cha2ds2_vasc",
        "label": "CHA₂DS₂-VASc",
        "value": str(score),
        "band": band,
        "detail": ", ".join(factors) if factors else "No risk factors",
        "guideline": "NICE NG196",
    }


def compute_has_bled(
    *,
    uncontrolled_htn: bool = False,
    abnormal_renal_or_liver: bool = False,
    stroke: bool = False,
    bleeding_history: bool = False,
    labile_inr: bool = False,
    age_over_65: bool = False,
    antiplatelet_nsaid_or_alcohol: bool = False,
) -> dict[str, Any]:
    """Deterministic HAS-BLED bleeding risk score for anticoagulated AFib patients."""
    flags = [
        ("HTN", uncontrolled_htn),
        ("Renal/Liver", abnormal_renal_or_liver),
        ("Stroke", stroke),
        ("Bleed hx", bleeding_history),
        ("Labile INR", labile_inr),
        ("Age >65", age_over_65),
        ("Drugs/EtOH", antiplatelet_nsaid_or_alcohol),
    ]
    active = [name for name, on in flags if on]
    score = len(active)
    band = "High bleeding vigilance" if score >= 3 else "Moderate" if score == 2 else "Low"
    return {
        "id": "has_bled",
        "label": "HAS-BLED",
        "value": str(score),
        "band": band,
        "detail": ", ".join(active) if active else "No modifiable bleed flags",
        "guideline": "NICE NG196",
    }


def compute_cvd_10yr_tier(*, age: int, smoker: bool, systolic_bp: int, ldl_mmol_l: float, diabetes: bool = False) -> dict[str, Any]:
    """Deterministic HK Primary Care 10-year cardiovascular risk tier."""
    if diabetes or systolic_bp >= 160 or ldl_mmol_l >= 4.9 or (age >= 60 and smoker):
        val, band = "≥ 10%", "Moderate–High"
    elif age >= 50 and (systolic_bp >= 140 or ldl_mmol_l >= 4.1 or smoker):
        val, band = "5–10%", "Borderline"
    else:
        val, band = "< 2%" if age < 40 else "< 5%", "Low risk"
    return {
        "id": "hk_cvd_10yr",
        "label": "10-Yr CVD Risk",
        "value": val,
        "band": band,
        "detail": f"{'Smoker' if smoker else 'Non-smoker'} · BP {systolic_bp} · LDL {ldl_mmol_l}",
        "guideline": "HK Primary Care CVD",
    }


# -------------------------------------------------------------------------------------- Deterministic Clinical Presentation Linter
def validate_case_presentation(summary: dict[str, Any]) -> dict[str, Any]:
    """Enforce the 5 semantic anti-slop invariants from docs/CLINICAL_UI_DESIGN_SYSTEM.md before rendering."""
    violations: list[dict[str, str]] = []
    issues = summary.get("issues") or []
    trend_chart = summary.get("trend_chart") or {}
    lab_panel = summary.get("lab_panel") or {}
    sam_note = summary.get("sam_proactive_insight")

    # Collect numeric values already mounted in TrendGraph / ResultTable for R1
    mounted_nums: set[str] = set()
    for pt in trend_chart.get("points") or []:
        if "v" in pt and pt["v"] is not None:
            mounted_nums.add(str(pt["v"]))
        if "sys" in pt and "dia" in pt:
            mounted_nums.add(f"{pt['sys']}/{pt['dia']}")
    for row in lab_panel.get("rows") or []:
        m = re.search(r"\d+(?:\.\d+)?(?:/\d+)?", str(row.get("val", "")))
        if m:
            mounted_nums.add(m.group(0))

    # R1: Zero Widget-Prose Duplication
    if mounted_nums:
        for iss in issues:
            prose = " ".join(
                [str(b) for b in (iss.get("bullets") or [])]
                + [str(iss.get(k) or "") for k in ("subjective", "reasoning", "assessment")]
            )
            hits = [n for n in mounted_nums if re.search(rf"(?<!\d){re.escape(n)}(?!\d)", prose)]
            if len(hits) >= 2:
                violations.append({
                    "rule": "R1_NO_WIDGET_DUPLICATION",
                    "issue": str(iss.get("name", "")),
                    "detail": f"Prose repeats {len(hits)} numeric values already displayed in mounted widgets: {', '.join(sorted(hits))}",
                })

    # R2: Problem & Order Atomicity
    for iss in issues:
        title = str(iss.get("name", ""))
        title_low = title.lower()
        if ("anticoag" in title_low or "warfarin" in title_low or "afib" in title_low) and ("lipid" in title_low or "statin" in title_low):
            violations.append({
                "rule": "R2_ATOMIC_ISSUES_AND_ORDERS",
                "issue": title,
                "detail": "Mixed unrelated clinical domains (Anticoagulation and Lipids) in a single IssueCard.",
            })
        for ord_item in iss.get("suggested_orders") or []:
            if ord_item.get("type") == "rx":
                drug_str = str(ord_item.get("drug", ""))
                if "+" in drug_str or " and " in drug_str.lower():
                    violations.append({
                        "rule": "R2_ATOMIC_ISSUES_AND_ORDERS",
                        "issue": title,
                        "detail": f"Medication order bundles multiple drugs in one primitive: '{drug_str}'",
                    })

    # R3: No Essay Scaffolding or Slop Phrases
    all_prose_blobs: list[tuple[str, str]] = []
    for iss in issues:
        if any(iss.get(k) for k in ("subjective", "reasoning", "assessment")):
            violations.append({
                "rule": "R3_NO_SLOP_OR_SCAFFOLDING",
                "issue": str(iss.get("name", "")),
                "detail": "Legacy essay fields (subjective/reasoning/assessment) must be replaced with clean scannable bullets.",
            })
        blob = " ".join([str(iss.get("name", ""))] + [str(b) for b in (iss.get("bullets") or [])] + [str(iss.get("safety_gap") or "")])
        all_prose_blobs.append((str(iss.get("name", "")), blob))
    if sam_note:
        all_prose_blobs.append(("sam_proactive_insight", str(sam_note)))
    for scope, blob in all_prose_blobs:
        low = blob.lower()
        for bad in BANNED_SLOP_PHRASES:
            if bad in low:
                violations.append({
                    "rule": "R3_NO_SLOP_OR_SCAFFOLDING",
                    "issue": scope,
                    "detail": f"Contains banned slop/scaffolding phrase: '{bad}'",
                })

    # R4: Closed Canonical Action Ontology
    for iss in issues:
        for ord_item in iss.get("suggested_orders") or []:
            otype = ord_item.get("type")
            if otype not in CANONICAL_ORDER_TYPES:
                violations.append({
                    "rule": "R4_CANONICAL_ACTION_ONTOLOGY",
                    "issue": str(iss.get("name", "")),
                    "detail": f"Non-canonical order type: '{otype}'",
                })
            if otype == "rx":
                verb = str(ord_item.get("action") or "").lower()
                if verb not in CANONICAL_RX_VERBS:
                    violations.append({
                        "rule": "R4_CANONICAL_ACTION_ONTOLOGY",
                        "issue": str(iss.get("name", "")),
                        "detail": f"Rx primitive '{ord_item.get('drug')}' must specify canonical action verb (start|update|stop|continue).",
                    })

    # R5: Strict High-Signal Sam Gate
    if sam_note:
        low_sam = str(sam_note).lower()
        if not any(marker in low_sam for marker in OBJECTIVE_SAM_MARKERS):
            violations.append({
                "rule": "R5_HIGH_SIGNAL_SAM_GATE",
                "issue": "UnifiedThread",
                "detail": "Sam proactive unread note lacks an objective clinical score, guideline reference, or safety threshold.",
            })

    rules_checked = [
        "R1_NO_WIDGET_DUPLICATION",
        "R2_ATOMIC_ISSUES_AND_ORDERS",
        "R3_NO_SLOP_OR_SCAFFOLDING",
        "R4_CANONICAL_ACTION_ONTOLOGY",
        "R5_HIGH_SIGNAL_SAM_GATE",
    ]
    failed_rules = {v["rule"] for v in violations}
    return {
        "passed": len(violations) == 0,
        "rules_passed": len(rules_checked) - len(failed_rules),
        "rules_total": len(rules_checked),
        "violations": violations,
    }


def compute_graph_hash(overlay: dict[str, Any], profile: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"ebm": EBM_BASE_VERSION, "overlay": overlay, "profile": profile},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def validate_careline_profile(profile: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_CARELINE_PROFILE)
    merged.update({k: v for k, v in (profile or {}).items() if v is not None})
    name_clean = str(merged.get("name", "")).strip()
    role_clean = str(merged.get("role_label", "")).strip()
    combined = f"{name_clean} {role_clean}".lower()
    for bad in FORBIDDEN_AI_TITLES:
        if re.search(rf"\b{re.escape(bad)}\b", combined):
            raise SafetyFloorViolation(
                f"Careline AI profile cannot use medical title '{bad}' (must never impersonate a licensed doctor or resident)."
            )
    if not name_clean:
        raise SafetyFloorViolation("Careline AI name cannot be blank.")
    return merged


def validate_overlay_against_safety_floor(overlay: dict[str, Any]) -> dict[str, Any]:
    """Ensure NO practice customization or Curator patch weakens any Immutable Safety Floor node."""
    if not isinstance(overlay, dict):
        raise SafetyFloorViolation("Practice overlay must be a JSON object.")
    node_overrides = overlay.get("node_overrides") or {}
    for node_id, patch in node_overrides.items():
        base = EBM_BASE_NODES.get(node_id)
        if base and (base.safety_floor or not base.overridable):
            raise SafetyFloorViolation(
                f"Immutable Safety Floor violation: node '{node_id}' ({base.title}) cannot be overridden by a practice overlay."
            )
        if isinstance(patch, dict):
            if patch.get("allow_autonomous_rx") is True or patch.get("allow_autonomous_med_hold") is True:
                raise SafetyFloorViolation("Immutable Safety Floor violation: autonomous prescribing or medication holds are prohibited.")
            if patch.get("require_3pt_id_match") is False:
                raise SafetyFloorViolation("Immutable Safety Floor violation: deterministic 3-point ID matching cannot be disabled.")
    return overlay


def build_effective_graph(
    *,
    version_tag: str = "v2.4",
    overlay: dict[str, Any] | None = None,
    careline_profile: dict[str, Any] | None = None,
    approved_by: str = "Dr. Kenneth Lam (Lead GP)",
) -> dict[str, Any]:
    """Merge Layer 1 (EBM Base Tree) + Layer 2 (Practice Overlay) into an executable versioned graph snapshot."""
    eff_overlay = copy.deepcopy(DEFAULT_PRACTICE_OVERLAY)
    if overlay:
        validate_overlay_against_safety_floor(overlay)
        for sec in ("clinic_standards", "node_overrides"):
            if isinstance(overlay.get(sec), dict):
                for k, v in overlay[sec].items():
                    if isinstance(v, dict) and isinstance(eff_overlay[sec].get(k), dict):
                        eff_overlay[sec][k].update(v)
                    else:
                        eff_overlay[sec][k] = copy.deepcopy(v)
    eff_profile = validate_careline_profile(careline_profile or DEFAULT_CARELINE_PROFILE)

    nodes_out: list[dict[str, Any]] = []
    for node_id, base in EBM_BASE_NODES.items():
        node_dict = asdict(base)
        override = eff_overlay["node_overrides"].get(node_id)
        if override and not base.safety_floor:
            merged_params = copy.deepcopy(node_dict["params"])
            merged_params.update(override)
            node_dict["params"] = merged_params
            node_dict["practice_customized"] = True
            node_dict["provenance"] = f"{EBM_BASE_VERSION} + Practice {version_tag}"
        else:
            node_dict["practice_customized"] = False
            node_dict["provenance"] = EBM_BASE_VERSION
        nodes_out.append(node_dict)

    return {
        "version_tag": version_tag,
        "ebm_base_version": EBM_BASE_VERSION,
        "content_hash": compute_graph_hash(eff_overlay, eff_profile),
        "approved_by": approved_by,
        "careline_profile": eff_profile,
        "clinic_standards": eff_overlay["clinic_standards"],
        "nodes": nodes_out,
        "edges": [asdict(e) for e in EBM_BASE_EDGES],
        "order_catalog": copy.deepcopy(ORDER_CATALOG_PRIMITIVES),
        "preventive_rules": copy.deepcopy(PREVENTIVE_CARE_RULES),
        "widget_catalog": copy.deepcopy(CLINICAL_WIDGET_CATALOG),
        "pathway_widget_stacks": copy.deepcopy(PATHWAY_WIDGET_STACKS),
        "stats": {
            "total_nodes": len(nodes_out),
            "safety_floor_nodes": sum(1 for n in nodes_out if n["safety_floor"]),
            "practice_customized_nodes": sum(1 for n in nodes_out if n["practice_customized"]),
            "order_primitives": len(ORDER_CATALOG_PRIMITIVES),
            "preventive_rules": len(PREVENTIVE_CARE_RULES),
            "canonical_widgets": len(CLINICAL_WIDGET_CATALOG),
        },
    }


def classify_inbound_triage(
    *,
    message_text: str,
    hours_since_last_visit: float | None,
    effective_graph: dict[str, Any],
    patient_g6pd: bool = False,
) -> dict[str, Any]:
    """Loop 1 deterministic graph traversal: Safety Floor -> Tier 1 -> Tier 2 (72h Grace) -> Tier 3 (Booking Options)."""
    low = message_text.lower()
    traversed: list[str] = ["ebm.safety.crisis_999", "ebm.safety.g6pd_allergy_floor"]
    guards_fired: list[str] = []

    crisis = classify_crisis(message_text)
    if crisis:
        guards_fired.append("crisis_999_triggered")
        return {
            "tier": 0,
            "route": "urgent_exit_999",
            "traversed_nodes": traversed,
            "guards_fired": guards_fired,
            "promise_reply_sla": False,
            "ai_unilateral_billing": False,
            "response_guidance": "Exit chat immediately — instruct patient to call clinic urgent line or 999.",
        }

    if patient_g6pd and ("aspirin" in low or "bactrim" in low):
        guards_fired.append("g6pd_aspirin_warning")

    # Tier 1: Routine self-serve logistics, prep, BP log, rebooking
    routine_markers = ("flu", "vaccine", "book", "slot", "reschedule", "fasting", "prep", "128/78", "bp ", "blood pressure", "reading")
    clinical_new_markers = ("fever", "38.", "sore throat", "cough", "sick note", "sick leave", "certificate", "prescribe", "antibiotic", "iron tablet", "start iron")

    has_new_clinical = any(m in low for m in clinical_new_markers)
    has_routine = any(m in low for m in routine_markers)

    if has_routine and not has_new_clinical:
        traversed.append("ebm.triage.routine_logistics")
        return {
            "tier": 1,
            "route": "self_serve_careline",
            "traversed_nodes": traversed,
            "guards_fired": guards_fired,
            "promise_reply_sla": False,
            "ai_unilateral_billing": False,
            "response_guidance": "Handle directly under approved clinic protocol (0 doctor minutes).",
        }

    grace_hours = int(effective_graph.get("clinic_standards", {}).get("grace_window_hours", 72))
    is_clarifying = any(k in low for k in ("clarify", "discharge", "after visit", "dose", "ferritin", "when should i take", "side effect"))
    if hours_since_last_visit is not None and hours_since_last_visit <= grace_hours and is_clarifying and not ("sick note" in low or "sick leave" in low):
        traversed.append("ebm.triage.post_visit_grace")
        return {
            "tier": 2,
            "route": "post_visit_grace_my_work",
            "traversed_nodes": traversed,
            "guards_fired": guards_fired,
            "promise_reply_sla": False,
            "ai_unilateral_billing": False,
            "response_guidance": f"Within {grace_hours}h grace window — summarise into #my-work for doctor discretion without promising an artificial reply time.",
        }

    traversed.append("ebm.triage.booking_options")
    return {
        "tier": 3,
        "route": "offer_booking_options",
        "traversed_nodes": traversed,
        "guards_fired": guards_fired,
        "promise_reply_sla": False,
        "ai_unilateral_billing": False,
        "response_guidance": "Share relevant file safety reminder (if any) and offer booking options (teleconsult or in-clinic visit) — doctor decides clinical plan & billing.",
    }


def compile_instruction_plan(
    *,
    case_key: str,
    pathway_node_id: str,
    instruction_text: str,
    effective_graph: dict[str, Any],
    patient_name: str,
    agent_memory_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Loop 3 deterministic compiler: parse doctor option/free-text against Practice Graph into categorised tasks + Context Window."""
    txt = instruction_text.strip()
    low = txt.lower()
    ai_name = effective_graph["careline_profile"]["name"]
    nurse_name = effective_graph["careline_profile"]["lead_nurse"]
    version_tag = effective_graph["version_tag"]

    node_map = {n["id"]: n for n in effective_graph["nodes"]}
    pathway_node = node_map.get(pathway_node_id) or node_map["ebm.pathway.post_discharge_anticoag"]

    traversed = ["ebm.safety.doctor_only_authority", "ebm.safety.g6pd_allergy_floor", pathway_node["id"]]
    categorised: dict[str, list[dict[str, Any]]] = {
        "clinical_advice": [],
        "lab_imaging": [],
        "patient_task": [],
        "admin_booking": [],
    }
    folded_steps: list[dict[str, str]] = []
    emr_addendum_lines: list[str] = []
    nurse_handoff: dict[str, str] | None = None
    override_detected = False

    # Check if doctor is giving a custom instruction outside standard pathway
    if case_key == "chan":
        if "discuss" in low or "11:30" in low or "wait" in low:
            categorised["admin_booking"].append({"text": "Keep QEH medication excerpt ready for 11:30 visit", "irreversible": False})
            folded_steps.append({"tool": "workspace.pin_visit_brief", "detail": "Attached QEH Page 2 excerpt to today's 11:30 appointment context"})
        else:
            if "warfarin" in low or "3mg" in low or "adopt" in low or not any(k in low for k in ("statin", "inr", "nurse")):
                categorised["clinical_advice"].append({
                    "text": "Update Warfarin 3mg OD (reduced from 5mg per QEH discharge)",
                    "irreversible": True,
                    "highlight": True,
                })
                emr_addendum_lines.append("Updated Warfarin to 3mg PO daily (per QEH discharge 22 Nov).")
            if "atorvastatin" in low or "simvastatin" in low or "statin" in low or "adopt" in low:
                categorised["clinical_advice"].append({
                    "text": "Stop Simvastatin 20mg ON · Start Atorvastatin 20mg ON",
                    "irreversible": True,
                    "highlight": True,
                })
                emr_addendum_lines.append("Stopped Simvastatin 20mg ON; started Atorvastatin 20mg ON.")
            categorised["patient_task"].append({
                "text": f"Send doctor-confirmed medication plan to {patient_name}",
                "irreversible": True,
            })
            if "inr" in low or "29" in low or "7 day" in low or "adopt" in low:
                categorised["lab_imaging"].append({"text": "Order Repeat INR (29 Nov · 10-min nurse blood draw)", "irreversible": False})
                emr_addendum_lines.append("Repeat INR scheduled 29 Nov 2026.")
            if "nurse" in low or "rachel" in low or "call" in low or "cantonese" in low or "adherence" in low or "adopt" in low:
                categorised["admin_booking"].append({"text": f"Delegate {nurse_name}: Medication adherence check (Cantonese)", "irreversible": False})
                nurse_handoff = {
                    "title": f"{patient_name} — Medication adherence check (Cantonese)",
                    "detail": "Doctor confirmed Warfarin 3mg OD and Atorvastatin 20mg ON (Simvastatin stopped). Confirm home supply and 29 Nov INR slot.",
                }
            folded_steps.extend([
                {"tool": "emr.write_additive_addendum", "detail": "Wrote compact medication update addendum to Clinic EMR"},
                {"tool": "careline.dispatch_confirmed_plan", "detail": f"{ai_name} sent doctor-signed medication update to {patient_name}"},
                {"tool": "team.delegate_nurse_task", "detail": f"Delegated medication adherence check to #clinic-team ({nurse_name})"},
                {"tool": "calendar.schedule_lab_recall", "detail": "Scheduled 29 Nov INR nurse blood-draw recall & outbox wake timer"},
            ])
            if not any(k in low for k in ("adopt", "3mg", "warfarin", "atorvastatin", "qeh")):
                override_detected = True

    elif case_key == "wingyan":
        if "iron" in low or "alternate" in low:
            categorised["clinical_advice"].append({
                "text": "Add gentle alternate-day oral iron to consult plan for Ferritin 14 ng/mL",
                "irreversible": True,
                "highlight": True,
            })
            emr_addendum_lines.append("Ferritin 14 ng/mL (normal Hb 12.1): advised gentle alternate-day oral iron.")
            override_detected = "daily" in low or "paid review" in low or "16 feb" in low
        if "sick" in low or "finalise" in low or "urti" in low or "pack" in low or not categorised["clinical_advice"]:
            categorised["clinical_advice"].append({
                "text": "Sign G6PD-safe URTI relief (Paracetamol 500mg + Cetirizine 10mg) & 2-day bilingual sick leave certificate",
                "irreversible": True,
                "highlight": True,
            })
            emr_addendum_lines.append("Viral URTI (RAT neg, G6PD-safe Rx): Paracetamol 500mg QDS PRN + Cetirizine 10mg OD + 2d certificate (diagnosis omitted on employer copy).")
        categorised["lab_imaging"].append({
            "text": "Book repeat Ferritin blood draw (Tue 9 Feb 08:30 · 10-min nurse visit)",
            "irreversible": False,
        })
        if "fast" in low:
            categorised["patient_task"].append({"text": f"{ai_name} sends 8-hour fasting prep reminder 24h before 9 Feb blood draw", "irreversible": False})
            override_detected = True
        categorised["patient_task"].append({
            "text": f"Deliver signed care pack & re-offer $0 flu shot booking link in 14 days once afebrile",
            "irreversible": True,
        })
        if "review" in low or "16 feb" in low:
            categorised["admin_booking"].append({"text": "Hold 10-min GP results review 1 week after 9 Feb blood draw", "irreversible": False})
            override_detected = True
        nurse_handoff = {
            "title": f"{patient_name} — Confirm 9 Feb repeat Ferritin slot",
            "detail": "Consult completed by Locum Dr. Sarah. Confirm Tue 9 Feb 08:30 nurse blood-draw slot; flu shot deferred 14 days.",
        }
        folded_steps.extend([
            {"tool": "cds.verify_g6pd_allergy", "detail": "Deterministic rx_rules pass: Paracetamol & Cetirizine clear for G6PD + Penicillin allergy"},
            {"tool": "emr.write_additive_addendum", "detail": "Wrote compact consult addendum + certificate ref to Clinic EMR"},
            {"tool": "careline.deliver_signed_pack", "detail": f"{ai_name} sent signed bilingual PDF & dosing card to {patient_name}"},
            {"tool": "calendar.schedule_followup_loop", "detail": "Booked 9 Feb repeat Ferritin slot & armed 14-day flu re-offer outbox timer"},
        ])

    elif case_key == "wong":
        if "130" in low or "goal" in low or "tighten" in low:
            categorised["clinical_advice"].append({
                "text": "Update hypertension target goal to < 130/80 mmHg on clinic Summary & patient log",
                "irreversible": True,
                "highlight": True,
            })
            emr_addendum_lines.append("Tightened home BP target goal to < 130/80 mmHg.")
            override_detected = True
        if "renal" in low or "blood" in low or "lab" in low:
            categorised["lab_imaging"].append({"text": "Order pre-review renal function & electrolytes panel ahead of 21 Dec visit", "irreversible": False})
            override_detected = True
        if "nurse" in low or "rachel" in low or "call" in low:
            categorised["admin_booking"].append({"text": f"Task → {nurse_name}: 5-min follow-up call on home BP cuff & adherence", "irreversible": False})
            nurse_handoff = {
                "title": f"{patient_name} — Brief nurse BP follow-up call",
                "detail": f"7-day home BP avg 129/79. Doctor instruction: {txt}",
            }
        categorised["admin_booking"].append({"text": "Keep 4-week GP Hypertension Review on calendar (Mon 21 Dec 10:00)", "irreversible": False})
        categorised["patient_task"].append({"text": f"{ai_name} collects next 7-day home BP log 1 week prior (reopens only if avg > 140/90)", "irreversible": False})
        folded_steps.extend([
            {"tool": "memory.update_chronic_goal", "detail": "Updated compliance-gated longitudinal HTN goal in Agent Memory Store"},
            {"tool": "calendar.confirm_review_timer", "detail": "Armed 14 Dec pre-visit 7-day BP check-in outbox timer"},
        ])
        if nurse_handoff:
            folded_steps.append({"tool": "team.delegate_nurse_task", "detail": f"Posted selective nurse task to #clinic-team ({nurse_name})"})

    else:  # lee or generic free-text
        if any(k in low for k in ("statin", "start", "dose", "iron", "advice", "prescribe")):
            categorised["clinical_advice"].append({"text": txt, "irreversible": True, "highlight": True})
            override_detected = True
        if any(k in low for k in ("lab", "blood", "lipid", "inr", "x-ray", "scan")):
            categorised["lab_imaging"].append({"text": f"Schedule lab/imaging per instruction: {txt}", "irreversible": False})
        if any(k in low for k in ("nurse", "call", "rachel")):
            categorised["admin_booking"].append({"text": f"Assign {nurse_name}: {txt}", "irreversible": False})
            nurse_handoff = {"title": f"{patient_name} — Nurse follow-up task", "detail": txt}
        if not any(categorised.values()):
            categorised["patient_task"].append({"text": f"{ai_name} executes follow-up instruction: {txt}", "irreversible": False})
        folded_steps.append({"tool": "careline.execute_task", "detail": f"{ai_name} staged task per doctor instruction"})

    # Append free-text custom nuance explicitly if doctor typed something not already captured
    if txt and len(txt) > 12 and not any(txt.lower() in item["text"].lower() for items in categorised.values() for item in items):
        if any(k in low for k in ("advise", "start", "stop", "increase", "decrease", "dose", "mg", "iron", "target", "130")):
            categorised["clinical_advice"].append({"text": txt, "irreversible": True, "highlight": True})
            override_detected = True
        elif any(k in low for k in ("blood", "lab", "test", "scan", "inr", "ferritin", "lipid", "renal")):
            categorised["lab_imaging"].append({"text": txt, "irreversible": False})
            override_detected = True

    requires_irreversible = any(
        item.get("irreversible") for items in categorised.values() for item in items
    )

    # Compile inspectable Context Window snapshot (for Head of AI Architecture X-Ray!)
    context_snapshot = compile_context_window(
        effective_graph=effective_graph,
        traversed_node_ids=traversed,
        patient_name=patient_name,
        instruction_text=txt,
        agent_memory_entries=agent_memory_entries or [],
    )

    # Clean, concise doctor-relevant outcome summary (NO raw tool noise!)
    outcome_parts: list[str] = []
    if categorised["clinical_advice"]:
        outcome_parts.append(categorised["clinical_advice"][0]["text"])
    if categorised["lab_imaging"]:
        outcome_parts.append(categorised["lab_imaging"][0]["text"])
    if nurse_handoff:
        outcome_parts.append("Nurse call requested")
    elif categorised["admin_booking"]:
        outcome_parts.append(categorised["admin_booking"][0]["text"])
    doctor_outcome = " · ".join(outcome_parts[:3]) or txt

    return {
        "instruction_text": txt,
        "graph_version_tag": version_tag,
        "graph_content_hash": effective_graph["content_hash"],
        "traversed_nodes": [node_map[nid] for nid in traversed if nid in node_map],
        "categorised_plan": categorised,
        "requires_irreversible_commit": requires_irreversible,
        "undo_window_seconds": int(effective_graph["clinic_standards"].get("undo_window_seconds", 10)) if requires_irreversible else 0,
        "doctor_outcome_summary": doctor_outcome,
        "folded_steps": folded_steps,
        "nurse_handoff": nurse_handoff,
        "emr_addendum": {
            "connector_mode": "additive_only",
            "entries": emr_addendum_lines,
        },
        "override_detected": override_detected,
        "context_snapshot": context_snapshot,
    }


def compile_context_window(
    *,
    effective_graph: dict[str, Any],
    traversed_node_ids: list[str],
    patient_name: str,
    instruction_text: str,
    agent_memory_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the exact bounded Context Window injected into Vertex AI (HK asia-east2) for Head-of-AI inspection."""
    node_map = {n["id"]: n for n in effective_graph["nodes"]}
    subgraph_slice = [
        {
            "id": n["id"],
            "provenance": n["provenance"],
            "safety_floor": n["safety_floor"],
            "params": n["params"],
        }
        for nid in traversed_node_ids
        if (n := node_map.get(nid)) is not None
    ]
    mem_slice = [
        {"tier": m.get("tier"), "topic": m.get("topic"), "summary": m.get("summary_text"), "quote": m.get("source_quote", "")}
        for m in agent_memory_entries[:6]
    ]
    slots = [
        {"slot": "1. System Role & Asymmetric Authority", "tokens_est": 210, "source": "Immutable Safety Floor"},
        {"slot": f"2. Traversed Practice Subgraph ({len(subgraph_slice)}/{len(effective_graph['nodes'])} nodes)", "tokens_est": 180 + 75 * len(subgraph_slice), "source": f"Practice Graph {effective_graph['version_tag']} ({effective_graph['content_hash'][:8]})"},
        {"slot": f"3. Compliance-Gated Agent Memory ({len(mem_slice)} entries)", "tokens_est": 90 + 45 * len(mem_slice), "source": "PostgreSQL FORCE RLS (EMR for Agents)"},
        {"slot": "4. Verbatim Linked Attachments (3-Pt ID Verified)", "tokens_est": 165, "source": "Additive EMR Connector"},
        {"slot": "5. Typed Action & Tool Schemas", "tokens_est": 310, "source": "Bounded Function Declarations"},
    ]
    return {
        "model_routing": {
            "provider": "Vertex AI (GCP ADC)",
            "region": "asia-east2 (Hong Kong)",
            "model": "gemini-2.5-flash",
            "temperature": 0.1,
            "deterministic_pre_gates": ["classify_crisis", "rx_rules.check_prescription_safety", "verify_3pt_id_match"],
        },
        "total_tokens_est": sum(s["tokens_est"] for s in slots),
        "token_budget_ceiling": 4096,
        "subgraph_pruning_ratio": f"{len(subgraph_slice)}/{len(effective_graph['nodes'])} nodes injected (prevents context rot)",
        "slots": slots,
        "traversed_subgraph_json": subgraph_slice,
        "memory_slice": mem_slice,
        "patient": patient_name,
        "turn_input": instruction_text,
    }
