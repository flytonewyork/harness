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
import re
import threading
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
)
from .security import mint_jwt
from .curator import ensure_active_practice_graph, get_effective_practice_graph
from .db import (
    AgentMemoryEntry,
    CaseLoop,
    Certificate,
    Database,
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
from .practice_graph import (
    compute_cha2ds2_vasc,
    compute_cvd_10yr_tier,
    compute_has_bled,
    validate_case_presentation,
)
from .rx_rules import check_prescription_safety
from .triage import classify_bilingual_async_triage
from .workspace_engine import (
    _compile_5slot_context_snapshot,
    _ensure_workspace_fk_entities,
    reset_workspace,
)

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


# -------------------------------------------------------------------------------------- Curated Demo Journeys
LIVE_JOURNEYS: tuple[dict[str, Any], ...] = (
    {
        "key": "wingyan",
        "patient_id": "pat_lam_urti_demo",
        "patient_name": "LAM Wing-Yan",
        "patient_name_zh": "林詠欣",
        "age_sex": "32F",
        "pathway_node_id": "ebm.pathway.low_ferritin_normal_hb",
        "title": "Acute URTI, Sick Leave & Low Ferritin",
        "title_zh": "上呼吸道感染、病假紙與低鐵蛋白",
        "subtitle": "G6PD deficient · Penicillin allergy · KingMed Ferritin 14 · $0 flu pass",
        "welcome_en": (
            "Hi Wing-Yan, welcome to Central & Mid-Levels Family Medical. "
            "I'm Sam, the clinic's care assistant — I help our doctors and nurses coordinate your visit, "
            "check your clinic record, and line up follow-ups (your doctor always reviews and signs off on any prescriptions or certificates).\n\n"
            "I also see your 8 Nov KingMed blood panel is back on file. How are you feeling today, and how can we help?"
        ),
        "welcome_zh": (
            "詠欣你好，歡迎聯絡中環及半山家庭醫務中心。我係診所護理助手 Sam，"
            "負責協助醫生及護士跟進你嘅病歷、預約及覆診安排（所有處方同病假紙都會由醫生親自審閱及簽發）。\n\n"
            "我見到你 11 月 8 日嘅金域驗血報告已經返咗。你今日身體點樣？有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "Woke up with a sore throat and 38.2°C fever — can I take aspirin? Also need a 2-day sick note and wanted to ask about my ferritin result.",
            "My home RAT is negative and I'm breathing fine with no chest pain. Can we arrange a teleconsult today?",
            "Can I also book my Harbour FinTech flu shot once I recover?",
        ],
        "suggested_replies_zh": [
            "今朝開始喉嚨痛同發燒38.2度，我可以食阿士匹靈嗎？想攞兩日病假紙，順便問吓我份鐵蛋白報告。",
            "屋企快測陰性，冇氣促亦都冇心口痛，可唔可以今日約視像睇醫生？",
        ],
    },
    {
        "key": "chan",
        "patient_id": "pat_chan_warfarin",
        "patient_name": "CHAN Tai Man",
        "patient_name_zh": "陳大文",
        "age_sex": "62M",
        "pathway_node_id": "ebm.pathway.post_discharge_anticoag",
        "title": "Post-QEH AFib Discharge & Med Check",
        "title_zh": "伊利沙伯醫院出院覆診・薄血丸核對",
        "subtitle": "Warfarin 5mg → 3mg · Statin switch · 11:30 follow-up · CHA₂DS₂-VASc",
        "welcome_en": (
            "Welcome home from Queen Elizabeth Hospital, Mr. Chan. "
            "I'm Sam, the care assistant at Central & Mid-Levels Family Medical — I help Dr. Wong and Nurse Rachel prepare for your follow-up today at 11:30.\n\n"
            "We've received your QEH discharge summary. How are you feeling this morning, and do you have any questions about your hospital medication bags before your visit?"
        ),
        "welcome_zh": (
            "陳先生你好，歡迎由伊利沙伯醫院平安出院。我係診所護理助手 Sam，"
            "幫黃醫生同陳護士準備你今日 11:30 嘅覆診。\n\n"
            "診所已經收到你嘅出院總結。你今朝覺得點樣？對醫院開嘅新藥袋有冇任何疑問？"
        ),
        "suggested_replies_en": [
            "I'm a bit confused — QEH gave me Warfarin 3mg and Atorvastatin, but I still have 5mg Warfarin and Simvastatin at home. Which should I take?",
            "No bruising or bleeding at all. Can I take ibuprofen for a mild headache?",
        ],
        "suggested_replies_zh": [
            "尋日喺伊利沙伯醫院出院，醫院開咗3mg薄血丸同新降膽固醇藥，但我屋企仲有之前診所5mg同舊藥，應該食邊隻？",
            "冇瘀傷亦都冇流牙血。頭有少少痛，可唔可以食布洛芬止痛？",
        ],
    },
    {
        "key": "wong",
        "patient_id": "pat_wong_htn",
        "patient_name": "WONG Mei Ling",
        "patient_name_zh": "黃美玲",
        "age_sex": "58F",
        "pathway_node_id": "ebm.pathway.chronic_htn_home_bp",
        "title": "Home Blood Pressure Check-In",
        "title_zh": "高血壓家居血壓追蹤",
        "subtitle": "Amlodipine 5mg OD · On-target quiet loop vs >140/90 doctor wake-up",
        "welcome_en": (
            "Good morning Ms. Wong — this is Sam from Central & Mid-Levels Family Medical. "
            "It's Day 7 of your home blood pressure check-in on Amlodipine 5mg.\n\n"
            "What was your blood pressure reading today, and how have you been feeling?"
        ),
        "welcome_zh": (
            "黃女士早晨，我係中環及半山家庭醫務中心嘅護理助手 Sam。今日係你服食 Amlodipine 5mg 第 7 日家居血壓記錄。\n\n"
            "請問你今日量度嘅血壓係幾多？身體有冇頭暈或者腳腫？"
        ),
        "suggested_replies_en": [
            "Good morning! Today's reading is 128/78 and I'm feeling great, no dizziness or swelling.",
            "Actually I rechecked this evening and it's 156/96 with a mild headache.",
        ],
        "suggested_replies_zh": [
            "今朝量係 128/78，感覺好好，冇頭暈冇腳腫。",
            "頭先再量高咗，係 156/96，仲有少少頭痛。",
        ],
    },
    {
        "key": "lee",
        "patient_id": "pat_lee_ldl",
        "patient_name": "LEE Ming Kit",
        "patient_name_zh": "李明傑",
        "age_sex": "36M",
        "pathway_node_id": "ebm.pathway.borderline_ldl",
        "title": "Borderline LDL Follow-Up & Thin Intake",
        "title_zh": "輕微高膽固醇覆檢・簡短問診防護",
        "subtitle": "LDL 3.6 mmol/L · Quiet blood-draw booking or vague-symptom safety guard",
        "welcome_en": (
            "Hi Ming Kit, welcome to Central & Mid-Levels Family Medical. "
            "I'm Sam, the clinic's care assistant. Following up on your 2 Nov checkup (borderline LDL 3.6 mmol/L), "
            "would you like to confirm a fasting blood draw slot for February, or is there something else you'd like help with today?"
        ),
        "welcome_zh": (
            "明傑你好，我係中環及半山家庭醫務中心護理助手 Sam。跟進返你 11 月 2 日驗身報告（輕微偏高低密度膽固醇 3.6 mmol/L），"
            "你想唔想確認二月嘅空腹抽血覆檢時段，定係今日有其他身體問題想問？"
        ),
        "suggested_replies_en": [
            "Yes, Friday 12 Feb at 08:00 works for my fasting blood test — can I bundle my seasonal flu shot too?",
            "Actually I've just been feeling vaguely unwell since yesterday, can I get antibiotics?",
        ],
        "suggested_replies_zh": [
            "好呀，2月12號星期五朝早8點空腹抽血冇問題，可唔可以順便打埋流感針？",
            "其實我琴日開始覺得個人唔係好舒服，可唔可以直接開抗生素？",
        ],
    },
    {
        "key": "cheung",
        "patient_id": "pat_cheung_t2dm",
        "patient_name": "CHEUNG Kwok-Wai",
        "patient_name_zh": "張國偉",
        "age_sex": "56M",
        "pathway_node_id": "ebm.pathway.t2dm_hba1c",
        "title": "T2DM HbA1c 7.8% & Cantonese Red-Flag Test",
        "title_zh": "二型糖尿病覆診・廣東話誇飾 vs 999警示",
        "subtitle": "HbA1c 7.8%, eGFR 64 · Tests Cantonese hyperbole safe negation & 999 exit",
        "welcome_en": (
            "Hi Mr. Cheung, welcome to Central & Mid-Levels Family Medical. "
            "I'm Sam, the clinic's care assistant — I see your recent diabetes panel (HbA1c 7.8%, eGFR 64) is ready for your upcoming review.\n\n"
            "How have you been feeling on Metformin 500mg twice daily, and how can we help today?"
        ),
        "welcome_zh": (
            "張先生你好，歡迎聯絡中環及半山家庭醫務中心。我係診所護理助手 Sam，"
            "我見到你最新嘅糖尿病覆檢報告（HbA1c 7.8%，eGFR 64）已經返咗。\n\n"
            "你最近食 Metformin 500mg 每日兩次感覺點樣？今日有咩可以幫你？"
        ),
        "suggested_replies_en": [
            "呢兩日咳到想死，但係完全冇氣促、冇心口痛、亦都冇發燒，想約醫生覆診順便睇吓血糖報告。",
            "My home glucose has been running around 8–9 mmol/L fasting. Should Dr. Wong adjust my Metformin?",
            "我突然間心口好痛、唞唔到氣、標晒冷汗，好似就快暈低咁！",
        ],
        "suggested_replies_zh": [
            "呢兩日咳到想死，但係完全冇氣促、冇心口痛、亦都冇發燒，想約醫生覆診順便睇吓血糖報告。",
            "我突然間心口好痛、唞唔到氣、標晒冷汗，好似就快暈低咁！",
        ],
    },
)

JOURNEY_BY_KEY: dict[str, dict[str, Any]] = {j["key"]: j for j in LIVE_JOURNEYS}
JOURNEY_BY_PATIENT_ID: dict[str, dict[str, Any]] = {j["patient_id"]: j for j in LIVE_JOURNEYS}


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
    chat = [{"actor": "Sam", "time": _hkt_hhmm(), "text": welcome_msg, "lang": "zh-HK" if _CJK_RE.search(welcome_msg) else "en"}]

    if j_key == "wingyan":
        return {
            "alerts": [
                {"level": "high", "label": "G6PD Deficient", "detail": "Avoid Aspirin, NSAIDs, sulfonamides, nitrofurantoin"},
                {"level": "high", "label": "Penicillin Allergy", "detail": "Urticarial rash — avoid Amoxicillin / Augmentin"},
            ],
            "preventive_care": [
                {
                    "id": "prev_flu",
                    "title": "Seasonal Influenza Vaccine (2026/27)",
                    "badge": "$0 Harbour FinTech",
                    "detail": "Inactivated (G6PD-safe) · 10-min nurse visit (defer 14d if febrile)",
                    "order": {"id": "ord_prev_flu", "type": "book", "issue": "Preventive Care", "visitType": "10-min Nurse Flu Shot ($0)", "when": "In 14 days (post-recovery)"},
                },
            ],
            "scheduled_tasks": [
                {"status": "waiting", "label": "Careline conversation active with patient"},
            ],
            "issues": [
                {
                    "id": "iss_intake_live",
                    "name": "1. Patient check-in — gathering history",
                    "bullets": [
                        "Patient scanned clinic QR code; Sam sent welcome message and loaded 8 Nov KingMed panel.",
                        "Awaiting patient's reason for contact before staging clinical actions.",
                    ],
                    "safety_gap": None,
                    "suggested_orders": [],
                }
            ],
            "lab_panel": {
                "source": "KingMed Annual Blood Panel · 8 Nov 2026",
                "rows": [
                    {"test": "Serum Ferritin", "val": "14 ng/mL", "ref": "15–150", "flag": "LOW"},
                    {"test": "Haemoglobin (Hb)", "val": "12.1 g/dL", "ref": "11.5–15.5", "flag": "Normal"},
                    {"test": "eGFR", "val": "104 mL/min", "ref": "> 90", "flag": "Normal"},
                ],
            },
            "trend_chart": {
                "kind": "ferritin",
                "title": "Ferritin Trend (ng/mL)",
                "unit": "ng/mL",
                "target_label": "Lower ref 15",
                "target_value": 15,
                "points": [{"label": "Nov 24", "v": 32}, {"label": "Nov 25", "v": 21}, {"label": "Nov 26", "v": 14}],
            },
            "attachments": [{"id": doc_id or "att_wy", "label": doc_label, "quote": doc_quote, "verified_3pt": True}],
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
                {"status": "booked", "label": "Today 11:30 · Post-QEH clinic visit (Careline check-in active)"},
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
            ],
            "scheduled_tasks": [
                {"status": "booked", "label": "Mon 21 Dec 10:00 · Routine GP Hypertension Review"},
                {"status": "auto", "label": "Day-7 home BP check-in sent via Careline"},
            ],
            "issues": [
                {
                    "id": "iss_htn_live",
                    "name": "1. Essential hypertension — awaiting Day-7 reading",
                    "bullets": [
                        "On Amlodipine 5mg OD; Sam sent Day-7 home blood pressure prompt.",
                        "Case stays quiet unless readings breach > 140/90 mmHg or symptoms are reported.",
                    ],
                    "safety_gap": None,
                    "suggested_orders": [],
                }
            ],
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
            "agent_thread": [],
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
        "prescriptions": [
            {
                "id": r.id,
                "items": list(r.items_json or []),
                "fulfilment_status": r.fulfilment_status,
            }
            for r in rxs
        ],
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
def get_live_journeys_status(db: Database, tenant_id: str, live_bus: LiveHarnessBus) -> dict[str, Any]:
    """List all 5 demo patient journeys with live status for the phone sidebar & laptop QR launchpad."""
    with db.tenant_session(tenant_id) as session:
        cases = session.scalars(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id)
        ).all()
        by_key = {c.case_key: c for c in cases}

        items: list[dict[str, Any]] = []
        for j in LIVE_JOURNEYS:
            c = by_key.get(j["key"])
            if c is None:
                status = "not_started"
                msg_count = 0
                case_id = None
            else:
                has_signed = bool(_build_signed_plan_snapshot(session, tenant_id, j["patient_id"]))
                status = "signed" if has_signed else c.state
                msg_count = len((c.summary_json or {}).get("original_chat") or [])
                case_id = c.id
            items.append({
                "key": j["key"],
                "patient_id": j["patient_id"],
                "patient_name": j["patient_name"],
                "patient_name_zh": j["patient_name_zh"],
                "age_sex": j["age_sex"],
                "title": j["title"],
                "title_zh": j["title_zh"],
                "subtitle": j["subtitle"],
                "status": status,
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
            session.delete(existing)
            session.flush()
            existing = None

        already_started = existing is not None
        if existing is None:
            doc_id, doc_label, doc_quote = _seed_journey_prior_record(session, tenant_id, j_key)
            summary = _build_initial_summary(j_key, doc_id, doc_label, doc_quote, welcome_msg)
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
                risk_tier="high" if j_key == "chan" else "standard",
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
        messages = list((case.summary_json or {}).get("original_chat") or [])
        case_id = case.id
        case_state = case.state
        crisis = (case.summary_json or {}).get("_active_crisis")

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
            "patient_id": pid,
            "patient_name": j["patient_name"],
            "patient_name_zh": j["patient_name_zh"],
            "age_sex": j["age_sex"],
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
        "suggested_replies": j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"],
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
                "suggested_replies": j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"],
            }

        messages = list((case.summary_json or {}).get("original_chat") or [])
        signed = _build_signed_plan_snapshot(session, tenant_id, pid)
        crisis = (case.summary_json or {}).get("_active_crisis")
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
            "crisis": crisis,
            "suggested_replies": j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"],
        }


def _compile_journey_turn_update(
    *,
    j_key: str,
    patient_text: str,
    existing_summary: dict[str, Any],
    cds_blocked_drugs: list[str],
    cds_warnings: list[str],
    triage_out: dict[str, Any],
    is_zh: bool,
) -> tuple[str, str, str, str, str, dict[str, Any], str, list[str]]:
    """Update the journey's CaseLoop fields + 5/5-linted summary and craft a natural fallback reply.

    Returns:
      (new_state, new_baton, ai_title, lead_line, next_checkpoint, updated_summary, fallback_reply, agent_steps)
    """
    low = (patient_text or "").lower()
    summary = copy.deepcopy(existing_summary)
    steps: list[str] = []

    # Detect prior patient turns so Turn 2/3 follow-ups feel natural and never repeat Turn 1
    prior_patient_msgs = [
        str(m.get("text") or "")
        for m in (existing_summary.get("original_chat") or [])[:-1]
        if m.get("actor") not in ("Sam", "Assistant") and m.get("role") != "doctor"
    ]
    is_followup_turn = len(prior_patient_msgs) >= 1
    is_simple_ack = bool(
        re.fullmatch(
            r"\s*(?:thanks|thank you|thx|ok|okay|got it|sounds good|great|perfect|sure|多謝|唔該|明白|好呀|好嘅|收到)[!.。！\s]*",
            patient_text or "",
            re.I,
        )
    )

    # 1. LAM Wing-Yan (URTI + G6PD/Allergy + Ferritin 14 + Flu Shot)
    if j_key == "wingyan":
        all_text = " ".join([*prior_patient_msgs, patient_text])
        temp_m = _TEMP_RE.search(all_text)
        temp_val = f"{temp_m.group(1)} °C" if temp_m else "38.2 °C"
        days_m = _DAYS_RE.search(all_text)
        days_off = 2
        if days_m:
            raw_d = days_m.group(1) or days_m.group(2)
            days_off = _ZH_NUM_MAP.get(raw_d, int(raw_d) if raw_d and raw_d.isdigit() else 2)
        rat_neg_now = any(k in low for k in ("negative", "陰性", "rat neg"))
        rat_neg_ever = rat_neg_now or any(k in all_text.lower() for k in ("negative", "陰性", "rat neg"))
        mentions_teleconsult = any(k in low for k in ("teleconsult", "book", "slot", "appointment", "視像", "預約", "約"))
        mentions_urti = any(k in low for k in ("sore throat", "fever", "cough", "sick", "喉嚨", "發燒", "咳", "病假"))
        mentions_ferritin = any(k in low for k in ("ferritin", "iron", "鐵蛋白", "鐵丸", "blood", "驗血"))
        mentions_flu = any(k in low for k in ("flu", "vaccine", "流感", "打針"))
        asked_para = any(k in low for k in ("paracetamol", "panadol", "必理痛", "撲熱息痛"))
        asked_penicillin = "amoxicillin" in cds_blocked_drugs or any(k in low for k in ("amoxicillin", "augmentin", "penicillin", "盤尼西林", "阿莫西林"))

        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {_hkt_hhmm()}",
            "items": [
                {"label": "Temp (home)", "val": temp_val},
                {"label": "COVID / Flu RAT", "val": "Negative" if rat_neg_ever else "Pending / Not yet logged"},
                {"label": "Red flags", "val": "Denies SOB, chest pain, or neck stiffness"},
            ],
        }
        summary["scheduled_tasks"] = [
            {
                "status": "booked" if mentions_teleconsult else "staged",
                "label": "Today 14:30 · Teleconsult with Locum Dr. Sarah" + (" (patient requested)" if mentions_teleconsult else " (offered)"),
            },
        ]
        if mentions_flu or "flu" in all_text.lower() or "流感" in all_text:
            summary["scheduled_tasks"].append(
                {"status": "auto", "label": "Flu shot ($0 Harbour FinTech) — auto-deferred 14d until afebrile"}
            )

        summary["issues"] = [
            {
                "id": "iss_urti",
                "name": "1. Acute viral URTI",
                "bullets": [
                    f"Patient messaged with sore throat and fever ({temp_val}); requesting {days_off}-day sick leave certificate.",
                    "No clinical antibiotic indication; G6PD and Penicillin safety blocks active.",
                ],
                "safety_gap": "Aspirin queried by patient — G6PD contraindication flagged in Careline." if ("aspirin" in cds_blocked_drugs or "aspirin" in all_text.lower() or "阿士匹靈" in all_text) else None,
                "suggested_orders": [
                    {"id": "wy_cert", "type": "cert", "issue": "1. Acute viral URTI", "daysOff": days_off, "dates": "23–24 Nov 2026" if days_off == 2 else f"{days_off} days from 23 Nov 2026", "omitDx": True},
                    {"id": "wy_rx_para", "type": "rx", "action": "start", "issue": "1. Acute viral URTI", "drug": "Paracetamol 500mg", "freq": "QDS PRN", "days": "5d", "note": "G6PD-safe analgesic"},
                    {"id": "wy_rx_cet", "type": "rx", "action": "start", "issue": "1. Acute viral URTI", "drug": "Cetirizine 10mg", "freq": "OD PRN", "days": "5d", "note": "Non-sedating antihistamine"},
                ],
            },
            {
                "id": "iss_ferr",
                "name": "2. Non-anaemic iron deficiency",
                "bullets": [
                    "Steady decline in ferritin across annual panels with preserved haemoglobin (see Trend & Labs).",
                    "Prior 10 Nov note documented regular periods and no GI bleeding; patient asked about oral iron.",
                ],
                "safety_gap": None,
                "suggested_orders": [
                    {"id": "wy_rx_iron", "type": "rx", "action": "start", "issue": "2. Non-anaemic iron deficiency", "drug": "Ferrous bisglycinate 25mg", "freq": "Alternate days", "days": "90d", "note": "Gentle oral iron · minimizes GI intolerance"},
                    {"id": "wy_lab_ferr", "type": "lab", "issue": "2. Non-anaemic iron deficiency", "testName": "Repeat Serum Ferritin + FBC", "lab": "KingMed", "fasting": False, "when": "Tue 9 Feb 2027 (08:30 Nurse)"},
                ],
            },
        ]
        summary["sam_proactive_insight"] = (
            "BSG Iron Deficiency Guideline: Ferritin < 15 ng/mL with normal Hb meets non-anaemic iron deficiency threshold "
            "(alternate-day gentle oral iron + 3-month repeat). G6PD & Penicillin formulary blocks active; Harbour FinTech $0 flu shot eligible after 14d recovery."
        )
        steps.append("PracticeGraph · Traversed ebm.pathway.acute_urti_g6pd + ebm.pathway.low_ferritin_normal_hb")
        steps.append("PresentationCompiler · Compiled URTI + Ferritin workspace case (5/5 linter pass)")

        parts_en: list[str] = []
        parts_zh: list[str] = []
        if is_simple_ack and is_followup_turn:
            parts_en.append("You're very welcome — everything is ready in Dr. Sarah Wong's workspace now. As soon as she signs off on your care plan and sick-leave certificate, they'll appear right here in this chat.")
            parts_zh.append("唔使客氣——所有資料已經交到黃莎拉醫生嘅工作台。醫生一簽署你嘅護理計劃同病假證明書，就會即時傳送到呢個對話。")
        else:
            if "aspirin" in cds_blocked_drugs:
                parts_en.append(
                    "First — please do **not** take aspirin. Your clinic record notes G6PD deficiency, so aspirin and NSAIDs should be avoided (paracetamol is generally the safer option your doctor can confirm)."
                )
                parts_zh.append(
                    "首先請**唔好食阿士匹靈**——你嘅診所病歷記錄咗你有蠶豆症（G6PD 缺乏症），需要避免阿士匹靈及消炎止痛藥（一般會由醫生考慮較安全嘅撲熱息痛）。"
                )
            if asked_penicillin:
                parts_en.append(
                    "Also flagging from your allergy record: please avoid **amoxicillin / penicillin antibiotics** (documented urticarial rash). Viral URTI typically doesn't need antibiotics anyway, and Dr. Wong will confirm safe symptom relief."
                )
                parts_zh.append(
                    "另外按你嘅過敏記錄提醒：請避免服食**盤尼西林類抗生素（如 Amoxicillin / Augmentin）**。一般病毒性上呼吸道感染亦毋須抗生素，黃醫生會為你安排安全嘅紓緩方案。"
                )
            if asked_para:
                parts_en.append(
                    "Paracetamol (Panadol) is G6PD-safe and doesn't cross-react with your penicillin allergy — I've noted that for Dr. Sarah Wong to confirm your dosing in the care plan."
                )
                parts_zh.append(
                    "撲熱息痛（必理痛）對蠶豆症安全，亦唔會引起盤尼西林過敏——我已經記低畀黃莎拉醫生喺處方中確認劑量。"
                )
            if rat_neg_now or (is_followup_turn and mentions_teleconsult and not mentions_urti):
                parts_en.append(
                    "Thank you for confirming — glad your home RAT is negative and your breathing is comfortable. I've updated Dr. Sarah Wong's chart and held the **14:30 teleconsult** slot today; once she reviews and signs your plan, your e-script and MCHK sick-leave PDF will land right here."
                )
                parts_zh.append(
                    "多謝你確認——快測陰性同呼吸暢順就好放心。我已經更新咗黃莎拉醫生嘅病歷並為你預留今日 **14:30 視像覆診**；醫生一審閱簽署，電子處方同病假紙就會直接送返嚟呢度。"
                )
            elif mentions_urti or (not is_followup_turn and not (mentions_ferritin or mentions_flu or asked_para or asked_penicillin)):
                parts_en.append(
                    f"I've noted your symptoms ({temp_val}) and your {days_off}-day sick-leave request, and prepared a summary for Locum Dr. Sarah Wong so she can review everything together — we have a 14:30 teleconsult slot open today."
                )
                parts_zh.append(
                    f"我已經記錄咗你嘅症狀（{temp_val}）同埋 {days_off} 日病假紙申請，並整理好資料交畀黃莎拉醫生一齊審閱——今日 14:30 有視像覆診時段。"
                )
            if mentions_ferritin:
                parts_en.append(
                    "I've also attached your 8 Nov KingMed report (Ferritin 14 ng/mL with normal haemoglobin 12.1) for Dr. Wong to discuss gentle alternate-day oral iron and a 3-month repeat blood test."
                )
                parts_zh.append(
                    "我亦已附上你 11 月 8 日嘅金域報告（鐵蛋白 14 ng/mL，血紅素正常 12.1），黃醫生會喺覆診時同你傾隔日口服補鐵同三個月後覆驗。"
                )
            if mentions_flu:
                parts_en.append(
                    "Regarding your $0 Harbour FinTech flu shot: because you're currently unwell with a fever, our clinic protocol defers vaccination by 14 days until you're fully recovered — Dr. Wong can queue that nurse slot for you right in your plan."
                )
                parts_zh.append(
                    "至於你嘅 Harbour FinTech 免費流感針：由於你而家發燒唔舒服，按診所指引會暫緩 14 日等你退燒康復後先打，黃醫生可以幫你預先排好護士接種時段。"
                )
            if not rat_neg_ever and mentions_urti and "aspirin" in cds_blocked_drugs:
                parts_en.append("Just to help Dr. Wong prepare: have you done a home COVID/Flu RAT test, and is your breathing comfortable with no chest pain?")
                parts_zh.append("另外想幫黃醫生確認一下：你有冇做過屋企快測（新冠/流感）？呼吸暢順、冇心口痛嗎？")
            if not parts_en:
                parts_en.append("Got it — I've added that note directly to your live case for Dr. Sarah Wong so she has the full picture ready.")
                parts_zh.append("收到——我已經將呢段補充即時加入你嘅個案，方便黃莎拉醫生一併審閱。")

        reply = "\n\n".join(parts_zh if is_zh else parts_en)
        return (
            "needs_decision",
            "with_doctor",
            "Acute Viral URTI & Non-Anaemic Iron Deficiency",
            f"Patient reported viral URTI ({temp_val}), requesting {days_off}-day sick leave alongside Ferritin 14 review.",
            "Today 14:30 teleconsult",
            summary,
            reply,
            steps,
        )

    # 2. CHAN Tai Man (Post-QEH AFib Warfarin + Statin Switch)
    if j_key == "chan":
        asked_nsaid = "ibuprofen" in cds_blocked_drugs or "ibuprofen" in low or "布洛芬" in low
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

        if asked_nsaid:
            reply_en = (
                "Please avoid **ibuprofen** (and other NSAIDs) while taking Warfarin — combining them significantly raises bleeding risk. "
                "I've flagged this for Dr. Sarah Wong ahead of your 11:30 visit today so she can confirm a safe pain-relief option and reconcile your medication bags with you."
            )
            reply_zh = (
                "服食薄血丸（Warfarin）期間請**避免服食布洛芬（Ibuprofen）**或其他消炎止痛藥，因為會大幅增加出血風險。"
                "我已經通知咗黃莎拉醫生，等陣 11:30 覆診時會同你逐一核對藥袋同安排安全嘅止痛選擇。"
            )
        else:
            reply_en = (
                "Thank you for checking before taking anything — please hold off taking both statins together and bring **all** of your hospital and old clinic medicine bags to your 11:30 appointment today.\n\n"
                "Your QEH discharge letter lists Warfarin 3mg daily (reduced from 5mg) and Atorvastatin 20mg replacing Simvastatin. "
                "I've flagged this exact comparison directly in Dr. Sarah Wong's workspace so she can confirm your updated regimen, order your 7-day INR, and have Nurse Rachel walk through your pillbox in Cantonese. Have you noticed any unusual bruising or bleeding?"
            )
            reply_zh = (
                "好多謝你食藥前先問清楚——請千祈唔好同時食兩隻降膽固醇藥，並帶齊**所有**醫院新藥袋同屋企舊藥嚟今日 11:30 覆診。\n\n"
                "伊利沙伯醫院出院紙寫明薄血丸改為每日 3mg（由 5mg 減量），同埋轉用 Atorvastatin 20mg 代替舊嘅 Simvastatin。"
                "我已經將完整對照放咗喺黃莎拉醫生嘅工作台，陣間會幫你核對藥單、安排 7 日後覆驗 INR，並由陳護士用廣東話幫你執好藥盒。你最近有冇發現容易瘀傷或者流牙血？"
            )
        return (
            "needs_decision",
            "with_doctor",
            "Post-QEH AFib Discharge — Medication Review",
            "Post-discharge Warfarin 5→3mg & statin switch flagged via patient Careline ahead of 11:30 visit.",
            "Today 11:30 visit",
            summary,
            reply_zh if is_zh else reply_en,
            steps,
        )

    # 3. WONG Mei Ling (Home BP: Quiet Scheduled vs >140/90 Wake-Up!)
    if j_key == "wong":
        bp_m = _BP_RE.search(patient_text)
        sys_v = int(bp_m.group(1)) if bp_m else (156 if any(k in low for k in ("high", "headache", "高", "頭痛")) else 128)
        dia_v = int(bp_m.group(2)) if bp_m else (96 if sys_v >= 140 else 78)
        breached = sys_v >= 140 or dia_v >= 90

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
        if breached:
            summary["alerts"] = [
                {"level": "high", "label": f"Home BP Threshold Breach ({sys_v}/{dia_v})", "detail": "Exceeds > 140/90 wake-up gate · Escalated to #my-work"},
            ]
            summary["issues"] = [
                {
                    "id": "iss_htn_breach",
                    "name": "1. Essential hypertension — home BP elevated",
                    "bullets": [
                        "Patient logged an elevated home reading above clinic target via Careline (see BP Trajectory).",
                        "No emergency red flags reported; requires clinician review of Amlodipine titration vs repeat check.",
                    ],
                    "safety_gap": "Confirm seated 5-minute rest technique and screen for vision changes or chest discomfort.",
                    "suggested_orders": [
                        {"id": "wg_rx_amlo", "type": "rx", "action": "update", "issue": "1. Essential hypertension — home BP elevated", "drug": "Amlodipine 10mg", "freq": "OD", "days": "30d", "note": "Consider titration if confirmed"},
                        {"id": "wg_book_rev", "type": "book", "issue": "1. Essential hypertension — home BP elevated", "visitType": "15-min GP BP Review", "when": "Within 48 hours"},
                        {"id": "wg_nurse_bp", "type": "nurse", "issue": "1. Essential hypertension — home BP elevated", "assignee": "Nurse Rachel", "task": "BP / INR follow-up call", "due": "Tomorrow morning"},
                    ],
                }
            ]
            summary["sam_proactive_insight"] = (
                f"NICE NG136 / HK HTN Reference Framework: Home BP {sys_v}/{dia_v} mmHg exceeds the 140/90 wake-up threshold "
                "(target < 135/85). Case moved automatically from Scheduled into Needs Decision."
            )
            steps.append(f"LoopWatcher · BP threshold breach detected ({sys_v}/{dia_v} > 140/90) -> Woke loop to Needs Decision")
            reply_en = (
                f"Thank you for sharing that reading ({sys_v}/{dia_v} mmHg). Because that's above your target of 135/85, "
                "I've escalated your BP log directly into Dr. Sarah Wong's active review queue right now so she can check whether to adjust your plan or bring forward your visit.\n\n"
                "Please rest seated for 5 minutes and let me know straight away if you develop severe headache, chest pain, shortness of breath, or vision changes (if so, please call 999 or go to A&E immediately)."
            )
            reply_zh = (
                f"多謝你即時匯報（{sys_v}/{dia_v} mmHg）。由於讀數高於你嘅目標 135/85，"
                "我已經即時將你嘅血壓記錄提升至黃莎拉醫生嘅優先覆核清單，等醫生決定係咪需要調整藥量或者提早覆診。\n\n"
                "請先坐低休息 5 分鐘；如果出現劇烈頭痛、心口痛、氣促或者視力模糊，請立即致電 999 或前往急症室。"
            )
            return (
                "needs_decision",
                "with_doctor",
                "Essential Hypertension — Home BP Breach Review",
                f"Home BP {sys_v}/{dia_v} mmHg breached >140/90 wake-up rule; escalated to doctor queue.",
                "Escalated · review today",
                summary,
                reply_zh if is_zh else reply_en,
                steps,
            )

        # On-target quiet loop (ZERO doctor interruption!)
        summary["alerts"] = [
            {"level": "info", "label": "BP Target: < 135/85 mmHg", "detail": "On target · Routine review remains scheduled 21 Dec"},
        ]
        summary["issues"] = [
            {
                "id": "iss_htn",
                "name": "1. Essential hypertension — on target",
                "bullets": [
                    "Asymptomatic and well controlled on Amlodipine 5mg OD within home target (see BP Trajectory).",
                    "Routine GP review remains scheduled for 21 Dec; zero unscheduled doctor interruption needed.",
                ],
                "safety_gap": None,
                "suggested_orders": [
                    {"id": "wg_goal", "type": "goal", "issue": "1. Essential hypertension — on target", "metric": "Blood Pressure", "targetSys": 135, "targetDia": 85},
                    {"id": "wg_lab_ren", "type": "lab", "issue": "1. Essential hypertension — on target", "testName": "Renal Function & Electrolytes (U&E)", "lab": "KingMed", "fasting": False, "when": "1 week before 21 Dec review"},
                ],
            }
        ]
        summary["sam_proactive_insight"] = None  # Strict R5 high-signal gate: stay silent when on-target!
        steps.append(f"LoopWatcher · Evaluated Home BP {sys_v}/{dia_v} (< 135/85 target) -> Routed quietly to Scheduled (0 doctor interruption)")
        reply_en = (
            f"Great news — {sys_v}/{dia_v} mmHg is right inside your < 135/85 target! "
            "I've logged it into your clinic trend chart. Since everything is steady and you're feeling well, "
            "your routine hypertension review stays set for Mon 21 Dec at 10:00 — message me anytime if a reading goes up."
        )
        reply_zh = (
            f"太好喇！{sys_v}/{dia_v} mmHg 完全喺你 < 135/85 嘅目標範圍內！"
            "我已經幫你記錄咗喺血壓趨勢圖。由於血壓穩定而且感覺良好，你嘅常規覆診維持喺 12 月 21 日（星期一）上午 10:00——如果之後量到偏高隨時話我知。"
        )
        return (
            "scheduled",
            "idle",
            "Essential Hypertension — Routine Review 21 Dec",
            f"Home BP {sys_v}/{dia_v} mmHg on target on Amlodipine 5mg OD; stays quietly scheduled.",
            "21 Dec review",
            summary,
            reply_zh if is_zh else reply_en,
            steps,
        )

    # 4. LEE Ming Kit (Quiet Lipid Booking vs Thin/Vague Intake Guard!)
    if j_key == "lee":
        is_booking = any(k in low for k in ("12 feb", "08:00", "8am", "8:00", "works", "book", "confirm", "血", "2月12", "8點", "冇問題"))
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
                "All set! I've booked your fasting lipid blood draw for **Fri 12 Feb at 08:00** with Nurse Rachel and bundled your seasonal flu shot into the same 10-minute morning visit. "
                "Please fast (water only) for 8–10 hours beforehand — I'll send a reminder the evening before."
            )
            reply_zh = (
                "已經幫你預約好！**2 月 12 日（星期五）上午 8:00** 由陳護士為你安排空腹血脂抽血，並一併接種季節性流感疫苗。"
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

        # Thin / vague intake safety guard!
        summary["intake_sufficiency"] = "insufficient"
        summary["issues"] = [
            {
                "id": "iss_thin",
                "name": "1. Undifferentiated unwellness — thin intake",
                "bullets": [
                    "Patient reported feeling vaguely unwell without specific organ-system symptoms and requested antibiotics.",
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
        reply_en = (
            "I'm sorry you're not feeling well. Antibiotics require a doctor's clinical assessment first — we can't prescribe them automatically through chat.\n\n"
            "To help Dr. Sarah Wong evaluate you safely, could you share a bit more detail: what specific symptoms do you have (for example fever, cough, sore throat, stomach upset, or urinary symptoms), and are you having any chest pain or shortness of breath? "
            "I can also line up a 15-minute GP slot today."
        )
        reply_zh = (
            "聽到你唔舒服我都好關心。抗生素必須先由醫生臨床評估決定，唔可以透過對話直接開藥。\n\n"
            "為咗幫黃莎拉醫生安全了解你嘅情況，可唔可以講多啲具體症狀（例如有冇發燒、咳、喉嚨痛、腸胃唔舒服或者小便痛）？有冇任何心口痛或者氣促？我亦可以幫你安排今日 15 分鐘醫生診症時段。"
        )
        return (
            "needs_decision",
            "with_doctor",
            "Undifferentiated Unwellness — Insufficient Intake",
            "Vague symptom message & antibiotic query; thin-intake guard active (zero premature Rx staged).",
            "Needs clinical assessment",
            summary,
            reply_zh if is_zh else reply_en,
            steps,
        )

    # 5. CHEUNG Kwok-Wai (T2DM HbA1c 7.8% + Cantonese hyperbole safe negation)
    suppressed = (triage_out.get("triage_screen") or {}).get("suppressed_negations") or []
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
        "Type 2 Diabetes (HbA1c 7.8%) & Cough Review",
        "Cantonese intake cleared red-flag gate (hyperbole/negation verified); HbA1c 7.8% & eGFR 64 prepared for doctor.",
        "Review slot pending",
        summary,
        reply_zh if is_zh else reply_en,
        steps,
    )


def handle_live_chat_turn(
    db: Database,
    *,
    tenant_id: str,
    journey_key_or_pid: str,
    message_text: str,
    live_bus: LiveHarnessBus,
    lang: str = "en",
) -> dict[str, Any]:
    """Process a natural-language patient chat turn, run real pre-gates + agents, and update `/workspace` live."""
    text = (message_text or "").strip()
    if not text:
        raise ValueError("Message text cannot be empty")

    j = resolve_journey(journey_key_or_pid)
    j_key = j["key"]
    pid = j["patient_id"]
    is_zh = _is_zh(text, lang)
    engine = detect_live_llm_engine()
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
            patient_g6pd=bool(pat.g6pd_deficient) if pat else (j_key == "wingyan"),
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
            chat.append({"actor": "Sam", "time": hhmm, "text": reply, "lang": "zh-HK" if is_zh else "en", "is_crisis": True})
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
            chat.append({"actor": "Sam", "time": hhmm, "text": reply, "lang": "zh-HK" if is_zh else "en"})
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
        live_bus.update_patient_state(pid, active_agent="PresentationCompiler")
        new_state, new_baton, ai_title, lead_line, next_cp, updated_summary, fallback_reply, agent_steps = _compile_journey_turn_update(
            j_key=j_key,
            patient_text=text,
            existing_summary=summary,
            cds_blocked_drugs=cds_blocked,
            cds_warnings=cds_warnings,
            triage_out=triage_out,
            is_zh=is_zh,
        )

        # 4. Optional Live LLM Conversational Polish (strictly bounded & output-screened!)
        reply = fallback_reply
        if engine.get("live"):
            lang_directive = "Reply warmly and concisely in natural Hong Kong Traditional Chinese / Cantonese (繁體中文)." if is_zh else "Reply warmly and concisely in clear, natural English."
            sys_prompt = (
                "You are Sam, the Clinic Careline AI assistant at Central & Mid-Levels Family Medical (Hong Kong). "
                "NEVER claim to be a doctor, clinician, or resident; NEVER prescribe medications, promise reply SLAs, or diagnose definitively. "
                "Keep your reply concise (2–4 natural sentences, like a helpful clinic message). "
                f"{lang_directive}"
            )
            user_prompt = (
                f"Patient: {j['patient_name']} ({j['age_sex']})\n"
                f"Deterministic CDS findings: {cds_warnings or 'None'}\n"
                f"Patient just wrote: {text}\n"
                f"Clinic workflow guidance to convey naturally: {fallback_reply}"
            )
            llm_call = invoke_clinical_qa(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                temperature=0.2,
                max_output_tokens=320,
                patient_names_for_scrub=[j["patient_name"]],
                engine_override=engine,
            )
            if llm_call.ok and llm_call.text.strip():
                screened = screen_agent_output(llm_call.text.strip(), channel="patient_careline")
                if screened.passed:
                    reply = llm_call.text.strip()

        chat = list(updated_summary.get("original_chat") or [])
        chat.append({"actor": "Sam", "time": hhmm, "text": reply, "lang": "zh-HK" if is_zh else "en"})
        updated_summary["original_chat"] = chat
        updated_summary.pop("_active_crisis", None)

        lint = validate_case_presentation(updated_summary)
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
            "provider": engine.get("provider", "deterministic"),
            "model": engine.get("model", "clinic-harness-tool-engine-v1"),
            "region": engine.get("region", "local"),
            "live_model_invoked": bool(engine.get("live")),
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

    return {
        "ok": True,
        "session_epoch": live_demo_coordinator.epoch,
        "case_id": case_id,
        "case_state": new_state,
        "baton": new_baton,
        "reply": reply,
        "messages": chat,
        "crisis": None,
        "prompt_injection_detected": False,
        "lint_report": lint,
        "suggested_replies": j["suggested_replies_zh"] if is_zh else j["suggested_replies_en"],
    }


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
) -> None:
    """Append a structured Doctor-Signed Care Plan message into `CaseLoop.summary_json['original_chat']`
    so the physical phone chat AND laptop UnifiedThread both show it immediately (zero private Sam chat!)."""
    case = session.get(CaseLoop, case_id)
    if case is None:
        return
    summary = copy.deepcopy(case.summary_json or {})
    chat = list(summary.get("original_chat") or [])
    parts: list[str] = [f"{signed_by} (MCHK {mchk_reg_no}) has reviewed and signed your care plan."]
    rx_lines: list[str] = []
    for rx in prescriptions_out:
        for it in rx.get("items") or []:
            rx_lines.append(it.get("display") or f"{it.get('drug', '')} {it.get('dose_mg', '')}mg {it.get('frequency', '')}".strip())
    if rx_lines:
        parts.append("Prescription: " + "; ".join(rx_lines) + ".")
    if certificates_out:
        c0 = certificates_out[0]
        parts.append(f"Medical sick-leave certificate ready ({c0['days_off']} day(s), {c0['start_date']} to {c0['end_date']}).")
    if bookings_out:
        parts.append(f"{len(bookings_out)} follow-up / preventive booking(s) queued.")

    chat.append({
        "actor": signed_by,
        "role": "doctor",
        "time": _hkt_hhmm(),
        "text": " ".join(parts),
        "trace_id": trace_id,
        "care_plan": {
            "signed_by": signed_by,
            "mchk_reg_no": mchk_reg_no,
            "rx_lines": rx_lines,
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
    summary["original_chat"] = chat
    case.summary_json = summary
    flag_modified(case, "summary_json")


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
