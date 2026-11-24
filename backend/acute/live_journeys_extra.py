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
            "I've had burning when I pee and going to the toilet frequently since yesterday evening. No fever or back pain — can I get Nitrofurantoin?",
            "No pregnancy, drinking plenty of water, and no flank pain or chills. Can Dr. Wong arrange a safe antibiotic today?",
        ],
        "suggested_replies_zh": [
            "琴晚開始小便刺痛同成日急尿，冇發燒亦都冇腰痛，可唔可以開 Nitrofurantoin 消炎藥？",
            "冇懷孕，飲緊好多水，冇發冷冇背痛。今日可以安排醫生開安全嘅藥嗎？",
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
            "Just mild fatigue and a stuffy nose since yesterday, no fever, no cough, no chest pain. A 15-min slot later today works.",
        ],
        "suggested_replies_zh": [
            "琴日開始覺得個人唔係好舒服，返工好忙，可唔可以直接開啲抗生素？",
            "主要係少少攰同鼻塞，冇發燒、冇咳亦都冇心口痛，今日晏晝約 15 分鐘睇醫生都可以。",
        ],
    },
    {
        "key": "lau_chest",
        "category": "acute",
        "patient_id": "pat_lau_chest",
        "patient_name": "LAU Wai-Fong",
        "patient_name_zh": "劉慧芳",
        "age_sex": "64F",
        "pathway_node_id": "ebm.pathway.chronic_htn_home_bp",
        "title": "Chest tightness & feeling unwell",
        "title_zh": "心口翳悶不適",
        "subtitle": "On Perindopril & Rosuvastatin · urgent symptom screening",
        "welcome_en": (
            "Hello Ms. Lau, I'm Sam, the care assistant at Central & Mid-Levels Family Medical. How are you feeling today?"
        ),
        "welcome_zh": (
            "劉女士你好，我係中環及半山家庭醫務中心嘅護理助手 Sam。你今日覺得點樣？"
        ),
        "suggested_replies_en": [
            "I suddenly have crushing chest pain spreading to my left arm, short of breath and breaking out in a cold sweat.",
            "我突然間心口好痛、唞唔到氣、標晒冷汗，好似就快暈低咁！",
        ],
        "suggested_replies_zh": [
            "我突然間心口好痛、唞唔到氣、標晒冷汗，好似就快暈低咁！",
            "頭先心口突然翳痛同唞唔到氣，仲標冷汗。",
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
            "我係佢個女 Joyce，姐姐 Maria 話爸爸今朝食完血壓藥起身有少少頭暈（血壓 118/72），冇跌親亦冇心口痛，係咪要停藥？",
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
                {"level": "high", "label": "G6PD Deficient", "detail": "Avoid Nitrofurantoin, Sulfonamides (Bactrim), Aspirin"},
            ],
            "scheduled_tasks": [{"status": "waiting", "label": "Careline conversation active with patient"}],
            "issues": [{
                "id": "iss_tang_init",
                "name": "1. Patient check-in — gathering history",
                "bullets": ["Patient opened Careline chat; G6PD safety flag loaded from clinic record."],
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

    # tsang_thin, lau_chest, ng_quarantine, chui_cough
    return {
        "alerts": (
            [{"level": "info", "label": "Cardiovascular Risk Profile", "detail": "On Perindopril 4mg + Rosuvastatin 10mg"}]
            if j_key == "lau_chest"
            else [{"level": "info", "label": "No Known Drug Allergies", "detail": "Standard clinic profile loaded"}]
        ),
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

    # 6. TANG Ka-Yan (Acute Uncomplicated UTI + G6PD Deficiency)
    if j_key == "tang_uti":
        asked_nitro = "nitrofurantoin" in cds_blocked_drugs or "nitrofurantoin" in low or "macrobid" in low or "呋喃妥因" in low
        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {hhmm}",
            "items": [
                {"label": "Symptoms", "val": "Dysuria & frequency (<24h)"},
                {"label": "Red flags", "val": "Afebrile · No flank pain or chills"},
                {"label": "Pregnancy", "val": "Denies pregnancy" if ("no pregnancy" in all_low or "冇懷孕" in all_text) else "Confirm before Rx"},
            ],
        }
        summary["issues"] = [{
            "id": "iss_uti",
            "name": "1. Acute uncomplicated cystitis (G6PD deficient)",
            "bullets": [
                "Acute dysuria and urinary frequency since yesterday evening without fever or flank pain.",
                "Nitrofurantoin and sulfonamides are contraindicated due to G6PD deficiency; single-dose Fosfomycin is G6PD-safe.",
            ],
            "safety_gap": "Confirm no pregnancy and absence of flank pain before prescribing." if not ("no pregnancy" in all_low or "冇懷孕" in all_text) else None,
            "suggested_orders": [
                {"id": "tang_rx_fosfo", "type": "rx", "action": "start", "issue": "1. Acute uncomplicated cystitis (G6PD deficient)", "drug": "Fosfomycin 3g", "freq": "STAT single dose", "days": "1d", "note": "G6PD-safe first-line uncomplicated UTI"},
                {"id": "tang_lab_msu", "type": "lab", "issue": "1. Acute uncomplicated cystitis (G6PD deficient)", "testName": "Urine Dipstick / MSU Culture", "lab": "Clinic / KingMed", "fasting": False, "when": "If symptoms persist >48h"},
                {"id": "tang_goal", "type": "goal", "issue": "1. Acute uncomplicated cystitis (G6PD deficient)", "metric": "UTI Hydration & Red Flags", "target_text": "Hydration + seek care if fever/flank pain", "leaflet_id": "uti_hydration_hk"},
            ],
        }]
        summary["sam_proactive_insight"] = (
            "HK CHP Antibiotic Stewardship + G6PD Safety Floor: Nitrofurantoin is contraindicated in G6PD deficiency "
            "(haemolysis risk). Fosfomycin 3g single oral dose is G6PD-safe first-line for uncomplicated afebrile cystitis."
        )
        steps.append("PracticeGraph · Traversed ebm.pathway.acute_uti + ebm.safety.g6pd_allergy_floor")
        steps.append("PresentationCompiler · Staged G6PD-safe Fosfomycin 3g option (5/5 linter pass)")

        if is_simple_ack and is_followup_turn:
            reply = (
                "唔使客氣——黃莎拉醫生一簽署處方就會即時送過嚟呢個對話，記得多飲清水。"
                if is_zh
                else "You're welcome — as soon as Dr. Sarah Wong reviews and signs your prescription, it'll show up right here. Keep drinking plenty of water in the meantime."
            )
        elif asked_nitro:
            reply = (
                "先提醒一下：你嘅診所病歷記錄咗你有蠶豆症（G6PD 缺乏症），所以唔可以食 Nitrofurantoin（有機會引致溶血反應）。\n\n"
                "好消息係冇發燒同冇腰痛嘅單純性尿道炎有對蠶豆症安全嘅選擇（例如單次劑量 Fosfomycin）。我已經將你嘅症狀同 G6PD 提示放上黃莎拉醫生嘅工作台——請問你有冇機會懷孕？"
                if is_zh
                else "Important heads-up from your clinic record: because you have G6PD deficiency, we must avoid Nitrofurantoin (it can trigger haemolysis).\n\nThe good news is there are G6PD-safe options for uncomplicated UTI (such as single-dose Fosfomycin). I've flagged your symptoms and G6PD status for Dr. Sarah Wong right now — just to help her review safely, is there any chance you could be pregnant?"
            )
        else:
            reply = (
                "多謝你確認冇懷孕亦都冇發燒腰痛。我已經更新咗你嘅個案，黃莎拉醫生會盡快審閱並安排對蠶豆症安全嘅尿道炎處方；如果期間出現發燒、發冷或者後腰痛，請即刻求醫。"
                if is_zh
                else "Thank you for confirming — I've updated Dr. Sarah Wong's chart with your clear red-flag check and G6PD profile so she can sign off a safe treatment plan shortly. If you develop fever, chills, or back/flank pain at any point, please seek prompt in-person care."
            )
        return (
            "needs_decision",
            "with_doctor",
            "Acute Uncomplicated UTI (G6PD Deficient)",
            "Dysuria & frequency (<24h), afebrile; G6PD blocks Nitrofurantoin — Fosfomycin 3g option prepared.",
            "Today · doctor review",
            summary,
            reply,
            steps,
        )

    # 7. TSANG Chun-Hin (Thin/Vague Intake -> Dynamic Clarification!)
    if j_key == "tsang_thin":
        gave_specifics = any(k in low for k in ("nose", "stuffy", "fatigue", "cough", "throat", "fever", "stomach", "slot", "鼻塞", "攰", "咳", "喉嚨", "發燒", "15"))
        if not gave_specifics:
            summary["intake_sufficiency"] = "insufficient"
            summary["issues"] = [{
                "id": "iss_thin",
                "name": "1. Undifferentiated unwellness — thin intake",
                "bullets": [
                    "Patient reported feeling generally unwell since yesterday and asked for antibiotics without specifying symptoms.",
                    "No prescriptions or sick-leave orders staged pending clarifying history and doctor assessment.",
                ],
                "safety_gap": "Clarify symptom site, fever, duration, and red flags before any prescribing decision.",
                "suggested_orders": [
                    {"id": "tsang_book", "type": "book", "issue": "1. Undifferentiated unwellness — thin intake", "visitType": "15-min GP Assessment", "when": "Next available slot today"},
                    {"id": "tsang_preq", "type": "nurse", "issue": "1. Undifferentiated unwellness — thin intake", "protocol": "pre_visit_questionnaire", "assignee": "Nurse Rachel", "task": "Send structured pre-visit symptom & red-flag questionnaire via Sam", "due": "Before consult"},
                ],
            }]
            summary["sam_proactive_insight"] = None
            steps.append("IntakeGuard · Flagged intake_sufficiency='insufficient' (0 premature Rx/certs staged)")
            reply = (
                "明白你返工好忙，不過抗生素必須先由醫生了解具體症狀先可以決定，唔可以直接透過對話開藥。\n\n"
                "可以話我知多啲嗎——你主要係邊度唔舒服（例如鼻塞、喉嚨痛、咳、發燒定腸胃唔舒服）？有冇心口痛或者氣促？我亦可以幫你睇吓今日晏晝有冇 15 分鐘診症時段。"
                if is_zh
                else "I hear you on a busy workday — antibiotics need a doctor's clinical assessment first, so we can't send them automatically over chat.\n\nTo help Dr. Sarah Wong figure out what you actually need, could you share what specific symptoms you're having (like fever, sore throat, stuffy nose, cough, or stomach upset) and confirm you have no chest pain or shortness of breath? We also have quick 15-minute slots later today."
            )
            return (
                "needs_decision",
                "with_doctor",
                "Undifferentiated Unwellness — Insufficient Intake",
                "Brief symptom message requesting antibiotics; thin-intake guard active (zero premature Rx staged).",
                "Clarification pending",
                summary,
                reply,
                steps,
            )

        # Turn 2: Patient provided clear specifics -> promote cleanly!
        summary["intake_sufficiency"] = "sufficient"
        summary["vitals_exam"] = {
            "source": f"Patient Careline · Today {hhmm}",
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
        steps.append("IntakeGuard · Clarifying details received -> intake_sufficiency upgraded to 'sufficient'")
        reply = (
            "多謝你講得咁清楚——冇發燒亦冇氣促或心口痛係好消息。我已經幫你記低畀黃莎拉醫生，並預留今日下午 4:15 嘅 15 分鐘診症時段，等醫生親身評估需唔需要用藥。"
            if is_zh
            else "Thanks for sharing those details — having no fever or warning signs is reassuring. I've updated Dr. Sarah Wong's notes and held a 15-minute slot at 16:15 today so she can assess you and confirm the right treatment."
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

    # 11. LAU Wai-Fong (Fallback only if patient sends non-999 message; 999 messages exit earlier in handle_live_chat_turn!)
    if j_key == "lau_chest":
        steps.append("SafetySentinel · No acute 999 keywords in message; standard cardiac precaution advice given")
        reply = (
            "多謝劉女士聯絡診所。我已經幫你記低畀黃莎拉醫生跟進；不過如果你有任何心口痛、壓迫感、氣促或者標冷汗，請唔好等覆訊，立即致電 999 或去急症室。"
            if is_zh
            else "Thank you Ms. Lau — I've noted this for Dr. Sarah Wong to review. As an important precaution, if you notice any chest pain, tightness, shortness of breath, or cold sweats, please don't wait for a clinic message — call 999 or go straight to A&E."
        )
        return (
            "needs_decision",
            "with_doctor",
            "Cardiovascular Check-In — Doctor Review",
            "64F on Perindopril + Rosuvastatin messaged clinic; red-flag precautions reinforced.",
            "Today · doctor review",
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
