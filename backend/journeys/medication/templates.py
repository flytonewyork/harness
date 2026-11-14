"""Suggested replies the doctor can send once they have decided. The agent never sends these: it puts the right
one in the item's request text, and the doctor sends it, edits it or writes their own (the care line, harness #28,
is what delivers it).

Every template is checked by tests/test_journey_medication.py: none reassures ("nothing to worry about", "normal"),
none gives a dose or tells the patient to start, stop or change a medicine, and each one says what to do if the
patient gets worse. The doctor adds any clinical advice in their own words.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "emergency": {
        "en": "Please call 999 or go to the nearest A&E now, and take your medicines with you to show the doctor there. "
              "{doctor} has your message.",
        "zh": "請即刻打999或者去最近嘅急症室，帶埋你啲藥俾嗰度嘅醫生睇。{doctor}已經收到你嘅訊息。",
    },
    "crisis": {
        "en": "Thank you for telling us. If you might act on these thoughts, call 999 now. You can talk to someone any time "
              "at the Samaritan Befrienders on 2389 2222. {doctor} has your message and will contact you today.",
        "zh": "多謝你話俾我哋知。如果你覺得自己可能會傷害自己，請即刻打999。你隨時可以打香港撒瑪利亞防止自殺會 2389 2222 搵人傾。"
              "{doctor}已經收到你嘅訊息，今日會聯絡你。",
    },
    "doctor_will_call": {
        "en": "{doctor} has read your message and will contact you {when}. If it gets worse before then, or you feel very "
              "unwell, call 999 or go to A&E.",
        "zh": "{doctor}已經睇咗你嘅訊息，會喺{when}聯絡你。如果之前情況差咗，或者覺得好唔舒服，請打999或者去急症室。",
    },
    "dose_question": {
        "en": "{doctor} will answer your question about your medicine {when}. We can't advise on doses by message, so "
              "please wait for the doctor's reply before changing anything. If you feel very unwell, call 999.",
        "zh": "{doctor}會喺{when}答你關於藥嘅問題。我哋唔可以喺訊息度建議劑量，所以改變任何嘢之前請等醫生覆你。"
              "如果覺得好唔舒服，請打999。",
    },
    "repeat_received": {
        "en": "We have your request for {medicine}. {doctor} will review it; it is not approved until they do. "
              "We will message you {when}. If you have run out and feel unwell, call the clinic, or 999 in an emergency.",
        "zh": "我哋收到你想覆配{medicine}嘅要求。{doctor}會睇過，醫生批咗先算數。我哋會喺{when}覆你。"
              "如果你已經冇藥又覺得唔舒服，請打俾診所；緊急情況請打999。",
    },
    "repeat_needs_visit": {
        "en": "{doctor} would like to see you before repeating {medicine}. Please book a visit; the clinic's number is {phone}.",
        "zh": "{doctor}想覆配{medicine}之前先見一見你。請預約覆診，診所電話係{phone}。",
    },
}

# words no template may contain (checked in tests): reassurance, dosing, start/stop instructions
FORBIDDEN = ("nothing to worry", "don't worry", "no need to worry", "normal", "you're fine", "you are fine", "safe to",
             "mg", "tablet", "double", "stop taking", "keep taking", "carry on taking", "start taking", "increase", "reduce",
             "唔使擔心", "冇事", "正常", "冇問題", "停藥", "繼續食", "食多", "食少", "加藥", "減藥")


def pick(kinds: set[str], urgency: int) -> str:
    if "crisis" in kinds:
        return "crisis"
    if urgency >= 4:
        return "emergency"
    if kinds == {"repeat"}:
        return "repeat_received"
    if "dose_question" in kinds and urgency <= 2:
        return "dose_question"
    return "doctor_will_call"


def render(tid: str, lang: str = "en", **slots) -> str:
    t = TEMPLATES[tid][lang]
    for k, v in {"doctor": "your doctor", "when": "today", "medicine": "your medicine", "phone": "the clinic", **slots}.items():
        t = t.replace("{" + k + "}", str(v))
    return t[:1].upper() + t[1:]
