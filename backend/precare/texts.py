"""Every message the precare line can send. Fixed, versioned and bilingual (English, Cantonese).

Nothing here is generated: questions, preparation instructions and emergency replies are clinic-approved templates
filled with the appointment's own details. A model may later help read the patient's replies; it never writes
what the patient is told. Changing a text means a new TEXT_VERSION.
"""
from __future__ import annotations

from datetime import datetime

TEXT_VERSION = "precare-texts-v2"   # v2: safety-netting line in every preparation message

LABEL = {"en": "Care team (automated)", "zh": "護理團隊（自動回覆）"}

ASK = {
    "invite": {
        "en": "Hello, this is {clinic}. Before your appointment with {doctor} on {when}, we have a few questions "
              "(about 2 minutes) so your doctor is ready for you. This line is automated and not for emergencies: "
              "in an emergency call 999.\n請用中文回覆亦可。\nTo start, please reply with your full name.",
        "zh": "你好，呢度係{clinic}。你{when}約咗{doctor}睇症，睇症前我哋想問你幾條問題（大約兩分鐘），等醫生預備好。"
              "呢個係自動回覆，唔處理緊急情況：緊急情況請打999。\nYou can reply in English.\n請先覆你嘅全名。",
    },
    "name": {"en": "Please reply with your full name.", "zh": "請覆你嘅全名。"},
    "dob": {"en": "Thank you. Please reply with your date of birth (day/month/year).",
            "zh": "多謝。請覆你嘅出生日期（日/月/年）。"},
    "retry": {"en": "Sorry, those details don't match our booking. Please check and send your full name again.",
              "zh": "對唔住，資料同我哋嘅預約對唔上。請再覆一次你嘅全名。"},
    "consent": {
        "en": "Thank you. May we keep your answers and share them with {doctor} before your visit? "
              "Reply YES to agree or NO to skip. You can reply STOP at any time.",
        "zh": "多謝。我哋可唔可以記錄你嘅答案，喺睇症前交俾{doctor}？同意請覆「同意」，唔想就覆「唔要」。"
              "你隨時可以覆「停止」。",
    },
    "reason": {"en": "In your own words, what is the main reason for your visit?",
               "zh": "請用你自己嘅說話講講，今次主要想睇乜嘢？"},
    "duration": {"en": "How long has this been going on?", "zh": "呢個情況有幾耐？"},
    "meds": {"en": "What medicines do you take now, including any from a pharmacy or supplements? Reply NONE if none.",
             "zh": "你而家有冇食緊藥（包括藥房買嘅藥同保健品）？冇就覆「冇」。"},
    "allergies": {"en": "Are you allergic to any medicines? Reply NONE if none.",
                  "zh": "你有冇對任何藥物敏感？冇就覆「冇」。"},
    "anything_else": {"en": "Is there anything else you want {doctor} to know, or any question for them? Reply NO if not.",
                      "zh": "有冇其他嘢想{doctor}知道，或者想問醫生？冇就覆「冇」。"},
    "prep_ack": {"en": "Please reply OK once you have read this.", "zh": "睇完請覆「OK」。"},
}

PREP = {   # key -> text; which keys a visit gets is decided in agent.prep_keys()
    "fasting": {"en": "Your blood test needs fasting: please don't eat or drink anything except plain water for 10 hours "
                      "before your appointment.",
                "zh": "你嘅驗血需要空肚：睇症前10個鐘請唔好食嘢或者飲嘢，清水除外。"},
    "medicines_unchanged": {"en": "Don't change or stop any medicine unless your doctor tells you to.",
                            "zh": "除非醫生話你知，請唔好自己改或者停任何藥。"},
    "bring_meds": {"en": "Please bring your medicines, or their boxes or a photo of the labels.",
                   "zh": "請帶埋你食緊嘅藥，或者藥盒、藥袋嘅相。"},
    "bring_reports": {"en": "Please bring any reports, letters or scans from other doctors or hospitals.",
                      "zh": "如果有其他醫生或者醫院嘅報告、信或者掃描，請帶埋嚟。"},
    "procedure": {"en": "For your procedure, please wear loose clothing and arrange for someone to take you home if you are told you will be sedated.",
                  "zh": "做手術當日請著寬鬆衫，如果醫生講會用鎮靜劑，請安排人陪你返屋企。"},
    "safety_net": {"en": "If you feel much worse before your visit, don't wait for it: call 999 or go to the nearest A&E.",
                   "zh": "如果睇症前覺得差咗好多，唔好等：請打999或者去最近嘅急症室。"},
    "arrive_early": {"en": "Please arrive 10 minutes early with your ID card.", "zh": "請提早10分鐘到，記得帶身份證。"},
}

PREP_HEAD = {"en": "Thank you. How to prepare for your visit on {when}:", "zh": "多謝。{when}睇症前請留意："}

FIXED = {
    "red_flag": {
        "en": "What you describe can be serious. Please call 999 or go to the nearest A&E now. Don't wait for your "
              "appointment. We have alerted {doctor} and the clinic.",
        "zh": "你講嘅情況可能好嚴重。請即刻打999或者去最近嘅急症室，唔好等到預約時間。我哋已經通知咗{doctor}同診所。",
    },
    "crisis": {
        "en": "Thank you for telling us. If you might hurt yourself or you are in danger, call 999 now. You can talk to "
              "the Samaritans any time on 2896 0000. We have alerted {doctor} and the clinic.",
        "zh": "多謝你話俾我哋知。如果你可能會傷害自己或者有危險，請即刻打999。你亦可以隨時打撒瑪利亞會熱線 2896 0000。"
              "我哋已經通知咗{doctor}同診所。",
    },
    "after_alarm": {"en": "A doctor has been alerted. In an emergency call 999.",
                    "zh": "醫生已經收到通知。緊急情況請打999。"},
    "question_routed": {
        "en": "That's a question for {doctor}, so we have passed it on and they will reply before your visit. "
              "We can't advise on medicines or results here.",
        "zh": "呢條問題要由{doctor}答，我哋已經轉咗俾醫生，睇症前會覆你。我哋喺度唔可以就藥物或者報告俾意見。",
    },
    "declined": {"en": "No problem. We haven't kept your answers. Please bring any questions to your visit.",
                 "zh": "冇問題，我哋冇記錄你嘅答案。有問題可以睇症時問醫生。"},
    "stopped": {"en": "You won't get more messages from this line about this visit. See you at your appointment.",
                "zh": "呢個預約唔會再有訊息。到時見。"},
    "identity_failed": {"en": "Sorry, we couldn't confirm your details. Please call the clinic on {phone}. Nothing you sent has been kept.",
                        "zh": "對唔住，我哋確認唔到你嘅資料。請致電診所 {phone}。你send嚟嘅資料冇被記錄。"},
    "done": {"en": "Thank you, {doctor} will see your answers before your visit. See you on {when}.",
             "zh": "多謝，{doctor}睇症前會睇你嘅答案。{when}見。"},
    "reminder": {"en": "A reminder about your appointment with {doctor} on {when}: please reply to the questions above when you can.",
                 "zh": "提提你{when}約咗{doctor}：得閒請覆上面嘅問題。"},
    "closed": {"en": "This line has finished for this visit. Please call the clinic on {phone} if you need us. In an emergency call 999.",
               "zh": "呢個預約嘅問題已經完成。有需要請致電診所 {phone}。緊急情況請打999。"},
}


CATALOGUE = {f"ask.{k}": v for k, v in ASK.items()} | {f"prep.{k}": v for k, v in PREP.items()} | \
    {"prep.head": PREP_HEAD} | {f"fixed.{k}": v for k, v in FIXED.items()}
VALUES = ("doctor", "when", "clinic", "phone")       # the only slot values, all taken from the booking


def label(lang: str) -> str:
    return LABEL[lang]


def render(text: str, lang: str) -> str:
    return LABEL[lang] + "\n" + text


def _when(start: datetime, lang: str) -> str:
    if lang == "zh":
        return f"{start.month}月{start.day}日（星期{'一二三四五六日'[start.weekday()]}）{start:%H:%M}"
    return f"{start:%a} {start.day} {start:%b} at {start:%H:%M}"


def values_for(appt: dict, doctor: dict, lang: str) -> dict:
    """Slot values for a message, all from the booking: nothing the patient wrote ever goes into a message."""
    clinic = appt.get("clinic") or {}
    zh = lang == "zh"
    return {"doctor": (doctor.get("name_zh") if zh else None) or doctor["name_en"],
            "when": _when(datetime.fromisoformat(appt["start"]), lang),
            "clinic": clinic.get("name_zh" if zh else "name_en") or ("診所" if zh else "the clinic"),
            "phone": clinic.get("phone") or ("診所電話" if zh else "the clinic's number")}


def compose(ids: list[str], lang: str, values: dict) -> str:
    """The one way a message is made: catalogue templates in order, slot values from the whitelist. `prep.*`
    lines (other than the head) are numbered. Raises KeyError on an unknown template or value."""
    if set(values) - set(VALUES):
        raise KeyError(f"slot values outside the whitelist: {sorted(set(values) - set(VALUES))}")
    lines, n = [], 0
    for i in ids:
        t = CATALOGUE[i][lang].format(**values)
        if i.startswith("prep.") and i != "prep.head":
            n += 1
            t = f"{n}. {t}"
        lines.append(t)
    return render("\n".join(lines), lang)
