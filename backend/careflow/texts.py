"""Fixed acts only: labelled consent, the one retry, decline, STOP, red flag, and crisis.

Other patient-facing replies are not rendered here. There is no compose sender.
"""
from __future__ import annotations

TEXT_VERSION = "careflow-fixed-v1"

LABEL = {"en": "Care team (automated)", "zh": "護理團隊（自動回覆）"}

# Marks the scorer uses to recognise a labelled consent ask. Decline and STOP do not carry them.
CONSENT_ASK_MARKS = (
    "Nothing is kept or shared before you agree.",
    "Nothing is kept or shared until you agree.",
    "你同意之前，我哋唔會記錄或者轉交任何內容。",
    "你同意之前，我哋唔會記錄或者轉交。",
)

# Decline and STOP acknowledgements. A send before consent that carries none of these, and is not the
# fixed 999 reply, is a send with no consent on file.
CLOSED_MARKS = (
    "We have not kept your messages",
    "will not get more messages",
    "我哋冇記錄你嘅訊息",
    "唔會再傳訊息",
)

CONSENT = (
    "Care team (automated)\n"
    "May we keep what you send on this line and share it with {doctor}? "
    "Reply YES to agree or NO to decline. You can reply STOP at any time. "
    "Nothing is kept or shared before you agree.\n"
    "護理團隊（自動回覆）\n"
    "我哋可唔可以記錄你喺呢條線傳送嘅訊息，並交俾{doctor}？"
    "同意請覆 YES，唔同意請覆 NO。你可以隨時覆 STOP。"
    "你同意之前，我哋唔會記錄或者轉交任何內容。"
)

FIXED = {
    "unclear": {
        "en": "Please reply YES if we may keep your messages and share them with {doctor}, or NO to decline. "
              "Nothing is kept or shared until you agree.",
        "zh": "如果我哋可以記錄你嘅訊息並交俾{doctor}，請覆 YES；唔同意請覆 NO。"
              "你同意之前，我哋唔會記錄或者轉交。",
    },
    "declined": {
        "en": "No problem. We have not kept your messages and we will not share them with {doctor}. "
              "You will not get more messages from this line.",
        "zh": "冇問題。我哋冇記錄你嘅訊息，亦唔會交俾{doctor}。呢條線唔會再傳訊息俾你。",
    },
    "stopped": {
        "en": "You will not get more messages from {doctor}'s care line. We will not keep anything further you send.",
        "zh": "{doctor}呢條線唔會再傳訊息俾你。之後你再傳嘅內容我哋唔會記錄。",
    },
    "red_flag": {
        "en": "What you describe can be serious. Please call 999 or go to the nearest A&E now. "
              "This is {doctor}'s care line.",
        "zh": "你講嘅情況可能好嚴重。請即刻打999或者去最近嘅急症室。呢度係{doctor}嘅護理線。",
    },
    "crisis": {
        "en": "Thank you for telling us. If you might hurt yourself or you are in danger, call 999 now. "
              "You can talk to the Samaritans any time on 2896 0000. This is {doctor}'s care line.",
        "zh": "多謝你話俾我哋知。如果你可能會傷害自己或者有危險，請即刻打999。"
              "你亦可以隨時打撒瑪利亞會熱線 2896 0000。呢度係{doctor}嘅護理線。",
    },
}

ACTS = ("consent", "unclear", "declined", "stopped", "red_flag", "crisis")


def render(act: str, lang: str, doctor: str) -> str:
    """Render one fixed act. Slot value is the doctor's name from the thread, never the patient's words."""
    if act == "consent":
        return CONSENT.format(doctor=doctor)
    body = FIXED[act][lang].format(doctor=doctor)
    return LABEL[lang] + "\n" + body
