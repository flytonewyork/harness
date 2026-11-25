"""Live Patient-Initiated Demo Orchestrator (Physical Phone Chat -> Laptop /workspace -> Under-the-Hood X-Ray).

Implements the two-screen live demo lifecycle:
1. Laptop `/workspace` starts completely BLANK (`0` cases) with a scannable QR code.
2. Physical phone scans QR -> opens empty natural chat -> typing bubble -> Clinic AI welcome.
3. Selecting another patient journey from the phone sidebar starts a fresh case experience
   from a new patient perspective (again starting from empty chat) and creates a new live
   workflow thread on the laptop workspace.
4. Every patient turn runs deterministic pre-gates first (`triage.py`, `rx_rules.py`,
   `practice_graph.py`, `backend.agentic.prompts`) followed by bounded live LLM or natural
   conversational fallback, logs live agent steps into `CaseLoop.timeline_json` + `live_bus`,
   and compiles a 5/5-linted 14-widget clinical view in PostgreSQL 15 FORCE RLS.
5. One-click Reset clears all live cases, messages, traces, addenda, and phone sessions back
   to blank and notifies connected screens via `session_epoch` + `demo.reset`.

Product invariant: NEVER imports `loopbench`. Synthetic data only.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.agentic import (
    detect_prompt_injection_signals,
    screen_agent_output,
)

from .agent_harness import (
    detect_live_llm_engine,
    invoke_clinical_qa,
    invoke_structured_json,
    resolve_fast_chat_model,
)

logger = logging.getLogger(__name__)
from .certificates import enrich_prescription_item_for_patient
from .security import mint_jwt
from .curator import ensure_active_practice_graph, get_effective_practice_graph
from .db import (
    AgentMemoryEntry,
    CaseLoop,
    Certificate,
    Database,
    DocumentIngestion,
    Encounter,
    ExecutionTrace,
    Immunization,
    Message,
    Observation,
    Patient,
    Prescription,
    Proposal,
    ReviewDecision,
    new_id,
    utcnow,
)
from .emr_connector import ingest_external_document
from .live_bus import LiveHarnessBus
from .live_journeys_extra import (
    EXTRA_LIVE_JOURNEYS,
    build_extra_initial_summary,
    compile_extra_journey_turn,
    seed_extra_journey_prior_record,
)
from .practice_graph import (
    compute_cha2ds2_vasc,
    compute_cvd_10yr_tier,
    compute_has_bled,
    validate_case_presentation,
)
from .rx_rules import check_prescription_safety
from .scheduling import (
    build_clinic_availability,
    confirm_live_chat_booking,
    format_availability_for_prompt,
    get_live_availability_payload,
    select_booking_offer,
    should_attach_booking_offer,
)
from .triage import classify_bilingual_async_triage, screen_acute_message
from .workspace_engine import (
    _compile_5slot_context_snapshot,
    _ensure_workspace_fk_entities,
    reset_workspace,
)

EXTRA_JOURNEY_KEYS: frozenset[str] = frozenset(j["key"] for j in EXTRA_LIVE_JOURNEYS)

HKT = timezone(timedelta(hours=8))
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_BP_RE = re.compile(r"\b(\d{2,3})\s*/\s*(\d{2,3})\b")
_TEMP_RE = re.compile(r"\b(3[6-9](?:\.\d)?|4[0-2](?:\.\d)?)\s*(?:°?\s*c|度)?\b", re.I)
_DAYS_RE = re.compile(r"\b([1-7])\s*(?:-|\s)?(?:day|days|d)\b|([一兩二三四五六七1-7])\s*日", re.I)
_ZH_NUM_MAP = {"一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}


def _hkt_hhmm() -> str:
    return utcnow().astimezone(HKT).strftime("%H:%M")


def _is_zh(text: str, lang_hint: str = "en") -> bool:
    return bool(_CJK_RE.search(text or "")) or str(lang_hint or "").lower().startswith("zh")


# -------------------------------------------------------------------------------------- Curated 12 Realistic HK GP Journeys
CORE_LIVE_JOURNEYS: tuple[dict[str, Any], ...] = (
    {
        "key": "wingyan",
        "category": "acute",
        "patient_id": "pat_lam_urti_demo",
        "patient_name": "LAM Wing-Yan",
        "patient_name_zh": "林詠欣",
        "age_sex": "32F",
        "pathway_node_id": "ebm.pathway.acute_urti_simple",
        "title": "Sore throat, fever & sick leave",
        "title_zh": "喉嚨痛、發燒及病假紙",
        "subtitle": "Simple acute URTI · sick leave & symptomatic care",
        "welcome_en": (
            "Hello, I'm Sam, the AI Resident at Central & Mid-Levels Family Medical. How can I help you today?"
        ),
        "welcome_zh": (
            "你好，我係中環及半山家庭醫務中心嘅 AI 駐診助手 Sam。今日有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "Hi, I woke up feeling unwell today — could a doctor review my symptoms and help with a 2-day sick note?",
            "Mainly a sore throat, feeling feverish, and some body aches since this morning.",
            "No chest pain or shortness of breath, and no rash or trouble swallowing.",
            "Just checked — temperature is 38.2°C, SpO2 98%, and pulse 88 bpm.",
            "Thank you — is there anything else I should watch out for while I wait?",
        ],
        "suggested_replies_zh": [
            "你好，我今朝起身覺得唔舒服——請問可唔可以由醫生審閱我嘅症狀同開兩日病假紙？",
            "今朝開始主要係喉嚨痛、覺得發燒同周身骨痛。",
            "冇心口痛、冇氣促、冇出疹亦冇吞嚥困難。",
            "頭先量咗——體溫 38.2 度、血氧 98%、脈搏 88。",
            "多謝你——等醫生回覆期間有冇咩症狀需要特別留意？",
        ],
    },
    {
        "key": "chan",
        "default_lang": "zh-HK",
        "category": "chronic",
        "patient_id": "pat_chan_warfarin",
        "patient_name": "CHAN Tai Man",
        "patient_name_zh": "陳大文",
        "age_sex": "62M",
        "pathway_node_id": "ebm.pathway.post_discharge_anticoag",
        "title": "Post-hospital AFib medication check",
        "title_zh": "伊利沙伯醫院出院覆診・藥物核對",
        "subtitle": "Discharged from QEH yesterday · post-discharge med review",
        "welcome_en": (
            "Hi Mr. Chan, I'm Sam, the AI Resident at Central & Mid-Levels Family Medical. How are you doing today, and how can I help?"
        ),
        "welcome_zh": (
            "陳先生你好，我係中環及半山家庭醫務中心嘅 AI 駐診助手 Sam。你今日覺得點樣，有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "I just got discharged from QEH yesterday — they gave me Warfarin 3mg and Atorvastatin, but I still have 5mg Warfarin and Simvastatin at home. Which should I take?",
            "No bruising or bleeding at all. Can I take ibuprofen for a mild headache?",
        ],
        "suggested_replies_zh": [
            "尋日喺伊利沙伯醫院出院，醫院開咗 3mg 薄血丸同新降膽固醇藥，但我屋企仲有之前診所 5mg 同舊藥，應該食邊隻？",
            "冇瘀傷亦都冇流牙血。頭有少少痛，可唔可以食布洛芬止痛？",
        ],
    },
    {
        "key": "wong",
        "category": "chronic",
        "patient_id": "pat_wong_htn",
        "patient_name": "WONG Mei Ling",
        "patient_name_zh": "黃美玲",
        "age_sex": "58F",
        "pathway_node_id": "ebm.pathway.chronic_htn_home_bp",
        "title": "Cardiovascular risk & chronic BP management",
        "title_zh": "心血管風險及慢性高血壓管理",
        "subtitle": "Amlodipine 5mg OD · CVD risk score, lipid labs & preventive checkups",
        "welcome_en": (
            "Hi Ms. Wong, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How are you feeling today, and what can I help with?"
        ),
        "welcome_zh": (
            "黃女士你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你今日身體點樣，有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "Checking in with my home blood pressure — today's reading is 128/78 and I'm feeling fine.",
        ],
        "suggested_replies_zh": [
            "想記錄今日家居血壓：今朝量係 128/78，感覺好好，冇頭暈冇腳腫。",
        ],
    },
    {
        "key": "lee",
        "category": "family",
        "patient_id": "pat_lee_ldl",
        "patient_name": "LEE Ming Kit",
        "patient_name_zh": "李明傑",
        "age_sex": "36M",
        "pathway_node_id": "ebm.pathway.borderline_ldl",
        "title": "Annual checkup — borderline cholesterol",
        "title_zh": "年度驗身・輕微高膽固醇跟進",
        "subtitle": "LDL 3.6 mmol/L · fasting blood draw & seasonal flu shot",
        "welcome_en": (
            "Hi Ming Kit, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How can I help you today?"
        ),
        "welcome_zh": (
            "明傑你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。今日有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "I'd like to confirm Friday 12 Feb at 08:00 for my fasting cholesterol blood test — can I bundle my flu shot too?",
        ],
        "suggested_replies_zh": [
            "我想確認 2 月 12 號星期五朝早 8 點空腹抽血，可唔可以順便打埋流感針？",
        ],
    },
    {
        "key": "cheung",
        "default_lang": "zh-HK",
        "category": "chronic",
        "patient_id": "pat_cheung_t2dm",
        "patient_name": "CHEUNG Kwok-Wai",
        "patient_name_zh": "張國偉",
        "age_sex": "56M",
        "pathway_node_id": "ebm.pathway.t2dm_hba1c",
        "title": "Diabetes blood test & persistent cough",
        "title_zh": "糖尿病覆檢報告及持續咳嗽",
        "subtitle": "HbA1c 7.8% on Metformin · pre-review check-in",
        "welcome_en": (
            "Hi Mr. Cheung, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How are you feeling today, and how can I help?"
        ),
        "welcome_zh": (
            "張先生你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你今日身體點樣，有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "呢兩日咳到想死，但係完全冇氣促、冇心口痛、亦都冇發燒，想約醫生覆診順便睇吓血糖報告。",
        ],
        "suggested_replies_zh": [
            "呢兩日咳到想死，但係完全冇氣促、冇心口痛、亦都冇發燒，想約醫生覆診順便睇吓血糖報告。",
        ],
    },
)

LIVE_JOURNEYS: tuple[dict[str, Any], ...] = (*CORE_LIVE_JOURNEYS, *EXTRA_LIVE_JOURNEYS)
JOURNEY_BY_KEY: dict[str, dict[str, Any]] = {j["key"]: j for j in LIVE_JOURNEYS}
JOURNEY_BY_PATIENT_ID: dict[str, dict[str, Any]] = {j["patient_id"]: j for j in LIVE_JOURNEYS}

# Flagship 4 HK GP demo cases exposed by default in the UI picker (?all=1 exposes all 12 for test/eval)
FLAGSHIP_4_JOURNEY_KEYS: tuple[str, ...] = ("wingyan", "tang_uti", "chan", "lau_chest")

_POST_SIGN_REPLIES_EN: list[str] = [
    "I'm still feeling quite unwell — could I request 1 extra day of sick leave?",
    "Got the prescription and certificate, thank you. Anything else I should watch out for?",
]
_POST_SIGN_REPLIES_ZH: list[str] = [
    "我仲係覺得好唔舒服，可唔可以申請延長多一日病假紙？",
    "收到處方同病假紙，多謝。仲有冇咩需要注意？",
]
_POST_SIGN_CHAN_REPLIES_ZH: list[str] = [
    "收到藥單同抽血安排，多謝。食緊薄血丸期間有咩出血跡象需要注意，同埋 7 日後覆驗 INR 要唔要空腹？",
    "明白晒，我會只食 3mg 薄血丸同新降膽固醇藥，多謝安排。",
]
_POST_SIGN_CHAN_REPLIES_EN: list[str] = [
    "Got the updated plan, thank you. What bleeding signs should I watch for on Warfarin, and do I need to fast for the 7-day INR check?",
    "Understood — I'll stick to Warfarin 3mg and Atorvastatin only.",
]
_DAY3_REPLIES_EN: list[str] = [
    "Feeling much better, thank you. Yes — I'd like to book a flu shot and a preventive health checkup next week.",
    "All recovered now — no follow-up needed, thanks for checking in.",
]
_DAY3_REPLIES_ZH: list[str] = [
    "好返晒啦多謝。我想預約下星期打流感針，順便做個預防健康檢查。",
    "完全康復咗，唔使覆診，多謝關心。",
]
_DAY3_CONCERN_REPLIES_EN: list[str] = [
    "I've been feeling quite tired lately and would like routine blood tests (iron and thyroid) bundled with my flu shot.",
]
_DAY3_CONCERN_REPLIES_ZH: list[str] = [
    "我最近覺得幾容易攰，想做常規驗血（例如鐵質同甲狀腺）同埋一併打流感針。",
]


def _normalize_preset_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _is_defined_preset_message(j: dict[str, Any], message_text: str) -> bool:
    """Return True when the patient message matches a curated preset reply for this journey (deterministic path)."""
    norm = _normalize_preset_text(message_text)
    if not norm:
        return False
    pool: list[str] = [
        *(j.get("suggested_replies_en") or []),
        *(j.get("suggested_replies_zh") or []),
        *_POST_SIGN_REPLIES_EN,
        *_POST_SIGN_REPLIES_ZH,
        *_POST_SIGN_CHAN_REPLIES_EN,
        *_POST_SIGN_CHAN_REPLIES_ZH,
        *_DAY3_REPLIES_EN,
        *_DAY3_REPLIES_ZH,
        *_DAY3_CONCERN_REPLIES_EN,
        *_DAY3_CONCERN_REPLIES_ZH,
    ]
    return any(_normalize_preset_text(item) == norm for item in pool)


def _has_patient_turns(messages: list[dict[str, Any]] | None) -> bool:
    for m in messages or []:
        actor = str(m.get("actor") or "")
        role = str(m.get("role") or "")
        if actor not in ("Sam", "Assistant", "Clinic", "System") and role not in ("assistant", "doctor", "system"):
            return True
    return False


def _extract_dynamic_suggested_replies(summary: dict[str, Any] | None, is_zh: bool) -> list[str]:
    """Read AI-generated context-aware suggested replies persisted on CaseLoop.summary_json for the active language."""
    if not isinstance(summary, dict):
        return []
    dyn = summary.get("_dynamic_suggested_replies")
    if isinstance(dyn, list):
        return [str(x).strip() for x in dyn if isinstance(x, str) and str(x).strip()][:2]
    if not isinstance(dyn, dict):
        return []
    want_lang = "zh-HK" if is_zh else "en"
    if str(dyn.get("lang") or "") != want_lang:
        return []
    raw = dyn.get("replies")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()][:2]


def _suggested_replies_for(
    j: dict[str, Any],
    is_zh: bool,
    has_signed: bool = False,
    has_day3: bool = False,
    *,
    dynamic_replies: list[str] | None = None,
    has_patient_turns: bool = False,
    case_state: str | None = None,
    summary: dict[str, Any] | None = None,
) -> list[str]:
    if case_state == "closed":
        return []
    if has_day3:
        if isinstance(summary, dict) and summary.get("_day3_stage") == "awaiting_concerns":
            return list(_DAY3_CONCERN_REPLIES_ZH if is_zh else _DAY3_CONCERN_REPLIES_EN)
        return list(_DAY3_REPLIES_ZH if is_zh else _DAY3_REPLIES_EN)
    if has_signed:
        if j.get("key") == "chan":
            return list(_POST_SIGN_CHAN_REPLIES_ZH if is_zh else _POST_SIGN_CHAN_REPLIES_EN)
        return list(_POST_SIGN_REPLIES_ZH if is_zh else _POST_SIGN_REPLIES_EN)
    if dynamic_replies:
        return [str(x).strip() for x in dynamic_replies if str(x).strip()][:2]
    static_list = list(j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"])
    if has_patient_turns:
        if j.get("key") == "wingyan" and len(static_list) >= 5:
            stage = (summary or {}).get("_wingyan_stage") if isinstance(summary, dict) else None
            if stage == "awaiting_symptoms":
                return [static_list[1]]
            if stage == "awaiting_red_flags":
                return [static_list[2]]
            if stage == "awaiting_vitals":
                return [static_list[3]]
            if case_state == "running":
                return [static_list[1]]
            return [static_list[4]]
        if j.get("key") == "tang_uti" and len(static_list) >= 4:
            stage = (summary or {}).get("_tang_stage") if isinstance(summary, dict) else None
            if stage == "awaiting_red_flags":
                return [static_list[1]]
            if stage == "awaiting_vitals":
                return [static_list[2]]
            if case_state == "running":
                return [static_list[1]]
            return [static_list[3]]
        return static_list[1:]
    return static_list[:1]


def resolve_journey(key_or_patient_id: str | None) -> dict[str, Any]:
    raw = (key_or_patient_id or "wingyan").strip()
    if raw in JOURNEY_BY_KEY:
        return JOURNEY_BY_KEY[raw]
    if raw in JOURNEY_BY_PATIENT_ID:
        return JOURNEY_BY_PATIENT_ID[raw]
    return LIVE_JOURNEYS[0]


# -------------------------------------------------------------------------------------- Session Epoch Coordinator
class LiveDemoCoordinator:
    """Thread-safe coordinator tracking global reset epochs & live connected phone sessions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._epoch: str = uuid.uuid4().hex[:10]
        self._active_phone_journey: str | None = None
        self._phone_connected_at: str | None = None

    @property
    def epoch(self) -> str:
        with self._lock:
            return self._epoch

    def bump_epoch(self) -> str:
        with self._lock:
            self._epoch = uuid.uuid4().hex[:10]
            self._active_phone_journey = None
            self._phone_connected_at = None
            return self._epoch

    def mark_phone_active(self, journey_key: str) -> None:
        with self._lock:
            self._active_phone_journey = journey_key
            self._phone_connected_at = utcnow().astimezone(HKT).strftime("%H:%M:%S HKT")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_epoch": self._epoch,
                "phone_connected": self._active_phone_journey is not None,
                "active_phone_journey": self._active_phone_journey,
                "phone_connected_at": self._phone_connected_at,
            }


live_demo_coordinator = LiveDemoCoordinator()


# -------------------------------------------------------------------------------------- Journey Case Builders (5/5 Linter Clean)
def _seed_journey_prior_record(session: Session, tenant_id: str, j_key: str) -> tuple[str | None, str, str]:
    """Ensure the journey's verified historical EMR document & memory exist (returns doc_id, label, quote)."""
    if j_key in EXTRA_JOURNEY_KEYS:
        return seed_extra_journey_prior_record(session, tenant_id, j_key)

    if j_key == "wingyan":
        doc = ingest_external_document(
            session,
            tenant_id=tenant_id,
            external_doc_ref="KM-LAB-2026-1108-3319",
            doc_kind="lab_report",
            source_org="KingMed Diagnostics (HK)",
            claimed_mrn_or_hkid="PAT-32018",
            claimed_name="LAM Wing-Yan",
            claimed_dob="1994-04-18",
            verbatim_quote="Serum Ferritin: 14 ng/mL (Low; Ref 15–150) · Haemoglobin: 12.1 g/dL (Normal) · LDL-C: 2.3 mmol/L (Normal).",
            page_ref="p.1",
            parsed_diff={"ferritin_ng_ml": 14, "hb_g_dl": 12.1},
        )
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_lam_urti_demo",
            tier="longitudinal", topic="ferritin_trend",
            summary_text="Ferritin trending down 32 (2024) → 21 (2025) → 14 ng/mL (8 Nov 2026); Hb normal 12.1; G6PD deficient + Penicillin allergy.",
            source_doc_id=doc.id, source_quote=doc.verbatim_quote,
            payload_json={"series": [{"label": "Nov 24", "v": 32}, {"label": "Nov 25", "v": 21}, {"label": "Nov 26", "v": 14}]},
        ))
        return doc.id, "KingMed Annual Blood Panel · 8 Nov 2026", doc.verbatim_quote

    if j_key == "chan":
        doc = ingest_external_document(
            session,
            tenant_id=tenant_id,
            external_doc_ref="QEH-DC-2026-1122-8841",
            doc_kind="discharge_summary",
            source_org="Queen Elizabeth Hospital",
            claimed_mrn_or_hkid="PAT-62041",
            claimed_name="CHAN Tai Man",
            claimed_dob="1964-03-14",
            verbatim_quote="Discharge Medications (22/11/2026): 1. Warfarin 3 mg PO daily (reduced from 5 mg; INR 2.4 on 21/11). 2. Atorvastatin 20 mg PO nocte (switched from Simvastatin 20 mg). Next INR due 29/11/2026.",
            page_ref="p.2",
            parsed_diff={"emr_active": ["Warfarin 5mg daily", "Simvastatin 20mg nocte"], "qeh_discharge": ["Warfarin 3mg daily", "Atorvastatin 20mg nocte"]},
        )
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_chan_warfarin",
            tier="episodic", topic="qeh_discharge_medrec",
            summary_text="QEH reduced Warfarin 5mg → 3mg (INR 2.4) & switched Simvastatin → Atorvastatin; 11:30 follow-up today.",
            source_doc_id=doc.id, source_quote=doc.verbatim_quote,
            payload_json={"booked_visit": "Today 11:30"},
        ))
        return doc.id, "QEH Discharge Summary · 22 Nov 2026 (p.2)", doc.verbatim_quote

    if j_key == "wong":
        quote = "7-day home average: 129/79 mmHg (7/7 logged; range 124/76–134/82)."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_wong_htn",
            tier="longitudinal", topic="bp_trajectory",
            summary_text="BP improved 152/94 (Sep) → 138/86 (Oct) → 129/79 7d avg (Nov) on Amlodipine 5mg OD; goal < 135/85.",
            source_quote=quote,
        ))
        return "att_bp_nov", "Home BP Monitoring Baseline · Oct–Nov 2026", quote

    if j_key == "lee":
        doc = ingest_external_document(
            session,
            tenant_id=tenant_id,
            external_doc_ref="KM-LAB-2026-1102-1904",
            doc_kind="lab_report",
            source_org="KingMed Diagnostics (HK)",
            claimed_mrn_or_hkid="PAT-36044",
            claimed_name="LEE Ming Kit",
            claimed_dob="1990-02-04",
            verbatim_quote="LDL Cholesterol: 3.6 mmol/L (Borderline; Ref < 3.4) · Triglycerides: 1.4 mmol/L (Normal).",
            page_ref="p.1",
            parsed_diff={"ldl_mmol_l": 3.6},
        )
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_lee_ldl",
            tier="episodic", topic="ldl_repeat_booking",
            summary_text="Borderline LDL 3.6 mmol/L on 2 Nov checkup; 3-month lifestyle trial.",
            source_doc_id=doc.id, source_quote=doc.verbatim_quote,
        ))
        return doc.id, "KingMed Lipid Panel · 2 Nov 2026", doc.verbatim_quote

    # cheung (T2DM)
    quote = "HbA1c: 7.8% (Elevated; Ref < 7.0%) · Fasting Glucose: 8.4 mmol/L · eGFR: 64 mL/min · Urine ACR: 1.8 mg/mmol (Normal)."
    session.add(AgentMemoryEntry(
        id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_cheung_t2dm",
        tier="longitudinal", topic="t2dm_hba1c_trend",
        summary_text="T2DM on Metformin 500mg BD; HbA1c rose 6.9% (Nov 25) → 7.3% (May 26) → 7.8% (Nov 26); eGFR 64.",
        source_quote=quote,
    ))
    return "att_cheung_t2dm", "KingMed Diabetes Panel · 14 Nov 2026", quote


def _build_initial_summary(j_key: str, doc_id: str | None, doc_label: str, doc_quote: str, welcome_msg: str) -> dict[str, Any]:
    """Initial clean 5/5-linted summary when patient scans QR & Sam starts welcoming them."""
    j = JOURNEY_BY_KEY[j_key]
    if j_key in EXTRA_JOURNEY_KEYS:
        return build_extra_initial_summary(j_key, j, doc_id, doc_label, doc_quote, welcome_msg, _hkt_hhmm())
    chat = [{"actor": "Sam", "time": _hkt_hhmm(), "text": welcome_msg, "lang": "zh-HK" if _CJK_RE.search(welcome_msg) else "en"}]

    if j_key == "wingyan":
        return {
            "alerts": [],
            "preventive_care": [],
            "scheduled_tasks": [],
            "issues": [
                {
                    "id": "iss_intake_live",
                    "name": "1. New careline inquiry — gathering history",
                    "bullets": [
                        "Patient opened clinic careline; Sam is clarifying reason for contact before staging clinical actions.",
                    ],
                    "safety_gap": None,
                    "suggested_orders": [],
                }
            ],
            "lab_panel": None,
            "trend_chart": None,
            "attachments": [],
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    if j_key == "chan":
        return {
            "vitals_exam": {
                "source": "QEH Discharge · 22 Nov",
                "items": [
                    {"label": "BP", "val": "128/76 mmHg"},
                    {"label": "HR", "val": "72 bpm (irregular)"},
                    {"label": "SpO₂", "val": "98% RA"},
                ],
            },
            "clinical_scores": [
                compute_cha2ds2_vasc(age=62, sex="M", hypertension=True, diabetes=True, vascular_disease=True),
                compute_has_bled(labile_inr=True),
            ],
            "med_diff": {
                "rows": [
                    {"drug": "Warfarin", "qeh": "3 mg PO daily", "clinic": "5 mg PO daily", "tag": "Dose reduced"},
                    {"drug": "Statin", "qeh": "Atorvastatin 20 mg PO nocte", "clinic": "Simvastatin 20 mg PO nocte", "tag": "Switched · Duplicate risk"},
                ],
                "qeh": ["Warfarin 3mg PO daily", "Atorvastatin 20mg PO nocte"],
                "clinic": ["Warfarin 5mg PO daily", "Simvastatin 20mg PO nocte"],
            },
            "imaging_studies": [
                {
                    "id": "img_chan_ecg",
                    "modality": "12-Lead ECG",
                    "date": "21 Nov 2026 · QEH",
                    "summary": "Atrial fibrillation, rate-controlled (ventricular rate 72 bpm). No acute ST-T ischaemic changes.",
                    "status": "Reviewed at QEH",
                }
            ],
            "alerts": [
                {"level": "high", "label": "Warfarin Reduced (5mg → 3mg)", "detail": "QEH discharge 22 Nov; Clinic EMR still lists 5mg daily"},
                {"level": "high", "label": "Duplicate Statin Risk", "detail": "QEH switched Simvastatin 20mg → Atorvastatin 20mg"},
            ],
            "preventive_care": [
                {
                    "id": "prev_fit",
                    "title": "HK Colorectal FIT Screening",
                    "badge": "Overdue 18m",
                    "detail": "Age 50–75 HK subsidy eligible · Queue kit after INR stabilizes",
                    "order": {"id": "ord_prev_fit", "type": "lab", "issue": "Preventive Care", "testName": "HK Colorectal FIT Kit", "lab": "Clinic / Govt Programme", "fasting": False, "when": "After INR stable (Dec)"},
                },
            ],
            "scheduled_tasks": [
                {"status": "waiting", "label": "Post-QEH medication review slot offered (awaiting patient booking confirmation)"},
            ],
            "issues": [
                {
                    "id": "iss_chan_intake",
                    "name": "1. Post-QEH discharge check-in",
                    "bullets": [
                        "QEH discharge summary matched (3-pt ID); Sam opened pre-visit Careline check-in.",
                        "Awaiting patient reply regarding home medication bags and symptoms.",
                    ],
                    "safety_gap": "Confirm current home Warfarin tablet strength and screen for bleeding or bruising.",
                    "suggested_orders": [],
                }
            ],
            "lab_panel": {
                "source": "INR & Renal Baseline · QEH 21 Nov / Clinic 14 Sep",
                "rows": [
                    {"test": "INR (QEH 21 Nov)", "val": "2.4", "ref": "2.0–3.0", "flag": "Normal"},
                    {"test": "INR (Clinic 14 Sep)", "val": "2.9", "ref": "2.0–3.0", "flag": "Normal"},
                    {"test": "eGFR (14 Sep)", "val": "71 mL/min", "ref": "> 60", "flag": "Normal"},
                ],
            },
            "trend_chart": {
                "kind": "inr",
                "title": "INR Trend",
                "unit": "INR",
                "target_label": "Therapeutic 2.0–3.0",
                "points": [{"label": "Jul 26", "v": 2.3}, {"label": "14 Sep", "v": 2.9}, {"label": "21 Nov (QEH)", "v": 2.4}],
            },
            "attachments": [{"id": doc_id or "att_chan", "label": doc_label, "quote": doc_quote, "verified_3pt": True}],
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    if j_key == "wong":
        return {
            "clinical_scores": [
                compute_cvd_10yr_tier(age=58, smoker=False, systolic_bp=152, ldl_mmol_l=3.8),
            ],
            "alerts": [
                {"level": "info", "label": "BP Target: < 135/85 mmHg", "detail": "Amlodipine 5mg OD · Routine review booked 21 Dec"},
            ],
            "preventive_care": [
                {
                    "id": "prev_mammo",
                    "title": "Biennial Breast Mammogram",
                    "badge": "Due Dec 2026",
                    "detail": "Age 44–69 HK guideline · Bundle imaging request ahead of 21 Dec visit",
                    "order": {"id": "ord_prev_mammo", "type": "imaging", "issue": "Preventive Care", "modality": "Bilateral Screening Mammogram", "indication": "HK biennial breast screening (58F)", "priority": "Routine"},
                },
                {
                    "id": "prev_wong_flu",
                    "title": "Seasonal Influenza Vaccine (CV Priority)",
                    "badge": "Recommended",
                    "detail": "ESC / HK CHP priority for cardiovascular risk reduction · Bundle with nurse check",
                    "order": {"id": "ord_wong_flu", "type": "book", "issue": "Preventive Care", "visitType": "10-min Nurse Flu Shot", "when": "At 21 Dec review"},
                },
                {
                    "id": "prev_wong_fit",
                    "title": "HK Colorectal FIT Screening",
                    "badge": "Eligible (50–75)",
                    "detail": "HK Government Colorectal Cancer Screening Programme · Nurse kit dispatch",
                    "order": {"id": "ord_wong_fit", "type": "lab", "issue": "Preventive Care", "testName": "HK Colorectal FIT Kit", "lab": "Clinic / Govt Programme", "fasting": False, "when": "At 21 Dec review"},
                },
            ],
            "scheduled_tasks": [
                {"status": "booked", "label": "Mon 21 Dec 10:00 · Routine GP Cardiovascular & BP Review"},
                {"status": "auto", "label": "Day-7 home BP & preventive check-in sent via Careline"},
            ],
            "issues": [
                {
                    "id": "iss_htn_live",
                    "name": "1. Essential hypertension & cardiovascular risk — awaiting Day-7 reading",
                    "bullets": [
                        "On Amlodipine 5mg OD with elevated baseline LDL; Sam sent Day-7 home blood pressure check-in.",
                        "Case stays quiet unless readings breach > 140/90 mmHg or symptoms are reported.",
                    ],
                    "safety_gap": None,
                    "suggested_orders": [],
                }
            ],
            "lab_panel": {
                "source": "KingMed Cardiovascular & Renal Baseline · 18 Oct 2026",
                "rows": [
                    {"test": "LDL-Cholesterol", "val": "3.8 mmol/L", "ref": "< 2.6 (CV risk)", "flag": "HIGH"},
                    {"test": "HbA1c", "val": "5.8 %", "ref": "< 6.0", "flag": "Normal"},
                    {"test": "eGFR", "val": "82 mL/min", "ref": "> 60", "flag": "Normal"},
                    {"test": "Urine ACR", "val": "1.4 mg/mmol", "ref": "< 2.5", "flag": "Normal"},
                ],
            },
            "trend_chart": {
                "kind": "bp",
                "title": "BP Trajectory (mmHg)",
                "unit": "mmHg",
                "target_label": "Target < 135/85",
                "target_sys": 135,
                "points": [{"label": "12 Sep", "sys": 152, "dia": 94}, {"label": "18 Oct", "sys": 138, "dia": 86}],
            },
            "attachments": [{"id": doc_id or "att_bp", "label": doc_label, "quote": doc_quote, "verified_3pt": True}],
            "sam_proactive_insight": None,
            "agent_thread": [
                {
                    "actor": "Sam",
                    "time": _hkt_hhmm(),
                    "text": "Computed deterministic 10-year CVD risk score from age, systolic BP, and LDL baseline. Flagged three age-appropriate HK preventive gaps (biennial mammogram, seasonal flu shot, and colorectal FIT screening) for automated scheduling ahead of the 21 Dec review.",
                }
            ],
            "patient_chat_label": f"{j['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    if j_key == "lee":
        return {
            "clinical_scores": [
                compute_cvd_10yr_tier(age=36, smoker=False, systolic_bp=118, ldl_mmol_l=3.6),
            ],
            "alerts": [
                {"level": "info", "label": "No Drug Allergies", "detail": "Non-smoker · Lifestyle trial active"},
            ],
            "preventive_care": [
                {
                    "id": "prev_lee_flu",
                    "title": "Seasonal Influenza Vaccine",
                    "badge": "Eligible",
                    "detail": "Bundle with morning nurse lipid blood draw",
                    "order": {"id": "ord_lee_flu", "type": "book", "issue": "Preventive Care", "visitType": "Bundle Flu Shot with Lipid Draw", "when": "Feb 2027"},
                }
            ],
            "scheduled_tasks": [
                {"status": "waiting", "label": "Fasting lipid blood-draw invite sent via Careline"},
            ],
            "issues": [
                {
                    "id": "iss_ldl_live",
                    "name": "1. Borderline LDL cholesterol",
                    "bullets": [
                        "Asymptomatic 2 Nov checkup with low 10-year CVD risk; lifestyle trial active.",
                        "Sam opened Careline check-in to confirm February fasting lipid blood draw.",
                    ],
                    "safety_gap": None,
                    "suggested_orders": [],
                }
            ],
            "lab_panel": {
                "source": "KingMed Lipid Panel · 2 Nov 2026",
                "rows": [
                    {"test": "LDL-C", "val": "3.6 mmol/L", "ref": "< 3.4", "flag": "HIGH"},
                    {"test": "HDL-C", "val": "1.5 mmol/L", "ref": "> 1.0", "flag": "Normal"},
                    {"test": "Triglycerides", "val": "1.4 mmol/L", "ref": "< 1.7", "flag": "Normal"},
                ],
            },
            "attachments": [{"id": doc_id or "att_ldl", "label": doc_label, "quote": doc_quote, "verified_3pt": True}],
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    # cheung (T2DM)
    return {
        "alerts": [
            {"level": "info", "label": "eGFR 64 mL/min", "detail": "Metformin safe (>45); monitor renal function annually"},
        ],
        "preventive_care": [
            {
                "id": "prev_dm_eye",
                "title": "Annual Diabetic Retinopathy Screen",
                "badge": "Due Q1 2027",
                "detail": "HK Primary Care Reference Framework for Diabetes",
                "order": {"id": "ord_dm_eye", "type": "refer", "issue": "Preventive Care", "specialty": "Optometry / Diabetic Eye Screening", "urgency": "routine", "clinical_question": "Annual T2DM fundoscopy"},
            }
        ],
        "scheduled_tasks": [
            {"status": "waiting", "label": "Careline pre-review check-in active"},
        ],
        "issues": [
            {
                "id": "iss_dm_live",
                "name": "1. Type 2 diabetes — pre-review check-in",
                "bullets": [
                    "Recent diabetes blood panel returned ahead of scheduled clinic review (see Labs & Trend).",
                    "Sam opened Careline check-in to confirm home glucose and symptoms.",
                ],
                "safety_gap": None,
                "suggested_orders": [],
            }
        ],
        "lab_panel": {
            "source": "KingMed Diabetes Panel · 14 Nov 2026",
            "rows": [
                {"test": "HbA1c", "val": "7.8 %", "ref": "< 7.0", "flag": "HIGH"},
                {"test": "Fasting Glucose", "val": "8.4 mmol/L", "ref": "3.9–5.5", "flag": "HIGH"},
                {"test": "eGFR", "val": "64 mL/min", "ref": "> 60", "flag": "Normal"},
                {"test": "Urine ACR", "val": "1.8 mg/mmol", "ref": "< 2.5", "flag": "Normal"},
            ],
        },
        "trend_chart": {
            "kind": "hba1c",
            "title": "HbA1c Trend (%)",
            "unit": "%",
            "target_label": "Target < 7.0%",
            "target_value": 7.0,
            "points": [{"label": "Nov 25", "v": 6.9}, {"label": "May 26", "v": 7.3}, {"label": "Nov 26", "v": 7.8}],
        },
        "attachments": [{"id": doc_id or "att_dm", "label": doc_label, "quote": doc_quote, "verified_3pt": True}],
        "sam_proactive_insight": None,
        "agent_thread": [],
        "patient_chat_label": f"{j['patient_name']} ↔ Sam Careline Chat",
        "original_chat": chat,
    }


def _extract_asked_drugs(text: str) -> list[str]:
    low = (text or "").lower()
    mapping = [
        ("aspirin", ["aspirin", "阿士匹靈", "阿司匹靈"]),
        ("ibuprofen", ["ibuprofen", "布洛芬", "nsaid", "消炎止痛"]),
        ("amoxicillin", ["amoxicillin", "augmentin", "penicillin", "盤尼西林", "阿莫西林"]),
        ("nitrofurantoin", ["nitrofurantoin", "macrobid", "呋喃妥因"]),
        ("levothyroxine", ["levothyroxine", "thyroxine", "eltroxin", "甲狀腺藥", "甲狀腺素"]),
        ("codeine", ["codeine", "可待因"]),
        ("paracetamol", ["paracetamol", "panadol", "必理痛", "撲熱息痛"]),
        ("warfarin", ["warfarin", "薄血丸"]),
        ("simvastatin", ["simvastatin"]),
        ("atorvastatin", ["atorvastatin"]),
        ("metformin", ["metformin"]),
    ]
    found: list[str] = []
    for canonical, kws in mapping:
        if any(kw in low for kw in kws):
            found.append(canonical)
    return found


def _build_signed_plan_snapshot(session: Session, tenant_id: str, patient_id: str) -> dict[str, Any] | None:
    """Return latest signed prescriptions & MCHK certificates for the patient phone."""
    certs = session.scalars(
        select(Certificate)
        .where(Certificate.tenant_id == tenant_id, Certificate.patient_id == patient_id)
        .order_by(Certificate.signed_at.desc())
        .limit(3)
    ).all()
    rxs = session.scalars(
        select(Prescription)
        .where(Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id)
        .order_by(Prescription.signed_at.desc())
        .limit(3)
    ).all()
    if not certs and not rxs:
        return None
    rx_snapshots: list[dict[str, Any]] = []
    for r in rxs:
        events = list(r.tracking_events_json or [])
        pdf_meta = next((e for e in reversed(events) if isinstance(e, dict) and e.get("vault_doc_id")), {})
        rx_snapshots.append(
            {
                "id": r.id,
                "rx_number": r.id.upper(),
                "items": [enrich_prescription_item_for_patient(it) for it in (r.items_json or []) if isinstance(it, dict)],
                "fulfilment_status": r.fulfilment_status,
                "vault_doc_id": pdf_meta.get("vault_doc_id"),
                "verification_hash": pdf_meta.get("verification_hash"),
                "dispensing_location": "Central & Mid-Levels Family Medical Dispensary · Suite 1204, Central Building, 1–3 Pedder St, Central, HK",
                "dispensing_hours": "Mon–Fri 08:30–19:00 · Sat 09:00–13:00",
                "dispensing_note": "Present HKID and this signed e-Prescription at the clinic dispensary counter, or at any HK Registered Pharmacy (Cap. 138).",
                "prescriber_name": "Locum Dr. Sarah Wong",
                "mchk_reg_no": "M61042",
                "clinic_name": "Central & Mid-Levels Family Medical",
            }
        )
    return {
        "certificates": [
            {
                "id": c.id,
                "days_off": c.days_off,
                "start_date": c.start_date,
                "end_date": c.end_date,
                "omit_employer_diagnosis": True,
                "mchk_reg_no": c.mchk_reg_no,
            }
            for c in certs
        ],
        "prescriptions": rx_snapshots,
    }


def _collect_live_tool_calls(live_bus: LiveHarnessBus, limit: int = 30) -> list[dict[str, Any]]:
    all_states = live_bus.get_all_patient_states()
    calls: list[dict[str, Any]] = []
    for pid, st in all_states.items():
        for tc in st.get("tool_calls") or []:
            calls.append({"patient_id": pid, **tc})
    calls.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    return calls[:limit]


# -------------------------------------------------------------------------------------- Public Live Demo API Operations
def get_live_journeys_status(
    db: Database,
    tenant_id: str,
    live_bus: LiveHarnessBus,
    *,
    include_all: bool = False,
) -> dict[str, Any]:
    """List flagship 4 HK GP patient journeys by default (or all 12 when include_all=True / ?all=1)."""
    visible_journeys = (
        list(LIVE_JOURNEYS)
        if include_all
        else [JOURNEY_BY_KEY[k] for k in FLAGSHIP_4_JOURNEY_KEYS if k in JOURNEY_BY_KEY]
    )
    with db.tenant_session(tenant_id) as session:
        cases = session.scalars(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id)
        ).all()
        by_key = {c.case_key: c for c in cases}

        items: list[dict[str, Any]] = []
        for j in visible_journeys:
            c = by_key.get(j["key"])
            if c is None:
                status = "not_started"
                baton = None
                msg_count = 0
                case_id = None
            else:
                has_signed = bool(_build_signed_plan_snapshot(session, tenant_id, j["patient_id"]))
                status = "signed" if has_signed else c.state
                baton = c.baton
                msg_count = len((c.summary_json or {}).get("original_chat") or [])
                case_id = c.id
            items.append({
                "key": j["key"],
                "category": j.get("category", "acute"),
                "patient_id": j["patient_id"],
                "patient_name": j["patient_name"],
                "patient_name_zh": j["patient_name_zh"],
                "age_sex": j["age_sex"],
                "default_lang": j.get("default_lang", "en"),
                "title": j["title"],
                "title_zh": j["title_zh"],
                "subtitle": j["subtitle"],
                "status": status,
                "baton": baton,
                "case_id": case_id,
                "message_count": msg_count,
            })

    snap = live_demo_coordinator.snapshot()
    return {
        **snap,
        "journeys": items,
        "recent_tool_calls": _collect_live_tool_calls(live_bus, limit=25),
    }


def reset_live_demo(db: Database, tenant_id: str, live_bus: LiveHarnessBus) -> dict[str, Any]:
    """Completely reset the demo environment back to blank (0 cases, 0 live chats, fresh epoch)."""
    ov = reset_workspace(db, tenant_id, reseed=False)
    journey_pids = [j["patient_id"] for j in LIVE_JOURNEYS]
    with db.tenant_session(tenant_id) as session:
        for pid in journey_pids:
            for msg in session.scalars(select(Message).where(Message.tenant_id == tenant_id, Message.patient_id == pid)).all():
                session.delete(msg)
            for imm in session.scalars(select(Immunization).where(Immunization.tenant_id == tenant_id, Immunization.patient_id == pid)).all():
                session.delete(imm)
            for cert in session.scalars(select(Certificate).where(Certificate.tenant_id == tenant_id, Certificate.patient_id == pid)).all():
                session.delete(cert)
            for rx in session.scalars(select(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == pid)).all():
                session.delete(rx)
            session.flush()
            props = session.scalars(select(Proposal).where(Proposal.tenant_id == tenant_id, Proposal.patient_id == pid)).all()
            for prop in props:
                for rd in session.scalars(select(ReviewDecision).where(ReviewDecision.proposal_id == prop.id)).all():
                    session.delete(rd)
            session.flush()
            for prop in props:
                session.delete(prop)
            session.flush()
            encs = session.scalars(
                select(Encounter).where(
                    Encounter.tenant_id == tenant_id,
                    Encounter.patient_id == pid,
                    Encounter.id != "enc_lam_checkup_20261110",
                )
            ).all()
            for enc in encs:
                for ob in session.scalars(select(Observation).where(Observation.encounter_id == enc.id)).all():
                    session.delete(ob)
            session.flush()
            for enc in encs:
                session.delete(enc)
            session.flush()

    live_bus.reset_all()
    new_epoch = live_demo_coordinator.bump_epoch()
    live_bus.publish("demo.reset", patient_id=None, data={"scope": "all", "session_epoch": new_epoch})
    return {
        "ok": True,
        "session_epoch": new_epoch,
        "workspace_counts": ov.get("counts"),
    }


def start_live_journey(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str,
    live_bus: LiveHarnessBus,
    lang: str = "en",
    force_restart: bool = False,
) -> dict[str, Any]:
    """Called when physical phone scans QR or selects a journey in the sidebar.

    Starts a brand-new live case thread in PostgreSQL (`state="running"`) so the laptop
    `/workspace` immediately populates and shows agents working in real time.
    """
    j = resolve_journey(journey_key_or_pid)
    j_key = j["key"]
    pid = j["patient_id"]
    is_zh = str(lang or "en").lower().startswith("zh")
    welcome_msg = j["welcome_zh"] if is_zh else j["welcome_en"]
    engine = detect_live_llm_engine()

    live_demo_coordinator.mark_phone_active(j_key)
    live_bus.update_patient_state(
        pid,
        phone_connected=True,
        phone_connected_at=utcnow().astimezone(HKT).strftime("%H:%M:%S HKT"),
        active_agent="LoopWatcher",
        engine_mode=engine["label"],
        workflow_stage="qr_scanned",
    )

    with db.tenant_session(tenant_id) as session:
        ensure_active_practice_graph(session, tenant_id)
        _ensure_workspace_fk_entities(session, tenant_id)
        graph = get_effective_practice_graph(session, tenant_id)

        existing = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
        )
        if existing is not None and force_restart:
            session.execute(delete(ExecutionTrace).where(ExecutionTrace.case_loop_id == existing.id))
            session.execute(delete(Message).where(Message.tenant_id == tenant_id, Message.patient_id == pid))
            session.execute(delete(Certificate).where(Certificate.tenant_id == tenant_id, Certificate.patient_id == pid))
            session.execute(delete(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == pid))
            if j_key == "ng_quarantine":
                session.execute(
                    delete(DocumentIngestion).where(
                        DocumentIngestion.tenant_id == tenant_id,
                        DocumentIngestion.external_doc_ref == "TST-LAB-FAX-2026-67033",
                    )
                )
            session.delete(existing)
            session.flush()
            existing = None

        already_started = existing is not None
        if existing is None:
            doc_id, doc_label, doc_quote = _seed_journey_prior_record(session, tenant_id, j_key)
            summary = _build_initial_summary(j_key, doc_id, doc_label, doc_quote, welcome_msg)
            if summary.get("original_chat") and isinstance(summary["original_chat"][0], dict):
                summary["original_chat"][0]["text_en"] = j["welcome_en"]
                summary["original_chat"][0]["text_zh"] = j["welcome_zh"]
            now = utcnow()
            hhmm = _hkt_hhmm()
            case = CaseLoop(
                id=new_id("case"),
                tenant_id=tenant_id,
                patient_id=pid,
                patient_name=j["patient_name"],
                patient_age_sex=j["age_sex"],
                case_key=j_key,
                pathway_node_id=j["pathway_node_id"],
                graph_version_tag=graph["version_tag"],
                state="running",
                baton="with_patient",
                risk_tier="high" if j_key in ("chan", "lau_chest") else "standard",
                ai_title=f"{j['title']} — Careline Active",
                lead_line="Patient opened Clinic Careline chat; Sam loaded prior record and started intake.",
                next_checkpoint_label="Careline active · live chat",
                next_checkpoint_at=now + timedelta(minutes=30),
                summary_json=summary,
                timeline_json=[
                    {
                        "date": f"Today {hhmm}",
                        "label": f"Patient connected via Clinic QR · LoopWatcher loaded chart & {doc_label}.",
                        "agent": "LoopWatcher",
                    },
                    {
                        "date": f"Today {hhmm}",
                        "label": "Sam (CareAgent) sent clinic introduction & opened natural conversation.",
                        "agent": "CareAgent",
                    },
                ],
                goal_json=(
                    {"metric": "Blood Pressure", "target": "< 135/85 mmHg", "target_sys": 135, "target_dia": 85, "current": "Awaiting Day-7", "status": "monitoring"}
                    if j_key == "wong"
                    else (
                        {"metric": "LDL-C", "target": "< 3.4 mmol/L", "current": "3.6 mmol/L", "status": "monitoring"}
                        if j_key == "lee"
                        else {}
                    )
                ),
            )
            session.add(case)
            session.add(
                Message(
                    id=new_id("msg"),
                    tenant_id=tenant_id,
                    patient_id=pid,
                    direction="out",
                    sender_role="assistant",
                    body_text=welcome_msg,
                )
            )
            session.flush()

            # Compile initial 5-slot context snapshot so X-Ray has real live token budget
            _, ctx_tel = _compile_5slot_context_snapshot(
                graph=graph,
                case=case,
                memories=[{"tier": "episodic", "topic": "qr_checkin", "summary": doc_quote, "source_quote": doc_quote}],
                engine=engine,
                current_input="Patient scanned clinic QR code to start Careline conversation.",
                channel="patient_phone",
            )
            summary_copy = copy.deepcopy(case.summary_json)
            summary_copy["_context_snapshot"] = ctx_tel
            case.summary_json = summary_copy
            flag_modified(case, "summary_json")
            session.flush()

            live_bus.append_tool_call(
                pid,
                {
                    "agent": "LoopWatcher",
                    "tool": "emr.get_patient_chart",
                    "authority": "READ",
                    "emr_target": "chart",
                    "summary": f"Loaded {j['patient_name']} EMR chart & {doc_label}",
                },
            )
            live_bus.append_tool_call(
                pid,
                {
                    "agent": "CareAgent",
                    "tool": "careline.send_welcome",
                    "authority": "DRAFT",
                    "emr_target": "careline",
                    "summary": f"Sent clinic introduction to {j['patient_name']} via QR chat",
                },
            )
            live_bus.set_context_window(pid, ctx_tel)
        else:
            case = existing

        pat = session.get(Patient, pid)
        tok = mint_jwt(subject_id=pid, role="patient", tenant_id=tenant_id, step_up_verified=True)
        signed = _build_signed_plan_snapshot(session, tenant_id, pid)
        case_summary = case.summary_json or {}
        messages = list(case_summary.get("original_chat") or [])
        case_id = case.id
        case_state = case.state
        crisis = case_summary.get("_active_crisis")
        start_dyn_replies = _extract_dynamic_suggested_replies(case_summary, is_zh)
        start_has_patient = _has_patient_turns(messages)
        start_has_day3 = any(m.get("role") == "system" and m.get("time") == "Day +3" for m in messages)

    live_bus.publish(
        "phone.connected",
        patient_id=pid,
        data={"patient_id": pid, "patient_name": j["patient_name"], "journey_key": j_key, "case_id": case_id},
    )
    live_bus.publish(
        "case.updated",
        patient_id=pid,
        data={"case_id": case_id, "journey_key": j_key, "state": case_state, "event": "journey_started"},
    )
    live_bus.update_patient_state(pid, active_agent=None)

    return {
        "ok": True,
        "session_epoch": live_demo_coordinator.epoch,
        "already_started": already_started,
        "token": tok,
        "case_id": case_id,
        "case_state": case_state,
        "journey": {
            "key": j_key,
            "category": j.get("category", "acute"),
            "patient_id": pid,
            "patient_name": j["patient_name"],
            "patient_name_zh": j["patient_name_zh"],
            "age_sex": j["age_sex"],
            "default_lang": j.get("default_lang", "en"),
            "title": j["title"],
            "title_zh": j["title_zh"],
            "subtitle": j["subtitle"],
        },
        "patient": {
            "id": pid,
            "name_en": pat.name_en if pat else j["patient_name"],
            "name_zh": pat.name_zh if pat else j["patient_name_zh"],
            "age_sex": j["age_sex"],
        },
        "messages": messages,
        "suggested_replies": _suggested_replies_for(
            j,
            is_zh,
            has_signed=bool(signed),
            has_day3=start_has_day3,
            dynamic_replies=start_dyn_replies,
            has_patient_turns=start_has_patient,
        ),
        "signed_plan": signed,
        "crisis": crisis,
    }


def get_live_chat_state(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str,
    lang: str = "en",
) -> dict[str, Any]:
    """Lightweight poll endpoint for the physical phone to pick up doctor sign-offs & global resets."""
    j = resolve_journey(journey_key_or_pid)
    j_key = j["key"]
    pid = j["patient_id"]
    is_zh = str(lang or "en").lower().startswith("zh")

    with db.tenant_session(tenant_id) as session:
        case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
        )
        if case is None:
            return {
                "session_epoch": live_demo_coordinator.epoch,
                "started": False,
                "journey_key": j_key,
                "patient_id": pid,
                "messages": [],
                "signed_plan": None,
                "crisis": None,
                "suggested_replies": _suggested_replies_for(j, is_zh, has_signed=False, has_patient_turns=False),
            }

        case_summary = case.summary_json or {}
        messages = list(case_summary.get("original_chat") or [])
        signed = _build_signed_plan_snapshot(session, tenant_id, pid)
        crisis = case_summary.get("_active_crisis")
        dyn_replies = _extract_dynamic_suggested_replies(case_summary, is_zh)
        has_patient = _has_patient_turns(messages)
        has_day3 = any(m.get("role") == "system" and m.get("time") == "Day +3" for m in messages)
        return {
            "session_epoch": live_demo_coordinator.epoch,
            "started": True,
            "journey_key": j_key,
            "patient_id": pid,
            "case_id": case.id,
            "case_state": case.state,
            "baton": case.baton,
            "messages": messages,
            "signed_plan": signed,
            "booked_appointment": case_summary.get("booked_appointment"),
            "crisis": crisis,
            "suggested_replies": _suggested_replies_for(
                j,
                is_zh,
                has_signed=bool(signed),
                has_day3=has_day3,
                dynamic_replies=dyn_replies,
                has_patient_turns=has_patient,
                case_state=case.state,
                summary=case_summary,
            ),
        }


def _compile_journey_turn_update(
    *,
    session: Session,
    tenant_id: str,
    j_key: str,
    patient_text: str,
    existing_summary: dict[str, Any],
    cds_blocked_drugs: list[str],
    cds_warnings: list[str],
    triage_out: dict[str, Any],
    is_zh: bool,
    has_signed: bool = False,
    current_state: str = "running",
    current_baton: str = "with_patient",
    current_title: str = "",
    current_lead: str = "",
    current_cp: str = "Patient messaging Sam",
) -> tuple[str, str, str, str, str, dict[str, Any], str, list[str]]:
    """Update the journey's CaseLoop fields + 5/5-linted summary and craft a natural fallback reply.

    Returns:
      (new_state, new_baton, ai_title, lead_line, next_checkpoint, updated_summary, fallback_reply, agent_steps)
    """
    low = (patient_text or "").lower().strip()
    summary = copy.deepcopy(existing_summary)
    steps: list[str] = []

    # Detect prior patient & assistant turns so Sam responds to what the user ACTUALLY said
    raw_chat = existing_summary.get("original_chat") or []
    prior_patient_msgs = [
        str(m.get("text") or "")
        for m in raw_chat[:-1]
        if m.get("actor") not in ("Sam", "Assistant") and m.get("role") != "doctor"
    ]
    prior_sam_msgs = [
        str(m.get("text") or "")
        for m in raw_chat[:-1]
        if m.get("actor") in ("Sam", "Assistant")
    ]
    last_sam_text = (prior_sam_msgs[-1] if prior_sam_msgs else "").lower()
    is_followup_turn = len(prior_patient_msgs) >= 1

    is_greeting = bool(
        re.fullmatch(
            r"\s*(?:hi|hello|hey|good morning|good afternoon|good evening|morning|hiya|yo|你好|哈囉|早晨|午安|晚安)[!,.\s。！]*",
            patient_text or "",
            re.I,
        )
    )
    is_short_no = bool(
        re.fullmatch(
            r"\s*(?:no|nope|nah|nothing|none|no thanks|no thank you|not really|all good|i'?m fine|i'?m good|fine|nothing right now|冇|冇事|冇嘢|唔使|唔需要|唔使喇|好地地|冇問題)[!,.\s。！]*",
            patient_text or "",
            re.I,
        )
    )
    is_simple_ack = bool(
        re.fullmatch(
            r"\s*(?:thanks|thank you|thx|ok|okay|got it|sounds good|great|perfect|sure|yes|yep|yeah|多謝|唔該|明白|好呀|好嘅|收到|係|好)[!,.\s。！]*",
            patient_text or "",
            re.I,
        )
    )

    has_day3 = any(m.get("role") == "system" and m.get("time") == "Day +3" for m in raw_chat)

    # Universal conversational handling: phase-aware so Sam NEVER ignores "hi", "no", or "thanks"
    # and NEVER says "waiting for doctor review" if the doctor already signed off!
    if is_greeting:
        steps.append("CareAgent · Greeted patient in context of current care phase")
        if has_day3:
            reply = (
                "你好！我係 Sam——見你覆診完已經過咗幾日，你而家身體好返晒未，有咩可以幫到你？"
                if is_zh
                else "Hi! Good to hear from you — how are you feeling a few days after your visit, and is there anything I can help arrange?"
            )
        elif has_signed:
            reply = (
                "你好！你嘅處方同相關文件已經喺上面對話入面——今日身體感覺點樣，有冇咩用藥或者覆診問題想問？"
                if is_zh
                else "Hi! Your care plan and documents are right above in this thread — how are you feeling, and is there anything about your recovery, medications, or a follow-up I can help with?"
            )
        elif current_state == "needs_decision":
            reply = (
                "你好！我已經將你之前嘅資料整理好交畀黃醫生審閱中——有冇咩最新症狀或者補充想話我知？"
                if is_zh
                else "Hi! Your summary is with Dr. Wong for review right now — is there any new symptom or detail you'd like me to add?"
            )
        else:
            reply = (
                "你好！請問今日有咩可以幫到你——係身體唔舒服、想問用藥、查詢報告，定係想預約覆診？"
                if is_zh
                else "Hi there! What can I help you with today — a symptom, a medication question, a lab result, or an appointment?"
            )
        return (
            current_state,
            current_baton,
            current_title or "Patient Careline Check-In",
            current_lead or "Patient greeted Sam on Careline; awaiting clinical or booking question.",
            current_cp,
            summary,
            reply,
            steps,
        )

    if is_short_no:
        if has_day3:
            steps.append("CareAgent · Patient declined extra Day-3 booking; closed check-in warmly (doctor kept out of loop)")
            reply = (
                "明白，冇問題。好開心知道你康復得順利——之後幾時有需要預約、打針或者問診，隨時喺度搵我就得。"
                if is_zh
                else "No problem at all — glad you're recovering smoothly. Just message here anytime if you ever need a future visit, vaccine, or anything else."
            )
            return ("scheduled", "idle", current_title, current_lead, "Check-in complete", summary, reply, steps)
        if has_signed:
            steps.append("CareAgent · Patient has no further post-sign questions; keeping loop quiet")
            reply = (
                "明白，一切清楚就好。記得多休息，我會喺 3 日後再問候你康復進度——期間有咩變化隨時話我知。"
                if is_zh
                else "Sounds good — glad everything is clear. Get plenty of rest, and I'll check in with you in 3 days. Message anytime if anything changes."
            )
            return ("scheduled", "idle", current_title, current_lead, current_cp, summary, reply, steps)
        if is_followup_turn and any(k in last_sam_text for k in ("bruising", "bleeding", "瘀", "流牙血")):
            steps.append("CareAgent · Logged negative bruising/bleeding screen for Dr. Wong")
            reply = (
                "明白，冇瘀傷亦都冇流血就好放心——我已經幫你記低喺黃醫生嘅覆診記錄度。仲有冇其他問題想我預先通知醫生？"
                if is_zh
                else "Good to hear there's no unusual bruising or bleeding — I've noted that for Dr. Wong ahead of your visit. Is there anything else you'd like me to flag?"
            )
            return (current_state, current_baton, current_title, current_lead, current_cp, summary, reply, steps)
        if is_followup_turn and any(k in last_sam_text for k in ("pregnant", "懷孕")):
            steps.append("CareAgent · Logged negative pregnancy screen for Dr. Wong")
            reply = (
                "多謝確認——我已經更新畀黃醫生，醫生好快就會確認合適嘅處方。記得多飲清水；如有發燒或腰背痛請即刻通知我哋。"
                if is_zh
                else "Thanks for confirming — I've updated Dr. Wong so she can send through your treatment shortly. Keep up your fluids, and let us know straight away if you develop fever or lower back pain."
            )
            return (current_state, current_baton, current_title, current_lead, current_cp, summary, reply, steps)
        if is_followup_turn and any(k in last_sam_text for k in ("rat", "快測", "breathing", "呼吸", "chest pain", "心口痛")):
            steps.append("CareAgent · Logged negative red-flag screen for Dr. Wong")
            reply = (
                "收到，冇氣促或心口痛就安心啲，我已經記低畀黃醫生知。仲有冇其他症狀或者需要（例如病假紙或覆診時間）想我一併整理？"
                if is_zh
                else "Good to hear you have no breathing trouble or chest pain — I've noted that for Dr. Wong. Is there anything else you'd like to mention (like fever readings, a sick note, or booking a visit)?"
            )
            return (current_state, current_baton, current_title, current_lead, current_cp, summary, reply, steps)

        steps.append("CareAgent · Patient indicated no questions right now; keeping thread quiet")
        reply = (
            "冇問題。如果你之後對身體狀況、用藥、報告或者覆診有任何疑問，隨時喺呢度搵我就得。"
            if is_zh
            else "No problem at all. I'm right here whenever you have a question about symptoms, medications, results, or appointments."
        )
        return (
            current_state,
            current_baton,
            current_title or "Patient Careline Check-In",
            current_lead or "Patient checked in with no questions right now.",
            current_cp,
            summary,
            reply,
            steps,
        )

    if is_simple_ack and is_followup_turn:
        if has_signed:
            steps.append("CareAgent · Warmly closed post-sign patient acknowledgement (care plan active)")
            reply = (
                "唔使客氣。你嘅處方同文件會一直保存喺對話入面，祝你早日康復——有任何變化隨時喺度搵我。"
                if is_zh
                else "You're very welcome. Your plan and documents stay right here in this chat — wishing you a smooth recovery, and message anytime if anything comes up."
            )
            return ("scheduled", "idle", current_title, current_lead, current_cp, summary, reply, steps)
        steps.append("CareAgent · Acknowledged patient reply cleanly")
        if current_state == "needs_decision":
            reply = (
                "唔使客氣——所有資料已經整理好放喺黃醫生嘅工作台。醫生一確認，更新就會直接傳送到呢個對話。"
                if is_zh
                else "You're very welcome — everything is ready in Dr. Wong's workspace now. As soon as she reviews and confirms, your plan will arrive right here."
            )
        else:
            reply = (
                "好呀，隨時話我知多啲你嘅症狀或者想安排嘅事項，我即刻幫你跟進。"
                if is_zh
                else "Sounds good. Whenever you're ready, just share a bit more about what you need and I'll get it lined up for you."
            )
        return (current_state, current_baton, current_title, current_lead, current_cp, summary, reply, steps)

    # Post-sign-off follow-up loop: preserve signed care plan, escalate sick-leave/Rx amendments & worsening symptoms
    # strictly to the MCHK doctor, handle non-clinical admin/preventive autonomously, and never dismiss confusion.
    if has_signed:
        is_confused = bool(
            re.fullmatch(
                r"\s*(?:wh+a+t+|huh|wait|excuse me|sorry|pardon|wtf|咩話|吓|下|搞錯|唔係)[?!.,\s。！？]*",
                patient_text or "",
                re.I,
            )
        ) or any(
            k in low
            for k in ("not what i asked", "that's not what", "didn't ask", "misunderstood", "wrong reply", "誤會", "答非所問")
        )
        if is_confused:
            steps.append("CareAgent · Apologized for misunderstanding and invited clarification")
            reply = (
                "唔好意思，頭先可能誤會咗你嘅意思。請問你而家係想延長病假紙、問用藥問題，定係想約覆診？話我知我即刻幫你處理或者轉交黃莎拉醫生。"
                if is_zh
                else "Sorry about that — I may have misread your last message. What can I help with right now — extending your sick-leave certificate, a medication question, or booking a follow-up? Tell me and I'll handle or route it straight to Dr. Sarah Wong."
            )
            return (
                current_state,
                current_baton,
                current_title or f"{JOURNEY_BY_KEY[j_key]['title']} — Follow-Up",
                current_lead or "Patient asked for clarification after doctor sign-off.",
                current_cp,
                summary,
                reply,
                steps,
            )

        is_educational_watch_q = any(
            k in low
            for k in (
                "what bleeding signs", "signs should i watch", "watch out for", "watch for",
                "anything else i should", "do i need to fast", "need to fast",
                "有咩出血跡象", "需要注意", "仲有冇咩需要", "要唔要空腹", "要唔要戒口",
            )
        )
        worsening = (not is_educational_watch_q) and any(
            k in low
            for k in (
                "worse", "worsening", "still fever", "high fever", "rash", "bleeding", "bruising",
                "dizzy", "side effect", "not better", "pain", "更嚴重", "仲發燒", "高燒", "出疹",
                "流血", "瘀傷", "頭暈", "副作用", "冇好轉",
            )
        )
        if worsening or (cds_blocked_drugs and not is_educational_watch_q):
            steps.append("LoopWatcher · Post-sign symptom update detected -> Re-opened case to #my-work for doctor review")
            reply = (
                "收到你嘅最新情況——由於你提及症狀變化或新顧慮，我已經將你嘅最新訊息重新置頂到黃莎拉醫生嘅工作台覆核。如果情況急劇惡化或者呼吸困難，請即刻致電 999 或去急症室。"
                if is_zh
                else "Thank you for letting us know right away — because you mentioned new or worsening symptoms after your care plan, I've flagged this back to Dr. Sarah Wong's active queue for review. If symptoms worsen sharply or you have trouble breathing, please call 999 or go straight to A&E."
            )
            return (
                "needs_decision",
                "with_doctor",
                f"{JOURNEY_BY_KEY[j_key]['title']} — Post-Plan Follow-Up",
                "Patient reported new or worsening symptoms after doctor sign-off; re-opened for review.",
                "Follow-up review",
                summary,
                reply,
                steps,
            )

        has_amend_verb = any(
            k in low
            for k in (
                "extra", "more day", "another day", "extend", "amend", "request", "additional", "longer",
                "change", "different", "stronger", "adjust", "延長", "多一日", "多幾日", "加多一日",
                "申請", "換藥", "改藥", "加藥", "轉藥", "改劑量",
            )
        )
        wants_sick_leave_amend = (
            any(
                k in low
                for k in (
                    "extra day", "more day", "another day", "extend", "延長", "多一日", "多幾日", "加多一日",
                )
            )
            or (
                has_amend_verb
                and (
                    any(k in low for k in ("sick leave", "sick-leave", "sick note", "sick-note", "medical cert", "certificate", "day off", "days off", "病假", "醫生紙"))
                    or bool(re.search(r"\bmc\b", low))
                )
            )
        )
        wants_rx_amend = has_amend_verb and any(
            k in low
            for k in (
                "change med", "different med", "stronger med", "prescription", "change dose", "meds", "medication",
                "換藥", "改藥", "加藥", "處方", "改劑量",
            )
        )
        if wants_sick_leave_amend or wants_rx_amend:
            req_kind = "Sick-leave certificate extension request" if wants_sick_leave_amend else "Prescription amendment request"
            existing_issues = [it for it in (summary.get("issues") or []) if isinstance(it, dict)]
            amend_issue = {
                "id": "iss_post_sign_amend",
                "name": f"{len(existing_issues) + 1}. {req_kind} (doctor sign-off required)",
                "bullets": [
                    f"Post-sign patient request: “{patient_text.strip()}”.",
                    "Asymmetric Authority: only an MCHK-registered doctor can amend or extend a signed sick-leave certificate or prescription.",
                ],
                "safety_gap": "Confirm ongoing symptoms / fitness for work before approving additional sick leave." if wants_sick_leave_amend else "Review clinical rationale before amending signed prescription.",
                "suggested_orders": (
                    [{"id": "wy_cert_ext", "type": "cert", "issue": f"{len(existing_issues) + 1}. {req_kind} (doctor sign-off required)", "daysOff": 1, "startDate": "2026-11-26", "dates": "26 Nov 2026 (+1d extension)", "omitDx": True}]
                    if wants_sick_leave_amend
                    else []
                ),
            }
            if not any(it.get("id") == "iss_post_sign_amend" for it in existing_issues):
                existing_issues.append(amend_issue)
            else:
                existing_issues = [amend_issue if it.get("id") == "iss_post_sign_amend" else it for it in existing_issues]
            summary["issues"] = existing_issues
            agent_thread = list(summary.get("agent_thread") or [])
            agent_thread.append({
                "actor": "Sam",
                "time": _hkt_hhmm(),
                "text": f"Registrar reasoning: Post-sign {req_kind.lower()} requires MCHK doctor authority (cannot be approved by AI or routed to admin) → Re-opened to Dr. Sarah Wong's review queue.",
            })
            summary["agent_thread"] = agent_thread[-5:]
            steps.append(f"AsymmetricAuthority · {req_kind} requires MCHK doctor approval -> Escalated to Dr. Sarah Wong")
            if wants_sick_leave_amend:
                reply = (
                    "病假紙同日數必須由註冊西醫親筆審批同修改，我唔可以代醫生延長病假。我已經將你想延長病假嘅要求即時交返畀黃莎拉醫生覆核。你而家係咪仲有發燒、喉嚨痛或者未適合返工？話我知我幫你一併補充畀醫生。"
                    if is_zh
                    else "I can't extend or amend a signed sick-leave certificate myself — only Dr. Sarah Wong can approve extra days. I've flagged your request for an extra day of sick leave straight back to her review queue. Are you still feeling unwell enough to miss work (for example ongoing fever or sore throat)? Let me know so I can add a brief update for her."
                )
            else:
                reply = (
                    "處方藥物同劑量改動必須由註冊西醫審批，我唔可以自行更改。我已經將你嘅要求即時轉交返黃莎拉醫生覆核——可唔可以講少少你想調整藥物嘅原因（例如未夠紓緩或者有唔適），方便醫生參考？"
                    if is_zh
                    else "I can't change a signed prescription myself — only Dr. Sarah Wong can approve medication adjustments. I've flagged your request straight back to her review queue. Could you share briefly why you'd like to adjust it (e.g. symptoms not settling or side effects) so I can include that for her?"
                )
            return (
                "needs_decision",
                "with_doctor",
                f"{JOURNEY_BY_KEY[j_key]['title']} — {req_kind}",
                f"Patient requested post-sign {req_kind.lower()}; re-opened for MCHK doctor review.",
                "Doctor approval needed",
                summary,
                reply,
                steps,
            )

        wants_booking_or_prev = (not is_educational_watch_q) and any(
            k in low
            for k in (
                "flu", "vaccine", "shot", "book", "rebook", "appointment", "slot", "schedule",
                "tired", "fatigue", "checkup", "blood test", "blood tests", "iron", "thyroid", "refill",
                "流感", "打針", "預約", "覆診", "驗血", "攰", "檢查", "鐵質", "甲狀腺",
            )
        )
        if wants_booking_or_prev:
            gave_specific_concern = any(
                k in low
                for k in ("tired", "fatigue", "blood test", "blood tests", "iron", "thyroid", "energy", "攰", "驗血", "鐵質", "甲狀腺")
            )
            # Step 1 of Day+3 Preventive Checkup: ask warmly about specific concerns or reasons for checkup before arranging
            if has_day3 and not gave_specific_concern and summary.get("_day3_stage") != "awaiting_concerns":
                summary["_day3_stage"] = "awaiting_concerns"
                steps.append("CareAgent · AI Resident asked about specific preventive checkup concerns (zero doctor interruption)")
                reply = (
                    "好開心聽到你好返好多。幫你同護士團隊安排流感針同健康檢查之前，有冇特別想檢查嘅原因或關注——例如最近容易攰、想做常規驗血，定係其他身體變化？"
                    if is_zh
                    else "Glad to hear you're feeling much better. Before I set up your flu shot and checkup with our nurse team, are there any specific concerns or reasons you'd like checked — like energy levels or routine blood tests?"
                )
                return (
                    "scheduled",
                    "idle",
                    f"{JOURNEY_BY_KEY[j_key]['title']} — Preventive Check-In",
                    "Patient recovered; AI Resident is clarifying preventive checkup concerns before handing off to nurse team.",
                    "Clarifying checkup goals",
                    summary,
                    reply,
                    steps,
                )

            # Step 2 (or direct concern stated): arrange downstream with Nurse Rachel & clinic team + attach preventive booking card + Case Closed
            summary["_day3_stage"] = "closed"
            summary["_attach_preventive_booking"] = True
            summary["_append_case_closed_pill"] = True
            tasks = list(summary.get("scheduled_tasks") or [])
            new_task = {
                "status": "booked",
                "label": "Preventive Care Handoff · Nurse Rachel blood panel (CBC/Ferritin/TSH) + seasonal flu shot (zero doctor workload)",
            }
            if not any("Preventive Care Handoff" in str(t.get("label", "")) for t in tasks):
                tasks.append(new_task)
            summary["scheduled_tasks"] = tasks
            admin_handoffs = list(summary.get("admin_handoffs") or [])
            admin_handoffs.append({
                "id": new_id("adm"),
                "route": "Sam · AI Resident → Nurse Rachel & Clinic Team (#clinic-team)",
                "task": f"Day+3 Preventive Checkup & Blood Panel for {JOURNEY_BY_KEY[j_key]['patient_name']}: “{patient_text.strip()}” — bundle baseline bloods (iron/thyroid) with seasonal flu shot (case closed; zero doctor interruption).",
                "status": "pending_admin",
                "created_at": utcnow().isoformat(),
            })
            summary["admin_handoffs"] = admin_handoffs
            steps.append("CareAgent · Arranged preventive blood panel & flu shot with Nurse Rachel / #clinic-team → Case closed (zero doctor interruption)")
            reply = (
                "明白——我已經記低你最近容易攰同想做常規驗血（鐵質／甲狀腺）加流感針，並直接交畀陳護士同診所團隊下游跟進，唔需要打擾醫生。你可以喺下方揀選預防保健時段，今次急症個案亦正式關閉。"
                if is_zh
                else "Got it — I've noted your recent fatigue and request for baseline blood tests alongside your flu shot, and arranged the handoff directly with Nurse Rachel and our clinic team, no need to interrupt the doctor. Pick your preferred preventive slot below, and I'm closing out this acute episode."
            )
            return (
                "closed",
                "idle",
                f"{JOURNEY_BY_KEY[j_key]['title']} — Closed · Nurse Preventive Handoff",
                "Recovered; preventive blood panel & flu shot handed off to Nurse Rachel & clinic team (zero doctor interruption).",
                "Case closed",
                summary,
                reply,
                steps,
            )

        if current_state == "needs_decision":
            steps.append("CareAgent · Appended follow-up patient note to active doctor review queue")
            reply = (
                "收到，我已經將你嘅補充一併加到黃莎拉醫生嘅覆核記錄度——醫生確認後更新就會直接傳送到呢個對話。"
                if is_zh
                else "Got it — I've added that update to Dr. Sarah Wong's review note. As soon as she reviews and confirms, any updated certificate or plan will arrive right here."
            )
            return (
                "needs_decision",
                "with_doctor",
                current_title or f"{JOURNEY_BY_KEY[j_key]['title']} — Follow-Up Review",
                current_lead or "Patient provided additional details for pending post-sign doctor review.",
                current_cp or "Doctor approval needed",
                summary,
                reply,
                steps,
            )

        asked_med_or_watch = is_educational_watch_q or any(
            k in low
            for k in ("how", "when", "take", "dose", "watch", "warning", "fever", "water", "work", "inr", "fast", "點食", "幾時", "注意", "發燒", "空腹")
        )
        steps.append("CareAgent · Logged post-sign patient follow-up quietly (care plan remains active in Scheduled)")
        if asked_med_or_watch and j_key == "chan":
            reply = (
                "好問題。服食薄血丸（Warfarin）期間，如果出現異常大片瘀傷、流鼻血或流牙血超過 10 分鐘未止、小便帶血或呈深茶色、大便呈黑色柏油狀，或者咳血，請盡快聯絡我哋或去急症室。7 日後覆驗 INR 凝血指數唔需要空腹，照常食早餐同飲水就得。"
                if is_zh
                else "Good question. On Warfarin, seek prompt review (or A&E/999 if heavy) for unusual bruising, nosebleeds or gum bleeding lasting over 10 minutes, pink or dark urine, black tarry stools, or coughing up blood. You do not need to fast for your 7-day INR blood test — just eat normally and stay hydrated."
            )
        elif asked_med_or_watch and j_key == "tang_uti":
            reply = (
                "請按指示完成整個 5 日療程並每日多飲清水（約 2 公升）。如果症狀喺 48 小時內未有改善，或者出現發燒、發冷、腰背痛或作嘔，請即刻通知我哋。"
                if is_zh
                else "Please complete the full 5-day course as prescribed and drink plenty of water (around 2L daily). Reach out promptly if symptoms are not easing after 48 hours, or straight away if you develop fever, chills, lower back pain, or vomiting."
            )
        elif asked_med_or_watch and j_key == "lau_chest":
            reply = (
                "請繼續每日準時服食血壓及膽固醇藥並記錄坐定 5 分鐘後嘅家居血壓。如果出現心口痛、氣促、頭暈、視力模糊或劇烈頭痛，請即刻通知我哋或致電 999。"
                if is_zh
                else "Please keep taking your daily blood pressure and cholesterol medicines as prescribed and log seated home BP readings after 5 minutes of rest. Reach out promptly or call 999 if you ever develop chest pain, shortness of breath, dizziness, vision changes, or a severe headache."
            )
        elif asked_med_or_watch:
            reply = (
                "你可以按處方指示服食退燒止痛藥，多飲溫水同多休息。一般如果 48 至 72 小時內仲未退燒，或者出現高燒超過 39 度、呼吸困難或喉嚨劇痛，就隨時再搵我哋。3 日後我亦會自動問候你康復進度。"
                if is_zh
                else "Follow the dosing on your care plan, stay well hydrated, and rest. Please reach out right away if your fever rises above 39°C, hasn't improved after 48–72 hours, or you notice trouble breathing or worsening pain. Otherwise I'll check in with you in 3 days."
            )
        else:
            reply = (
                "收到，謝謝你通知我哋。你嘅護理計劃同文件會一直留喺對話入面，3 日後我會自動問候你康復進度——期間有咩需要隨時搵我。"
                if is_zh
                else "Noted — thanks for letting us know. Your plan and documents stay right here in this chat, and I'll check in with you in 3 days. Message anytime if anything comes up before then."
            )
        return (
            "scheduled",
            "idle",
            f"{JOURNEY_BY_KEY[j_key]['title']} — Plan Signed & Active",
            "Care plan delivered; 3-day AI check-in scheduled.",
            "3-day AI check-in",
            summary,
            reply,
            steps,
        )

    if j_key in EXTRA_JOURNEY_KEYS:
        return compile_extra_journey_turn(
            session=session,
            tenant_id=tenant_id,
            j_key=j_key,
            patient_text=patient_text,
            existing_summary=existing_summary,
            prior_patient_msgs=prior_patient_msgs,
            is_followup_turn=is_followup_turn,
            is_simple_ack=is_simple_ack,
            cds_blocked_drugs=cds_blocked_drugs,
            is_zh=is_zh,
            hhmm=_hkt_hhmm(),
        )

    # 1. LAM Wing-Yan — Simple Focused Case 1: Bread-and-butter Acute URTI (focused red-flag & home-vitals history -> plan + booking)
    if j_key == "wingyan":
        all_text = " ".join([*prior_patient_msgs, patient_text])
        all_low = all_text.lower()
        temp_m = _TEMP_RE.search(all_text)
        has_temp = bool(temp_m)
        temp_val = f"{temp_m.group(1)} °C" if temp_m else "Not yet measured"
        spo2_m = re.search(r"(?:spo2|o2|oxygen|血氧)\D{0,6}(\d{2,3})\s*%?", all_text, re.I)
        pulse_m = re.search(r"(?:pulse|hr|heart rate|脈搏|心跳)\D{0,6}(\d{2,3})\s*(?:bpm)?", all_text, re.I)
        bp_m = _BP_RE.search(all_text)
        days_m = _DAYS_RE.search(all_text)
        days_off = 2
        if days_m:
            raw_d = days_m.group(1) or days_m.group(2)
            days_off = _ZH_NUM_MAP.get(raw_d, int(raw_d) if raw_d and raw_d.isdigit() else 2)
        has_red_flag_screen = any(
            k in all_low
            for k in (
                "no chest pain", "no shortness of breath", "no rash", "no trouble swallowing",
                "no red flag", "breathing is fine", "denies", "none of those",
                "冇心口痛", "冇氣促", "冇出疹", "冇吞嚥",
            )
        )
        mentions_urti = any(k in all_low for k in ("sore throat", "fever", "feverish", "cough", "cold", "throat", "runny", "喉嚨", "發燒", "咳", "傷風"))
        asked_watch_out = any(k in low for k in ("watch out", "anything else", "warning", "when should", "注意", "仲有咩"))
        already_sent_summary = any(
            "earliest open slot" in m.lower()
            or "sent a concise summary" in m.lower()
            or "async message review" in m.lower()
            or "最早普通科空檔" in m
            or "整理咗簡潔摘要" in m
            or "訊息審閱（最快）" in m
            for m in prior_sam_msgs
        )

        # Keep Case 1 ultra-clean: zero alerts, zero preventive care, zero lab panels, zero clinical guidance box
        summary["alerts"] = []
        summary["preventive_care"] = []
        summary["lab_panel"] = None
        summary["trend_chart"] = None
        summary["attachments"] = []
        summary["sam_proactive_insight"] = None

        # Autonomous registrar reasoning trace (visible in Private Doctor & Sam panel)
        agent_thread = list(summary.get("agent_thread") or [])

        # Multi-step conversational AI Resident history-taking (short 1-2 sentence turns):
        # Step 1: Open warmly & ask symptoms -> Step 2: Specific red flags -> Step 3: Home vitals -> Step 4: Brief doctor + booking offer
        has_onset = any(
            k in all_low
            for k in ("woke up", "this morning", "yesterday", "since ", "today", "last night", "days ago", "今朝", "朝早", "尋日", "噚晚", "開始")
        )

        # Step 1: Patient opened generally ("feeling unwell") without naming symptoms yet
        if not mentions_urti and not has_temp and not has_red_flag_screen:
            summary["vitals_exam"] = None
            summary["_wingyan_stage"] = "awaiting_symptoms"
            summary["issues"] = [
                {
                    "id": "iss_urti_intake",
                    "name": "1. Inbound symptom check — gathering history",
                    "bullets": [
                        f"Patient reported: “{patient_text.strip()}”.",
                        "Sam · AI Resident opened warmly and asked for main symptoms.",
                    ],
                    "safety_gap": "Awaiting symptom details, red-flag screen, and home vitals.",
                    "suggested_orders": [],
                }
            ]
            agent_thread.append({
                "actor": "Sam",
                "time": _hkt_hhmm(),
                "text": "AI Resident intake (Step 1/3): Opened warmly and asked patient to describe main symptoms before red-flag and vitals checks.",
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("CareAgent · AI Resident opened warmly & asked for main symptoms")
            reply = (
                "聽到你今朝起身唔舒服真係辛苦。主要係邊啲症狀最困擾你？"
                if is_zh
                else "Sorry to hear you woke up feeling unwell. What symptoms are bothering you most today?"
            )
            return (
                "running",
                "with_patient",
                "Feeling unwell — gathering symptoms",
                "Sam · AI Resident is asking about main symptoms before briefing the doctor.",
                "Gathering symptoms",
                summary,
                reply,
                steps,
            )

        # Step 2: Patient shared symptoms -> ask specifically about red flags (concise, 1-2 sentences)
        if not has_red_flag_screen and not has_temp:
            summary["vitals_exam"] = None
            summary["_wingyan_stage"] = "awaiting_red_flags"
            summary["issues"] = [
                {
                    "id": "iss_urti_intake",
                    "name": "1. Acute URTI — screening red flags",
                    "bullets": [
                        f"Patient reported: “{patient_text.strip()}”.",
                        "Sam · AI Resident is screening specific red flags (chest pain, shortness of breath, rash, trouble swallowing).",
                    ],
                    "safety_gap": "Awaiting red-flag screen and home vitals.",
                    "suggested_orders": [],
                }
            ]
            agent_thread.append({
                "actor": "Sam",
                "time": _hkt_hhmm(),
                "text": "AI Resident intake (Step 2/3): Noted URTI symptoms → screening chest pain, shortness of breath, rash, and dysphagia.",
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("CareAgent · AI Resident screening specific URTI red flags")
            if has_onset:
                reply = (
                    "明白，多謝你話我知。穩陣起見想確認一下，有冇心口痛、氣促、出疹或者吞嚥困難？"
                    if is_zh
                    else "Got it, thanks. Just to be safe, are you having any chest pain, shortness of breath, rash, or trouble swallowing?"
                )
            else:
                reply = (
                    "明白，大概幾時開始唔舒服？另外穩陣起見，有冇心口痛、氣促、出疹或者吞嚥困難？"
                    if is_zh
                    else "Got it — roughly when did that start? Just to be safe too, any chest pain, shortness of breath, rash, or trouble swallowing?"
                )
            return (
                "running",
                "with_patient",
                "Sore throat & fever — screening red flags",
                "Sam · AI Resident is checking specific red flags before collecting home vitals.",
                "Red-flag screen",
                summary,
                reply,
                steps,
            )

        # Step 3: Red flags cleared -> ask for measurable home vitals (concise, 1-2 sentences)
        if has_red_flag_screen and not has_temp:
            summary["vitals_exam"] = None
            summary["_wingyan_stage"] = "awaiting_vitals"
            summary["issues"] = [
                {
                    "id": "iss_urti_intake",
                    "name": "1. Acute URTI — collecting home vitals",
                    "bullets": [
                        "Sore throat, feverish feeling, and body aches since this morning; denies chest pain, shortness of breath, rash, or dysphagia.",
                        "Sam · AI Resident asked for home temperature and any measurable vitals (SpO₂ / pulse) before staging doctor review.",
                    ],
                    "safety_gap": "Awaiting home temperature / vitals if measurable.",
                    "suggested_orders": [],
                }
            ]
            agent_thread.append({
                "actor": "Sam",
                "time": _hkt_hhmm(),
                "text": "AI Resident intake (Step 3/3): Red flags cleared → requesting home temperature and oximeter/pulse readings.",
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("CareAgent · AI Resident requesting home vitals")
            reply = (
                "冇呢啲警號就放心啲。如果屋企有探熱針或者血氧儀，方便量一量體溫同讀數話我知嗎？"
                if is_zh
                else "Glad to hear that. If you have a thermometer or pulse oximeter at home, could you share your temperature and any readings?"
            )
            return (
                "running",
                "with_patient",
                "Sore throat & fever — collecting vitals",
                "Red flags clear; Sam · AI Resident is collecting home temperature and vitals.",
                "Collecting home vitals",
                summary,
                reply,
                steps,
            )

        summary["_wingyan_stage"] = "ready"

        vitals_items: list[dict[str, str]] = [
            {"label": "Temp (home)", "val": temp_val if has_temp else "38.2 °C"},
        ]
        if spo2_m:
            vitals_items.append({"label": "SpO₂ (home)", "val": f"{spo2_m.group(1)}%"})
        if pulse_m:
            vitals_items.append({"label": "Pulse (home)", "val": f"{pulse_m.group(1)} bpm"})
        elif bp_m:
            vitals_items.append({"label": "BP (home)", "val": f"{bp_m.group(1)}/{bp_m.group(2)} mmHg"})
        else:
            vitals_items.append({"label": "BP / Pulse (home)", "val": "Not measured at home"})
        vitals_items.append({
            "label": "Red flags",
            "val": "Denies dyspnoea, chest pain, severe dysphagia, or rash",
        })

        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {_hkt_hhmm()}",
            "encounter_context": "Inbound Remote · Self-Measured",
            "items": vitals_items,
        }
        summary["scheduled_tasks"] = [
            {
                "status": "staged",
                "label": "3-day AI post-visit check-in (queued on sign-off)",
            },
        ]
        vitals_inline = temp_val if has_temp else "38.2 °C"
        if spo2_m:
            vitals_inline += f", SpO₂ {spo2_m.group(1)}%"
        if pulse_m:
            vitals_inline += f", HR {pulse_m.group(1)} bpm"

        summary["issues"] = [
            {
                "id": "iss_urti",
                "name": "1. Acute viral URTI",
                "bullets": [
                    f"Sore throat and fever ({vitals_inline}) since this morning; denies shortness of breath, chest pain, severe dysphagia, or rash.",
                    f"Suitable for async doctor review (Instant Opinion) — requesting symptomatic relief and a {days_off}-day medical sick-leave certificate (no antibiotics indicated).",
                ],
                "safety_gap": None,
                "suggested_orders": [
                    {"id": "wy_rx_para", "type": "rx", "action": "start", "issue": "1. Acute viral URTI", "drug": "Paracetamol 500mg", "freq": "QDS PRN", "days": "5d", "note": "Antipyretic / analgesic — symptomatic URTI care"},
                    {"id": "wy_cert", "type": "cert", "issue": "1. Acute viral URTI", "daysOff": days_off, "dates": "24–25 Nov 2026" if days_off == 2 else f"{days_off} days from 24 Nov 2026", "omitDx": True},
                ],
            }
        ]
        if not any("Packaged 1-issue URTI" in str(x.get("text", "")) for x in agent_thread):
            agent_thread.append({
                "actor": "Sam",
                "time": _hkt_hhmm(),
                "text": f"AI Resident reasoning: Cleared respiratory/airway red flags and recorded home vitals ({vitals_inline}) → Packaged 1-issue async URTI review with Paracetamol 500mg and {days_off}d MCHK sick leave (no in-person visit or antibiotics required).",
            })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("PracticeGraph · Compiled single-issue async Acute URTI summary with home vitals for doctor review")
        steps.append("PresentationCompiler · Compiled clean 1-issue workspace brief (5/5 linter pass)")

        asked_to_book = any(
            k in low
            for k in ("book", "appointment", "teleconsult", "slot", "schedule", "come in", "visit", "預約", "視像", "睇醫生")
        )
        if asked_to_book and already_sent_summary:
            reply = (
                "冇問題——我查閱咗今日診所時間表，林啟賢醫生今日 11:00 有最早空檔，你可以喺下方一鍵預約視像診症或親臨診所，或者揀其他醫生時間。"
                if is_zh
                else "Of course — I checked today's clinic schedule and Dr. Kenneth Lam has our earliest open slot at 11:00 below. You can confirm Teleconsult or In-Clinic, or pick another doctor."
            )
        elif asked_watch_out:
            reply = (
                "好問題——記得多飲溫水同多休息。如果高燒超過 39 度、超過 3 日未退燒，或者出現呼吸困難、心口痛、吞嚥劇痛或出疹，請即刻通知我哋或去急症室。醫生審閱後就會直接喺呢度發送紓緩藥物處方同病假紙。"
                if is_zh
                else "Good question — rest and stay hydrated. Please reach out promptly or seek urgent care if your fever rises above 39°C, hasn't improved after 3 days, or you develop shortness of breath, chest pain, trouble swallowing, or a rash. Our doctor is reviewing your summary now."
            )
        elif (is_simple_ack and is_followup_turn) or already_sent_summary:
            reply = (
                "收到，已經幫你補充到醫生摘要度——醫生審閱並簽署處方同病假紙後就會即時喺呢度通知你。"
                if is_zh
                else "Got it — I've added that to the doctor's notes. As soon as your prescription and sick-leave certificate are signed, they will appear right here."
            )
        else:
            reply = (
                f"多謝你確認警號同提供家居讀數（{vitals_inline}，冇氣促、心口痛、吞嚥劇痛或出疹）。我已經將你嘅摘要同 {days_off} 日病假紙申請直接交畀林啟賢醫生審閱，簽署後處方同病假紙會直接喺呢度發送畀你。如果你想預約視像診症或親臨診所，隨時話我知。"
                if is_zh
                else f"Thank you for checking those red flags and sharing your home readings ({vitals_inline}, no breathing trouble, chest pain, dysphagia, or rash). I've sent your summary and {days_off}-day sick-leave request straight to Dr. Kenneth Lam for review — your prescription and certificate will appear right here once signed. If you would like to make a booking instead, just let me know."
            )

        return (
            "needs_decision",
            "with_doctor",
            "Acute Viral URTI — Doctor Review",
            f"Sore throat & fever ({vitals_inline}), red flags clear; doctor review for symptomatic relief and {days_off}-day sick leave.",
            "Ready for sign-off",
            summary,
            reply,
            steps,
        )

    # 2. CHAN Tai Man (Post-QEH AFib Warfarin + Statin Switch)
    if j_key == "chan":
        asked_nsaid = "ibuprofen" in cds_blocked_drugs or any(k in low for k in ("ibuprofen", "nsaid", "advil", "nurofen", "布洛芬", "消炎止痛"))
        asked_meds = any(
            k in low
            for k in (
                "warfarin", "statin", "atorvastatin", "simvastatin", "qeh", "hospital", "discharge",
                "pill", "pills", "medicine", "medication", "meds", "tablet", "bag", "bags", "dose", "which should i take",
                "薄血丸", "降膽固醇", "出院", "藥", "伊利沙伯",
            )
        )
        asked_appt = any(k in low for k in ("appointment", "11:30", "when", "time", "visit", "doctor", "覆診", "幾點", "預約"))
        mentioned_no_bleed = any(k in low for k in ("no bruising", "no bleeding", "冇瘀", "冇流"))

        if not (asked_nsaid or asked_meds or asked_appt or mentioned_no_bleed):
            steps.append("CareAgent · Replied to patient's message and offered help with visit or medications")
            r_en = "Got it — how can I best help you right now? I can help check your medications, note any symptoms for Dr. Sarah Wong, or confirm your visit details."
            r_zh = "收到。請問你今日想查詢邊方面——係醫院出院後嘅用藥安排、身體有冇唔舒服，定係想確認今日 11:30 嘅覆診？"
            summary["_last_reply_bilingual"] = (r_en, r_zh)
            reply = r_zh if is_zh else r_en
            return (current_state, current_baton, current_title or "Post-QEH Discharge Check-In", current_lead, current_cp, summary, reply, steps)

        if asked_appt and not (asked_nsaid or asked_meds):
            steps.append("CareAgent · Checked live doctor availability for post-discharge follow-up")
            r_en = "I checked today's clinic schedule — Dr. Sarah Wong has an open 11:30 slot for your post-discharge review below (or you can pick another doctor or time). Please bring all of your hospital and clinic medicine bags so we can review them together."
            r_zh = "我查閱咗今日診所時間表——黃莎拉醫生今日上午 11:30 有空檔幫你覆診及核對藥袋（可喺下方確認或揀其他醫生時間）。麻煩記得帶齊你屋企所有醫院新藥袋同舊藥過嚟核對。"
            summary["_last_reply_bilingual"] = (r_en, r_zh)
            reply = r_zh if is_zh else r_en
            return (current_state, current_baton, current_title or "Post-QEH Discharge Check-In", current_lead, current_cp, summary, reply, steps)

        summary["issues"] = [
            {
                "id": "iss_afib",
                "name": "1. AFib — Warfarin dose reduction",
                "bullets": [
                    "Discharged from QEH yesterday after AFib rate-control admission; patient flagged home pill-bottle confusion via Careline.",
                    "Discharge plan reduced daily Warfarin dose with a 7-day repeat check (see Med Diff & INR Trend).",
                ],
                "safety_gap": "Confirm current home Warfarin tablet strength and screen for bleeding or bruising at 11:30.",
                "suggested_orders": [
                    {"id": "ch_rx_warf", "type": "rx", "action": "update", "issue": "1. AFib — Warfarin dose reduction", "drug": "Warfarin 3mg", "freq": "PO daily", "days": "30d", "note": "Matches QEH discharge p.2"},
                    {"id": "ch_lab_inr", "type": "lab", "issue": "1. AFib — Warfarin dose reduction", "testName": "Repeat INR (7 days)", "lab": "KingMed", "fasting": False, "when": "Sun 29 Nov 2026"},
                    {"id": "ch_nurse_call", "type": "nurse", "issue": "1. AFib — Warfarin dose reduction", "assignee": "Nurse Rachel", "task": "Medication adherence check (Cantonese)", "due": "Today before 16:00"},
                ],
            },
            {
                "id": "iss_lipids",
                "name": "2. Dyslipidaemia — Statin switch",
                "bullets": [
                    "Hospital transitioned statin therapy on discharge; Clinic EMR still lists prior agent (see Med Diff).",
                ],
                "safety_gap": "Confirm prior Simvastatin supply is removed at home to prevent duplicate statin dosing.",
                "suggested_orders": [
                    {"id": "ch_rx_stop_simva", "type": "rx", "action": "stop", "issue": "2. Dyslipidaemia — Statin switch", "drug": "Simvastatin 20mg", "freq": "Discontinue", "days": "—", "note": "Replaced by Atorvastatin"},
                    {"id": "ch_rx_atorva", "type": "rx", "action": "start", "issue": "2. Dyslipidaemia — Statin switch", "drug": "Atorvastatin 20mg", "freq": "PO nocte", "days": "30d", "note": "Matches QEH discharge p.2"},
                ],
            },
        ]
        summary["sam_proactive_insight"] = (
            "CHA₂DS₂-VASc 3 · HAS-BLED 1 (NICE NG196) — ongoing anticoagulation indicated with 7-day repeat INR after dose change. "
            "HK Colorectal FIT screening is 18m overdue (eligible to queue once INR is steady)."
        )
        steps.append("PracticeGraph · Traversed ebm.pathway.post_discharge_anticoag (Python CHA₂DS₂-VASc=3, HAS-BLED=1)")
        steps.append("PresentationCompiler · Mounted MedDiff (Warfarin 5→3mg, Statin switch) & INR Trend (5/5 linter)")

        agent_thread = list(summary.get("agent_thread") or [])
        if asked_nsaid or mentioned_no_bleed:
            if not any("QEH discharge med diff" in str(x.get("text", "")) for x in agent_thread):
                agent_thread.append({
                    "actor": "Sam",
                    "time": _hkt_hhmm(),
                    "text": "AI Resident reasoning: Screened bleeding/bruising (denied) and blocked Ibuprofen/NSAID on Warfarin → compiled QEH discharge med diff (Warfarin 5→3mg, Simvastatin→Atorvastatin), CHA₂DS₂-VASc 3 / HAS-BLED 1, and 7-day INR plan for Dr. Sarah Wong's dose review.",
                })
            summary["agent_thread"] = agent_thread[-4:]

            if asked_nsaid:
                reply_en = (
                    "Please avoid ibuprofen (and other NSAIDs) while taking Warfarin — combining them significantly raises bleeding risk. "
                    "I've noted that you have no bruising or bleeding and sent the full QEH medication comparison (Warfarin 5mg to 3mg, Atorvastatin replacing Simvastatin) to Dr. Sarah Wong for her dose review — your updated plan will appear right here once confirmed."
                )
                reply_zh = (
                    "服食薄血丸（Warfarin）期間請避免服食布洛芬（Ibuprofen）或其他消炎止痛藥，因為會大幅增加出血風險。"
                    "我已經記低你冇瘀傷或流血，並將伊利沙伯醫院出院改藥對照（薄血丸 5mg 轉 3mg、轉用 Atorvastatin）交畀黃莎拉醫生覆核劑量，確認後更新計劃會直接喺呢度發送畀你。"
                )
            else:
                reply_en = (
                    "Good to hear there is no bruising or bleeding. I've sent your QEH discharge medication comparison (Warfarin 5mg to 3mg, Atorvastatin replacing Simvastatin) to Dr. Sarah Wong for her dose review — your updated plan will appear right here once confirmed."
                )
                reply_zh = (
                    "冇瘀傷亦都冇流血就好放心。我已經將伊利沙伯醫院出院改藥對照（薄血丸 5mg 轉 3mg、轉用 Atorvastatin）交畀黃莎拉醫生覆核劑量，確認後更新計劃會直接喺呢度發送畀你。"
                )
            summary["_last_reply_bilingual"] = (reply_en, reply_zh)
            return (
                "needs_decision",
                "with_doctor",
                "Post-QEH AFib Discharge — Medication Review",
                "Post-discharge Warfarin 5→3mg & statin switch; bleeding denied & NSAID blocked — awaiting Dr. Sarah Wong's dose review.",
                "Ready for dose review",
                summary,
                reply_zh if is_zh else reply_en,
                steps,
            )

        # Turn 1: One thing at a time — safety hold first, ask about symptoms/discomfort/bleeding, and stop (no abrupt booking card)
        agent_thread.append({
            "actor": "Sam",
            "time": _hkt_hhmm(),
            "text": "AI Resident intake (Step 1/2): Advised holding duplicate statins until Dr. Sarah Wong confirms doses; screening for bruising, bleeding, or discomfort before escalating.",
        })
        summary["agent_thread"] = agent_thread[-4:]
        reply_en = (
            "Thank you for checking before taking anything — please do not take both statins together, and wait for Dr. Sarah Wong to confirm your exact Warfarin and cholesterol doses. "
            "Before I send this over to her, have you noticed any unusual bruising, bleeding, or any other symptoms or discomfort today?"
        )
        reply_zh = (
            "好多謝你食藥前先問清楚——請千祈唔好同時食兩隻降膽固醇藥，並等黃莎拉醫生確認你嘅薄血丸同降膽固醇藥劑量後先好服食今日嘅藥。"
            "交畀醫生審閱前，你今日有冇發現容易瘀傷、流牙血或者其他唔舒服？"
        )
        summary["_last_reply_bilingual"] = (reply_en, reply_zh)
        return (
            "running",
            "with_patient",
            "Post-QEH AFib Discharge — Screening Symptoms",
            "Patient flagged QEH Warfarin/statin mismatch; AI Resident advised holding duplicate statins and is screening for bleeding or discomfort.",
            "Screening symptoms",
            summary,
            reply_zh if is_zh else reply_en,
            steps,
        )

    # 3. WONG Mei Ling (Cardiovascular Chronic Disease: CVD Risk Score + Home BP Quiet Loop vs >140/90 Wake-Up + Preventive Checkups)
    if j_key == "wong":
        bp_m = _BP_RE.search(patient_text)
        mentions_bp_context = bool(bp_m) or any(
            k in low
            for k in (
                "bp", "blood pressure", "reading", "high", "headache", "dizzy", "dizziness", "amlodipine",
                "checkup", "vaccine", "flu", "mammogram", "screening", "cholesterol", "cvd",
                "血壓", "頭痛", "頭暈", "腳腫", "檢查", "預防針", "流感", "乳房",
            )
        )
        if not mentions_bp_context:
            steps.append("CareAgent · Invited patient's blood pressure reading or preventive checkup question")
            reply = (
                "收到。如果你想記錄今日嘅家居血壓，可以直接話我知讀數（例如 128/78）；或者話我知你想安排常規身體檢查、流感針定係覆診。"
                if is_zh
                else "Got it — if you'd like to log a home blood pressure reading, just share the numbers (for example 128/78), or let me know if you'd like to arrange your routine preventive checkups."
            )
            return (current_state, current_baton, current_title or "Cardiovascular Risk & Chronic BP Review", current_lead, current_cp, summary, reply, steps)

        sys_v = int(bp_m.group(1)) if bp_m else (156 if any(k in low for k in ("high", "headache", "高", "頭痛")) else 128)
        dia_v = int(bp_m.group(2)) if bp_m else (96 if sys_v >= 140 else 78)
        breached = sys_v >= 140 or dia_v >= 90

        cvd_score = compute_cvd_10yr_tier(age=58, smoker=False, systolic_bp=sys_v, ldl_mmol_l=3.8)
        summary["clinical_scores"] = [cvd_score]
        summary["vitals_exam"] = {
            "source": f"Home BP Careline · Today {_hkt_hhmm()}",
            "items": [
                {"label": "Latest Reading", "val": f"{sys_v}/{dia_v} mmHg"},
                {"label": "Adherence", "val": "Amlodipine 5mg OD logged"},
                {"label": "Symptoms", "val": "Mild headache reported" if any(k in low for k in ("headache", "頭痛")) else "Asymptomatic"},
            ],
        }
        summary["trend_chart"] = {
            "kind": "bp",
            "title": "BP Trajectory (mmHg)",
            "unit": "mmHg",
            "target_label": "Target < 135/85",
            "target_sys": 135,
            "points": [
                {"label": "12 Sep", "sys": 152, "dia": 94},
                {"label": "18 Oct", "sys": 138, "dia": 86},
                {"label": "Today", "sys": sys_v, "dia": dia_v},
            ],
        }
        agent_thread = list(summary.get("agent_thread") or [])
        if breached:
            summary["alerts"] = [
                {"level": "high", "label": f"Home BP Threshold Breach ({sys_v}/{dia_v})", "detail": "Exceeds > 140/90 wake-up gate · Escalated to #my-work"},
                {"level": "info", "label": f"10-Yr CVD Risk: {cvd_score.get('tier', 'Moderate')}", "detail": "LDL above target + elevated SBP · Review titration & statin"},
            ]
            summary["issues"] = [
                {
                    "id": "iss_htn_breach",
                    "name": "1. Essential hypertension & cardiovascular risk — home BP elevated",
                    "bullets": [
                        "Patient logged an elevated home reading above clinic target via Careline (see BP Trajectory & 10-Yr CVD Risk Score).",
                        "No emergency red flags reported; review Amlodipine titration, lipid target, and overdue HK preventive checkups.",
                    ],
                    "safety_gap": "Confirm seated 5-minute rest technique and screen for vision changes or chest discomfort.",
                    "suggested_orders": [
                        {"id": "wg_rx_amlo", "type": "rx", "action": "update", "issue": "1. Essential hypertension & cardiovascular risk — home BP elevated", "drug": "Amlodipine 10mg", "freq": "OD", "days": "30d", "note": "Consider titration if clinic BP confirms"},
                        {"id": "wg_book_rev", "type": "book", "issue": "1. Essential hypertension & cardiovascular risk — home BP elevated", "visitType": "15-min GP Cardiovascular & BP Review", "when": "Within 48 hours"},
                        {"id": "wg_nurse_bp", "type": "nurse", "issue": "1. Essential hypertension & cardiovascular risk — home BP elevated", "assignee": "Nurse Rachel", "task": "BP follow-up call & preventive bundle", "due": "Tomorrow morning"},
                    ],
                }
            ]
            summary["sam_proactive_insight"] = (
                f"NICE NG136 / HK HTN Reference Framework: Home BP {sys_v}/{dia_v} mmHg breaches the 140/90 wake-up gate "
                f"({cvd_score.get('name', '10-Yr CVD Risk')}: {cvd_score.get('score_label', '')}). "
                "Consider Amlodipine titration and bundling due HK biennial mammogram, CV-priority flu vaccine, and colorectal FIT kit."
            )
            agent_thread.append({
                "actor": "Sam",
                "time": _hkt_hhmm(),
                "text": (
                    f"Registrar reasoning: Home BP {sys_v}/{dia_v} mmHg crossed >140/90 threshold with mild headache; "
                    f"recomputed deterministic 10-year CVD risk ({cvd_score.get('score_label', 'elevated')}) alongside baseline lipid panel. "
                    "Escalated to Dr. Wong for BP titration/statin review and staged preventive checkup bundle (mammogram, flu vaccine, FIT kit)."
                ),
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append(f"PracticeGraph · Recomputed Python 10-Yr CVD Risk Score ({cvd_score.get('score_label', '')}) for SBP {sys_v}")
            steps.append(f"LoopWatcher · BP threshold breach detected ({sys_v}/{dia_v} > 140/90) -> Woke loop to Needs Decision")
            reply_en = (
                f"Thank you for sharing that reading ({sys_v}/{dia_v} mmHg). Because that is above your target of 135/85, "
                "I have escalated your BP log and updated cardiovascular risk summary to Dr. Sarah Wong's active review queue so she can decide whether to adjust your medication or bring forward your visit.\n\n"
                "On preventive checkups, you are also due for your biennial mammogram, seasonal flu vaccine, and HK colorectal FIT screening — I have flagged these so we can bundle them cleanly at your next clinic visit. "
                "Please rest seated for 5 minutes and seek urgent care (call 999) straight away if you develop severe headache, chest pain, shortness of breath, or vision changes."
            )
            reply_zh = (
                f"多謝你即時匯報（{sys_v}/{dia_v} mmHg）。由於讀數高於你嘅目標 135/85，"
                "我已經即時將你嘅血壓記錄同心血管風險評估提升至黃莎拉醫生嘅優先覆核清單，等醫生決定係咪需要調整藥量或者提早覆診。\n\n"
                "預防性檢查方面，你亦到期安排兩年一度乳房造影、季節性流感疫苗及香港大腸癌篩查（FIT），我已經一併標記方便下次覆診順便安排。"
                "請先坐低休息 5 分鐘；如果出現劇烈頭痛、心口痛、氣促或者視力模糊，請立即致電 999 或前往急症室。"
            )
            return (
                "needs_decision",
                "with_doctor",
                "Cardiovascular Risk & Hypertension — Home BP Breach",
                f"Home BP {sys_v}/{dia_v} mmHg breached >140/90 wake-up rule; CVD risk score & preventive checkups staged.",
                "Escalated · review today",
                summary,
                reply_zh if is_zh else reply_en,
                steps,
            )

        # On-target quiet loop (ZERO doctor interruption!)
        summary["alerts"] = [
            {"level": "info", "label": "BP Target: < 135/85 mmHg", "detail": "On target · Routine review remains scheduled 21 Dec"},
            {"level": "info", "label": f"10-Yr CVD Risk: {cvd_score.get('tier', 'Moderate')}", "detail": "Preventive checkups & flu shot eligible ahead of 21 Dec visit"},
        ]
        summary["issues"] = [
            {
                "id": "iss_htn",
                "name": "1. Essential hypertension & cardiovascular risk — on target",
                "bullets": [
                    "Asymptomatic and well controlled on Amlodipine 5mg OD within home target (see BP Trajectory & 10-Yr CVD Risk Score).",
                    "Routine GP review remains scheduled for 21 Dec; automated AI offered due preventive screenings without interrupting the doctor.",
                ],
                "safety_gap": None,
                "suggested_orders": [
                    {"id": "wg_goal", "type": "goal", "issue": "1. Essential hypertension & cardiovascular risk — on target", "metric": "Blood Pressure", "targetSys": 135, "targetDia": 85},
                    {"id": "wg_lab_ren", "type": "lab", "issue": "1. Essential hypertension & cardiovascular risk — on target", "testName": "Renal Function & Electrolytes (U&E)", "lab": "KingMed", "fasting": False, "when": "1 week before 21 Dec review"},
                ],
            }
        ]
        summary["sam_proactive_insight"] = None  # Strict R5 high-signal gate: stay silent when on-target!
        agent_thread.append({
            "actor": "Sam",
            "time": _hkt_hhmm(),
            "text": (
                f"Registrar reasoning: Home BP {sys_v}/{dia_v} mmHg is well within <135/85 target on Amlodipine 5mg OD; "
                f"recomputed 10-year CVD risk ({cvd_score.get('score_label', '')}). Kept loop quietly in Scheduled (zero doctor interruption) "
                "and reminded patient of due biennial mammogram, CV-priority seasonal flu shot, and pre-visit U&E blood test."
            ),
        })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append(f"PracticeGraph · Recomputed Python 10-Yr CVD Risk Score ({cvd_score.get('score_label', '')}) for SBP {sys_v}")
        steps.append(f"LoopWatcher · Evaluated Home BP {sys_v}/{dia_v} (< 135/85 target) -> Routed quietly to Scheduled (0 doctor interruption)")
        reply_en = (
            f"That reading ({sys_v}/{dia_v} mmHg) is right inside your < 135/85 target, and I have logged it onto your clinic trend chart. "
            "Since everything is steady and you are feeling well, your routine cardiovascular review stays set for Mon 21 Dec at 10:00.\n\n"
            "As a quick preventive reminder, you are also due for your biennial mammogram, seasonal flu vaccine, and a pre-visit kidney/electrolyte blood check — let me know if you would like me to bundle those with Nurse Rachel ahead of 21 Dec."
        )
        reply_zh = (
            f"呢個讀數（{sys_v}/{dia_v} mmHg）完全喺你 < 135/85 嘅目標範圍內，我已經幫你記錄咗喺血壓趨勢圖。"
            "由於血壓穩定而且感覺良好，你嘅常規心血管覆診維持喺 12 月 21 日（星期一）上午 10:00。\n\n"
            "順帶提醒，你亦到期安排兩年一度乳房造影、季節性流感疫苗及覆診前腎功能抽血——如果想我幫你提前同陳護士一併預約，隨時話我知。"
        )
        return (
            "scheduled",
            "idle",
            "Cardiovascular Risk & Hypertension — Routine Review 21 Dec",
            f"Home BP {sys_v}/{dia_v} mmHg on target on Amlodipine 5mg OD; preventive checkups queued quietly.",
            "21 Dec review",
            summary,
            reply_zh if is_zh else reply_en,
            steps,
        )

    # 4. LEE Ming Kit (Quiet Lipid Booking vs Thin/Vague Intake Guard!)
    if j_key == "lee":
        is_booking = any(k in low for k in ("12 feb", "08:00", "8am", "8:00", "works", "book", "confirm", "blood test", "cholesterol", "ldl", "flu", "血", "2月12", "8點", "預約", "流感針"))
        if is_booking:
            summary["scheduled_tasks"] = [
                {"status": "booked", "label": "Fri 12 Feb 08:00 · Fasting Lipid Draw + Seasonal Flu Shot (Nurse Rachel)"},
            ]
            summary["issues"] = [
                {
                    "id": "iss_ldl",
                    "name": "1. Borderline LDL cholesterol",
                    "bullets": [
                        "Patient confirmed February fasting blood draw via Careline (low 10-year CVD risk; lifestyle trial active).",
                        "Statin therapy not indicated; results review will trigger automatically once lab posts.",
                    ],
                    "safety_gap": None,
                    "suggested_orders": [
                        {"id": "lee_rev", "type": "book", "issue": "1. Borderline LDL cholesterol", "visitType": "10-min GP Results Review", "when": "1 week post-blood draw"},
                    ],
                }
            ]
            summary["sam_proactive_insight"] = None
            steps.append("BookingAdapter · Confirmed Fri 12 Feb 08:00 fasting lipid slot -> Case stays quiet in Scheduled")
            reply_en = (
                "All set! I've booked your fasting lipid blood draw for Fri 12 Feb at 08:00 with Nurse Rachel and bundled your seasonal flu shot into the same 10-minute morning visit. "
                "Please fast (water only) for 8–10 hours beforehand — I'll send a reminder the evening before."
            )
            reply_zh = (
                "已經幫你預約好！2 月 12 日（星期五）上午 8:00 由陳護士為你安排空腹血脂抽血，並一併接種季節性流感疫苗。"
                "請於抽血前空腹 8 至 10 小時（可飲清水）——我會喺前一晚再提醒你。"
            )
            return (
                "scheduled",
                "idle",
                "Borderline Hyperlipidaemia — Fasting Draw Confirmed",
                "Patient confirmed Fri 12 Feb 08:00 fasting lipid blood draw + flu shot; stays quietly scheduled.",
                "Fri 12 Feb 08:00",
                summary,
                reply_zh if is_zh else reply_en,
                steps,
            )

        asked_abx = any(k in low for k in ("antibiotic", "amoxicillin", "抗生素"))
        mentions_unwell = asked_abx or any(k in low for k in ("unwell", "sick", "ill", "not feeling well", "symptom", "fever", "cough", "pain", "唔舒服", "病", "痛", "燒"))
        if not mentions_unwell:
            steps.append("CareAgent · Clarified patient request cleanly")
            reply = (
                "收到！請問你想預約 2 月嘅空腹抽血覆檢、打流感針，定係今日有身體唔舒服想搵醫生？"
                if is_zh
                else "Got it — would you like help booking your February fasting blood test, arranging a flu shot, or checking on a new symptom today?"
            )
            return (current_state, current_baton, current_title or "Clinic Careline Check-In", current_lead, current_cp, summary, reply, steps)

        # Thin / vague intake safety guard!
        summary["intake_sufficiency"] = "insufficient"
        summary["issues"] = [
            {
                "id": "iss_thin",
                "name": "1. Undifferentiated unwellness — thin intake",
                "bullets": [
                    (
                        "Patient reported feeling vaguely unwell without specific organ-system symptoms and requested antibiotics."
                        if asked_abx
                        else "Patient reported feeling unwell without specific localising symptoms."
                    ),
                    "Zero prescriptions or sick-leave certificates staged pending live history and clinical assessment.",
                ],
                "safety_gap": "Clarify symptom site, fever, duration, and red flags before any prescribing decision.",
                "suggested_orders": [
                    {"id": "lee_book_gp", "type": "book", "issue": "1. Undifferentiated unwellness — thin intake", "visitType": "15-min GP Assessment", "when": "Next available slot today"},
                    {"id": "lee_preq", "type": "nurse", "issue": "1. Undifferentiated unwellness — thin intake", "protocol": "pre_visit_questionnaire", "assignee": "Nurse Rachel", "task": "Send structured pre-visit symptom & red-flag questionnaire via Sam", "due": "Before consult"},
                ],
            }
        ]
        summary["sam_proactive_insight"] = None
        steps.append("IntakeGuard · Flagged intake_sufficiency='insufficient' (0 premature Rx/certs staged)")
        if asked_abx:
            reply_en = (
                "I'm sorry you're not feeling well. Antibiotics require a doctor's clinical assessment first — we can't prescribe them automatically through chat.\n\n"
                "To help Dr. Sarah Wong evaluate you safely, could you share a bit more detail: what specific symptoms do you have (for example fever, cough, sore throat, stomach upset, or urinary symptoms), and are you having any chest pain or shortness of breath? "
                "I can also line up a 15-minute GP slot today."
            )
            reply_zh = (
                "聽到你唔舒服我都好關心。抗生素必須先由醫生臨床評估決定，唔可以透過對話直接開藥。\n\n"
                "為咗幫黃莎拉醫生安全了解你嘅情況，可唔可以講多啲具體症狀（例如有冇發燒、咳、喉嚨痛、腸胃唔舒服或者小便痛）？有冇任何心口痛或者氣促？我亦可以幫你安排今日 15 分鐘醫生診症時段。"
            )
        else:
            reply_en = (
                "I'm sorry you're not feeling well. To help Dr. Sarah Wong evaluate you safely, could you share a bit more detail — "
                "what specific symptoms are you having, how long have they lasted, and do you have any fever, chest pain, or shortness of breath? "
                "I can also line up a 15-minute GP slot today."
            )
            reply_zh = (
                "聽到你唔舒服我都好關心。為咗幫黃莎拉醫生安全了解你嘅情況，可唔可以講多啲具體症狀（例如發燒、咳、喉嚨痛或者腸胃唔舒服）、持續咗幾耐，以及有冇心口痛或氣促？我亦可以幫你安排今日 15 分鐘診症時段。"
            )
        return (
            "needs_decision",
            "with_doctor",
            "Undifferentiated Unwellness — Insufficient Intake",
            "Vague symptom message; thin-intake guard active (zero premature Rx staged).",
            "Needs clinical assessment",
            summary,
            reply_zh if is_zh else reply_en,
            steps,
        )

    # 5. CHEUNG Kwok-Wai (T2DM HbA1c 7.8% + Cantonese hyperbole safe negation)
    suppressed = (triage_out.get("triage_screen") or {}).get("suppressed_negations") or []
    mentions_cough = any(k in low for k in ("cough", "咳"))
    mentions_glucose = any(k in low for k in ("glucose", "sugar", "metformin", "hba1c", "diabetes", "mmol", "adjust", "dose", "血糖", "劑量", "加藥", "糖尿"))
    mentions_slot = any(k in low for k in ("15:00", "3:00", "tomorrow", "morning", "今日", "聽朝", "三點"))
    if not (mentions_cough or mentions_glucose or mentions_slot or suppressed):
        steps.append("CareAgent · Invited patient's symptom, diabetes, or booking question")
        reply = (
            "收到！請問你想查詢最新嘅血糖報告、Metformin 用藥安排、身體唔舒服，定係想約覆診？"
            if is_zh
            else "Got it — would you like to review your latest diabetes labs, check on your Metformin dose, mention any new symptoms, or book a follow-up?"
        )
        return (current_state, current_baton, current_title or "Diabetes Careline Check-In", current_lead, current_cp, summary, reply, steps)
    summary["issues"] = [
        {
            "id": "iss_dm",
            "name": "1. Type 2 diabetes — suboptimal glycaemic control",
            "bullets": [
                "Patient messaged via Careline ahead of review; recent HbA1c rose above target on Metformin 500mg BD (see Trend & Labs).",
                "Renal function remains adequate for Metformin continuation or titration; zero emergency red flags.",
            ],
            "safety_gap": "Confirm home fasting glucose log and Metformin GI tolerance before dose escalation.",
            "suggested_orders": [
                {"id": "ch_dm_rx", "type": "rx", "action": "update", "issue": "1. Type 2 diabetes — suboptimal glycaemic control", "drug": "Metformin 850mg", "freq": "BD with meals", "days": "90d", "note": "eGFR 64 safe for titration"},
                {"id": "ch_dm_lab", "type": "lab", "issue": "1. Type 2 diabetes — suboptimal glycaemic control", "testName": "Repeat HbA1c + Renal Panel", "lab": "KingMed", "fasting": True, "when": "In 3 months"},
            ],
        }
    ]
    summary["sam_proactive_insight"] = (
        "HK Reference Framework for Diabetes Care: HbA1c 7.8% (> 7.0% target) with preserved eGFR 64 mL/min — "
        "safe to titrate Metformin or consider add-on therapy; annual diabetic retinopathy screening due Q1 2027."
    )
    if suppressed:
        steps.append(f"SafetySentinel · Cantonese hyperbole & explicit negation verified safe ({', '.join(str(x) for x in suppressed[:3])}) — zero false 999")
    steps.append("PracticeGraph · Traversed ebm.pathway.t2dm_hba1c (eGFR 64 Metformin renal check passed)")

    title_str = "Type 2 Diabetes (HbA1c 7.8%) & Cough Review"
    lead_str = "Cantonese intake cleared red-flag gate (hyperbole/negation verified); HbA1c 7.8% & eGFR 64 prepared for doctor."

    if is_simple_ack and is_followup_turn:
        reply_en = "You're welcome — everything is ready in Dr. Sarah Wong's workspace for your review."
        reply_zh = "唔使客氣——所有血糖記錄同驗血報告都已經交到黃莎拉醫生嘅工作台，等覆診時一齊傾。"
    elif is_followup_turn and mentions_slot and not (mentions_cough or mentions_glucose):
        summary["scheduled_tasks"] = [{"status": "booked", "label": "Clinic GP Review · Diabetes & Cough Follow-Up (requested)"}]
        reply_en = "Noted — I've held that clinic review slot for you with Dr. Sarah Wong and attached your latest diabetes panel so you can go through both your cough and glucose log together."
        reply_zh = "收到——我已經幫你預留黃莎拉醫生嘅診所覆診時段，並附上最新驗血報告，方便一次過睇咳同傾血糖方案。"
    elif mentions_glucose and not mentions_cough:
        reply_en = (
            "Thanks for sharing your home fasting readings. I've pulled your latest diabetes panel (HbA1c 7.8%, eGFR 64) and flagged your Metformin dosing question in Dr. Sarah Wong's review queue so she can decide whether to adjust your dose.\n\n"
            "Have you had any stomach upset with Metformin 500mg twice daily, and would you like an in-clinic slot today at 15:00 or tomorrow morning?"
        )
        reply_zh = (
            "多謝你分享屋企空腹血糖記錄。我已經整理好你最新嘅糖尿病報告（HbA1c 7.8%，eGFR 64），並將你嘅 Metformin 劑量問題交畀黃莎拉醫生審閱。\n\n"
            "你最近食 Metformin 500mg 每日兩次有冇腸胃唔舒服？你想約今日下午 3:00 定係聽朝嚟診所覆診？"
        )
        title_str = "Type 2 Diabetes (HbA1c 7.8%) — Metformin Review"
        lead_str = "Patient logged elevated home fasting glucose; HbA1c 7.8% & eGFR 64 prepared for doctor."
    else:
        reply_en = (
            "Thank you for letting us know — glad to hear you have no shortness of breath, chest pain, or fever. "
            "I've added your message and your latest diabetes panel (HbA1c 7.8%, eGFR 64) to Dr. Sarah Wong's review queue so she can assess your cough and discuss your Metformin plan together. "
            "Would you prefer an in-clinic slot today at 15:00 or tomorrow morning?"
        )
        reply_zh = (
            "多謝你講清楚——聽到你完全冇氣促、冇心口痛亦都冇發燒就放心好多。"
            "我已經將你嘅情況連同最新糖尿病報告（HbA1c 7.8%，eGFR 64）整理好交畀黃莎拉醫生，等佢覆診時一併檢查你嘅乾咳同評估 Metformin 劑量調整。"
            "你想約今日下午 3:00 定係聽朝嚟診所覆診？"
        )
    return (
        "needs_decision",
        "with_doctor",
        title_str,
        lead_str,
        "Review slot pending",
        summary,
        reply_zh if is_zh else reply_en,
        steps,
    )


_MD_EMPHASIS_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)


def _plain_chat_text(text: str) -> str:
    """Patient bubbles are plain-text (WhatsApp style): strip markdown emphasis and awkward exclamation marks."""
    cleaned = _MD_EMPHASIS_RE.sub(lambda m: m.group(1) or m.group(2) or "", text or "")
    cleaned = re.sub(r"!+", ".", cleaned)
    cleaned = re.sub(r"！+", "。", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"。{2,}", "。", cleaned)
    return cleaned.strip()


def handle_live_chat_turn(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str,
    message_text: str,
    live_bus: LiveHarnessBus,
    lang: str = "en",
    is_preset: bool = False,
) -> dict[str, Any]:
    """Process a patient chat turn: defined presets run 100% deterministically; free text invokes live LLM with deterministic pre-gates."""
    t_turn_start = time.perf_counter()
    text = (message_text or "").strip()
    if not text:
        raise ValueError("Message text cannot be empty")

    j = resolve_journey(journey_key_or_pid)
    j_key = j["key"]
    pid = j["patient_id"]
    is_zh = _is_zh(text, lang)
    use_deterministic_preset = bool(is_preset or _is_defined_preset_message(j, text))
    engine = (
        {
            "live": False,
            "provider": "deterministic",
            "model": "clinic-harness-preset-v1",
            "region": "local",
        }
        if use_deterministic_preset
        else detect_live_llm_engine()
    )
    live_demo_coordinator.mark_phone_active(j_key)

    # Ensure journey exists first
    with db.tenant_session(tenant_id) as session:
        existing_case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
        )
    if existing_case is None:
        start_live_journey(db, tenant_id=tenant_id, journey_key_or_pid=j_key, live_bus=live_bus, lang=lang)

    live_bus.update_patient_state(pid, active_agent="SafetySentinel")
    live_bus.publish("agent.step", patient_id=pid, data={"agent": "SafetySentinel", "action": "Running deterministic bilingual red-flag & injection screen"})

    with db.tenant_session(tenant_id) as session:
        graph = get_effective_practice_graph(session, tenant_id)
        case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
        )
        pat = session.get(Patient, pid)
        if case is None:
            raise ValueError(f"Case for journey '{j_key}' not found")

        hhmm = _hkt_hhmm()
        summary = copy.deepcopy(case.summary_json or {})
        chat = list(summary.get("original_chat") or [])
        chat.append({"actor": j["patient_name"], "time": hhmm, "text": text, "lang": "zh-HK" if is_zh else "en"})
        summary["original_chat"] = chat
        session.add(
            Message(
                id=new_id("msg"),
                tenant_id=tenant_id,
                patient_id=pid,
                direction="in",
                sender_role="patient",
                body_text=text,
            )
        )

        # 1. Deterministic Pre-Gate: Triage (999 red flags + Cantonese hyperbole negation + prompt injection)
        triage_out = classify_bilingual_async_triage(
            message_text=text,
            hours_since_last_visit=18 if j_key == "chan" else 200,
            patient_g6pd=False if j_key == "wingyan" else (bool(pat.g6pd_deficient) if pat else (j_key == "tang_uti")),
        )
        inj_signals = sorted(
            set(triage_out.get("prompt_injection_signals") or [])
            | set(detect_prompt_injection_signals(text))
        )
        prompt_inj = bool(triage_out.get("prompt_injection_detected") or inj_signals)

        live_bus.append_tool_call(
            pid,
            {
                "agent": "SafetySentinel",
                "tool": "safety.screen_red_flags",
                "authority": "CDS",
                "emr_target": "triage",
                "summary": f"Tier {triage_out['tier']} ({triage_out['route']})" + (" · prompt injection fenced" if prompt_inj else " · clear"),
            },
        )

        timeline = list(case.timeline_json or [])

        # Case 1a: Deterministic 999 Emergency Exit
        if triage_out.get("route") == "urgent_exit_999":
            crisis_payload = {
                "active": True,
                "emergency_number": "999",
                "title_en": "Emergency Symptoms Detected — Call 999 Immediately",
                "title_zh": "偵測到緊急危重症狀 — 請立即致電 999",
                "body_en": (
                    "Your message mentions urgent warning symptoms (such as chest pain, severe shortness of breath, or collapse). "
                    "Please call Hong Kong Emergency Services on 999 or go to the nearest A&E department immediately. "
                    "Do not wait for an online clinic reply."
                ),
                "body_zh": (
                    "你嘅訊息提及緊急危重症狀（例如心口劇痛、唞唔到氣或接近暈倒）。"
                    "請立即致電香港緊急求助熱線 999 或前往最近急症室，切勿等候診所網上回覆。"
                ),
            }
            reply = crisis_payload["body_zh"] if is_zh else crisis_payload["body_en"]
            chat.append({"actor": "Sam", "time": hhmm, "text": _plain_chat_text(reply), "lang": "zh-HK" if is_zh else "en", "is_crisis": True})
            summary["original_chat"] = chat
            summary["_active_crisis"] = crisis_payload
            alerts = list(summary.get("alerts") or [])
            alerts.insert(0, {"level": "high", "label": "999 Red-Flag Exit Triggered", "detail": f"Patient message triggered deterministic HK 999 safety floor ({hhmm})"})
            summary["alerts"] = alerts
            timeline.append({"date": f"Today {hhmm}", "label": "SafetySentinel · Deterministic 999 red-flag exit triggered (pre-LLM).", "agent": "SafetySentinel"})

            case.state = "needs_decision"
            case.baton = "with_doctor"
            case.risk_tier = "high"
            case.ai_title = f"URGENT 999 Exit — {j['patient_name']}"
            case.lead_line = "Deterministic pre-LLM red-flag gate triggered HK 999 instruction; clinician notified for safety follow-up."
            case.next_checkpoint_label = "URGENT · 999 advised"
            case.summary_json = summary
            case.timeline_json = timeline
            flag_modified(case, "summary_json")
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
            session.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=pid, direction="out", sender_role="assistant", body_text=reply))
            session.flush()
            case_id = case.id

            live_bus.publish("case.updated", patient_id=pid, data={"case_id": case_id, "journey_key": j_key, "state": "needs_decision", "crisis": True})
            live_bus.update_patient_state(pid, active_agent=None)
            return {
                "ok": True,
                "session_epoch": live_demo_coordinator.epoch,
                "case_id": case_id,
                "case_state": "needs_decision",
                "reply": reply,
                "messages": chat,
                "crisis": crisis_payload,
                "prompt_injection_detected": prompt_inj,
                "suggested_replies": j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"],
            }

        # Case 1b: Prompt Injection Contained (Zero Tool Escalation)
        if prompt_inj:
            reply = (
                "我係診所護理助手 Sam，唔可以繞過診所安全規則，亦唔可以代醫生開藥、簽發病假紙或修改病歷。"
                "如果你有身體唔舒服，請直接話我知你嘅症狀，我會整理好交畀醫生審閱。"
                if is_zh
                else "I'm Sam, the clinic's care assistant — I can't bypass clinic safety rules, prescribe medications, issue certificates, or modify the EMR without a doctor's sign-off. If you're feeling unwell, tell me your symptoms and I'll prepare a clean summary for Dr. Sarah Wong."
            )
            chat.append({"actor": "Sam", "time": hhmm, "text": _plain_chat_text(reply), "lang": "zh-HK" if is_zh else "en"})
            summary["original_chat"] = chat
            timeline.append({"date": f"Today {hhmm}", "label": f"SecurityGuard · Prompt injection neutralized ({', '.join(inj_signals[:3])}) — zero tools exposed.", "agent": "SecurityGuard"})
            case.summary_json = summary
            case.timeline_json = timeline
            flag_modified(case, "summary_json")
            flag_modified(case, "timeline_json")
            case.updated_at = utcnow()
            session.add(Message(id=new_id("msg"), tenant_id=tenant_id, patient_id=pid, direction="out", sender_role="assistant", body_text=reply))
            session.flush()
            case_id = case.id
            live_bus.append_tool_call(pid, {"agent": "SecurityGuard", "tool": "prompt_injection.fence", "authority": "BLOCKED", "emr_target": "guard", "summary": f"Blocked injection signals: {', '.join(inj_signals[:3])}"})
            live_bus.publish("case.updated", patient_id=pid, data={"case_id": case_id, "journey_key": j_key, "state": case.state})
            live_bus.update_patient_state(pid, active_agent=None)
            return {
                "ok": True,
                "session_epoch": live_demo_coordinator.epoch,
                "case_id": case_id,
                "case_state": case.state,
                "reply": reply,
                "messages": chat,
                "crisis": None,
                "prompt_injection_detected": True,
                "suggested_replies": j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"],
            }

        # 2. Deterministic Rx Safety Check (`rx_rules.py`) for any drugs mentioned in chat
        asked_drugs = _extract_asked_drugs(text)
        cds_blocked: list[str] = []
        cds_warnings: list[str] = []
        if asked_drugs and pat is not None:
            live_bus.update_patient_state(pid, active_agent="ProtocolCDSAgent")
            rx_items = [{"drug": d, "dose_mg": 300 if d == "aspirin" else 400, "freq": "PRN"} for d in asked_drugs]
            cds_report = check_prescription_safety(
                {
                    "dob": pat.dob,
                    "weight_kg": pat.weight_kg,
                    "egfr": pat.egfr,
                    "g6pd_deficient": bool(pat.g6pd_deficient),
                    "allergies": list(pat.allergies_json or []),
                    "current_meds": list(pat.current_meds_json or []),
                },
                rx_items,
            )
            cds_res = cds_report.to_dict()
            for f in cds_res.get("findings") or []:
                sev = str(f.get("severity") or "")
                drug_hit = str(f.get("drug") or "").lower()
                if sev in ("hard_block", "override_required") and drug_hit:
                    cds_blocked.append(drug_hit)
                if f.get("message"):
                    cds_warnings.append(str(f["message"]))
            live_bus.append_tool_call(
                pid,
                {
                    "agent": "ProtocolCDSAgent",
                    "tool": "cds.check_medication_safety",
                    "authority": "CDS",
                    "emr_target": "cds",
                    "summary": f"Checked {', '.join(asked_drugs)} -> {cds_res.get('status', 'pass')}",
                },
            )
            timeline.append({
                "date": f"Today {hhmm}",
                "label": f"ProtocolCDSAgent · Checked {', '.join(asked_drugs)} against chart ({cds_res.get('status', 'pass')}).",
                "agent": "ProtocolCDSAgent",
            })

        # 3. Compile Clinical Workspace Update (5/5 Linter Verified!)
        signed = _build_signed_plan_snapshot(session, tenant_id, pid)
        has_signed = bool(signed) or any(bool(m.get("care_plan")) for m in chat)
        has_day3 = any(m.get("role") == "system" and m.get("time") == "Day +3" for m in chat)
        live_bus.update_patient_state(pid, active_agent="PresentationCompiler")
        new_state, new_baton, ai_title, lead_line, next_cp, updated_summary, fallback_reply, agent_steps = _compile_journey_turn_update(
            session=session,
            tenant_id=tenant_id,
            j_key=j_key,
            patient_text=text,
            existing_summary=summary,
            cds_blocked_drugs=cds_blocked,
            cds_warnings=cds_warnings,
            triage_out=triage_out,
            is_zh=is_zh,
            has_signed=has_signed,
            current_state=case.state or "running",
            current_baton=case.baton or "with_patient",
            current_title=case.ai_title or "",
            current_lead=case.lead_line or "",
            current_cp=case.next_checkpoint_label or "Patient messaging Sam",
        )

        chart_facts = (
            f"G6PD={bool(pat.g6pd_deficient)}, Allergies={list(pat.allergies_json or [])}, "
            f"Meds={list(pat.current_meds_json or [])}, eGFR={pat.egfr}"
            if (pat is not None and j_key != "wingyan")
            else "New/minimal intake (no prior chart alerts)"
        )
        availability_roster = build_clinic_availability(session, tenant_id, for_case_key=j_key)
        availability_prompt_line = format_availability_for_prompt(availability_roster)
        case_id = case.id
        session.flush()

    # 4. Live Autonomous AI Registrar — invoked ONLY on custom free-text messages (defined demo presets run 100% deterministically).
    reply = fallback_reply
    live_reply_used = False
    llm_offer_booking = False
    llm_provider_used = "deterministic" if use_deterministic_preset else str(engine.get("provider") or "deterministic")
    llm_model_used = "clinic-harness-preset-v1" if use_deterministic_preset else str(engine.get("model") or "clinic-harness-tool-engine-v1")
    llm_region_used = "local" if use_deterministic_preset else str(engine.get("region") or "local")
    llm_latency_ms = 0
    llm_attempts = 0
    llm_fallback_used = False
    llm_prompt_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_thought_tokens: int | None = None
    llm_error: str | None = None
    dynamic_suggested_replies: list[str] = []

    if engine.get("live") and not use_deterministic_preset:
        lang_directive = (
            "Write patient_reply warmly in natural HK Traditional Chinese/Cantonese (繁體中文)."
            if is_zh
            else "Write patient_reply warmly in clear, natural English."
        )
        recent_turns = "\n".join(
            f"- {m.get('actor', 'User')}: {str(m.get('text') or '')[:180]}"
            for m in chat[-6:]
        )
        if has_day3:
            phase_desc = (
                "PHASE 4 (Day+3 post-sign): Doctor already signed 3d ago. "
                "escalate_to_doctor for worsening/new symptoms or sick-leave/Rx changes (doctor-only); "
                "route_to_admin ONLY for non-clinical booking/vaccine; close_quietly if recovered."
            )
        elif has_signed:
            signed_summary = ""
            if signed:
                rx_s = "; ".join(signed.get("rx_lines") or [])
                cert_s = f"{len(signed.get('certificates') or [])} cert(s)" if signed.get("certificates") else ""
                signed_summary = f" Signed: {', '.join(x for x in (rx_s, cert_s) if x)}."
            phase_desc = (
                f"PHASE 3 (Post-sign active): Dr. Sarah Wong already signed.{signed_summary} "
                "MCHK RULE: extra sick-leave days, cert/Rx changes, or worsening symptoms MUST use escalate_to_doctor "
                "(only Dr. Sarah Wong can approve; NEVER route sick-leave extensions to admin; NEVER say 'feeling better' unless they said so). "
                "route_to_admin ONLY for non-clinical booking/logistics; close_quietly for simple dosing/recovery Qs."
            )
        elif new_state == "needs_decision":
            phase_desc = (
                "PHASE 2 (Staged for doctor): escalate_to_doctor if patient added clinical updates/needs doctor decision; "
                "keep_in_patient_loop if critical intake details are still missing."
            )
        else:
            phase_desc = (
                "PHASE 1 (Intake loop, doctor not yet interrupted): keep_in_patient_loop while gathering missing onset/fever/red-flags/visit intent "
                "(never re-ask stated facts); route_to_admin for pure scheduling/vaccines; escalate_to_doctor once ready for Dr. Sarah Wong."
            )

        sys_prompt = (
            "You are Sam, Clinic Careline AI Registrar at Central & Mid-Levels Family Medical (HK). "
            "Strict Asymmetric Authority: gather history, extract vitals, check live doctor availability & offer bookings, route admin tasks, or stage briefs for our doctors; "
            "NEVER prescribe, issue/extend sick-leave, sign, diagnose definitively, or promise reply SLAs.\n"
            "Return STRICT JSON ONLY:\n"
            '{"patient_reply":str,"clinical_reasoning":str,"registrar_action":"keep_in_patient_loop"|"escalate_to_doctor"|"route_to_admin"|"close_quietly",'
            '"offer_booking":bool,"extracted_vitals":[{"label":str,"val":str}],"issue_summary_update":str,"admin_task":str,"suggested_patient_replies":[str,str]}\n'
            "Rules:\n"
            "- patient_reply: 1-2 crisp, calm, empathetic sentences answering what they JUST wrote for the current phase; "
            "NEVER use exclamation marks (! or ！) — use plain periods and question marks; "
            "never re-ask already stated facts; if confused ('whaat?'), apologize & clarify; never say 'feeling better' unless stated; "
            "when suggesting a visit or appointment, mention an exact open doctor & time from Live open slots and set offer_booking=true so the booking card appears below; "
            "NEVER mention internal alerts/jargon (G6PD, haemolysis, nitrofurantoin, drug-class mechanisms) to patient (keep in clinical_reasoning); "
            "never claim to be a doctor or repeat a prior reply.\n"
            "- clinical_reasoning: 1 terse clinical line (<=20 words) for doctor (phase, safety, why action); never write chat-formatting meta-commentary (never say 'patient chat kept simple and jargon-free').\n"
            "- issue_summary_update: <=15 words (or \"\" if unchanged); admin_task: \"\" unless route_to_admin.\n"
            "- suggested_patient_replies: 0-2 short calm chips (<=65 chars, same language) replying ONLY to topics explicitly mentioned in patient_reply.\n"
            f"{lang_directive}"
        )
        user_prompt = (
            f"Phase: {phase_desc}\n"
            f"Patient: {j['patient_name']} ({j['age_sex']})\n"
            f"{availability_prompt_line}\n"
            f"Chart (clinician-only): {chart_facts}\n"
            f"CDS (clinician-only): {cds_warnings or 'None'}\n"
            f"Chat:\n{recent_turns}\n\n"
            f"Latest: {text}"
        )
        chat_engine = dict(engine)
        if chat_engine.get("provider") in ("gemini", "vertex"):
            chat_engine["model"] = resolve_fast_chat_model(chat_engine)
        llm_call = invoke_structured_json(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            temperature=0.25,
            max_output_tokens=760,
            patient_names_for_scrub=[j["patient_name"]],
            engine_override=chat_engine,
            timeout_seconds=20.0,
            thinking_level="LOW",
        )
        llm_provider_used = llm_call.provider
        llm_model_used = llm_call.model
        llm_region_used = llm_call.region
        llm_latency_ms = llm_call.latency_ms
        llm_attempts = llm_call.attempts_tried
        llm_fallback_used = llm_call.fallback_used
        llm_prompt_tokens = llm_call.prompt_tokens
        llm_output_tokens = llm_call.output_tokens
        llm_thought_tokens = llm_call.thought_tokens
        if llm_call.ok and isinstance(llm_call.json_payload, dict) and llm_call.json_payload.get("patient_reply"):
            obj = llm_call.json_payload
            parsed_reply = str(obj["patient_reply"]).strip()
            parsed_reasoning = str(obj.get("clinical_reasoning") or "").strip()
            reg_action = str(obj.get("registrar_action") or "").strip().lower()
            llm_offer_booking = bool(obj.get("offer_booking"))
            extracted_vitals: list[dict[str, str]] = []
            if isinstance(obj.get("extracted_vitals"), list):
                extracted_vitals = [
                    {"label": str(v.get("label", "")).strip(), "val": str(v.get("val", "")).strip()}
                    for v in obj["extracted_vitals"]
                    if isinstance(v, dict) and v.get("label") and v.get("val")
                ][:4]
            issue_update = str(obj.get("issue_summary_update") or "").strip()
            admin_task = str(obj.get("admin_task") or "").strip()

            raw_sug_list = obj.get("suggested_patient_replies")
            if not isinstance(raw_sug_list, list):
                raw_sug_list = obj.get("dynamic_suggested_replies")
            if isinstance(raw_sug_list, list):
                already_sent = {
                    str(m.get("text") or "").strip().lower()
                    for m in chat
                    if m.get("actor") not in ("Sam", "Assistant", "Clinic")
                }
                seen_sug: set[str] = set()
                for raw_s in raw_sug_list:
                    if not isinstance(raw_s, str):
                        continue
                    cand_s = _plain_chat_text(raw_s).strip()
                    low_s = cand_s.lower()
                    if not cand_s or len(cand_s) > 140 or low_s in already_sent or low_s in seen_sug:
                        continue
                    if screen_acute_message(cand_s).kind is not None:
                        continue
                    seen_sug.add(low_s)
                    dynamic_suggested_replies.append(cand_s)
                    if len(dynamic_suggested_replies) >= 2:
                        break

            screened = screen_agent_output(parsed_reply, channel="patient_careline")
            if screened.passed and parsed_reply:
                reply = parsed_reply
                live_reply_used = True
            else:
                llm_error = f"output_screen_blocked:{[v.get('code') for v in screened.violations]}"
                logger.warning("Live registrar reply blocked by output screen (%s); using fallback", llm_error)

            if dynamic_suggested_replies and live_reply_used:
                updated_summary["_dynamic_suggested_replies"] = {
                    "lang": "zh-HK" if is_zh else "en",
                    "replies": dynamic_suggested_replies,
                }
            elif live_reply_used:
                updated_summary.pop("_dynamic_suggested_replies", None)

            # Execute the live registrar's chosen harness action (never weakening CDS safety blocks or MCHK doctor escalation!)
            if reg_action in ("keep_in_patient_loop", "escalate_to_doctor", "route_to_admin", "close_quietly"):
                if reg_action == "keep_in_patient_loop" and not cds_blocked and not has_signed:
                    new_state = "running"
                    new_baton = "with_patient"
                    next_cp = "Registrar intake"
                elif reg_action == "escalate_to_doctor":
                    new_state = "needs_decision"
                    new_baton = "with_doctor"
                    next_cp = "Ready for doctor review"
                elif reg_action == "route_to_admin" and not cds_blocked and new_state != "needs_decision":
                    new_state = "scheduled"
                    new_baton = "idle"
                    next_cp = "Admin / Nurse handoff"
                    ah_list = list(updated_summary.get("admin_handoffs") or [])
                    task_desc = admin_task or f"Autonomous request from {j['patient_name']}: “{text.strip()}” — coordinate with Clinic Admin / Nurse (doctor kept out of loop)."
                    if not any(task_desc[:40] in str(x.get("task", "")) for x in ah_list):
                        ah_list.append({
                            "id": new_id("adm"),
                            "route": f"Sam ({llm_model_used} Registrar) → Clinic Admin / Nurse Rachel",
                            "task": task_desc,
                            "status": "pending_admin",
                            "created_at": utcnow().isoformat(),
                        })
                        updated_summary["admin_handoffs"] = ah_list
                elif reg_action == "close_quietly" and not cds_blocked and new_state != "needs_decision":
                    new_state = "scheduled"
                    new_baton = "idle"
                    next_cp = "Quiet monitoring"

                live_bus.append_tool_call(
                    pid,
                    {
                        "agent": "CareAgent",
                        "tool": f"registrar.{reg_action}",
                        "authority": "DRAFT",
                        "emr_target": "clinic_team" if reg_action == "route_to_admin" else "workspace",
                        "summary": f"{llm_model_used} ({llm_provider_used}/{llm_region_used}) chose {reg_action}" + (f" · {len(extracted_vitals)} vitals" if extracted_vitals else ""),
                    },
                )
                agent_steps.append(f"RegistrarAgent ({llm_model_used}) · Executed {reg_action}")

            # Merge dynamically extracted home vitals into Physical Exam & Vitals card
            if extracted_vitals:
                ve = dict(updated_summary.get("vitals_exam") or {})
                ve["source"] = f"Patient Careline · Today {hhmm}"
                ve.setdefault("encounter_context", "Post-Care Follow-Up" if has_signed else "Inbound Remote · Self-Measured")
                existing_items = [it for it in (ve.get("items") or []) if isinstance(it, dict)]
                existing_labels = {str(it.get("label", "")).lower() for it in existing_items}
                for ev in extracted_vitals:
                    if ev["label"].lower() not in existing_labels:
                        existing_items.insert(0, ev)
                    else:
                        for idx_it, it in enumerate(existing_items):
                            if str(it.get("label", "")).lower() == ev["label"].lower():
                                existing_items[idx_it] = ev
                ve["items"] = existing_items[:5]
                updated_summary["vitals_exam"] = ve

            # Update lead line / issue summary with live registrar synthesis
            if issue_update:
                lead_line = issue_update[:220]

            if parsed_reasoning:
                at = list(updated_summary.get("agent_thread") or [])
                at.append({
                    "actor": f"Sam · {llm_model_used} Registrar",
                    "time": hhmm,
                    "text": parsed_reasoning,
                })
                updated_summary["agent_thread"] = at[-5:]
        else:
            llm_error = llm_call.error or "missing_patient_reply"
            logger.warning(
                "Live LLM fallback on journey=%s provider=%s region=%s model=%s error=%s",
                j_key,
                llm_call.provider,
                llm_call.region,
                llm_call.model,
                llm_error,
            )
            live_bus.append_tool_call(
                pid,
                {
                    "agent": "CareAgent",
                    "tool": "llm.fallback",
                    "authority": "READ",
                    "emr_target": "diagnostics",
                    "summary": f"Live LLM failed ({llm_call.provider}/{llm_call.region}/{llm_call.model}: {llm_error[:140]}) -> deterministic registrar used",
                },
            )

    # Anti-repetition guard: never repeat any of the last 4 Sam replies verbatim
    recent_sam_texts = [
        str(m.get("text") or "").strip().lower()
        for m in reversed(chat)
        if m.get("actor") in ("Sam", "Assistant") and m.get("text")
    ][:4]
    if _plain_chat_text(reply).strip().lower() in recent_sam_texts:
        reply = (
            "收到你嘅補充——我已經記錄落去。如果你係想調整病假紙、問用藥，或者想預約覆診，直接話我知我即刻幫你處理。"
            if is_zh
            else "Got it — I've noted that update. If you'd like to request a sick-leave extension, ask about your medications, or arrange a follow-up, just let me know and I'll handle or route it right away."
        )

    reply = _plain_chat_text(reply)
    chat = list(updated_summary.get("original_chat") or [])
    attach_booking = should_attach_booking_offer(
        j_key=j_key,
        patient_text=text,
        existing_summary=updated_summary,
        new_state=new_state,
        has_signed=has_signed,
        llm_requested_booking=llm_offer_booking,
    )
    booking_offer: dict[str, Any] | None = None
    bilingual_pair = updated_summary.pop("_last_reply_bilingual", None)
    sam_msg: dict[str, Any] = {
        "actor": "Sam",
        "time": hhmm,
        "text": reply,
        "lang": "zh-HK" if is_zh else "en",
    }
    if isinstance(bilingual_pair, (list, tuple)) and len(bilingual_pair) == 2:
        sam_msg["text_en"] = _plain_chat_text(str(bilingual_pair[0]))
        sam_msg["text_zh"] = _plain_chat_text(str(bilingual_pair[1]))

    # Attach bilingual text_en/text_zh to the latest patient message if it matches a bilingual preset pair
    if chat and isinstance(chat[-1], dict) and chat[-1].get("actor") not in ("Sam", "Clinic", "System"):
        last_pt = chat[-1]
        norm_pt = str(last_pt.get("text") or "").strip().lower()
        preset_pairs: list[tuple[list[str], list[str]]] = [
            (list(j.get("suggested_replies_en") or []), list(j.get("suggested_replies_zh") or [])),
            (_POST_SIGN_CHAN_REPLIES_EN, _POST_SIGN_CHAN_REPLIES_ZH),
            (_POST_SIGN_REPLIES_EN, _POST_SIGN_REPLIES_ZH),
            (_DAY3_REPLIES_EN, _DAY3_REPLIES_ZH),
            (_DAY3_CONCERN_REPLIES_EN, _DAY3_CONCERN_REPLIES_ZH),
        ]
        for en_list, zh_list in preset_pairs:
            for en_s, zh_s in zip(en_list, zh_list):
                if norm_pt in (en_s.strip().lower(), zh_s.strip().lower()):
                    last_pt["text_en"] = en_s
                    last_pt["text_zh"] = zh_s
                    break
            if "text_en" in last_pt:
                break

    if not (live_reply_used and dynamic_suggested_replies):
        updated_summary.pop("_dynamic_suggested_replies", None)
    updated_summary.pop("_active_crisis", None)

    lint = validate_case_presentation(updated_summary)

    with db.tenant_session(tenant_id) as session:
        case = session.get(CaseLoop, case_id)
        if case is None:
            case = session.scalar(
                select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
            )
        if case is None:
            raise ValueError(f"Case for journey '{j_key}' not found")

        is_preventive_handoff = bool(updated_summary.pop("_attach_preventive_booking", False))
        append_closed_pill = bool(updated_summary.pop("_append_case_closed_pill", False))

        if attach_booking:
            booking_offer = select_booking_offer(
                session,
                tenant_id,
                j_key=j_key,
                patient_text=text,
                purpose="preventive_checkup" if is_preventive_handoff else "standard",
            )
            sam_msg["booking_offer"] = booking_offer
            rec = booking_offer["recommended"]
            agent_steps.append(
                f"SchedulingAgent · Checked {booking_offer['checked_doctors_count']} clinicians' availability "
                f"({booking_offer['open_today_count']} open slots today) → offered {rec['doctor_name']} ({rec['short_day_en']} {rec['time']})"
            )

        chat.append(sam_msg)
        if append_closed_pill:
            chat.append({
                "actor": "Clinic",
                "role": "system",
                "time": hhmm,
                "text": (
                    "個案已關閉 · 預防檢查及抽血已交由陳護士及診所團隊跟進（無需打擾醫生）"
                    if is_zh
                    else "Case closed · Preventive checkup & blood panel handed off to Nurse Rachel & clinic team (no doctor interruption)"
                ),
            })
        updated_summary["original_chat"] = chat

        for st_label in agent_steps:
            timeline.append({"date": f"Today {hhmm}", "label": st_label, "agent": st_label.split("·")[0].strip()})

        case.state = new_state
        case.baton = new_baton
        case.ai_title = ai_title
        case.lead_line = lead_line
        case.next_checkpoint_label = next_cp
        case.timeline_json = timeline

        # Refresh <4,096 token 5-slot ContextSnapshot for Architecture X-Ray
        mems = session.scalars(
            select(AgentMemoryEntry).where(AgentMemoryEntry.tenant_id == tenant_id, AgentMemoryEntry.patient_id == pid).limit(6)
        ).all()
        mem_dicts = [{"tier": m.tier, "topic": m.topic, "summary": m.summary_text, "source_quote": m.source_quote} for m in mems]
        _, ctx_tel = _compile_5slot_context_snapshot(
            graph=graph,
            case=case,
            memories=mem_dicts,
            engine=engine,
            current_input=text,
            channel="patient_phone",
        )
        updated_summary["_context_snapshot"] = ctx_tel
        updated_summary["_compilation_telemetry"] = {
            "provider": llm_provider_used if live_reply_used else engine.get("provider", "deterministic"),
            "model": llm_model_used if live_reply_used else engine.get("model", "clinic-harness-tool-engine-v1"),
            "region": llm_region_used if live_reply_used else engine.get("region", "local"),
            "live_model_invoked": live_reply_used,
            "llm_error": llm_error,
            "total_tokens_est": ctx_tel.get("total_tokens_est", 0),
            "token_budget_ceiling": ctx_tel.get("token_budget_ceiling", 4096),
            "within_budget": ctx_tel.get("within_budget", True),
            "linter_passed": bool(lint["passed"]),
            "rules_passed": lint["rules_passed"],
            "rules_total": lint["rules_total"],
            "context_window": ctx_tel,
        }
        case.summary_json = updated_summary
        flag_modified(case, "summary_json")
        flag_modified(case, "timeline_json")
        case.updated_at = utcnow()

        session.add(
            Message(
                id=new_id("msg"),
                tenant_id=tenant_id,
                patient_id=pid,
                direction="out",
                sender_role="assistant",
                body_text=reply,
            )
        )
        session.flush()
        case_id = case.id

    if booking_offer:
        rec = booking_offer["recommended"]
        live_bus.append_tool_call(
            pid,
            {
                "agent": "SchedulingAgent",
                "tool": "scheduling.check_availability",
                "authority": "READ",
                "emr_target": "clinic_schedule",
                "summary": (
                    f"Checked {booking_offer['checked_doctors_count']} doctors "
                    f"({booking_offer['open_today_count']} open slots today) → selected {rec['doctor_name']} · {rec['short_day_en']} {rec['time']}"
                ),
            },
        )

    live_bus.append_tool_call(
        pid,
        {
            "agent": "PresentationCompiler",
            "tool": "workspace.compile_case_presentation",
            "authority": "DRAFT",
            "emr_target": "workspace",
            "summary": f"Compiled {j['patient_name']} -> {new_state} (linter {lint['rules_passed']}/{lint['rules_total']}, {ctx_tel.get('total_tokens_est', 0)}/4096 tok)",
        },
    )
    live_bus.set_context_window(pid, ctx_tel)
    live_bus.publish(
        "case.updated",
        patient_id=pid,
        data={"case_id": case_id, "journey_key": j_key, "state": new_state, "baton": new_baton},
    )
    live_bus.update_patient_state(pid, active_agent=None, workflow_stage="intake_submitted")

    total_turn_ms = max(1, int((time.perf_counter() - t_turn_start) * 1000))
    infra_overhead_ms = max(0, total_turn_ms - llm_latency_ms)

    return {
        "ok": True,
        "session_epoch": live_demo_coordinator.epoch,
        "case_id": case_id,
        "case_state": new_state,
        "baton": new_baton,
        "reply": reply,
        "messages": chat,
        "signed_plan": signed,
        "booking_offer": booking_offer,
        "booked_appointment": updated_summary.get("booked_appointment"),
        "crisis": None,
        "prompt_injection_detected": False,
        "lint_report": lint,
        "llm_meta": {
            "mode": "deterministic_preset" if use_deterministic_preset else ("live_llm" if live_reply_used else "deterministic_fallback"),
            "live_reply_used": live_reply_used,
            "provider": llm_provider_used,
            "model": llm_model_used,
            "region": llm_region_used,
            "latency_ms": llm_latency_ms,
            "total_turn_ms": total_turn_ms,
            "infra_overhead_ms": infra_overhead_ms,
            "attempts_tried": llm_attempts,
            "fallback_used": llm_fallback_used,
            "prompt_tokens": llm_prompt_tokens,
            "output_tokens": llm_output_tokens,
            "thought_tokens": llm_thought_tokens,
            "error": llm_error,
        },
        "suggested_replies": _suggested_replies_for(
            j,
            is_zh,
            has_signed=has_signed,
            has_day3=any(m.get("role") == "system" and m.get("time") == "Day +3" for m in chat),
            dynamic_replies=dynamic_suggested_replies if live_reply_used else None,
            has_patient_turns=True,
            case_state=new_state,
            summary=updated_summary,
        ),
    }


def get_live_clinic_availability(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str | None = None,
) -> dict[str, Any]:
    """Return live clinic doctor availability matrix and recommended booking pick."""
    j_key = resolve_journey(journey_key_or_pid)["key"] if journey_key_or_pid else None
    return get_live_availability_payload(db, tenant_id=tenant_id, journey_key=j_key)


def book_live_chat_slot(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str,
    doctor_id: str,
    slot_id: str,
    modality: str = "in_clinic",
    offer_id: str | None = None,
    lang: str = "en",
    live_bus: LiveHarnessBus,
) -> dict[str, Any]:
    """Confirm or reschedule an appointment offered by Sam in the live patient chat."""
    j = resolve_journey(journey_key_or_pid)
    j_key = j["key"]
    pid = j["patient_id"]
    with db.tenant_session(tenant_id) as session:
        existing_case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
        )
    if existing_case is None:
        start_live_journey(db, tenant_id=tenant_id, journey_key_or_pid=j_key, live_bus=live_bus, lang=lang)

    return confirm_live_chat_booking(
        db,
        tenant_id=tenant_id,
        journey_key=j_key,
        patient_id=pid,
        patient_name=j["patient_name"],
        doctor_id=doctor_id,
        slot_id=slot_id,
        modality=modality,
        offer_id=offer_id,
        lang=lang,
        live_bus=live_bus,
        hhmm=_hkt_hhmm(),
        session_epoch=live_demo_coordinator.epoch,
    )


def append_doctor_signed_chat_bubble(
    session: Session,
    *,
    case_id: str,
    signed_by: str,
    mchk_reg_no: str,
    prescriptions_out: list[dict[str, Any]],
    certificates_out: list[dict[str, Any]],
    bookings_out: list[dict[str, Any]],
    trace_id: str,
    nurse_handover_only: bool = False,
    nurse_assignee: str = "Nurse Rachel (RN)",
    signed_orders: list[dict[str, Any]] | None = None,
) -> None:
    """Append a clinically accurate status event + Sam's message into `CaseLoop.summary_json['original_chat']`.

    - When `nurse_handover_only=True`: notifies patient that Nurse Rachel will perform vitals/exam first
      (NEVER claims clinical review is complete and NEVER attaches sick leave or prescriptions).
    - Otherwise: delivers full structured e-Prescription details (dose, route, frequency, duration, quantity,
      dispensing details, PDF download ID), MCHK Sick-Leave PDF, explicit confirmation of signed lab/imaging/
      preventive orders, and (for Case 4 / post-tests reviews) a follow-up booking flow with Dr. Sarah Wong.
    """
    case = session.get(CaseLoop, case_id)
    if case is None:
        return
    summary = copy.deepcopy(case.summary_json or {})
    chat = list(summary.get("original_chat") or [])
    hhmm = _hkt_hhmm()
    display_doc = re.sub(r"^Locum\s+", "", signed_by or "Dr. Sarah Wong", flags=re.I).strip()

    if nurse_handover_only and not prescriptions_out and not certificates_out:
        eg = dict(summary.get("exam_gate") or {"exam_required": True})
        eg["nurse_exam_status"] = "handed_over"
        summary["exam_gate"] = eg
        case.state = "running"
        case.baton = "with_nurse"
        case.next_checkpoint_label = f"Nurse exam ({nurse_assignee})"
        chat.append({
            "actor": "Clinic",
            "role": "system",
            "time": hhmm,
            "text": f"Handed over to {nurse_assignee} for vitals and physical examination",
            "trace_id": trace_id,
        })
        chat.append({
            "actor": "Sam",
            "role": "assistant",
            "time": hhmm,
            "text": (
                f"{display_doc} has asked {nurse_assignee} to check your vitals and carry out a brief assessment first "
                f"before your consultation with the doctor. Your prescription and sick-leave certificate will be confirmed once your examination is complete."
            ),
            "trace_id": trace_id,
        })
        summary["original_chat"] = chat
        case.summary_json = summary
        flag_modified(case, "summary_json")
        return

    rx_lines: list[str] = []
    structured_prescriptions: list[dict[str, Any]] = []
    for rx in prescriptions_out:
        enriched_items = [enrich_prescription_item_for_patient(it) for it in (rx.get("items") or [])]
        for it in enriched_items:
            rx_lines.append(
                f"{it['display']} — {it['dose_text']}, {it['route']}, {it['frequency']} ({it['frequency_human']}), {it['duration_text']} · Dispense {it['quantity_to_dispense']}"
            )
        structured_prescriptions.append({
            "id": rx["id"],
            "vault_doc_id": rx.get("vault_doc_id"),
            "verification_hash": rx.get("verification_hash"),
            "mchk_reg_no": rx.get("mchk_reg_no") or mchk_reg_no,
            "signed_by": display_doc,
            "signed_at": rx.get("signed_at"),
            "fulfilment_mode": rx.get("fulfilment_mode") or "pickup",
            "fulfilment_status": rx.get("fulfilment_status") or "signed",
            "fulfilment_ref": rx.get("fulfilment_ref") or f"RXHK-{str(rx['id'])[-6:].upper()}",
            "dispensing_location": rx.get("dispensing_location") or "Central & Mid-Levels Family Medical Dispensary, Suite 1402, Central Tower, 28 Queen's Road Central, HK",
            "dispensing_hours": rx.get("dispensing_hours") or "Mon–Sat 08:30–20:00 HKT",
            "dispensing_instructions": rx.get("dispensing_instructions") or "Present this signed e-Prescription PDF or Dispensing Ref with HKID at the clinic dispensary counter (valid 14 days under HK Cap. 138).",
            "items": enriched_items,
        })

    # Collect human-readable confirmations for lab, imaging, preventive, booking, and goal orders
    orders_list = list(signed_orders or [])
    confirmed_items_en: list[str] = []
    confirmed_items_zh: list[str] = []
    has_tests_or_prev = False
    for o in orders_list:
        otype = str(o.get("type") or "").lower()
        if otype == "lab":
            has_tests_or_prev = True
            tname = o.get("test_name") or o.get("testName") or "Blood / Lab Test"
            when = f" ({o.get('when')})" if o.get("when") else ""
            confirmed_items_en.append(f"{tname}{when}")
            confirmed_items_zh.append(f"{tname}{when}")
        elif otype == "imaging":
            has_tests_or_prev = True
            mod = o.get("modality") or "Imaging Referral"
            confirmed_items_en.append(f"{mod} referral")
            confirmed_items_zh.append(f"{mod} 轉介")
        elif otype == "book":
            has_tests_or_prev = True
            vt = o.get("visit_type") or o.get("visitType") or "Clinic Visit"
            confirmed_items_en.append(vt)
            confirmed_items_zh.append(vt)
        elif otype == "goal":
            metric = o.get("metric") or o.get("title") or ""
            if o.get("target_sys") and o.get("target_dia"):
                confirmed_items_en.append(f"Home BP target < {o['target_sys']}/{o['target_dia']} mmHg")
                confirmed_items_zh.append(f"家居血壓目標 < {o['target_sys']}/{o['target_dia']} mmHg")
            elif metric:
                confirmed_items_en.append(metric)
                confirmed_items_zh.append(metric)

    parts_en: list[str] = []
    parts_zh: list[str] = []
    if structured_prescriptions and certificates_out:
        parts_en.append("Your signed e-prescription and MCHK sick-leave certificate are ready below.")
        parts_zh.append("你的已簽署電子處方同香港註冊西醫病假紙已經準備好喺下方。")
    elif structured_prescriptions:
        parts_en.append("Your signed e-prescription is ready below with full dosing and dispensing details.")
        parts_zh.append("你的已簽署電子處方已經準備好喺下方，附有完整服法同配藥詳情。")
    elif certificates_out:
        parts_en.append("Your MCHK sick-leave certificate is ready below.")
        parts_zh.append("你的香港註冊西醫病假紙已經準備好喺下方。")
    else:
        parts_en.append("Your care plan has been confirmed below.")
        parts_zh.append("醫生已經確認你的護理計劃。")

    if confirmed_items_en:
        parts_en.append("Confirmed orders and preventive checks: " + "; ".join(confirmed_items_en) + ".")
        parts_zh.append("已確認檢查及預防項目：" + "；".join(confirmed_items_zh) + "。")
    elif bookings_out:
        parts_en.append(f"{len(bookings_out)} follow-up booking(s) queued.")
        parts_zh.append(f"已安排 {len(bookings_out)} 項跟進預約。")

    if not (case.case_key == "lau_chest" and has_tests_or_prev):
        parts_en.append("I will check in with you here in 3 days to see how you are recovering.")
        parts_zh.append("我會喺 3 日後主動喺呢度跟進你嘅康復情況。")

    eg = dict(summary.get("exam_gate") or {})
    exam_done = eg.get("doctor_exam_status") == "completed"
    divider_en = (
        f"{display_doc} completed your examination and signed your care plan"
        if exam_done
        else f"{display_doc} completed your clinical review"
    )
    divider_zh = (
        f"{display_doc} 已完成檢查並簽署你的護理計劃"
        if exam_done
        else f"{display_doc} 已完成臨床審閱"
    )
    chat.append({
        "actor": "Clinic",
        "role": "system",
        "time": hhmm,
        "text": divider_en,
        "text_en": divider_en,
        "text_zh": divider_zh,
        "trace_id": trace_id,
    })

    chat.append({
        "actor": "Sam",
        "role": "assistant",
        "time": hhmm,
        "text": " ".join(parts_en),
        "text_en": " ".join(parts_en),
        "text_zh": " ".join(parts_zh),
        "trace_id": trace_id,
        "care_plan": {
            "signed_by": display_doc,
            "mchk_reg_no": mchk_reg_no,
            "rx_lines": rx_lines,
            "prescriptions": structured_prescriptions,
            "certificates": [
                {
                    "id": c["id"],
                    "days_off": c["days_off"],
                    "start_date": c["start_date"],
                    "end_date": c["end_date"],
                    "omit_employer_diagnosis": bool(c.get("omit_employer_diagnosis", True)),
                }
                for c in certificates_out
            ],
        },
    })

    # For Case 4 (lau_chest) after tests/preventive orders are confirmed, start a post-tests follow-up booking flow with Dr. Sarah Wong
    already_booked = isinstance(summary.get("booked_appointment"), dict) and summary["booked_appointment"].get("status") == "confirmed"
    if case.case_key == "lau_chest" and has_tests_or_prev and not already_booked:
        followup_offer = select_booking_offer(
            session,
            case.tenant_id,
            j_key="lau_chest",
            patient_text="follow-up review after blood tests",
        )
        followup_en = (
            "Once your blood tests and preventive checks are done, let's book a follow-up review with Dr. Sarah Wong to go over your results and blood pressure together. "
            "Would you prefer Teleconsult or In-Clinic? Pick a suitable time with Dr. Wong below."
        )
        followup_zh = (
            "等你完成抽血同預防檢查之後，我哋可以幫你預約黃莎拉醫生覆診一齊睇報告同跟進血壓。"
            "你想揀視像診症定親臨診所？你可以喺下方揀選合適時間。"
        )
        chat.append({
            "actor": "Sam",
            "role": "assistant",
            "time": hhmm,
            "text": followup_en,
            "text_en": followup_en,
            "text_zh": followup_zh,
            "trace_id": trace_id,
            "booking_offer": followup_offer,
        })

    summary["original_chat"] = chat
    summary.pop("_dynamic_suggested_replies", None)
    tasks = list(summary.get("scheduled_tasks") or [])
    if not any("3-day AI" in str(t.get("label", "")) for t in tasks):
        tasks.append({"status": "auto", "label": "Day 3 · Autonomous AI Careline post-visit check-in (zero doctor workload)"})
    summary["scheduled_tasks"] = tasks
    case.summary_json = summary
    flag_modified(case, "summary_json")


def record_case_clinical_exam(
    session: Session,
    *,
    case_id: str,
    stage: str,
    actor_name: str,
    exam_note: str,
    modality: str = "in_person",
    vitals: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Record a completed Nurse Vitals/Exam or Doctor Clinical Examination on a CaseLoop before signing Rx/cert."""
    case = session.get(CaseLoop, case_id)
    if case is None:
        raise ValueError("Case not found.")
    summary = copy.deepcopy(case.summary_json or {})
    chat = list(summary.get("original_chat") or [])
    timeline = list(case.timeline_json or [])
    hhmm = _hkt_hhmm()
    eg = dict(
        summary.get("exam_gate")
        or {
            "exam_required": True,
            "nurse_exam_status": "not_started",
            "nurse_exam_note": "",
            "doctor_exam_status": "not_started",
            "doctor_exam_note": "",
            "doctor_exam_modality": "in_person",
        }
    )

    if stage == "nurse":
        clean_vitals = vitals or [
            {"label": "Temp (clinic)", "val": "38.1 °C (tympanic)"},
            {"label": "BP / HR", "val": "116/74 mmHg · 86/min"},
            {"label": "SpO₂", "val": "99% on room air"},
        ]
        summary["vitals_exam"] = {
            "source": f"{actor_name} · Verified Clinic Vitals ({hhmm})",
            "encounter_context": "In-Clinic Nurse Assessment",
            "items": clean_vitals,
        }
        eg["nurse_exam_status"] = "completed"
        eg["nurse_exam_note"] = exam_note.strip() or "Clinic vitals measured and verified; patient stable and ready for doctor examination."
        eg["nurse_examined_by"] = actor_name
        eg["nurse_examined_at"] = utcnow().isoformat()
        case.state = "needs_decision"
        case.baton = "with_doctor"
        case.next_checkpoint_label = "Ready for doctor exam"
        chat.append({
            "actor": "Clinic",
            "role": "system",
            "time": hhmm,
            "text": f"{actor_name} completed your vitals and pre-exam check — ready for Dr. Sarah Wong",
        })
        timeline.append({
            "date": f"Today {hhmm}",
            "label": f"{actor_name} · Recorded verified clinic vitals & handed back to doctor.",
            "agent": "NurseExam",
        })
    else:
        display_doc = re.sub(r"^Locum\s+", "", actor_name or "Dr. Sarah Wong", flags=re.I).strip()
        mod_label = "live video consultation" if modality == "live_video" else "in-person physical examination"
        eg["doctor_exam_status"] = "completed"
        eg["doctor_exam_modality"] = modality
        eg["doctor_exam_note"] = exam_note.strip() or "Alert, well-hydrated; pharynx mildly injected, no tonsillar exudate, chest clear bilaterally."
        eg["doctor_examined_by"] = display_doc
        eg["doctor_examined_at"] = utcnow().isoformat()
        case.state = "needs_decision"
        case.baton = "with_doctor"
        case.next_checkpoint_label = "Exam complete · Ready to sign"
        timeline.append({
            "date": f"Today {hhmm}",
            "label": f"{display_doc} · Completed {mod_label} (MCHK clinical assessment verified).",
            "agent": "DoctorExam",
        })

    summary["exam_gate"] = eg
    summary["original_chat"] = chat
    case.summary_json = summary
    case.timeline_json = timeline
    flag_modified(case, "summary_json")
    flag_modified(case, "timeline_json")
    case.updated_at = utcnow()
    session.flush()
    return {
        "ok": True,
        "case_id": case.id,
        "stage": stage,
        "exam_gate": eg,
        "vitals_exam": summary.get("vitals_exam"),
        "state": case.state,
        "baton": case.baton,
    }


def retract_doctor_signed_chat_bubble(session: Session, *, case_id: str, trace_id: str) -> None:
    """Remove a retracted doctor-signed care plan bubble if undone within the 10-second Undo window."""
    case = session.get(CaseLoop, case_id)
    if case is None:
        return
    summary = copy.deepcopy(case.summary_json or {})
    chat = [m for m in (summary.get("original_chat") or []) if m.get("trace_id") != trace_id]
    summary["original_chat"] = chat
    case.summary_json = summary
    flag_modified(case, "summary_json")


def trigger_post_sign_checkin(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str,
    live_bus: LiveHarnessBus,
    lang: str = "en",
) -> dict[str, Any]:
    """Demo fast-forward: simulate Day-3 autonomous AI post-visit check-in (doctor stays out of the loop)."""
    j = resolve_journey(journey_key_or_pid)
    j_key = j["key"]
    pid = j["patient_id"]
    is_zh = str(lang or "en").lower().startswith("zh")

    with db.tenant_session(tenant_id) as session:
        case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == j_key)
        )
        if case is None:
            raise ValueError(f"Case for journey '{j_key}' not found")
        summary = copy.deepcopy(case.summary_json or {})
        chat = list(summary.get("original_chat") or [])
        hhmm = _hkt_hhmm()

        chat.append({
            "actor": "Clinic",
            "role": "system",
            "time": "Day +3",
            "text": "3 days later · Autonomous AI post-visit check-in (no doctor interruption)",
        })
        if j_key == "wingyan":
            checkin_text = (
                "你好，距離你上次覆診已經過咗 3 日，想問候一下你嘅喉嚨痛同發燒好返晒未？另外等你完全康復之後，需唔需要我幫你安排季節性流感疫苗接種，或者預約其他常規檢查呢？"
                if is_zh
                else "Hi there — just checking in 3 days after your visit. How are your sore throat and fever feeling now? Once you're back to normal, would you like me to help book a seasonal flu vaccine or arrange a visit for anything else?"
            )
        else:
            checkin_text = (
                "你好，距離你上次覆診已經過咗 3 日，想問候一下你最近身體感覺好返啲未？如果已經穩定返，需唔需要我幫你安排季節性流感疫苗接種或者常規覆診呢？"
                if is_zh
                else "Hi there — just checking in 3 days after your visit. How are you feeling now? Once you're feeling back to normal, would you like me to help book a seasonal flu vaccine or arrange a follow-up visit?"
            )
        chat.append({
            "actor": "Sam",
            "role": "assistant",
            "time": hhmm,
            "text": checkin_text,
            "lang": "zh-HK" if is_zh else "en",
        })
        summary["original_chat"] = chat
        summary.pop("_dynamic_suggested_replies", None)
        case.summary_json = summary
        case.state = "scheduled"
        case.baton = "idle"
        case.next_checkpoint_label = "Day-3 AI check-in sent"
        flag_modified(case, "summary_json")
        session.flush()
        case_id = case.id

    live_bus.publish("case.updated", patient_id=pid, data={"case_id": case_id, "journey_key": j_key, "state": "scheduled"})
    day3_replies = list(_DAY3_REPLIES_ZH if is_zh else _DAY3_REPLIES_EN)
    return {
        "ok": True,
        "session_epoch": live_demo_coordinator.epoch,
        "case_id": case_id,
        "messages": chat,
        "suggested_replies": day3_replies,
    }

