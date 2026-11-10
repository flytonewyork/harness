"""WhatsApp message templates the line needs, in English and Chinese (Hong Kong).

WhatsApp lets a business start a conversation, or write more than 24 hours after the person last wrote, only with a
template Meta has approved. The line uses five, all in the utility category. `scripts/line_templates.py` submits
them and reads their status back. The wording here must match what Meta approved, or sends fail.
"""
from __future__ import annotations

TEMPLATES = {
    "line_checkin": {
        "category": "UTILITY",
        "en": {"body": "Hello {{1}}, it's {{2}} days since your visit with {{3}}. How are you feeling? This is {{3}}'s care line (automated, by tmrw).",
               "example": ["Ka Yan", "3", "Dr Thomas Hu"], "buttons": ["Better", "About the same", "Worse"]},
        "zh_HK": {"body": "{{1}}你好，距離你見{{3}}已經{{2}}日。你感覺點呀？呢度係{{3}}護理熱線（自動回覆，由 tmrw 提供）。",
                  "example": ["家欣", "3", "胡醫生"], "buttons": ["好咗", "差唔多", "差咗"]},
    },
    "line_reminder": {
        "category": "UTILITY",
        "en": {"body": "{{1}}'s care line (automated, by tmrw). A reminder from your plan: {{2}}. Reply here if you have a question.",
               "example": ["Dr Thomas Hu", "fasting blood test this week"], "buttons": []},
        "zh_HK": {"body": "{{1}}護理熱線（自動回覆，由 tmrw 提供）。計劃提提你：{{2}}。有問題可以喺度回覆。",
                  "example": ["胡醫生", "今個星期空腹驗血"], "buttons": []},
    },
    "line_waiting": {
        "category": "UTILITY",
        "en": {"body": "{{1}}'s care line (automated, by tmrw) has a message for you. Tap below to read it.",
               "example": ["Dr Thomas Hu"], "buttons": ["Read it"]},
        "zh_HK": {"body": "{{1}}護理熱線（自動回覆，由 tmrw 提供）有訊息俾你，請按下面睇。",
                  "example": ["胡醫生"], "buttons": ["睇訊息"]},
    },
    "line_doctor_new": {
        "category": "UTILITY",
        "en": {"body": "tmrw: {{1}} new item(s) are waiting on your care line. Tap below to see them.",
               "example": ["2"], "buttons": ["Show me"]},
    },
    "line_payment": {
        "category": "UTILITY",
        "en": {"body": "{{1}}'s care line (automated, by tmrw): {{2}}. Pay here to confirm: {{3}}",
               "example": ["Dr Thomas Hu", "repeat of Amlodipine 5 mg approved, HK$230", "https://checkout.stripe.com/c/pay/example"], "buttons": []},
        "zh_HK": {"body": "{{1}}護理熱線（自動回覆，由 tmrw 提供）：{{2}}。請喺呢度付款確認：{{3}}",
                  "example": ["胡醫生", "已批准覆配 Amlodipine 5 mg，HK$230", "https://checkout.stripe.com/c/pay/example"], "buttons": []},
    },
}


def lang_code(lang: str, name: str) -> str:
    """The template language to send: zh_HK when the patient reads Chinese and that version exists."""
    return "zh_HK" if lang == "zh" and "zh_HK" in TEMPLATES[name] else "en"


def payload_body(name: str, lang_key: str) -> dict:
    """The Graph API body to create one template language."""
    t = TEMPLATES[name][lang_key]
    comps = [{"type": "BODY", "text": t["body"], "example": {"body_text": [t["example"]]}}]
    if t["buttons"]:
        comps.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": b} for b in t["buttons"]]})
    return {"name": name, "language": lang_key, "category": TEMPLATES[name]["category"], "components": comps}
