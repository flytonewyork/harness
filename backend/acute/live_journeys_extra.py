"""7 Additional Realistic HK Family Clinic Patient Episodes for the 12-Case Live Demo Catalog.

Every episode represents an everyday Hong Kong GP scenario (never artificial "red-team test" labels
in patient-facing UI) where clinical & operational guardrails surface naturally during real-time
multi-turn conversation.

Product invariant: NEVER imports `loopbench`. Synthetic data only.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy.orm import Session

from .db import AgentMemoryEntry, new_id
from .emr_connector import ingest_external_document
from .practice_graph import compute_cvd_10yr_tier

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

EXTRA_LIVE_JOURNEYS: tuple[dict[str, Any], ...] = (
    # ---- Acute & Same-Day ----
    {
        "key": "tang_uti",
        "category": "acute",
        "patient_id": "pat_tang_uti",
        "patient_name": "TANG Ka-Yan",
        "patient_name_zh": "鄧嘉欣",
        "age_sex": "29F",
        "pathway_node_id": "ebm.pathway.acute_uti",
        "title": "Burning urination since yesterday",
        "title_zh": "小便刺痛及尿頻",
        "subtitle": "Uncomplicated dysuria · G6PD deficient on file",
        "welcome_en": (
            "Hi Ka-Yan, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. What's bothering you today?"
        ),
        "welcome_zh": (
            "嘉欣你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你今日身體有咩唔舒服？"
        ),
        "suggested_replies_en": [
            "I've had burning when I pee and going to the toilet frequently since yesterday evening.",
            "No fever, chills, or lower back pain, and no chance of pregnancy. Could I see a doctor today?",
        ],
        "suggested_replies_zh": [
            "琴晚開始小便刺痛同成日急尿，請問醫生可以點幫我？",
            "冇發燒、冇發冷亦冇腰背痛，亦冇機會懷孕。今日可唔可以約醫生？",
        ],
    },
    {
        "key": "tsang_thin",
        "category": "acute",
        "patient_id": "pat_tsang_thin",
        "patient_name": "TSANG Chun-Hin",
        "patient_name_zh": "曾俊軒",
        "age_sex": "34M",
        "pathway_node_id": "default.thin_intake",
        "title": "Feeling run down since yesterday",
        "title_zh": "琴日起覺得唔係好舒服",
        "subtitle": "Brief message asking for medicine · clarifies symptoms safely",
        "welcome_en": (
            "Hi Chun-Hin, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. Tell me a bit about what's going on today and I'll help line up the right care."
        ),
        "welcome_zh": (
            "俊軒你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你講吓今日邊度唔舒服，我幫你安排醫生跟進。"
        ),
        "suggested_replies_en": [
            "Not feeling well since yesterday, super busy with work — can the doctor just send me some antibiotics?",
        ],
        "suggested_replies_zh": [
            "琴日開始覺得個人唔係好舒服，返工好忙，可唔可以直接開啲抗生素？",
        ],
    },
    {
        "key": "lau_chest",
        "category": "chronic",
        "patient_id": "pat_lau_chest",
        "patient_name": "LAU Wai-Fong",
        "patient_name_zh": "劉慧芳",
        "age_sex": "64F",
        "pathway_node_id": "ebm.pathway.chronic_htn_home_bp",
        "title": "Cardiac chronic disease & prevention",
        "title_zh": "心血管慢性病管理及預防檢查",
        "subtitle": "Perindopril & Rosuvastatin · CVD risk score, BP trend & preventive checkups",
        "welcome_en": (
            "Hello Ms. Lau, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How are you feeling today?"
        ),
        "welcome_zh": (
            "劉女士你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你今日覺得點樣？"
        ),
        "suggested_replies_en": [
            "My home blood pressure has been creeping up to around 148/92 this week on Perindopril 4mg — no chest pain or shortness of breath. Could Dr. Wong review my blood pressure, cholesterol, and any routine checkups I'm due for?",
            "Yes, I take my Perindopril and Rosuvastatin every day with no side effects — please bundle my flu shot and routine checks with the visit.",
        ],
        "suggested_replies_zh": [
            "今個星期食住 Perindopril 4mg 屋企血壓升到大約 148/92，冇心口痛亦冇氣促。請問黃醫生需唔需要覆核血壓藥、膽固醇，同埋我有冇常規檢查到期？",
            "有呀，每日都有準時食 Perindopril 同 Rosuvastatin，冇咩副作用——麻煩覆診時一併幫我安排流感針同常規檢查。",
        ],
    },
    # ---- Hospital Follow-Up & Chronic Care ----
    {
        "key": "ho_thyroid",
        "category": "chronic",
        "patient_id": "pat_ho_thyroid",
        "patient_name": "HO Siu-Fong",
        "patient_name_zh": "何小芳",
        "age_sex": "44F",
        "pathway_node_id": "ebm.pathway.thyroid_fatigue",
        "title": "Ongoing fatigue & thyroid blood test",
        "title_zh": "持續疲倦及甲狀腺驗血報告",
        "subtitle": "TSH 11.4, Free T4 8.2 · already taking daily oral iron",
        "welcome_en": (
            "Hi Siu-Fong, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How have you been feeling, and how can I help today?"
        ),
        "welcome_zh": (
            "小芳你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你最近身體覺得點樣，有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "Still feeling exhausted and cold all the time, and yes I take my iron tablet every morning with breakfast. Do I need thyroid medicine?",
            "Got it — I can take thyroid medication first thing on an empty stomach and move my iron to lunch. Can we review with Dr. Wong?",
        ],
        "suggested_replies_zh": [
            "仲係好攰同好怕凍，我每朝食早餐都有食鐵丸。係咪需要開始食甲狀腺藥？",
            "明白——我可以朝早空肚食甲狀腺藥，中午先食鐵丸。可以安排黃醫生跟進嗎？",
        ],
    },
    # ---- Checkups, Family & Routine Care ----
    {
        "key": "kwok_proxy",
        "category": "family",
        "patient_id": "pat_kwok_proxy",
        "patient_name": "KWOK Wing-Keung",
        "patient_name_zh": "郭永強",
        "age_sex": "78M",
        "pathway_node_id": "ebm.pathway.chronic_htn_home_bp",
        "title": "Elderly father dizzy after morning meds",
        "title_zh": "代年長父親查詢頭暈（家屬代訊）",
        "subtitle": "Messaged by daughter Joyce & helper · on Amlodipine + Losartan",
        "welcome_en": (
            "Hello, I'm Sam, the care assistant at Central & Mid-Levels Family Medical, helping with Mr. Kwok Wing-Keung's care. Could you let me know who I'm speaking with and how he's doing today?"
        ),
        "welcome_zh": (
            "你好，我係中環及半山家庭醫務中心嘅護理助手 Sam，負責跟進郭永強先生嘅護理。請問你係邊位家人，郭先生今日感覺點樣？"
        ),
        "suggested_replies_en": [
            "Hi Sam, this is Joyce (his daughter) — our helper Maria said Dad felt slightly lightheaded standing up after his morning BP pills (118/72). Should we stop his Losartan?",
            "Got it — he's resting and drinking water now. Could we book a review with Dr. Wong this week and check his kidney function?",
        ],
        "suggested_replies_zh": [
            "我係佢個女 Joyce，姐姐 Maria 話爸爸今朝食完血壓藥起身有少少頭暈（血壓 118/72），冇跌親亦冇心口痛，係咪要停藥？",
            "明白，佢今日飲緊水休息緊，我想幫佢約今個星期睇醫生同驗吓腎功能。",
        ],
    },
    {
        "key": "ng_quarantine",
        "category": "family",
        "patient_id": "pat_ng_quarantine",
        "patient_name": "NG Siu-Lun",
        "patient_name_zh": "吳兆麟",
        "age_sex": "67M",
        "pathway_node_id": "ebm.pathway.borderline_ldl",
        "title": "Outside blood test report follow-up",
        "title_zh": "外間診所驗血報告跟進",
        "subtitle": "Faxed lab PDF received · routine identity check before chart link",
        "welcome_en": (
            "Hello Mr. Ng, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How can I help you today?"
        ),
        "welcome_zh": (
            "吳先生你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。今日有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "Hi Sam, I had a blood test done at a private centre in Tsim Sha Tsui yesterday and they faxed the report over — did Dr. Wong receive it yet?",
            "Thanks for catching that typo! My correct date of birth is 14 Aug 1959 — happy for Nurse Rachel to verify and link it.",
        ],
        "suggested_replies_zh": [
            "Sam 你好，我琴日喺尖沙咀私家化驗所驗咗血，佢哋傳真咗報告過嚟，請問黃醫生收到未？",
            "多謝你哋咁細心核對！我正確出生日期係 1959 年 8 月 14 日，麻煩陳護士幫手核實返。",
        ],
    },
    {
        "key": "chui_cough",
        "default_lang": "zh-HK",
        "category": "family",
        "patient_id": "pat_chui_cough",
        "patient_name": "CHUI Ka-Ming",
        "patient_name_zh": "徐家明",
        "age_sex": "41M",
        "pathway_node_id": "ebm.pathway.acute_urti_g6pd",
        "title": "Lingering dry cough — asking for syrup",
        "title_zh": "持續乾咳想配止咳水",
        "subtitle": "Requesting strong cough medicine · doctor consult & safety review",
        "welcome_en": (
            "Hi Ka-Ming, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. What's been bothering you today?"
        ),
        "welcome_zh": (
            "家明你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你今日有咩唔舒服？"
        ),
        "suggested_replies_en": [
            "I've had a dry tickly cough keeping me up at night for 4 days. Can you just send me the strong codeine cough syrup I had last year?",
            "Fair enough — no fever and no shortness of breath, just a dry cough at night. Let's book a quick doctor visit today.",
        ],
        "suggested_replies_zh": [
            "呢四晚乾咳到瞓唔到覺，可唔可以直接幫我開返上年嗰隻可待因強效止咳水？",
            "明白，冇發燒亦冇氣促，只係夜晚乾咳。咁幫我約今日睇黃醫生啦。",
        ],
    },
)


def seed_extra_journey_prior_record(session: Session, tenant_id: str, j_key: str) -> tuple[str | None, str, str]:
    """Seed verified historical EMR record & compliance-gated AgentMemoryEntry for episodes 6–12."""
    if j_key == "tang_uti":
        quote = "Allergies: NKDA | Special Alert: G6PD Deficient (avoid nitrofurantoin, sulfonamides, aspirin)."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_tang_uti",
            tier="longitudinal", topic="g6pd_alert",
            summary_text="G6PD Deficient on file; avoid Nitrofurantoin and Trimethoprim-Sulfamethoxazole.",
            source_quote=quote,
        ))
        return "att_tang_chart", "Clinic Safety Profile · G6PD Flag", quote

    if j_key == "tsang_thin":
        quote = "Baseline Health Profile (2025): Healthy 34M, NKDA, no chronic medications."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_tsang_thin",
            tier="episodic", topic="baseline_profile",
            summary_text="Healthy 34M, NKDA, no regular medications on file.",
            source_quote=quote,
        ))
        return "att_tsang_base", "Clinic Baseline Profile · 2025", quote

    if j_key == "lau_chest":
        quote = "Active Meds: Perindopril 4mg daily, Rosuvastatin 10mg nocte · History: Essential HTN, Hyperlipidaemia."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_lau_chest",
            tier="longitudinal", topic="cardiovascular_profile",
            summary_text="64F with HTN & hyperlipidaemia on Perindopril 4mg + Rosuvastatin 10mg.",
            source_quote=quote,
        ))
        return "att_lau_cv", "Clinic Cardiovascular Profile · Oct 2026", quote

    if j_key == "ho_thyroid":
        doc = ingest_external_document(
            session,
            tenant_id=tenant_id,
            external_doc_ref="KM-LAB-2026-1119-4402",
            doc_kind="lab_report",
            source_org="KingMed Diagnostics (HK)",
            claimed_mrn_or_hkid="PAT-44029",
            claimed_name="HO Siu-Fong",
            claimed_dob="1982-01-16",
            verbatim_quote="Serum TSH: 11.4 mIU/L (HIGH; Ref 0.4–4.0) · Free T4: 8.2 pmol/L (LOW; Ref 12.0–22.0) · Anti-TPO: Positive.",
            page_ref="p.1",
            parsed_diff={"tsh_miu_l": 11.4, "free_t4_pmol_l": 8.2},
        )
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_ho_thyroid",
            tier="longitudinal", topic="thyroid_panel",
            summary_text="Overt primary hypothyroidism (TSH 11.4, Free T4 8.2); currently takes Ferrous sulfate 200mg daily.",
            source_doc_id=doc.id, source_quote=doc.verbatim_quote,
        ))
        return doc.id, "KingMed Thyroid Panel · 19 Nov 2026", doc.verbatim_quote

    if j_key == "kwok_proxy":
        quote = "Active Meds: Amlodipine 5mg daily, Losartan 50mg daily · Last eGFR: >18 months ago (not recent)."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_kwok_proxy",
            tier="longitudinal", topic="elderly_htn_profile",
            summary_text="78M on dual antihypertensives (Amlodipine + Losartan); no recent eGFR on file; daughter Joyce listed as family contact.",
            source_quote=quote,
        ))
        return "att_kwok_htn", "Clinic Medication List · Sep 2026", quote

    if j_key == "ng_quarantine":
        quote = "Clinic EMR Record: NG Siu-Lun (MRN PAT-67033 · DOB 1959-08-14) · On Atorvastatin 20mg nocte."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_ng_quarantine",
            tier="episodic", topic="emr_identity_baseline",
            summary_text="NG Siu-Lun (67M, DOB 1959-08-14) awaiting outside private lab report.",
            source_quote=quote,
        ))
        return "att_ng_base", "Clinic EMR Identity Profile", quote

    # chui_cough
    quote = "Clinic Baseline (2025): 41M, NKDA, non-smoker, prior viral URTI treated conservatively."
    session.add(AgentMemoryEntry(
        id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_chui_cough",
        tier="episodic", topic="prior_urti",
        summary_text="41M, NKDA, healthy adult; no chronic respiratory disease.",
        source_quote=quote,
    ))
    return "att_chui_base", "Clinic Baseline Profile · 2025", quote


def build_extra_initial_summary(
    j_key: str,
    j_meta: dict[str, Any],
    doc_id: str | None,
    doc_label: str,
    doc_quote: str,
    welcome_msg: str,
    hhmm: str,
) -> dict[str, Any]:
    """Build clean 5/5-linted initial summary for episodes 6–12 when phone starts chat."""
    chat = [{"actor": "Sam", "time": hhmm, "text": welcome_msg, "lang": "zh-HK" if _CJK_RE.search(welcome_msg) else "en"}]
    base_att = [{"id": doc_id or f"att_{j_key}", "label": doc_label, "quote": doc_quote, "verified_3pt": True}]

    if j_key == "tang_uti":
        return {
            "alerts": [
                {"level": "info", "label": "G6PD Deficient · Clinician note", "detail": "Avoid Nitrofurantoin & Co-trimoxazole · NKDA (Augmentin is G6PD-safe)"},
            ],
            "scheduled_tasks": [{"status": "waiting", "label": "Careline conversation active with patient"}],
            "issues": [{
                "id": "iss_tang_init",
                "name": "1. Patient check-in — gathering history",
                "bullets": ["Patient opened Careline chat; awaiting symptom details."],
                "safety_gap": None,
                "suggested_orders": [],
            }],
            "attachments": base_att,
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j_meta['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    if j_key == "ho_thyroid":
        return {
            "alerts": [
                {"level": "high", "label": "TSH Elevated (11.4 mIU/L)", "detail": "Low Free T4 (8.2 pmol/L) · Takes daily oral Ferrous sulfate"},
            ],
            "scheduled_tasks": [{"status": "waiting", "label": "Careline check-in active ahead of thyroid review"}],
            "issues": [{
                "id": "iss_ho_init",
                "name": "1. Thyroid panel review — awaiting patient reply",
                "bullets": ["KingMed thyroid results matched (3-pt ID); Sam checking symptom burden and iron timing."],
                "safety_gap": None,
                "suggested_orders": [],
            }],
            "lab_panel": {
                "source": "KingMed Thyroid Panel · 19 Nov 2026",
                "rows": [
                    {"test": "Serum TSH", "val": "11.4 mIU/L", "ref": "0.4–4.0", "flag": "HIGH"},
                    {"test": "Free T4", "val": "8.2 pmol/L", "ref": "12.0–22.0", "flag": "LOW"},
                    {"test": "Anti-TPO Ab", "val": "Positive", "ref": "Negative", "flag": "HIGH"},
                ],
            },
            "attachments": base_att,
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j_meta['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    if j_key == "kwok_proxy":
        return {
            "alerts": [
                {"level": "info", "label": "Age 78 · Dual Antihypertensives", "detail": "Amlodipine 5mg + Losartan 50mg · No recent eGFR (>18m)"},
            ],
            "scheduled_tasks": [{"status": "waiting", "label": "Careline check-in active with family contact"}],
            "issues": [{
                "id": "iss_kwok_init",
                "name": "1. Family check-in — clarifying reporter & symptoms",
                "bullets": ["Careline opened on Mr. Kwok's record; Sam confirming reporter identity and symptoms."],
                "safety_gap": None,
                "suggested_orders": [],
            }],
            "attachments": base_att,
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j_meta['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    if j_key == "lau_chest":
        return {
            "clinical_scores": [
                compute_cvd_10yr_tier(age=64, smoker=False, systolic_bp=148, ldl_mmol_l=2.9),
            ],
            "alerts": [
                {"level": "info", "label": "Cardiovascular Risk Profile", "detail": "On Perindopril 4mg + Rosuvastatin 10mg · Baseline ECG & Echo on file"},
            ],
            "scheduled_tasks": [{"status": "waiting", "label": "Careline conversation active with patient"}],
            "issues": [{
                "id": "iss_lau_chest_init",
                "name": "1. Cardiovascular chronic care check-in — gathering history",
                "bullets": ["Patient opened Careline chat; awaiting symptom & home BP details."],
                "safety_gap": None,
                "suggested_orders": [],
            }],
            "attachments": base_att,
            "sam_proactive_insight": None,
            "agent_thread": [],
            "patient_chat_label": f"{j_meta['patient_name']} ↔ Sam Careline Chat",
            "original_chat": chat,
        }

    # tsang_thin, ng_quarantine, chui_cough
    return {
        "alerts": [{"level": "info", "label": "No Known Drug Allergies", "detail": "Standard clinic profile loaded"}],
        "scheduled_tasks": [{"status": "waiting", "label": "Careline conversation active with patient"}],
        "issues": [{
            "id": f"iss_{j_key}_init",
            "name": "1. Patient check-in — gathering history",
            "bullets": ["Patient opened Careline chat; awaiting reason for contact before staging orders."],
            "safety_gap": None,
            "suggested_orders": [],
        }],
        "attachments": base_att,
        "sam_proactive_insight": None,
        "agent_thread": [],
        "patient_chat_label": f"{j_meta['patient_name']} ↔ Sam Careline Chat",
        "original_chat": chat,
    }


def compile_extra_journey_turn(
    *,
    session: Session,
    tenant_id: str,
    j_key: str,
    patient_text: str,
    existing_summary: dict[str, Any],
    prior_patient_msgs: list[str],
    is_followup_turn: bool,
    is_simple_ack: bool,
    cds_blocked_drugs: list[str],
    is_zh: bool,
    hhmm: str,
) -> tuple[str, str, str, str, str, dict[str, Any], str, list[str]]:
    """Compile dynamic multi-turn clinical workspace updates & natural fallback replies for episodes 6–12."""
    low = (patient_text or "").lower()
    all_text = " ".join([*prior_patient_msgs, patient_text])
    all_low = all_text.lower()
    summary = copy.deepcopy(existing_summary)
    steps: list[str] = []

    # 2. TANG Ka-Yan (Acute Uncomplicated UTI — pregnancy + pyelonephritis red flags + G6PD check -> HK GP Augmentin 375mg TDS x 5d)
    if j_key == "tang_uti":
        confirmed_no_preg = any(
            k in all_low or k in all_text
            for k in (
                "no pregnancy", "not pregnant", "no chance of pregnancy", "definitely not pregnant",
                "negative pregnancy", "isn't pregnant", "cannot be pregnant", "can't be pregnant",
                "冇懷孕", "無懷孕", "冇可能懷孕", "唔係懷孕", "冇大肚",
            )
        )
        confirmed_preg = (not confirmed_no_preg) and any(
            k in all_low or k in all_text
            for k in ("i am pregnant", "i'm pregnant", "might be pregnant", "could be pregnant", "positive pregnancy", "懷孕咗", "有咗身孕", "大肚")
        )
        preg_answered = confirmed_no_preg or confirmed_preg
        confirmed_no_red_flags = any(
            k in all_low or k in all_text
            for k in (
                "no fever", "no back pain", "no flank pain", "no chills", "no vomiting", "no nausea",
                "afebrile", "none of those", "no red flags", "冇發燒", "冇腰痛", "冇背痛", "冇發冷", "冇作嘔",
            )
        )
        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {hhmm}",
            "encounter_context": "Inbound Remote · Self-Reported",
            "items": [
                {"label": "Symptoms", "val": "Dysuria & frequency (<24h)"},
                {"label": "Red flags", "val": "Afebrile · No flank pain, chills, or vomiting" if confirmed_no_red_flags else "Pending red-flag screen (fever / chills / flank pain / vomiting)"},
                {"label": "Pregnancy", "val": ("Pregnant — confirm trimester" if confirmed_preg else ("Denies pregnancy" if confirmed_no_preg else "Pending confirmation"))},
            ],
        }
        uti_alerts: list[dict[str, str]] = [
            {
                "level": "info",
                "label": "G6PD Deficient · Clinician note",
                "detail": "Avoid Nitrofurantoin & Co-trimoxazole · NKDA (Augmentin 375mg TDS × 5d or Cefuroxime 250mg BD × 5d safe)",
            },
        ]
        if confirmed_preg:
            uti_alerts.insert(0, {
                "level": "high",
                "label": "Pregnancy Reported",
                "detail": "Confirm gestational age & MSU culture; Augmentin / Cefuroxime require antenatal clinician sign-off.",
            })
        elif not preg_answered:
            uti_alerts.append({
                "level": "high",
                "label": "Confirm Pregnancy Status",
                "detail": "Awaiting patient confirmation of pregnancy status before antibiotic sign-off.",
            })
        summary["alerts"] = uti_alerts

        if confirmed_preg:
            safety_gap: str | None = "Patient reported possible pregnancy — confirm gestational age and MSU culture before prescribing."
        elif not preg_answered and not confirmed_no_red_flags:
            safety_gap = "Confirm pregnancy status and screen pyelonephritis red flags (fever, chills, flank pain, vomiting) before prescribing."
        elif not preg_answered:
            safety_gap = "Confirm pregnancy status before prescribing."
        elif not confirmed_no_red_flags:
            safety_gap = "Confirm absence of fever, chills, flank pain, or vomiting before prescribing."
        else:
            safety_gap = None

        summary["issues"] = [{
            "id": "iss_uti",
            "name": "1. Acute uncomplicated cystitis",
            "bullets": [
                (
                    "Acute burning on urination and urinary frequency since yesterday evening; denies fever, chills, flank pain, or vomiting."
                    if confirmed_no_red_flags
                    else "Acute burning on urination and urinary frequency since yesterday evening; screening for fever, chills, flank pain, and vomiting."
                ),
                "G6PD deficiency on chart (Nitrofurantoin & Co-trimoxazole contraindicated) · NKDA · Augmentin (Amoxicillin-Clavulanate) 375 mg TDS × 5 days staged (alternative: Cefuroxime 250 mg BD × 5 days).",
            ],
            "safety_gap": safety_gap,
            "suggested_orders": [
                {
                    "id": "tang_rx_augmentin",
                    "type": "rx",
                    "action": "start",
                    "issue": "1. Acute uncomplicated cystitis",
                    "drug": "Amoxicillin-Clavulanate (Augmentin) 375mg",
                    "dose_mg": 375,
                    "freq": "TDS",
                    "days": "5d",
                    "note": "HK private GP G6PD-safe choice (alternative: Cefuroxime 250mg BD × 5d); NKDA",
                },
                {"id": "tang_lab_msu", "type": "lab", "issue": "1. Acute uncomplicated cystitis", "testName": "Urine Dipstick / MSU Culture", "lab": "Clinic / KingMed", "fasting": False, "when": "Before starting Abx or if >48h"},
                {
                    "id": "tang_leaflet",
                    "type": "goal",
                    "issue": "1. Acute uncomplicated cystitis",
                    "metric": "UTI Hydration & Red-Flag Leaflet",
                    "leaflet_id": "uti_hydration_hk",
                    "title": "Acute UTI Hydration & Red-Flag Care Leaflet",
                    "template": "Central Medical · Uncomplicated Cystitis Care Sheet",
                    "target_text": "Drink 2L water daily; complete the full 5-day Augmentin course; seek urgent review if fever, chills, vomiting, or flank pain develop.",
                },
            ],
        }]
        summary["sam_proactive_insight"] = (
            "G6PD deficiency is recorded on chart (Nitrofurantoin and Co-trimoxazole contraindicated); NKDA. "
            "Augmentin (Amoxicillin-Clavulanate) 375 mg TDS × 5 days is staged as the G6PD-safe HK private GP option (Cefuroxime 250 mg BD × 5 days as alternative)."
        )
        agent_thread = list(summary.get("agent_thread") or [])
        preg_note = (
            "Pregnancy denied" if confirmed_no_preg
            else ("Possible pregnancy reported" if confirmed_preg else "Pregnancy status requested")
        )
        rf_note = "pyelonephritis red flags denied (afebrile, no flank pain/chills/vomiting)" if confirmed_no_red_flags else "screening pyelonephritis red flags"
        uti_reason = (
            f"UTI intake: {rf_note}; {preg_note}. Cross-checked G6PD deficiency (Nitrofurantoin & Co-trimoxazole blocked) and NKDA → staged Augmentin 375 mg TDS × 5 days (Cefuroxime 250 mg BD × 5 days alternative)."
        )
        agent_thread = [x for x in agent_thread if not str(x.get("text", "")).startswith("UTI intake:")]
        agent_thread.append({"actor": "Sam", "time": hhmm, "text": uti_reason})
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("PracticeGraph · Traversed ebm.pathway.acute_uti (HK GP G6PD-safe Augmentin 375mg TDS x5d)")
        steps.append("PresentationCompiler · Staged Augmentin 375mg TDS x5d + UTI leaflet (5/5 linter pass)")

        if is_simple_ack and is_followup_turn:
            reply = (
                "唔使客氣，黃醫生審閱同確認處方之後就會喺呢度通知你，記得多飲清水。"
                if is_zh
                else "You are welcome. As soon as Dr. Wong reviews and confirms your prescription, it will appear right here. Keep drinking plenty of water in the meantime."
            )
        elif confirmed_preg:
            reply = (
                "多謝你話我知可能懷孕。我已經即時通知黃醫生，等醫生確認懷孕週數同埋安排適合孕婦使用嘅安全治療及小便化驗。期間記得多飲清水，如果出現發燒、發冷或腰背痛，請即刻通知我哋。"
                if is_zh
                else "Thank you for letting me know you may be pregnant. I have flagged this straight to Dr. Wong so she can review your gestational stage, arrange a urine test, and select a pregnancy-safe antibiotic. Please keep up your fluids and let us know straight away if you develop fever, chills, vomiting, or lower back pain."
            )
        elif not preg_answered or not confirmed_no_red_flags:
            reply = (
                "明白，小便刺痛同尿頻真係好唔舒服。為咗幫黃醫生安全安排治療，想先確認兩件事：第一，你有冇發燒、發冷、腰背痛或者作嘔？第二，你有冇機會懷孕？"
                if is_zh
                else "Sorry you are dealing with that. Before I pass your summary to Dr. Wong, could you confirm two quick things: are you having any fever, chills, lower back pain, or vomiting, and is there any chance you could be pregnant?"
            )
        else:
            reply = (
                "多謝你確認。我已經將你嘅症狀同病歷安全核對結果整理畀醫生團隊，亦查閱咗今日診所時間表——張詠欣醫生（急症及婦女健康）今日 11:15 有空檔，你可以喺下方一鍵確認親臨診所或視像診症，或者揀其他醫生時間。期間記得多飲清水，如果出現發燒、發冷或腰背痛，請即刻通知我哋。"
                if is_zh
                else "Thank you for confirming. I have passed your details and chart safety checks to the clinic team so the doctor can arrange an appropriate antibiotic prescription, and I checked today's schedule — Dr. Wing-Yan Cheung has an open 11:15 slot below (in-clinic or teleconsult, or you can pick another doctor). Keep drinking plenty of water, and let us know straight away if you develop fever, chills, vomiting, or lower back pain."
            )
        return (
            "needs_decision",
            "with_doctor",
            "Acute Uncomplicated UTI",
            "Burning urination & frequency (<24h); G6PD-safe Augmentin 375mg TDS x5d staged.",
            "Today · doctor review",
            summary,
            reply,
            steps,
        )

    # 7. TSANG Chun-Hin (Autonomous Registrar Intake Clarification — responds to actual intent, zero nurse-questionnaire button!)
    if j_key == "tsang_thin":
        asked_antibiotics = any(k in low for k in ("antibiotic", "amoxicillin", "抗生素", "消炎藥"))
        asked_results_or_med = any(k in low for k in ("test result", "blood test", "result", "medication", "prescription", "refill", "報告", "驗血", "藥物"))
        asked_booking = any(k in low for k in ("book", "reschedule", "appointment", "slot", "預約", "改期"))
        gave_specifics = any(k in low for k in ("nose", "stuffy", "fatigue", "cough", "throat", "fever", "stomach", "slot", "鼻塞", "攰", "咳", "喉嚨", "發燒", "15"))

        agent_thread = list(summary.get("agent_thread") or [])

        if asked_results_or_med and not asked_antibiotics and not gave_specifics:
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "Registrar reasoning: Patient selected test-result / medication follow-up → clarifying which lab report or medication they're asking about in the patient loop before involving Dr. Wong.",
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("CareAgent · Clarifying test result / medication follow-up autonomously in patient loop")
            reply = (
                "好呀！請問你想跟進邊一份驗血／檢查報告，或者邊一種藥物？如果有外間報告相片或詳情，都可以直接喺度話我知，我幫你整理好再交畀醫生。"
                if is_zh
                else "Of course! Which test result or medication would you like to follow up on? Feel free to share the details or any symptoms you've noticed, and I'll line it up for Dr. Wong."
            )
            return (
                "running",
                "with_patient",
                "Test Result / Medication Follow-Up — Clarifying",
                "Sam is clarifying which result or medication the patient is following up on.",
                "Careline intake",
                summary,
                reply,
                steps,
            )

        if asked_booking and not gave_specifics:
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "Registrar reasoning: Patient requested appointment booking/rescheduling → handling logistics autonomously in patient loop without interrupting doctor.",
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("CareAgent · Handling appointment logistics autonomously")
            reply = (
                "冇問題！我們今日下午 16:15 有 15 分鐘診症時段，聽日朝早亦有空檔。你想預約邊個時間，以及大約係睇邊方面？"
                if is_zh
                else "Happy to help with that! We have a 15-minute slot today at 16:15 or openings tomorrow morning. Which works better for you, and what would you like to be seen for?"
            )
            return (
                "running",
                "with_patient",
                "Appointment Request — Scheduling",
                "Sam is confirming preferred slot and visit reason autonomously.",
                "Careline scheduling",
                summary,
                reply,
                steps,
            )

        if not gave_specifics:
            summary["intake_sufficiency"] = "insufficient"
            summary["issues"] = [{
                "id": "iss_thin",
                "name": "1. Undifferentiated unwellness — gathering history",
                "bullets": [
                    f"Patient messaged: “{patient_text.strip()}”.",
                    "Sam is autonomously running structured symptom & red-flag clarification in the patient loop before escalating to Dr. Wong.",
                ],
                "safety_gap": "Clarify symptom site, fever, duration, and red flags before any prescribing decision.",
                "suggested_orders": [
                    {"id": "tsang_book", "type": "book", "issue": "1. Undifferentiated unwellness — gathering history", "visitType": "15-min GP Assessment", "when": "Today 16:15"},
                ],
            }]
            summary["sam_proactive_insight"] = None
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "Registrar reasoning: Thin intake received → holding autonomously in patient loop to gather symptom site, onset, fever/home vitals, and red flags (zero premature Rx or nurse handoff).",
            })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("IntakeGuard · Autonomous registrar clarification in patient loop (0 premature Rx)")
            if asked_antibiotics:
                reply = (
                    "明白你返工好忙，不過抗生素需要先由醫生了解具體症狀先可以決定。可以簡單講吓你主要邊度唔舒服（例如鼻塞、喉嚨痛、咳定發燒），以及有冇氣促或心口痛嗎？我亦可以幫你預留今日 15 分鐘時段。"
                    if is_zh
                    else "I hear you on a busy workday — antibiotics need a quick symptom check and doctor review first. Could you share what specific symptoms you're having (like fever, sore throat, stuffy nose, or cough) and confirm you have no chest pain or shortness of breath? We also have quick 15-minute slots later today."
                )
            else:
                reply = (
                    "明白，想幫你安排最合適嘅跟進——可以簡單講多啲你而家有咩唔舒服、持續咗幾耐，以及有冇發燒、氣促或心口痛嗎？"
                    if is_zh
                    else "Got it — to make sure we get you the right care quickly, could you share a bit more about what symptoms you're experiencing, how long they've lasted, and whether you have any fever, shortness of breath, or chest pain?"
                )
            return (
                "running",
                "with_patient",
                "Feeling Unwell — Autonomous Registrar Intake",
                "Sam is autonomously clarifying symptoms & red flags before packaging for doctor review.",
                "Careline intake",
                summary,
                reply,
                steps,
            )

        # Turn 2: Patient provided clear specifics -> promote cleanly to doctor!
        summary["intake_sufficiency"] = "sufficient"
        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {hhmm}",
            "encounter_context": "Inbound Remote · Clarified",
            "items": [
                {"label": "Symptoms", "val": "Mild fatigue & nasal congestion (1d)"},
                {"label": "Fever / Cough", "val": "Denies fever or cough"},
                {"label": "Red flags", "val": "Denies chest pain or SOB"},
            ],
        }
        summary["scheduled_tasks"] = [{"status": "booked", "label": "Today 16:15 · 15-min GP Assessment (requested)"}]
        summary["issues"] = [{
            "id": "iss_tsang_clarified",
            "name": "1. Mild upper respiratory congestion & fatigue",
            "bullets": [
                "Patient clarified mild nasal congestion and fatigue since yesterday with no fever or red flags.",
                "Antibiotic therapy not indicated on history; 15-minute GP assessment requested for today.",
            ],
            "safety_gap": None,
            "suggested_orders": [
                {"id": "tsang_book2", "type": "book", "issue": "1. Mild upper respiratory congestion & fatigue", "visitType": "15-min GP Assessment", "when": "Today 16:15"},
            ],
        }]
        summary["sam_proactive_insight"] = None
        agent_thread.append({
            "actor": "Sam",
            "time": hhmm,
            "text": "Registrar reasoning: Structured symptom & red-flag screen complete (1d mild congestion/fatigue, afebrile, no red flags) → promoted to Dr. Wong with 16:15 consult slot.",
        })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("IntakeGuard · Clarifying details received -> intake_sufficiency upgraded to 'sufficient'")
        reply = (
            "多謝你講得咁清楚——冇發燒亦冇氣促或心口痛係好消息。我已經幫你記低畀黃醫生，並預留今日下午 4:15 嘅 15 分鐘診症時段，等醫生親身評估需唔需要用藥。"
            if is_zh
            else "Thanks for sharing those details — having no fever or warning signs is reassuring. I've updated Dr. Wong's notes and held a 15-minute slot at 16:15 today so she can assess you and confirm the right treatment."
        )
        return (
            "needs_decision",
            "with_doctor",
            "Mild Nasal Congestion & Fatigue — 16:15 Slot",
            "Clarified 1-day stuffy nose & fatigue without fever or red flags; 16:15 GP consult held.",
            "Today 16:15 consult",
            summary,
            reply,
            steps,
        )

    # 8. HO Siu-Fong (Thyroid / Fatigue + Levothyroxine-Iron 4h Spacing!)
    if j_key == "ho_thyroid":
        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {hhmm}",
            "items": [
                {"label": "Symptoms", "val": "Persistent fatigue & cold intolerance"},
                {"label": "Current Meds", "val": "Ferrous sulfate 200mg daily (morning)"},
                {"label": "Red flags", "val": "No chest pain, syncope, or palpitations"},
            ],
        }
        summary["issues"] = [{
            "id": "iss_thyroid",
            "name": "1. Overt primary hypothyroidism",
            "bullets": [
                "Fatigue and cold intolerance with elevated TSH, low Free T4, and positive Anti-TPO antibodies (see Thyroid Panel).",
                "Currently taking morning oral iron — Levothyroxine requires at least 4-hour spacing to prevent malabsorption.",
            ],
            "safety_gap": "Advise taking Levothyroxine fasting on waking and separating oral iron by ≥ 4 hours (NICE NG145).",
            "suggested_orders": [
                {"id": "ho_rx_levo", "type": "rx", "action": "start", "issue": "1. Overt primary hypothyroidism", "drug": "Levothyroxine 50mcg", "freq": "OD morning fasting", "days": "60d", "note": "Separate from oral iron by ≥4 hours"},
                {"id": "ho_lab_tsh", "type": "lab", "issue": "1. Overt primary hypothyroidism", "testName": "Repeat Serum TSH + Free T4", "lab": "KingMed", "fasting": False, "when": "In 6 weeks"},
                {"id": "ho_book", "type": "book", "issue": "1. Overt primary hypothyroidism", "visitType": "15-min GP Thyroid Review", "when": "In 6 weeks (post-labs)"},
            ],
        }]
        summary["sam_proactive_insight"] = (
            "NICE NG145 Hypothyroidism Guideline: TSH 11.4 mIU/L + Free T4 8.2 pmol/L confirms overt primary hypothyroidism. "
            "Active CDS note: patient takes daily Ferrous sulfate — enforce ≥4-hour spacing from Levothyroxine 50mcg."
        )
        steps.append("PracticeGraph · Traversed ebm.pathway.thyroid_fatigue (NICE NG145 + Levothyroxine-iron 4h spacing)")
        steps.append("PresentationCompiler · Mounted Thyroid Panel & 50mcg starting option (5/5 linter pass)")

        if is_followup_turn and any(k in low for k in ("lunch", "empty stomach", "move", "review", "中午", "空肚", "明白")):
            reply = (
                "好主意！朝早起身空肚用清水服食甲狀腺藥、中午先食鐵丸就完全唔會影響吸收。我已經通知咗黃莎拉醫生，等醫生審閱確認處方同 6 星期後覆驗抽血。"
                if is_zh
                else "That schedule works great — taking Levothyroxine on an empty stomach first thing and moving your iron to lunchtime avoids any absorption interference. I've let Dr. Sarah Wong know so she can review and sign off your starting prescription plus a 6-week repeat blood test."
            )
        else:
            reply = (
                "聽到你成日好攰又怕凍辛苦晒。你嘅金域驗血報告顯示甲狀腺功能偏低（TSH 11.4、Free T4 8.2），黃莎拉醫生好可能會同你傾開始低劑量甲狀腺素補充。\n\n"
                "有個好重要嘅用藥貼士我已經標註咗畀醫生：因為你每日朝早有食鐵丸，鐵質會阻礙甲狀腺藥吸收，所以如果醫生開藥，兩隻藥需要相隔至少 4 小時（例如朝早空肚食甲狀腺藥，中午先食鐵丸）。"
                if is_zh
                else "I'm sorry you've been feeling so drained and cold. Your KingMed panel shows an underactive thyroid (TSH 11.4, Free T4 8.2), so Dr. Sarah Wong will review starting gentle thyroid replacement with you.\n\nOne key detail I've flagged for Dr. Wong: because you take oral iron each morning, iron blocks thyroid hormone absorption — so if she prescribes Levothyroxine, you'll want to take it on an empty stomach first thing and space your iron tablet at least 4 hours later (such as at lunch)."
            )
        return (
            "needs_decision",
            "with_doctor",
            "Overt Hypothyroidism — Levothyroxine & Iron Spacing",
            "Fatigue & cold intolerance with TSH 11.4 / Free T4 8.2; Levothyroxine–iron 4h spacing flagged.",
            "Today · doctor review",
            summary,
            reply,
            steps,
        )

    # 9. KWOK Wing-Keung (78M — Family Proxy via Daughter Joyce & Helper Maria!)
    if j_key == "kwok_proxy":
        summary["proxy_context"] = {
            "is_proxy": True,
            "reporter_name": "Joyce Kwok (daughter) & Maria (helper)",
            "verified_patient_name": "KWOK Wing-Keung (78M)",
            "relationship": "Daughter & domestic helper",
        }
        summary["vitals_exam"] = {
            "source": f"Proxy Report (Daughter Joyce) · Today {hhmm}",
            "items": [
                {"label": "Home BP", "val": "118/72 mmHg"},
                {"label": "Symptoms", "val": "Mild postural lightheadedness after morning meds"},
                {"label": "Falls / Red flags", "val": "No fall, no syncope, no chest pain"},
            ],
        }
        summary["issues"] = [{
            "id": "iss_kwok_dizz",
            "name": "1. Postural lightheadedness on dual antihypertensives",
            "bullets": [
                "Daughter Joyce (via helper Maria) reported mild standing dizziness after morning Amlodipine 5mg + Losartan 50mg.",
                "Home BP 118/72 mmHg without fall or syncope; caregiver asked whether to stop Losartan (advised not to stop without doctor review).",
            ],
            "safety_gap": "Elderly patient (78M) on Losartan with no recent eGFR (>18m) — check U&E/eGFR and lying-standing BP in clinic.",
            "suggested_orders": [
                {"id": "kwok_lab_ue", "type": "lab", "issue": "1. Postural lightheadedness on dual antihypertensives", "testName": "Renal Function & Electrolytes (U&E + eGFR)", "lab": "KingMed", "fasting": False, "when": "At clinic visit this week"},
                {"id": "kwok_book", "type": "book", "issue": "1. Postural lightheadedness on dual antihypertensives", "visitType": "15-min In-Clinic GP Review (Lying/Standing BP)", "when": "Tomorrow morning"},
                {"id": "kwok_nurse", "type": "nurse", "issue": "1. Postural lightheadedness on dual antihypertensives", "protocol": "bp_inr_followup_call", "assignee": "Nurse Rachel", "task": "Check-in call with Joyce & Mr. Kwok (Cantonese)", "due": "Today afternoon"},
            ],
        }]
        summary["sam_proactive_insight"] = (
            "Elderly Medication Safety (78M on Losartan + Amlodipine): Recent eGFR is >18 months old. "
            "Check renal function/K+ and postural BP before adjusting antihypertensive therapy; proxy reporter logged clearly."
        )
        steps.append("ProxyGuard · Preserved caregiver identity (Joyce/Maria) distinct from patient KWOK Wing-Keung (78M)")
        steps.append("Verification · Elderly unknown-eGFR check flagged on Losartan + Amlodipine")

        if is_followup_turn and not any(k in low for k in ("stop", "losartan", "停藥")):
            reply = (
                "多謝你 Joyce——我已經將爸爸嘅情況同需要驗腎功能嘅提醒放咗喺黃莎拉醫生嘅工作台，並為郭先生預留聽朝診所覆診，順便由陳護士今日晏晝打電話關心一下。"
                if is_zh
                else "Thank you Joyce — I've placed your update and a note to check Mr. Kwok's kidney bloods on Dr. Sarah Wong's desk, held an in-clinic slot for tomorrow morning (to check sitting and standing BP), and flagged Nurse Rachel to give you a quick check-in call this afternoon."
            )
        else:
            reply = (
                "多謝你話我知 Joyce（同埋 Maria）。請先唔好自行停服 Losartan 或血壓藥，先畀郭先生飲足夠清水，起身時慢慢坐起停一停先站立。\n\n"
                "我已經清楚記錄係由你代爸爸匯報（血壓 118/72、冇跌倒冇心口痛），並即時交畀黃莎拉醫生審閱——亦留意到郭先生超過 18 個月未覆驗腎功能，醫生可以一併安排抽血同量度坐立血壓。如果佢出現昏厥、心口痛或者嚴重無力，請即刻致電 999。"
                if is_zh
                else "Thank you for letting us know, Joyce (and Maria). Please don't stop his Losartan or BP tablets unilaterally just yet — encourage Mr. Kwok to stay hydrated and rise slowly from sitting to standing.\n\nI've logged this clearly as a family update on Mr. Kwok's chart (BP 118/72, no fall or chest pain) for Dr. Sarah Wong to review today, and also noted he hasn't had a kidney blood test (eGFR) in over 18 months so she can check that alongside sitting/standing BP. If he feels faint, falls, or has chest pain, please call 999 immediately."
            )
        return (
            "needs_decision",
            "with_doctor",
            "Postural Lightheadedness — Family Proxy Update",
            "Daughter Joyce reported mild dizziness in 78M on Amlodipine + Losartan (BP 118/72); eGFR overdue.",
            "Tomorrow morning · clinic",
            summary,
            reply,
            steps,
        )

    # 10. NG Siu-Lun (External Faxed Lab -> REAL 2/3 ID Mismatch Quarantine to Nurse Rachel!)
    if j_key == "ng_quarantine":
        if not is_followup_turn:
            # Trigger REAL fail-closed 2/3 ID mismatch (DOB 1959-08-19 vs EMR 1959-08-14) -> Nurse Rachel quarantine!
            q_doc = ingest_external_document(
                session,
                tenant_id=tenant_id,
                external_doc_ref="TST-LAB-FAX-2026-67033",
                doc_kind="lab_report",
                source_org="Tsim Sha Tsui Private Diagnostics",
                claimed_mrn_or_hkid="PAT-67033",
                claimed_name="NG Siu-Lun",
                claimed_dob="1959-08-19",  # Intentional 1-digit fax typo vs 1959-08-14!
                verbatim_quote="Fasting Lipid & Renal Panel: LDL-C 2.5 mmol/L (On target) · eGFR 78 mL/min.",
                page_ref="p.1",
                parsed_diff={"ldl_mmol_l": 2.5, "egfr": 78},
            )
            session.flush()
            summary["alerts"] = [
                {"level": "high", "label": "Outside Lab Held (2/3 ID Match)", "detail": f"{q_doc.external_doc_ref}: DOB 1959-08-19 vs EMR 1959-08-14 → Routed to Nurse Rachel"},
            ]
            summary["scheduled_tasks"] = [
                {"status": "waiting", "label": "Nurse Rachel verifying 2/3 ID mismatch in #clinic-team before doctor review"},
            ]
            summary["issues"] = [{
                "id": "iss_ng_id",
                "name": "1. Outside lab report — identity verification in progress",
                "bullets": [
                    "Patient enquired about a faxed blood report from Tsim Sha Tsui Private Diagnostics.",
                    "Automated 3-point ID gate caught a date-of-birth discrepancy (2/3 match) and quarantined the PDF to Nurse Rachel.",
                ],
                "safety_gap": "External lab report held from clinical chart until Nurse Rachel confirms patient DOB in #clinic-team.",
                "suggested_orders": [],
            }]
            summary["sam_proactive_insight"] = None
            steps.append(f"3-Point ID Gate · {q_doc.external_doc_ref} matched 2/3 (DOB mismatch) -> Quarantined to Nurse Rachel in #clinic-team")
            reply = (
                "我哋確實收到尖沙咀化驗所傳真過嚟嘅報告，不過系統嘅三點身份核對發現報告上嘅出生日期同你診所病歷相差幾日（懷疑係化驗所打錯字）。\n\n"
                "為咗保障病人安全，系統已經暫時將份報告交咗畀陳護士（Nurse Rachel）人手核實，核對清楚就會立即放入你嘅病歷畀黃醫生覆核——方便確認一下你嘅出生日期嗎？"
                if is_zh
                else "We did receive a faxed report from Tsim Sha Tsui Private Diagnostics, but our safety check spotted a small date-of-birth discrepancy on the lab cover sheet compared with your clinic record (likely a typo at the lab).\n\nTo make sure results never land on the wrong chart, I've routed it straight to Nurse Rachel to verify before linking it for Dr. Sarah Wong — could you quickly confirm your date of birth for us?"
            )
            return (
                "running",
                "with_nurse",
                "Outside Lab Report — Nurse ID Verification",
                "Faxed TST lab matched 2/3 ID points (DOB typo); quarantined strictly to Nurse Rachel in #clinic-team.",
                "With Nurse Rachel",
                summary,
                reply,
                steps,
            )

        # Follow-up: patient confirmed DOB 14 Aug 1959
        steps.append("CareAgent · Logged patient's DOB confirmation (14 Aug 1959) for Nurse Rachel's #clinic-team check")
        reply = (
            "多謝吳先生確認（1959 年 8 月 14 日）！我已經將你嘅確認記錄交畀陳護士，佢核實並放返入你嘅病歷後，黃莎拉醫生就會審閱你嘅驗血結果。"
            if is_zh
            else "Thank you for confirming 14 Aug 1959, Mr. Ng! I've passed your confirmation straight to Nurse Rachel — as soon as she verifies and links the report to your file, Dr. Sarah Wong will review your results."
        )
        return (
            "running",
            "with_nurse",
            "Outside Lab Report — Ready for Nurse Release",
            "Patient confirmed DOB 14 Aug 1959; awaiting Nurse Rachel's 1-click verification in #clinic-team.",
            "With Nurse Rachel",
            summary,
            reply,
            steps,
        )

    # 11. LAU Wai-Fong — Cardiac Chronic Disease & Prevention Canvas (CVD risk score, BP trend, MedDiff, ECG/Echo, Labs, Preventive Care)
    if j_key == "lau_chest":
        cvd_score = compute_cvd_10yr_tier(age=64, smoker=False, systolic_bp=148, ldl_mmol_l=2.9, sex="F", on_bp_treatment=True)
        summary["clinical_scores"] = [cvd_score]
        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {hhmm}",
            "encounter_context": "Inbound Remote · Home BP Logged",
            "items": [
                {"label": "Home BP", "val": "148/92 mmHg (upward trend)"},
                {"label": "Current Rx", "val": "Perindopril 4mg OM · Rosuvastatin 10mg ON"},
                {"label": "Red flags", "val": "Denies chest pain, dyspnoea, or diaphoresis"},
            ],
        }
        summary["alerts"] = [
            {"level": "high", "label": "Upward Home BP Drift (132 → 148/92)", "detail": "Above <135/85 home target on Perindopril 4mg · Review titration & clinic BP"},
            {"level": "info", "label": f"10-Yr CVD Risk: {cvd_score.get('value', '5–10%')} ({cvd_score.get('band', 'Borderline')})", "detail": "LDL 2.9 mmol/L on Rosuvastatin 10mg (target < 2.6) · Preventive checkups due"},
        ]
        summary["trend_chart"] = {
            "kind": "bp",
            "title": "Home Systolic BP Trend (mmHg)",
            "unit": "mmHg",
            "target_label": "Target < 135",
            "target_value": 135,
            "points": [
                {"label": "Sep", "v": 132},
                {"label": "Oct", "v": 138},
                {"label": "Nov W2", "v": 144},
                {"label": "Today", "v": 148},
            ],
        }
        summary["med_diff"] = {
            "rows": [
                {"drug": "Perindopril", "qeh": "4 mg PO daily", "clinic": "4 mg PO daily", "tag": "Sub-optimal BP control"},
                {"drug": "Rosuvastatin", "qeh": "10 mg PO nocte", "clinic": "10 mg PO nocte", "tag": "LDL 2.9 · Review target"},
            ],
            "qeh": ["Perindopril 4mg PO daily", "Rosuvastatin 10mg PO nocte"],
            "clinic": ["Perindopril 4mg PO daily", "Rosuvastatin 10mg PO nocte"],
        }
        summary["lab_panel"] = {
            "source": "KingMed Cardiovascular Panel · 12 Oct 2026",
            "rows": [
                {"test": "LDL-Cholesterol", "val": "2.9 mmol/L", "ref": "< 2.6 (high risk)", "flag": "HIGH"},
                {"test": "HbA1c", "val": "5.9 %", "ref": "< 6.0", "flag": "Normal"},
                {"test": "eGFR", "val": "78 mL/min", "ref": "> 60", "flag": "Normal"},
                {"test": "Potassium (K+)", "val": "4.3 mmol/L", "ref": "3.5–5.1", "flag": "Normal"},
            ],
        }
        summary["imaging_studies"] = [
            {
                "id": "img_lau_ecg",
                "modality": "Baseline 12-Lead ECG",
                "date": "12 Oct 2026 · Clinic",
                "summary": "Normal sinus rhythm (68 bpm). Mild LVH voltage criteria; no prior ischaemic ST-T changes.",
                "status": "Baseline for comparison",
            },
            {
                "id": "img_lau_echo",
                "modality": "Transthoracic Echocardiogram",
                "date": "Mar 2025 · HKSH",
                "summary": "Preserved LVEF 62%, Grade 1 diastolic dysfunction, no regional wall motion abnormality.",
                "status": "Prior baseline",
            },
        ]
        summary["preventive_care"] = [
            {
                "id": "prev_lau_flu",
                "title": "Seasonal Influenza Vaccine (CV Risk Priority)",
                "badge": "Recommended",
                "detail": "Reduces major adverse cardiovascular events in hypertensive patients (ESC / HK CHP Guideline)",
                "order": {"id": "ord_lau_flu", "type": "book", "issue": "Preventive Care", "visitType": "10-min Nurse Flu Shot", "when": "At clinic review"},
            },
            {
                "id": "prev_lau_mammo",
                "title": "Biennial Breast Mammogram",
                "badge": "Due (Age 64)",
                "detail": "HK Cancer Expert Working Group age 44–69 biennial screening · Bundle referral at routine checkup",
                "order": {"id": "ord_lau_mammo", "type": "imaging", "issue": "Preventive Care", "modality": "Bilateral Screening Mammogram", "indication": "HK biennial breast screening (64F)", "priority": "Routine"},
            },
            {
                "id": "prev_lau_lipid",
                "title": "Fasting Lipid & HbA1c Recheck",
                "badge": "Target LDL < 2.6",
                "detail": "Reassess cardiovascular risk profile & statin response in 8–12 weeks",
                "order": {"id": "ord_lau_lipid", "type": "lab", "issue": "Preventive Care", "testName": "Fasting Lipid Profile + HbA1c + Renal Panel (RFT/eGFR)", "lab": "KingMed", "fasting": True, "when": "In 8 weeks"},
            },
        ]
        confirmed_adherence = any(
            k in all_low or k in all_text
            for k in ("every day", "take my perindopril", "no side effects", "每日都有準時食", "冇咩副作用", "準時食藥")
        )
        summary["issues"] = [{
            "id": "iss_lau_cardiac",
            "name": "1. Chronic hypertension & cardiovascular risk optimization",
            "bullets": [
                "Home BP drifted upward over 8 weeks on Perindopril 4mg; asymptomatic at rest with no chest pain or dyspnoea.",
                "LDL remains above high-risk target on Rosuvastatin 10mg alongside mild baseline LVH on Oct ECG (see 10-Yr CVD Risk Score & BP Trend).",
            ],
            "safety_gap": (
                "Adherence confirmed (daily Perindopril 4mg & Rosuvastatin 10mg, no side effects); verify seated clinic BP before titration."
                if confirmed_adherence
                else "Confirm seated 5-minute home BP technique & medication adherence before Perindopril titration."
            ),
            "suggested_orders": [
                {"id": "lau_book", "type": "book", "issue": "1. Chronic hypertension & cardiovascular risk optimization", "visitType": "In-Clinic GP Cardiovascular Review & Repeat ECG", "when": "Today 14:15 (20 min)"},
                {"id": "lau_rx_perind", "type": "rx", "action": "update", "issue": "1. Chronic hypertension & cardiovascular risk optimization", "drug": "Perindopril 8mg", "freq": "OM daily", "days": "30d", "note": "Consider titration after confirming clinic BP & renal panel"},
                {"id": "lau_lab", "type": "lab", "issue": "1. Chronic hypertension & cardiovascular risk optimization", "testName": "Fasting Lipid Profile + HbA1c + Renal Panel (RFT/eGFR)", "lab": "KingMed", "fasting": True, "when": "Before clinic review"},
                {"id": "lau_goal_bp", "type": "goal", "issue": "1. Chronic hypertension & cardiovascular risk optimization", "metric": "Blood Pressure", "targetSys": 135, "targetDia": 85},
            ],
        }]
        summary["sam_proactive_insight"] = (
            f"10-year CVD risk ({cvd_score.get('value', '5–10%')} · {cvd_score.get('band', 'Borderline')}) with upward home BP drift (132 → 148 mmHg on Perindopril 4mg), "
            "LDL above target on Rosuvastatin 10mg, and mild LVH on baseline ECG. Consider Perindopril titration after RFT/eGFR check and bundle due CV-priority flu shot & biennial mammogram."
        )
        agent_thread = list(summary.get("agent_thread") or [])
        if not any("10-year CVD risk" in str(x.get("text", "")) for x in agent_thread):
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": (
                    f"Registrar reasoning: Confirmed asymptomatic at rest (no chest pain or dyspnoea) → computed deterministic 10-year CVD risk ({cvd_score.get('value', '5–10%')} · {cvd_score.get('band', 'Borderline')}) "
                    "and mounted BP trend, Perindopril/Rosuvastatin profile, Oct ECG/Echo, and cardiovascular labs → staged clinic BP review, Perindopril titration option, Fasting Lipid + HbA1c + RFT/eGFR panel, and preventive checkups (CV-priority flu shot & biennial mammogram)."
                ),
            })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append(f"PracticeGraph · Computed Python 10-Yr CVD Risk ({cvd_score.get('value', '5–10%')}) & mounted Cardiac Prevention Canvas")

        if is_followup_turn and (confirmed_adherence or any(k in low for k in ("ecg", "check", "today", "adjust", "vaccine", "flu", "mammogram", "book", "bundle", "心電圖", "血壓", "覆檢", "預防", "預約", "流感", "一併"))):
            reply = (
                "多謝你確認每日有準時服藥同冇副作用。我已經記錄落病歷，並為黃莎拉醫生一併預備咗季節性流感疫苗、兩年一度乳房造影同空腹血脂／腎功能抽血，等覆診時可以一次過安排。期間如果出現任何心口痛、氣促、頭暈或劇烈頭痛，請即刻通知我哋或致電 999。"
                if is_zh
                else "Thank you for confirming daily adherence with no side effects. I have updated your chart and bundled your CV-priority seasonal flu shot, biennial mammogram referral, and fasting lipid and renal panel for Dr. Sarah Wong's review. If you ever develop chest discomfort, shortness of breath, dizziness, or severe headache, please let us know promptly or call 999."
            )
        else:
            reply = (
                "多謝你定期記錄血壓。由於屋企血壓由 132 慢慢升到 148/92（高於 135/85 目標），加上上次膽固醇略高於理想水平，我已經整理好你嘅血壓趨勢、心血管風險評估同過往心電圖畀黃莎拉醫生覆核。\n\n"
                "我亦查閱咗今日診所時間表——黃莎拉醫生今日 14:15 有 20 分鐘空檔可以一併檢視血壓藥、安排心血管優先流感針同兩年一度乳房造影（可喺下方確認或揀其他時段）。請問你每日有冇準時食 Perindopril 同 Rosuvastatin，有冇副作用？"
                if is_zh
                else "Thank you for keeping track of your readings. Because your home BP has drifted up toward 148/92 alongside slightly elevated LDL, I have compiled your BP trend, 10-year cardiovascular risk profile, and baseline ECG and Echo for Dr. Sarah Wong.\n\n"
                "I also checked today's clinic schedule and found an open 20-minute review slot with Dr. Sarah Wong at 14:15 below, where we can bundle your CV-priority flu shot and biennial mammogram. Have you been taking your Perindopril and Rosuvastatin every day without side effects?"
            )
        return (
            "needs_decision",
            "with_doctor",
            "Cardiac Chronic Disease & Prevention — BP Drift & CVD Risk",
            "64F on Perindopril + Rosuvastatin with upward home BP drift (148/92); 10-Yr CVD risk score, ECG/Echo & preventive checkups staged.",
            "This week · CV Review",
            summary,
            reply,
            steps,
        )

    # 12. CHUI Ka-Ming (Lingering Dry Cough — Cap. 134 Codeine Guard!)
    asked_codeine = "codeine" in cds_blocked_drugs or "codeine" in low or "可待因" in low or "強效止咳" in low
    summary["vitals_exam"] = {
        "source": f"Patient Careline · Today {hhmm}",
        "items": [
            {"label": "Symptoms", "val": "4-day dry nocturnal cough"},
            {"label": "Red flags", "val": "Afebrile · Denies SOB or haemoptysis"},
        ],
    }
    summary["issues"] = [{
        "id": "iss_chui_cough",
        "name": "1. Acute post-viral dry cough",
        "bullets": [
            "4-day nocturnal dry cough without fever, dyspnoea, or chest pain.",
            "Patient requested codeine cough syrup via Careline — advised HK Cap. 134 controlled-drug rules prevent chat/remote issuing.",
        ],
        "safety_gap": "HK Cap. 134 Dangerous Drug hard block active for Codeine; evaluate non-opioid options in consult.",
        "suggested_orders": [
            {"id": "chui_book", "type": "book", "issue": "1. Acute post-viral dry cough", "visitType": "15-min In-Clinic GP Review", "when": "Today 15:30"},
            {"id": "chui_rx_dex", "type": "rx", "action": "start", "issue": "1. Acute post-viral dry cough", "drug": "Dextromethorphan 15mg", "freq": "TDS PRN", "days": "5d", "note": "Non-opioid antitussive (Cap. 134 safe)"},
        ],
    }]
    summary["sam_proactive_insight"] = (
        "HK Cap. 134 Dangerous Drugs Ordinance: Codeine-containing antitussives are non-overridably blocked from remote/automated "
        "staging (`RX_HARD_BLOCK` HTTP 422). Non-opioid symptom relief and in-clinic assessment staged for Dr. Sarah Wong."
    )
    steps.append("ProtocolCDSAgent · Cap. 134 Dangerous Drug rule checked (Codeine hard-blocked from remote staging)")

    if asked_codeine:
        reply = (
            "明白晚晚乾咳真係好難瞓。不過按香港《危險藥物條例》（第 134 章），含可待因（Codeine）嘅止咳藥屬於受管制危險藥物，診所唔可以透過對話直接配發，亦需要由醫生親身診斷評估。\n\n"
            "我已經幫你整理好症狀交畀黃莎拉醫生，今日下午 3:30 有親身診症時段，醫生可以幫你聽肺並安排安全有效嘅非鴉片類止咳方案。請問你有冇發燒或者氣促？"
            if is_zh
            else "I hear you — a dry cough keeping you awake for four nights is exhausting. Under Hong Kong's Dangerous Drugs Ordinance (Cap. 134), codeine-containing cough syrups are strictly controlled medicines that can never be issued over chat or without an in-person doctor assessment.\n\nI've prepared your notes for Dr. Sarah Wong and we have a 15:30 in-clinic slot today so she can listen to your chest and arrange safe, effective non-opioid relief. Just to check first — do you have any fever or shortness of breath?"
        )
    else:
        reply = (
            "收到，冇發燒亦冇氣促就安心啲。我已經幫你預留今日下午 3:30 親身見黃莎拉醫生，並準備好相關病歷資料。"
            if is_zh
            else "Got it — glad you have no fever or shortness of breath. I've held the 15:30 in-clinic slot today with Dr. Sarah Wong so she can check your chest and get you sorted with safe relief."
        )
    return (
        "needs_decision",
        "with_doctor",
        "Acute Dry Cough — In-Clinic Review (Cap. 134 Guard)",
        "4-day nocturnal dry cough requesting codeine syrup; Cap. 134 explained, 15:30 in-clinic slot staged.",
        "Today 15:30 clinic",
        summary,
        reply,
        steps,
    )
