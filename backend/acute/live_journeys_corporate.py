"""4 Flagship Corporate Flu Vaccination & Preventive Health Campaign Journeys
for Central Health · Corporate Wellness Demo (?pack=corporate_health).

Showcases how the exact same Clinical Loop OS harness (8 order primitives + 14-widget canvas +
# my-work / # clinic-team / # ai-team + PDPO-safe /corp HR portal) composes a complete
corporate vaccination & preventive/longevity campaign with minimal doctor burden.

Product invariant: NEVER imports `loopbench`. Synthetic data only.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from .db import AgentMemoryEntry, new_id
from .practice_graph import compute_cvd_10yr_tier

CORPORATE_LIVE_JOURNEYS: tuple[dict[str, Any], ...] = (
    # 1. Routine Corporate Flu Shot — Zero Doctor Interruption (Self-Serve Nurse Bay + Day +2 AI Check-In)
    {
        "key": "corp_flu_clean",
        "pack": "corporate_health",
        "category": "acute",
        "patient_id": "pat_corp_flu_clean",
        "patient_name": "CHENG Wing-Sze",
        "patient_name_zh": "鄭詠詩",
        "age_sex": "34F",
        "default_lang": "en",
        "pathway_node_id": "ebm.triage.routine_logistics",
        "title": "Corporate Flu Shot (Routine)",
        "title_zh": "企業流感疫苗接種（常規）",
        "subtitle": "Harbour Capital Partners · Zero-doctor nurse bay booking",
        "welcome_en": (
            "Hi Wing-Sze, welcome to Central Health Corporate Wellness. I'm Sam, your AI Resident for the Harbour Capital Partners 2026 Flu & Preventive Campaign. Ready to get your seasonal flu shot booked?"
        ),
        "welcome_zh": (
            "詠詩你好，歡迎使用 Central Health 企業健康服務。我係負責 Harbour Capital Partners 2026 流感及預防保健計劃嘅 AI 駐診助手 Sam，準備好預約季節性流感針未？"
        ),
        "suggested_replies_en": [
            "Yes please — I'd like to book my company-sponsored flu shot this week.",
            "No fever or illness today, no egg or vaccine allergies, no history of Guillain-Barré syndrome, and not pregnant.",
        ],
        "suggested_replies_zh": [
            "好呀，我想預約今個星期打公司贊助嘅流感針。",
            "今日冇發燒或唔舒服，冇雞蛋或疫苗敏感，冇吉巴氏綜合症病史，亦冇懷孕。",
        ],
    },
    # 2. Vaccine Reaction / Anticoagulant Contraindication — Deterministic Safety Escalation to Doctor
    {
        "key": "corp_flu_flag",
        "pack": "corporate_health",
        "category": "acute",
        "patient_id": "pat_corp_flu_flag",
        "patient_name": "PATEL Arjun",
        "patient_name_zh": "巴特爾・阿俊",
        "age_sex": "47M",
        "default_lang": "en",
        "pathway_node_id": "ebm.safety.g6pd_allergy_floor",
        "title": "Flu Shot · Prior Vaccine Reaction",
        "title_zh": "流感針篩查・過往疫苗反應",
        "subtitle": "Prior hives/wheeze + Apixaban · Blocks nurse bay & escalates to GP",
        "welcome_en": (
            "Hello Arjun, welcome to Central Health Corporate Wellness. I'm Sam, your AI Resident for the Harbour Capital Partners 2026 Flu Campaign. How can I help you get set up today?"
        ),
        "welcome_zh": (
            "阿俊你好，歡迎使用 Central Health 企業健康服務。我係 Harbour Capital Partners 2026 流感疫苗計劃嘅 AI 駐診助手 Sam，今日有咩可以幫到你？"
        ),
        "suggested_replies_en": [
            "I want to book my corporate flu shot today — quick heads up, I got hives and mild wheezing after a flu jab 4 years ago, and I take Apixaban daily.",
            "It started about 20 minutes after the jab. I did not need an EpiPen or hospital stay — antihistamines settled it quickly and my breathing stayed fine.",
        ],
        "suggested_replies_zh": [
            "我想預約今日打公司流感針——不過提一提你，我 4 年前打流感針後出過風癩同有少少喘鳴，而且我每日都有食薄血藥 Apixaban。",
            "當年打完針大約 20 分鐘出現，冇用過腎上腺素筆亦冇入醫院，食咗抗敏感藥好快就退，呼吸一直正常。",
        ],
    },
    # 3. Flu Shot -> Executive Preventive & Longevity Upsell (Shingrix + Mammogram + ApoB/Lp(a)/hs-CRP/DEXA)
    {
        "key": "corp_prev_bundle",
        "pack": "corporate_health",
        "category": "chronic",
        "patient_id": "pat_corp_prev_bundle",
        "patient_name": "WONG Mei-Ling",
        "patient_name_zh": "黃美玲",
        "age_sex": "54F",
        "default_lang": "zh-HK",
        "pathway_node_id": "ebm.pathway.chronic_htn_home_bp",
        "title": "Flu Shot + Preventive & Longevity Panel",
        "title_zh": "流感針＋預防篩查及長壽驗血",
        "subtitle": "Shingrix, Mammogram, FIT + ApoB / Lp(a) / hs-CRP / DEXA bundle",
        "welcome_en": (
            "Hello Ms. Wong, welcome to Central Health Corporate Wellness. I'm Sam, your AI Resident for Harbour Capital Partners. Would you like to book your annual flu shot and check any preventive health screenings you're due for?"
        ),
        "welcome_zh": (
            "黃女士你好，歡迎使用 Central Health 企業健康服務。我係負責 Harbour Capital Partners 計劃嘅 AI 駐診助手 Sam，想幫你安排年度流感針，順便睇吓有冇預防健康檢查到期？"
        ),
        "suggested_replies_en": [
            "Yes, I'd like my corporate flu shot — no fever, no allergies, and feeling well. What routine checkups or longevity blood tests am I due for?",
            "I haven't had fasting bloods or a mammogram in over 2 years. Never smoked, no diabetes, and my mother had high cholesterol — I'd love to add ApoB, Lp(a), Shingrix, and a DEXA scan too.",
        ],
        "suggested_replies_zh": [
            "好呀，我想打公司流感針——今日冇發燒、冇敏感、身體好好。順便想知我有冇常規預防檢查或者長壽指標驗血到期？",
            "我超過兩年冇空腹驗血同做乳房造影。我唔食煙、冇糖尿病，媽媽有高膽固醇——我想一併加做 ApoB、Lp(a)、生蛇針（Shingrix）同埋 DEXA 身體成分掃描。",
        ],
    },
    # 4. Acute Fever Deferral + Slot Release + Day +5 Auto-Rebook Loop (Zero Doctor Burden)
    {
        "key": "corp_flu_defer",
        "pack": "corporate_health",
        "category": "acute",
        "patient_id": "pat_corp_flu_defer",
        "patient_name": "HO Ka-Ming",
        "patient_name_zh": "何家明",
        "age_sex": "39M",
        "default_lang": "en",
        "pathway_node_id": "ebm.triage.routine_logistics",
        "title": "Flu Shot · Fever Deferral & Rebook",
        "title_zh": "流感針・發燒延期及自動改期",
        "subtitle": "38.6°C fever · Releases tomorrow's bay & sets Day +5 AI rebook loop",
        "welcome_en": (
            "Hi Ka-Ming, welcome to Central Health Corporate Wellness. I see you're scheduled for your Harbour Capital Partners flu shot tomorrow at 10:15 — how are you feeling ahead of your visit?"
        ),
        "welcome_zh": (
            "家明你好，歡迎使用 Central Health 企業健康服務。見到你預約咗聽朝 10:15 打 Harbour Capital Partners 企業流感針，今日身體狀況點樣？"
        ),
        "suggested_replies_en": [
            "I've got a 38.6°C fever and body aches today — can I still come in for my flu shot tomorrow?",
            "No shortness of breath or chest pain — just fever and fatigue. Please release tomorrow's flu slot and check in with me next week to rebook.",
        ],
        "suggested_replies_zh": [
            "我今日發燒 38.6 度同全身骨痛，聽日仲可唔可以嚟打流感針？",
            "冇氣促亦冇心口痛，只係發燒同攰。麻煩先幫我取消聽日流感針時段，下星期再喺度提我改期。",
        ],
    },
)

CORPORATE_JOURNEY_KEYS: frozenset[str] = frozenset(j["key"] for j in CORPORATE_LIVE_JOURNEYS)
CORPORATE_4_JOURNEY_KEYS: tuple[str, ...] = (
    "corp_flu_clean",
    "corp_flu_flag",
    "corp_prev_bundle",
    "corp_flu_defer",
)


def seed_corporate_journey_prior_record(session: Session, tenant_id: str, j_key: str) -> tuple[str | None, str, str]:
    """Seed verified corporate campaign eligibility & chart memory for the 4 Central Health Wellness cases."""
    if j_key == "corp_flu_clean":
        quote = "Corporate Eligibility: Harbour Capital Partners (2026 Flu & Preventive Campaign) · NKDA · Prior QIV 2025 tolerated well."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_corp_flu_clean",
            tier="longitudinal", topic="corporate_campaign_eligibility",
            summary_text="Harbour Capital Partners eligible employee; NKDA; 2025 flu shot tolerated with no adverse reaction.",
            source_quote=quote,
        ))
        return "att_corp_clean", "Corporate Eligibility · Harbour Capital 2026", quote

    if j_key == "corp_flu_flag":
        quote = "Active Meds: Apixaban 5mg BD (Paroxysmal AFib) | Chart Flag: Prior urticaria & mild wheeze post-IIV4 (2022)."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_corp_flu_flag",
            tier="longitudinal", topic="vaccine_allergy_anticoag",
            summary_text="Prior flu-vaccine reaction (hives + mild wheeze, 2022) and daily Apixaban 5mg BD; requires GP clearance before vaccination.",
            source_quote=quote,
        ))
        return "att_corp_flag", "Clinic Chart · Vaccine Precaution & Apixaban", quote

    if j_key == "corp_prev_bundle":
        quote = "Last Executive Panel (Mar 2024): LDL-C 3.5 mmol/L, HbA1c 5.8%, BP 134/84 | Overdue: Biennial Mammogram, Colorectal FIT, Shingrix."
        session.add(AgentMemoryEntry(
            id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_corp_prev_bundle",
            tier="longitudinal", topic="preventive_longevity_gaps",
            summary_text="54F eligible for Harbour Capital corporate flu shot; >24m since last lipid/HbA1c panel & mammogram; eligible for Shingrix & longevity ApoB/Lp(a)/DEXA.",
            source_quote=quote,
        ))
        return "att_corp_prev", "Executive Wellness Profile · Mar 2024", quote

    # corp_flu_defer
    quote = "Booked: Tomorrow 10:15 · Nurse Vaccination Bay (Harbour Capital Partners 2026 Flu Campaign) · NKDA."
    session.add(AgentMemoryEntry(
        id=new_id("mem"), tenant_id=tenant_id, patient_id="pat_corp_flu_defer",
        tier="longitudinal", topic="corporate_flu_booking",
        summary_text="Scheduled for Nurse Vaccination Bay tomorrow 10:15 under Harbour Capital Partners 2026 campaign.",
        source_quote=quote,
    ))
    return "att_corp_defer", "Campaign Booking · Tomorrow 10:15", quote


def build_corporate_initial_summary(
    j_key: str,
    doc_id: str | None,
    doc_label: str,
    doc_quote: str,
    welcome_msg: str,
    hhmm: str,
) -> dict[str, Any]:
    """Initial blank-slate workspace summary when a corporate employee opens Central Health Wellness chat."""
    base: dict[str, Any] = {
        "corporate_campaign": {
            "corporate_name": "Harbour Capital Partners",
            "campaign_code": "HCP-FLU-2026",
            "clinic_brand": "Central Health · Corporate Wellness Demo",
        },
        "origin_strip": {
            "source": "Central Health · Corporate Wellness QR",
            "why": "Employee opened Harbour Capital Partners 2026 Flu & Preventive Campaign check-in.",
            "since": f"Today {hhmm}",
        },
        "alerts": [
            {
                "level": "info",
                "label": "Harbour Capital Partners · 2026 Campaign",
                "detail": "PDPO consent verified · Corporate billing uses k-anonymity (<5) aggregate reporting only",
            }
        ],
        "issues": [],
        "agent_thread": [
            {
                "actor": "Sam",
                "time": hhmm,
                "text": f"AI Resident loaded corporate eligibility & {doc_label}. Running autonomous screening before deciding whether Nurse Bay or GP review is needed.",
            }
        ],
        "original_chat": [
            {"actor": "Sam", "role": "assistant", "time": hhmm, "text": welcome_msg}
        ],
        "scheduled_tasks": [],
        "source_docs": [
            {"id": doc_id or f"att_{j_key}", "label": doc_label, "quote": doc_quote}
        ],
    }
    if j_key == "corp_flu_flag":
        base["alerts"].append({
            "level": "high",
            "label": "Chart Precaution · Prior Vaccine Reaction & Apixaban",
            "detail": "Hold self-serve nurse bay booking until reaction history is clarified for GP clearance.",
        })
    elif j_key == "corp_prev_bundle":
        base["alerts"].append({
            "level": "info",
            "label": "Preventive & Longevity Opportunities (54F)",
            "detail": "Overdue fasting lipids/HbA1c (>24m), biennial mammogram, HK FIT, Shingrix, and optional ApoB/Lp(a)/DEXA.",
        })
    return base


def compile_corporate_journey_turn(
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
    """Compile deterministic multi-turn clinical & operational updates for the 4 Central Health Corporate Wellness cases."""
    low = (patient_text or "").lower()
    all_text = " ".join([*prior_patient_msgs, patient_text])
    all_low = all_text.lower()
    summary = copy.deepcopy(existing_summary)
    steps: list[str] = []
    agent_thread = list(summary.get("agent_thread") or [])

    # ------------------------------------------------------------------ 1. Routine Flu Shot (Zero Doctor Workload!)
    if j_key == "corp_flu_clean":
        screened_clear = any(
            k in all_low or k in all_text
            for k in (
                "no fever", "no egg", "not pregnant", "guillain", "no allergies",
                "冇發燒", "冇雞蛋", "冇敏感", "冇懷孕", "吉巴氏",
            )
        )
        summary["vitals_exam"] = {
            "source": f"Corporate Flu Screen · Today {hhmm}",
            "encounter_context": "Harbour Capital Partners · HK CHP Pre-Vax Screen",
            "items": [
                {"label": "Corporate Plan", "val": "Harbour Capital Partners (HCP-FLU-2026 · Covered)"},
                {"label": "Contraindications", "val": "Cleared — afebrile, no egg/vaccine allergy, no GBS, not pregnant" if screened_clear else "Pending HK CHP 4-point pre-vax screen"},
                {"label": "Doctor Load", "val": "Zero doctor interruption · Direct to Nurse Vaccination Bay" if screened_clear else "Autonomous AI screening"},
            ],
        }
        if not screened_clear:
            summary["_corp_stage"] = "awaiting_screen"
            if not any("Step 1/2" in str(x.get("text", "")) for x in agent_thread):
                agent_thread.append({
                    "actor": "Sam",
                    "time": hhmm,
                    "text": "AI Resident (Step 1/2): Verified Harbour Capital Partners 2026 eligibility. Running HK CHP 4-point contraindication screen autonomously (zero doctor involvement).",
                })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("FluVaxScreen · Verified corporate eligibility; asking HK CHP 4-point contraindication check")
            r_en = (
                "Great — your Harbour Capital Partners corporate flu shot is covered. "
                "Just one quick safety check before I open Nurse Rachel's vaccination slots: do you have any fever or acute illness today, any severe egg or vaccine allergies, a history of Guillain-Barré syndrome, or any chance of pregnancy?"
            )
            r_zh = (
                "好呀，你的 Harbour Capital Partners 企業流感針已獲全額贊助。"
                "開放護士接種時段前先做一個簡短安全核對：今日有冇發燒或急性唔舒服、嚴重雞蛋或疫苗敏感、吉巴氏綜合症病史，或者有機會懷孕？"
            )
            summary["_last_reply_bilingual"] = (r_en, r_zh)
            return (
                "running",
                "with_patient",
                "Corporate Flu Shot — Safety Screening",
                "Employee requested Harbour Capital flu shot; AI Resident running HK CHP contraindication screen.",
                "Flu · Safety Screen",
                summary,
                r_zh if is_zh else r_en,
                steps,
            )

        # Screened clear -> Direct to Nurse Vaccination Bay + # ai-team Day +2 check-in (ZERO doctor workload!)
        summary["_corp_stage"] = "ready_to_book"
        summary["_attach_preventive_booking"] = True
        summary["alerts"] = [
            {
                "level": "info",
                "label": "HK CHP Pre-Vax Screen Clear · Zero Doctor Load",
                "detail": "Routed directly to Nurse Rachel's 10-min Vaccination Bay · Auto Day +2 post-vax AI check-in armed",
            }
        ]
        summary["scheduled_tasks"] = [
            {"status": "auto", "label": "Nurse Vaccination Bay · 10-min QIV Flu Shot (Harbour Capital HCP-FLU-2026)"},
            {"status": "auto", "label": "Day +2 · Autonomous # ai-team Post-Vaccination Symptom Check-In"},
        ]
        summary["issues"] = [{
            "id": "iss_corp_flu_clean",
            "name": "1. Seasonal influenza vaccination (routine corporate)",
            "bullets": [
                "Harbour Capital Partners employee passed all HK CHP contraindication checks (afebrile, no allergies, no GBS, not pregnant).",
                "Handled end-to-end by AI Resident and Nurse Vaccination Bay with zero doctor slot consumed.",
            ],
            "safety_gap": None,
            "suggested_orders": [
                {"id": "corp_book_vax", "type": "book", "issue": "1. Seasonal influenza vaccination (routine corporate)", "visitType": "10-min Nurse Flu Vaccination Bay", "when": "Self-serve slot offered"},
                {"id": "corp_nurse_vax", "type": "nurse", "issue": "1. Seasonal influenza vaccination (routine corporate)", "assignee": "Nurse Rachel", "task": "Administer QIV seasonal flu shot (CVX 150) & log lot", "due": "At booked bay slot"},
            ],
        }]
        if not any("Step 2/2" in str(x.get("text", "")) for x in agent_thread):
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "AI Resident (Step 2/2): HK CHP contraindication screen 100% clear → bypassed doctor queue, offered live 10-min Nurse Vaccination Bay slots with Nurse Rachel, and armed Day +2 # ai-team post-vax check-in.",
            })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("FluVaxAdapter · Contraindications clear -> Direct to Nurse Vaccination Bay (zero doctor interruption)")
        r_en = (
            "All clear on the safety check — you are good to go for your seasonal flu shot. "
            "I checked Nurse Rachel's vaccination bay schedule and picked our earliest 10-minute slot below. Confirm a time that suits you, and I will check in automatically two days after your jab."
        )
        r_zh = (
            "安全核對完全通過，可以放心接種季節性流感針。"
            "我已經查閱陳護士接種站嘅實時空檔，並喺下方為你挑選最早嘅 10 分鐘時段。確認後我會喺接種兩日後自動喺呢度跟進你嘅情況。"
        )
        summary["_last_reply_bilingual"] = (r_en, r_zh)
        return (
            "scheduled",
            "with_nurse",
            "Corporate Flu Shot — Nurse Bay Ready",
            "HK CHP screen clear; routed directly to Nurse Rachel's Vaccination Bay with zero doctor workload.",
            "Nurse bay · AI Day-2 watch",
            summary,
            r_zh if is_zh else r_en,
            steps,
        )

    # ------------------------------------------------------------------ 2. Vaccine Reaction + Apixaban Escalation
    if j_key == "corp_flu_flag":
        clarified_reaction = any(
            k in all_low or k in all_text
            for k in (
                "20 minutes", "epipen", "antihistamine", "throat swelling", "hospital",
                "20 分鐘", "腎上腺素", "抗敏感藥", "喉嚨腫",
            )
        )
        summary["vitals_exam"] = {
            "source": f"Corporate Safety Screen · Today {hhmm}",
            "encounter_context": "Deterministic Pre-Vax Contraindication Gate",
            "items": [
                {"label": "Corporate Plan", "val": "Harbour Capital Partners (HCP-FLU-2026)"},
                {"label": "Vaccine History", "val": "Hives & mild wheeze 20 min post-IIV4 (4 yrs ago); no EpiPen/throat swelling" if clarified_reaction else "Prior hives & mild wheeze post-flu jab (4 yrs ago)"},
                {"label": "Anticoagulation", "val": "Apixaban 5mg BD (apply firm 2-min IM pressure without rubbing)"},
            ],
        }
        summary["alerts"] = [
            {
                "level": "high",
                "label": "Nurse Bay Self-Booking Blocked · Prior Vaccine Reaction",
                "detail": "Hives & mild wheeze post-IIV4 (2022) — requires GP clearance, egg-free recombinant Flublok (CVX 185) consideration & 30-min clinic observation",
            },
            {
                "level": "info",
                "label": "Anticoagulant Precaution (Apixaban 5mg BD)",
                "detail": "IM deltoid injection permitted with fine-gauge needle (23–25G) & firm pressure >= 2 min (no rubbing).",
            },
        ]

        if not clarified_reaction:
            summary["_corp_stage"] = "awaiting_reaction_detail"
            if not any("Step 1/2" in str(x.get("text", "")) for x in agent_thread):
                agent_thread.append({
                    "actor": "Sam",
                    "time": hhmm,
                    "text": "AI Resident (Step 1/2): Deterministic safety gate blocked self-serve Nurse Bay booking due to prior flu-vaccine hives/wheeze + daily Apixaban. Clarifying reaction onset and severity before escalating to on-duty GP.",
                })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("SafetySentinel · Blocked self-serve nurse bay booking; clarifying prior flu-vaccine reaction severity")
            r_en = (
                "Thank you for flagging that upfront — because you had hives and wheezing after a previous flu shot and take Apixaban, I've paused standard nurse-bay booking so our doctor can review the safest vaccine option for you. "
                "How soon after that shot did the reaction start, and did you ever have throat swelling or need an EpiPen or hospital visit?"
            )
            r_zh = (
                "多謝你主動話我知——由於你以前打流感針後試過出風癩同喘鳴，而且正服食薄血藥 Apixaban，我已經先暫停常規護士站自助預約，安排醫生為你審核最安全嘅疫苗選擇。"
                "請問當年打完針幾耐出現反應，有冇試過喉嚨腫或者需要用腎上腺素筆／入醫院？"
            )
            summary["_last_reply_bilingual"] = (r_en, r_zh)
            return (
                "running",
                "with_patient",
                "Corporate Flu — Clarifying Vaccine Reaction",
                "Safety gate paused nurse bay booking for prior flu-jab hives/wheeze & Apixaban; clarifying reaction severity.",
                "Flu · Clarifying Reaction",
                summary,
                r_zh if is_zh else r_en,
                steps,
            )

        # Reaction clarified -> Escalate to Dr. Sarah Wong with structured options!
        summary["_corp_stage"] = "escalated"
        summary["issues"] = [{
            "id": "iss_corp_flu_flag",
            "name": "1. Influenza vaccination precaution — prior urticaria/wheeze & Apixaban",
            "bullets": [
                "Prior hives and mild wheeze post-IIV4 (settled with oral antihistamine; no throat swelling, no EpiPen, no hospitalisation).",
                "On daily Apixaban for AFib; consider egg-free recombinant vaccine (Flublok, CVX 185) with 30-min clinic observation and firm post-IM pressure, or brief pre-vax GP consult.",
            ],
            "safety_gap": None,
            "suggested_orders": [
                {"id": "corp_flag_rx_flublok", "type": "rx", "action": "start", "issue": "1. Influenza vaccination precaution — prior urticaria/wheeze & Apixaban", "drug": "Recombinant Influenza Vaccine (Flublok · CVX 185)", "freq": "IM single dose", "days": "1d", "note": "Egg-free recombinant vaccine; 30-min post-vax clinic observation + firm 2-min pressure (Apixaban)"},
                {"id": "corp_flag_book_gp", "type": "book", "issue": "1. Influenza vaccination precaution — prior urticaria/wheeze & Apixaban", "visitType": "15-min GP Pre-Vaccination Review & Observed Jab", "when": "Doctor-supervised clinic slot"},
                {
                    "id": "corp_flag_leaflet",
                    "type": "goal",
                    "issue": "1. Influenza vaccination precaution — prior urticaria/wheeze & Apixaban",
                    "metric": "Post-Vaccination & Anticoagulant Care Guide",
                    "leaflet_id": "flu_post_vax_hk",
                    "title": "Seasonal Flu Shot & Post-Vaccination Safety Guide",
                    "template": "Central Health · Vaccination Safety Sheet",
                    "target_text": "Remain in clinic for 30 minutes after supervised vaccination; apply firm pressure (do not rub) to prevent bruising on Apixaban; seek urgent care for hives or breathing trouble.",
                },
            ],
        }]
        summary["sam_proactive_insight"] = (
            "HK CHP / CDC ACIP Guidance: Non-anaphylactic prior urticaria/mild wheeze requires clinician-supervised vaccination rather than fast-track nurse bay. "
            "Recombinant egg-free Flublok (CVX 185) + 30-min observation + firm 2-min IM pressure (Apixaban) staged for Dr. Sarah Wong's sign-off."
        )
        if not any("Step 2/2" in str(x.get("text", "")) for x in agent_thread):
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "AI Resident (Step 2/2): Clarified prior non-anaphylactic reaction (20-min onset, settled with antihistamine, no throat swelling/EpiPen) + Apixaban → escalated to Dr. Sarah Wong with recombinant egg-free Flublok (30-min supervised observation) and GP consult options.",
            })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("PracticeGraph · Escalated vaccine precaution + Apixaban to #my-work (blocked nurse bay self-serve)")
        r_en = (
            "Thank you for sharing those details. Because of your past reaction and Apixaban, I've sent your summary directly to Dr. Sarah Wong so she can approve a doctor-supervised, egg-free flu vaccine option with 30-minute observation. "
            "Your confirmed plan will appear right here as soon as she signs off."
        )
        r_zh = (
            "多謝你詳細說明。考慮到你過往嘅疫苗反應同正服食 Apixaban，我已經將你嘅資料直接交畀黃莎拉醫生，安排由醫生監督嘅無蛋成分流感疫苗及 30 分鐘診所觀察。"
            "醫生簽署確認後，詳細安排會即時顯示喺呢度。"
        )
        summary["_last_reply_bilingual"] = (r_en, r_zh)
        return (
            "needs_decision",
            "with_doctor",
            "Flu Shot Precaution — GP Clearance",
            "Prior flu-jab urticaria/wheeze + Apixaban; nurse bay blocked, egg-free recombinant Flublok & 30-min observation staged.",
            "Flu · GP Clearance Needed",
            summary,
            r_zh if is_zh else r_en,
            steps,
        )

    # ------------------------------------------------------------------ 3. Preventive Health + Longevity Upsell Bundle
    if j_key == "corp_prev_bundle":
        gave_history = any(
            k in all_low or k in all_text
            for k in (
                "apob", "lp(a)", "shingrix", "dexa", "mammogram", "never smoked", "cholesterol",
                "生蛇針", "乳房造影", "唔食煙", "膽固醇", "兩年",
            )
        )
        cvd_score = compute_cvd_10yr_tier(age=54, smoker=False, systolic_bp=134, ldl_mmol_l=3.5, sex="F", on_bp_treatment=False)
        summary["clinical_scores"] = [cvd_score]
        summary["vitals_exam"] = {
            "source": f"Executive Wellness Intake · Today {hhmm}",
            "encounter_context": "Harbour Capital Partners · Preventive & Longevity Bundle",
            "items": [
                {"label": "Flu Pre-Vax Screen", "val": "Clear — afebrile, NKDA, eligible for Nurse Bay"},
                {"label": "Last Executive Labs", "val": "Mar 2024 (>24m overdue · LDL 3.5, HbA1c 5.8%)"},
                {"label": "Risk & Goals", "val": "Non-smoker · FHx high cholesterol · Opted into Preventive + Longevity Panel" if gave_history else "Checking screening history & longevity goals"},
            ],
        }
        summary["lab_panel"] = {
            "source": "Prior Executive Panel · Mar 2024",
            "rows": [
                {"test": "LDL-Cholesterol", "val": "3.5 mmol/L", "ref": "< 2.6 optimal", "flag": "HIGH"},
                {"test": "HbA1c", "val": "5.8 %", "ref": "< 5.7 optimal", "flag": "Borderline"},
                {"test": "ApoB / Lp(a)", "val": "Not tested", "ref": "Longevity baseline", "flag": "Due"},
                {"test": "hs-CRP", "val": "Not tested", "ref": "< 1.0 mg/L", "flag": "Due"},
            ],
        }
        summary["preventive_care"] = [
            {
                "id": "prev_corp_shingrix",
                "title": "Shingrix Shingles Vaccine (Age 50+)",
                "badge": "HK CHP Recommended",
                "detail": "2-dose recombinant zoster vaccine; Dose 1 bundled at flu visit, Dose 2 tracked in # ai-team at Week 8",
                "order": {"id": "ord_corp_shingrix", "type": "rx", "action": "start", "issue": "Preventive Care", "drug": "Shingrix (Recombinant Zoster Vaccine) Dose 1", "freq": "IM single dose", "days": "1d", "note": "Dose 2 recall in 8 weeks via # ai-team"},
            },
            {
                "id": "prev_corp_mammo",
                "title": "Biennial Bilateral Mammogram & HK Colorectal FIT",
                "badge": "Overdue (>24m)",
                "detail": "HK Cancer Expert Working Group guidelines for 54F — bundle imaging referral & FIT kit with visit",
                "order": {"id": "ord_corp_mammo", "type": "imaging", "issue": "Preventive Care", "modality": "Bilateral Screening Mammogram", "indication": "HK biennial breast screening (54F)", "priority": "Routine"},
            },
            {
                "id": "prev_corp_longevity",
                "title": "Longevity Biomarker & DEXA Baseline",
                "badge": "Executive Upsell",
                "detail": "Fasting Lipids + ApoB + Lp(a) + hs-CRP + HbA1c + Renal Panel + DEXA Body Composition / Visceral Fat",
                "order": {"id": "ord_corp_dexa", "type": "imaging", "issue": "Preventive Care", "modality": "DEXA Bone Density & Body Composition", "indication": "Baseline longevity & bone health (54F)", "priority": "Routine"},
            },
        ]

        if not gave_history:
            summary["_corp_stage"] = "awaiting_preventive_goals"
            if not any("Step 1/2" in str(x.get("text", "")) for x in agent_thread):
                agent_thread.append({
                    "actor": "Sam",
                    "time": hhmm,
                    "text": "AI Resident (Step 1/2): Cleared corporate flu shot contraindications. Detected >24m overdue preventive gaps (fasting lipids, biennial mammogram, FIT, Shingrix) + longevity biomarker opportunity (ApoB, Lp(a), hs-CRP, DEXA). Prompting patient autonomously.",
                })
            summary["agent_thread"] = agent_thread[-4:]
            steps.append("PreventiveEngine · Flu screen clear; surfaced HK CHP preventive + longevity upsell opportunities")
            r_en = (
                "You're all clear for your corporate flu shot. Looking at your Central Health wellness profile, you're also due for a few high-value preventive checks — including fasting cholesterol & blood sugar, a biennial mammogram, Shingrix shingles protection (age 50+), and optional longevity markers (ApoB, Lp(a), hs-CRP, and DEXA). "
                "When was your last fasting blood test or mammogram, and do you have any smoking or family history of cholesterol or heart disease?"
            )
            r_zh = (
                "你的企業流感針安全核對已通過。睇返你嘅 Central Health 健康紀錄，你有幾項重要預防檢查亦到期——包括空腹血脂血糖、兩年一度乳房造影、50 歲以上適用嘅生蛇針（Shingrix），仲可以順便加做長壽健康指標（ApoB、Lp(a)、hs-CRP 同 DEXA 掃描）。"
                "請問你上次空腹驗血同做乳房造影係幾耐之前，有冇吸煙或者家族高膽固醇／心臟病史？"
            )
            summary["_last_reply_bilingual"] = (r_en, r_zh)
            return (
                "running",
                "with_patient",
                "Corporate Flu + Preventive Intake",
                "Flu screen clear; AI Resident discussing overdue HK preventive screenings & longevity biomarkers.",
                "Preventive · Gathering Goals",
                summary,
                r_zh if is_zh else r_en,
                steps,
            )

        # Patient opted into Preventive + Longevity Bundle -> Stage complete executive bundle for Dr. Sarah Wong!
        summary["_corp_stage"] = "ready"
        summary["scheduled_tasks"] = [
            {"status": "auto", "label": "Week +8 · Autonomous # ai-team Shingrix Dose 2 Recall & Booking"},
            {"status": "staged", "label": "Lab & Imaging Watch · Auto-summary on Fasting ApoB/Lp(a)/Lipids, Mammogram & DEXA"},
        ]
        summary["issues"] = [{
            "id": "iss_corp_prev_bundle",
            "name": "1. Executive preventive health & longevity bundle (with corporate flu shot)",
            "bullets": [
                "Cleared for Harbour Capital corporate flu shot; opted to bundle overdue HK CHP screenings (Shingrix, biennial mammogram, FIT) and longevity biomarkers (non-smoker, FHx hypercholesterolaemia).",
                "Baseline fasting LDL was elevated and HbA1c borderline in prior panel (see Lab Panel & 10-Yr CVD Risk Score).",
            ],
            "safety_gap": None,
            "suggested_orders": [
                {"id": "corp_prev_shingrix", "type": "rx", "action": "start", "issue": "1. Executive preventive health & longevity bundle (with corporate flu shot)", "drug": "Shingrix (Recombinant Zoster Vaccine) Dose 1", "freq": "IM single dose", "days": "1d", "note": "Co-administer in opposite deltoid with corporate QIV flu shot; Dose 2 in 8w"},
                {"id": "corp_prev_lab_long", "type": "lab", "issue": "1. Executive preventive health & longevity bundle (with corporate flu shot)", "testName": "Fasting Lipid Profile + ApoB + Lp(a) + hs-CRP + HbA1c + Renal Panel + HK FIT", "lab": "Central Health / KingMed", "fasting": True, "when": "Morning bundled with flu shot"},
                {"id": "corp_prev_mammo", "type": "imaging", "issue": "1. Executive preventive health & longevity bundle (with corporate flu shot)", "modality": "Bilateral Screening Mammogram + DEXA Body Composition", "indication": "HK biennial breast screening & baseline longevity DEXA (54F)", "priority": "Routine"},
                {"id": "corp_prev_book_nurse", "type": "book", "issue": "1. Executive preventive health & longevity bundle (with corporate flu shot)", "visitType": "Morning Preventive Bloods + Flu & Shingrix Nurse Bay", "when": "Bundled single clinic visit"},
                {
                    "id": "corp_prev_leaflet",
                    "type": "goal",
                    "issue": "1. Executive preventive health & longevity bundle (with corporate flu shot)",
                    "metric": "Preventive Health & Longevity Prep Guide",
                    "leaflet_id": "longevity_preventive_hk",
                    "title": "Executive Preventive Screening & Longevity Biomarker Guide",
                    "template": "Central Health · Preventive & Longevity Care Sheet",
                    "target_text": "Fast 8–10 hours (plain water fine) before morning blood draw; corporate flu shot and Shingrix Dose 1 are bundled in the same 15-min visit; Shingrix Dose 2 reminder runs automatically at Week 8.",
                },
            ],
        }]
        summary["sam_proactive_insight"] = (
            "Converted corporate flu visit into full Executive Preventive & Longevity episode: QIV Flu Shot + Shingrix Dose 1 (Week-8 Dose 2 loop in # ai-team), "
            "Fasting Lipids/ApoB/Lp(a)/hs-CRP/HbA1c/FIT lab slip, and Biennial Mammogram + DEXA referral staged for 1-click sign-off."
        )
        if not any("Step 2/2" in str(x.get("text", "")) for x in agent_thread):
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "AI Resident (Step 2/2): Computed 10-Yr CVD Risk and compiled combined HK Preventive + Longevity bundle (Shingrix Dose 1, Fasting ApoB/Lp(a)/hs-CRP/Lipids/HbA1c/FIT, Biennial Mammogram + DEXA, and Patient Prep Leaflet) for Dr. Sarah Wong's sign-off.",
            })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("PracticeGraph · Packaged combined HK Preventive + Longevity executive bundle (5/5 linter)")
        r_en = (
            "Wonderful — I've packaged your corporate flu shot together with your Shingrix vaccine, fasting longevity & heart blood panel (cholesterol, ApoB, Lp(a), hs-CRP, blood sugar, kidney, and FIT screening), biennial mammogram and DEXA referral, plus a preparation guide for Dr. Sarah Wong to sign off. "
            "As soon as she approves, your lab slip, care guide, and single-visit booking slots will appear right here."
        )
        r_zh = (
            "太好喇——我已經將你嘅企業流感針，連同生蛇針（Shingrix 第一針）、空腹長壽及護心驗血（膽固醇、ApoB、Lp(a)、hs-CRP、血糖、腎功能及大腸癌篩查）、乳房造影同 DEXA 掃描轉介，以及檢查前準備單張一併交畀黃莎拉醫生批核。"
            "醫生簽署後，化驗單、護理單張同埋一次過完成嘅預約時段會即時顯示喺呢度。"
        )
        summary["_last_reply_bilingual"] = (r_en, r_zh)
        return (
            "needs_decision",
            "with_doctor",
            "Corporate Flu + Executive Preventive & Longevity",
            "54F Harbour Capital employee converted from flu shot to Shingrix, Mammogram, FIT & ApoB/Lp(a)/hs-CRP/DEXA bundle.",
            "Preventive · Longevity Bundle",
            summary,
            r_zh if is_zh else r_en,
            steps,
        )

    # ------------------------------------------------------------------ 4. Acute Fever Deferral + Slot Release + Day +5 Auto-Rebook
    confirmed_defer = any(
        k in all_low or k in all_text
        for k in (
            "release", "rebook", "no shortness", "chest pain", "next week",
            "取消", "改期", "冇氣促", "冇心口痛", "下星期",
        )
    )
    summary["vitals_exam"] = {
        "source": f"Corporate Flu Deferral · Today {hhmm}",
        "encounter_context": "Harbour Capital Partners · Slot Protection & Auto-Recall",
        "items": [
            {"label": "Home Temp", "val": "38.6 °C with body aches"},
            {"label": "Red Flags", "val": "Denies SOB or chest pain" if confirmed_defer else "Screening acute red flags"},
            {"label": "Bay Slot Action", "val": "Tomorrow 10:15 Nurse Bay released · Day +5 AI rebook armed" if confirmed_defer else "Pending slot release confirmation"},
        ],
    }

    if not confirmed_defer:
        summary["_corp_stage"] = "awaiting_defer_confirm"
        if not any("Step 1/2" in str(x.get("text", "")) for x in agent_thread):
            agent_thread.append({
                "actor": "Sam",
                "time": hhmm,
                "text": "AI Resident (Step 1/2): Employee reported 38.6°C fever ahead of tomorrow's 10:15 corporate flu bay. Advised HK CHP 48h fever-free deferral rule, screening red flags, and preparing to release slot & arm Day +5 rebook loop.",
            })
        summary["agent_thread"] = agent_thread[-4:]
        steps.append("FluVaxScreen · Fever 38.6°C detected -> Advised 48h fever-free deferral & red-flag check")
        r_en = (
            "With a 38.6°C fever, it's best to hold off on the flu shot until you have been fever-free for 48 hours. "
            "First, are you having any shortness of breath, chest pain, or severe headache? If not, I can release tomorrow's 10:15 vaccination slot right away and check in automatically next week to rebook you — or arrange a GP consult if you'd like symptom care or sick leave today."
        )
        r_zh = (
            "發燒 38.6 度期間建議先延遲打流感針，等退燒滿 48 小時後再接種會最安全。"
            "先確認一下你有冇氣促、心口痛或者劇烈頭痛？如果冇，我可以即刻幫你釋放聽朝 10:15 嘅接種名額，並於下星期自動喺度提你改期；或者如果你想今日睇醫生攞退燒藥／病假紙，都可以話我知。"
        )
        summary["_last_reply_bilingual"] = (r_en, r_zh)
        return (
            "running",
            "with_patient",
            "Corporate Flu — Fever Deferral Check",
            "Employee has 38.6°C fever before tomorrow's flu bay; screening red flags & preparing slot release.",
            "Flu · Fever Deferral",
            summary,
            r_zh if is_zh else r_en,
            steps,
        )

    # Deferral confirmed -> Release slot, arm Day +5 # ai-team rebook loop, ZERO doctor interruption!
    summary["_corp_stage"] = "deferred"
    summary["alerts"] = [
        {
            "level": "info",
            "label": "Tomorrow 10:15 Nurse Bay Released · Zero No-Show Waste",
            "detail": "Slot returned to Harbour Capital pool · Day +5 # ai-team auto-rebook loop armed (zero doctor interruption)",
        }
    ]
    summary["scheduled_tasks"] = [
        {"status": "auto", "label": "Tomorrow 10:15 Nurse Bay · Released back to Harbour Capital booking pool"},
        {"status": "auto", "label": "Day +5 · Autonomous # ai-team Fever-Free Check & Corporate Flu Auto-Rebook"},
    ]
    summary["issues"] = [{
        "id": "iss_corp_flu_defer",
        "name": "1. Corporate flu shot deferred — acute fever (auto-rebook armed)",
        "bullets": [
            "Employee reported 38.6°C fever without acute red flags; deferred per HK CHP 48-hour fever-free vaccination guideline.",
            "Tomorrow's 10:15 Nurse Bay slot released back to campaign pool and Day +5 # ai-team rebook loop scheduled with zero doctor interruption.",
        ],
        "safety_gap": None,
        "suggested_orders": [
            {"id": "corp_defer_book", "type": "book", "issue": "1. Corporate flu shot deferred — acute fever (auto-rebook armed)", "visitType": "Corporate Flu Shot Rebook (Nurse Bay)", "when": "Auto-offered on Day +5 once fever-free 48h"},
        ],
    }]
    if not any("Step 2/2" in str(x.get("text", "")) for x in agent_thread):
        agent_thread.append({
            "actor": "Sam",
            "time": hhmm,
            "text": "AI Resident (Step 2/2): Red flags cleared. Released tomorrow's 10:15 Nurse Bay slot (preventing no-show waste) and handed loop to # ai-team for Day +5 fever-free auto-rebook — zero doctor workload.",
        })
    summary["agent_thread"] = agent_thread[-4:]
    steps.append("SchedulingAgent · Released tomorrow 10:15 Nurse Bay slot back to campaign pool")
    steps.append("LoopWatcher · Armed Day +5 # ai-team fever-free auto-rebook loop (zero doctor workload)")
    r_en = (
        "All sorted — I've released your 10:15 slot tomorrow so a colleague can use it, and set an automatic check-in for 5 days from now to rebook your corporate flu shot once you're fever-free. "
        "Rest up and stay hydrated; if your fever rises above 39°C or you'd like a doctor review or sick-leave certificate at any point, just message me here."
    )
    r_zh = (
        "已經幫你處理好——我取消咗聽朝 10:15 嘅名額讓畀其他同事，並設定咗 5 日後自動喺呢度搵你，等你退燒後幫你一鍵改期打企業流感針。"
        "記得多休息同多飲水；如果高燒超過 39 度或者需要睇醫生攞病假紙，隨時喺呢度話我知。"
    )
    summary["_last_reply_bilingual"] = (r_en, r_zh)
    return (
        "scheduled",
        "idle",
        "Corporate Flu Deferred — Day +5 AI Rebook",
        "Tomorrow's 10:15 bay released cleanly; # ai-team armed for Day +5 fever-free auto-rebook (zero doctor load).",
        "Slot released · AI Day-5 rebook",
        summary,
        r_zh if is_zh else r_en,
        steps,
    )
