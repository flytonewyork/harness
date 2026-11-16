"""Every word the care line sends a patient, in English and Cantonese.

The agent sends only these texts, filled with facts from the clinic's settings or the chart; it never writes
free text to a patient, so it cannot give clinical advice of its own (spec 003, `clinical_advice`). Clinical
words reach a patient only inside a reply the doctor approved (the written plan, approved test preparation).
TEXT_VERSION goes into the record of every message, so a change of wording is traceable. All of it is a proposal
until the doctor approves it (`clinic_settings.json`, approved_by).
"""
from __future__ import annotations

TEXT_VERSION = "careline-texts-v1"

T: dict[str, dict[str, str]] = {
    # AC-05: every message says who sent it
    "label": {"en": "{clinic} care team (automated)", "zh": "{clinic}護理團隊（自動回覆）"},

    # AC-01: join after the visit
    "welcome": {
        "en": ("Hello, this is the {clinic} care team. We are an automated service working for your doctor, {doctor}.\n"
               "We reply in the clinic's opening hours. A person at the clinic sees anything clinical.\n"
               "For an emergency, call 999 or go to A&E. Don't wait for a reply here.\n\n"
               "Would you like to join, so we can send you your plan and help with bookings, results and repeat prescriptions?"),
        "zh": ("你好，呢度係{clinic}護理團隊。我哋係為你嘅醫生{doctor}服務嘅自動回覆系統。\n"
               "我哋喺診所開放時間內回覆，所有醫療問題都會由診所嘅人睇過。\n"
               "如有緊急情況，請即打999或去急症室，唔好等呢度回覆。\n\n"
               "你想唔想加入？加入後我哋可以send你嘅治療計劃，同埋幫你預約、查報告同覆配藥物。"),
    },
    "consent_wording": {
        "en": "Join the {clinic} care line: the clinic's automated care team may message me about my care on WhatsApp.",
        "zh": "加入{clinic}護理熱線：診所嘅自動護理團隊可以用WhatsApp同我聯絡有關我嘅護理。",
    },
    "btn_join": {"en": "Yes, join", "zh": "好，加入"},
    "btn_no": {"en": "No thanks", "zh": "唔使喇"},
    "declined": {"en": "No problem. Nothing has been saved. You can call the clinic on {phone}.",
                 "zh": "冇問題，我哋冇保存任何資料。你可以打{phone}搵診所。"},
    "ask_name": {"en": "Thank you. To find your record, what is your full name as the clinic has it? (e.g. WONG Ka Yan)",
                 "zh": "多謝。為咗搵到你嘅紀錄，請講你喺診所登記嘅全名（例如：黃家欣 或 WONG Ka Yan）。"},
    "ask_dob": {"en": "And your date of birth? (e.g. 31/12/1980)", "zh": "你嘅出世日期係？（例如：31/12/1980）"},
    "bad_dob": {"en": "Sorry, I couldn't read that date. Please write it as day/month/year, e.g. 31/12/1980.",
                "zh": "唔好意思，睇唔明呢個日期。請用 日/月/年 嘅格式，例如 31/12/1980。"},
    "linked": {"en": "Thank you, {first}. You're joined. Choose what you need from the menu any time.",
               "zh": "多謝你，{first}。已經加入咗。你隨時可以喺選單揀你需要嘅服務。"},
    "no_match": {"en": "Thank you. I couldn't match that to one record, so I've asked the clinic to check. A person will reply by {when}. Nothing from a chart is shared until then.",
                 "zh": "多謝。我未能配對到一份紀錄，已經請診所核對。診所職員會喺{when}之前回覆你。核對前唔會分享任何紀錄內容。"},
    "ask_invites": {"en": "One more thing: would you also like invitations, such as the yearly flu vaccine? This is a separate choice. You can say no and still get your care messages.",
                    "zh": "另外：你想唔想收邀請（例如每年嘅流感疫苗）？呢個係另一個選擇，揀唔要都會照收護理訊息。"},
    "btn_invites_yes": {"en": "Yes, invitations", "zh": "要邀請"},
    "btn_invites_no": {"en": "No invitations", "zh": "唔要邀請"},
    "invites_saved": {"en": "Saved: invitations {state}.", "zh": "已記錄：邀請{state}。"},

    # AC-04: stop
    "stopped": {"en": "You won't get any more messages from the care line, and the clinic has been told. To start again, write JOIN. For anything urgent, the clinic will phone you.",
                "zh": "你唔會再收到護理熱線嘅訊息，我哋已經通知診所。想重新開始，請寫 JOIN。如有急事，診所會打電話俾你。"},

    # AC-02: identity
    "check_dob": {"en": "Before I share anything from your record, please confirm your date of birth.",
                  "zh": "分享你嘅紀錄之前，請確認你嘅出世日期。"},
    "check_failed": {"en": "Sorry, I can't share that here. I've asked the clinic to contact you. A person will reply by {when}.",
                     "zh": "唔好意思，我喺度唔可以分享。我已經請診所聯絡你，職員會喺{when}之前回覆。"},

    # menu
    "menu_body": {"en": "What do you need?", "zh": "你需要咩服務？"},
    "menu_button": {"en": "Menu", "zh": "選單"},
    "menu_hint": {"en": "Choose from the menu, or write what you need.", "zh": "請喺選單揀，或者直接寫低你需要咩。"},

    # FD-03
    "info": {"en": "{clinic}\n{address}\nPhone {phone}\n\nOpening hours:\n{hours}",
             "zh": "{clinic}\n{address}\n電話 {phone}\n\n開放時間：\n{hours}"},
    # FD-05
    "fees": {"en": "Fees at {clinic}:\n{rows}\nThe clinic can tell you more about insurance.",
             "zh": "{clinic}收費：\n{rows}\n保險方面可以問診所職員。"},

    # FD-01
    "slots": {"en": "These times are free with {doctor}. Choose one:", "zh": "{doctor}以下時間有空，請揀一個："},
    "no_slots": {"en": "I can't see a free time in the next few days, so I've asked the front desk to call you. They will reply by {when}.",
                 "zh": "未來幾日暫時冇空檔，我已經請前台聯絡你，佢哋會喺{when}之前回覆。"},
    "booked": {"en": "Booked: {when} with {doctor}. Reply CANCEL if you can't come.",
               "zh": "已預約：{when}，{doctor}。如果嚟唔到，請回覆 CANCEL。"},
    "cancelled": {"en": "Cancelled your visit at {when}. Choose Book from the menu to pick a new time.",
                  "zh": "已取消{when}嘅預約。想再約，請喺選單揀「預約」。"},

    # handoffs (HE-02, HE-05, FD-08)
    "handoff_open": {"en": "I've passed this to {owner}, word for word. They will reply by {when}.\nIf it can't wait, call 999 or go to A&E.",
                     "zh": "我已經將你嘅訊息原文轉交{owner}，佢會喺{when}之前回覆。\n如果唔可以等，請即打999或去急症室。"},
    "handoff_closed": {"en": "The clinic is closed now. I've passed this to {owner}, word for word. They will reply by {when}, after the clinic opens.\nIf it can't wait, call 999 or go to A&E.",
                       "zh": "診所而家休息。我已經將你嘅訊息原文轉交{owner}，佢會喺診所開門後、{when}之前回覆。\n如果唔可以等，請即打999或去急症室。"},
    "handoff_staff": {"en": "I've passed this to the front desk, word for word. They will reply by {when}.",
                      "zh": "我已經將你嘅訊息原文轉交前台，佢哋會喺{when}之前回覆。"},

    # NV-01..05: things the care team never does
    "refuse_clinical": {"en": "I can't advise on symptoms, medicines or results: only your doctor can. I've passed your message to {owner}, word for word. They will reply by {when}.\nIf it can't wait, call 999 or go to A&E.",
                        "zh": "我唔可以就病徵、藥物或者報告俾意見，只有你嘅醫生可以。我已經將你嘅訊息原文轉交{owner}，佢會喺{when}之前回覆。\n如果唔可以等，請即打999或去急症室。"},
    "refuse_sick_leave": {"en": "Sick leave needs the doctor to see you. Would you like to book a visit? Choose Book from the menu.",
                          "zh": "病假紙需要醫生睇過先可以發。想預約嘅話，請喺選單揀「預約」。"},

    # RT-08: never values, never test names (RP-02), never "normal" (NV-04)
    "results_none": {"en": "I can't see any test results in your record from the last 3 months.",
                     "zh": "你嘅紀錄入面過去3個月冇驗身報告。"},
    "results_line_pending_review": {"en": "• Report of {date}: it's back. {doctor} hasn't reviewed it yet. The clinic will contact you if you need to do anything.",
                                    "zh": "• {date}嘅報告：已經返嚟，{doctor}未睇。如果需要你做啲咩，診所會聯絡你。"},
    "results_line_reviewed": {"en": "• Report of {date}: it's back and {doctor} has reviewed it. The clinic will contact you if you need to do anything.",
                              "zh": "• {date}嘅報告：已經返嚟，{doctor}已經睇過。如果需要你做啲咩，診所會聯絡你。"},
    "results_line_waiting": {"en": "• Test of {date}: not back yet.", "zh": "• {date}嘅檢驗：報告未返。"},
    "results_tail": {"en": "I can't tell you what a result means: ask the doctor at your next visit, or choose Talk to the clinic.",
                     "zh": "我唔可以解釋報告內容，請喺下次覆診問醫生，或者揀「搵職員」。"},

    # RT-01
    "test_prep": {"en": "From {doctor}:\n{text}", "zh": "{doctor}嘅指示：\n{text}"},

    # MD-01
    "repeat_pick": {"en": "Which medicine do you need again? These are the ones the clinic gave you:", "zh": "你想覆配邊隻藥？以下係診所開過俾你嘅藥："},
    "repeat_other": {"en": "Something else", "zh": "其他"},
    "repeat_ask": {"en": "Which medicine do you need again?", "zh": "你想覆配邊隻藥？"},
    "repeat_sent": {"en": "Your request for {med} is with {owner}. The doctor decides whether it can be repeated without a visit. You'll hear by {when}.\nIf you'll run out before then, call the clinic on {phone}.",
                    "zh": "你覆配{med}嘅要求已經交咗俾{owner}。醫生會決定唔使覆診可唔可以覆配，你會喺{when}之前收到回覆。\n如果喺嗰之前會食完，請打{phone}搵診所。"},

    # AV-01
    "plan_header": {"en": "Your plan from {doctor}, visit of {date} (approved by {doctor}):", "zh": "{doctor}俾你嘅計劃（{date}覆診，{doctor}已批核）："},
    "plan_pending": {"en": "{doctor} hasn't approved your written plan yet. I've asked; you'll get it by {when}.",
                     "zh": "{doctor}未批核你嘅書面計劃。我已經提醒，你會喺{when}之前收到。"},
    "plan_none": {"en": "I can't find a recent visit with a written plan. If you have a question about your plan, choose Talk to the clinic.",
                  "zh": "搵唔到最近有書面計劃嘅覆診紀錄。如有關於計劃嘅問題，請揀「搵職員」。"},
    "plan_tail": {"en": "Questions about your plan? Write them here and the doctor will reply in opening hours.",
                  "zh": "對計劃有疑問？可以喺度寫低，醫生會喺開放時間回覆。"},

    # AV-02
    "checkin": {"en": "Hello {first}, it's {days} days since your visit with {doctor}. How are you feeling?",
                "zh": "{first}你好，距離你見{doctor}已經{days}日。你感覺點呀？"},
    "btn_better": {"en": "Better", "zh": "好咗"},
    "btn_same": {"en": "About the same", "zh": "差唔多"},
    "btn_worse": {"en": "Worse", "zh": "差咗"},
    "checkin_better": {"en": "Good to hear. Thank you for telling us; the doctor will see your answer.",
                       "zh": "多謝你話俾我哋知，醫生會睇到你嘅回覆。"},
    "checkin_same": {"en": "Thank you. The doctor will see your answer. If you'd like to talk to the clinic, write your question here.",
                     "zh": "多謝。醫生會睇到你嘅回覆。如果想問診所，可以喺度寫低你嘅問題。"},
    "checkin_worse": {"en": "Thank you for telling us. I've passed this to {owner}. They will reply by {when}.\nIf it can't wait, call 999 or go to A&E.",
                      "zh": "多謝話俾我哋知。我已經轉交{owner}，佢會喺{when}之前回覆。\n如果唔可以等，請即打999或去急症室。"},

    # AH-01, AV-04
    "ask_document": {"en": "Please send a photo of the discharge letter or report here. I'll file it for {doctor} without reading it to you.",
                     "zh": "請喺度send出院紙或者報告嘅相，我會交俾{doctor}存檔，唔會同你解讀內容。"},
    "document_filed": {"en": "Thank you. I've filed it for {owner}. They will reply by {when}.",
                       "zh": "多謝。已經交咗俾{owner}存檔，佢會喺{when}之前回覆。"},

    # FD-06
    "receipt": {"en": "Your receipt for the visit of {date} is on its way. If you need a different document, choose Talk to the clinic.",
                "zh": "{date}覆診嘅收據會send俾你。如果需要其他文件，請揀「搵職員」。"},
    "no_receipt": {"en": "I can't find a recent visit to send a receipt for. I've asked the front desk; they will reply by {when}.",
                   "zh": "搵唔到最近嘅覆診紀錄，我已經請前台跟進，佢哋會喺{when}之前回覆。"},

    # tester mode: anyone not on the test list
    "demo_only": {"en": "This WhatsApp number is a test of an automated clinic care line. It isn't a medical service and can't help with your health. Please don't send personal or health information.\nFor an emergency, call 999 or go to A&E.",
                  "zh": "呢個WhatsApp號碼係自動診所護理熱線嘅測試，唔係醫療服務，幫唔到你處理健康問題。請唔好send個人或健康資料。\n如有緊急情況，請即打999或去急症室。"},
}

# HE-03 / HE-04: fixed replies. Sent word for word, before anything else, whatever the state of the conversation.
FIXED = {
    "red_flag": {
        "en": "This could be an emergency. Call 999 now or go to the nearest A&E. Don't wait for a reply here. We have told {doctor}.",
        "zh": "呢個可能係緊急情況。請即打999或去最近嘅急症室，唔好等呢度回覆。我哋已經通知{doctor}。",
    },
    "crisis": {
        "en": ("I'm sorry you're going through this. You don't have to face it alone. If you might act on these thoughts, call 999 now. "
               "You can talk to someone at any hour: The Samaritans 2896 0000 (24 hours, English and Cantonese) or "
               "The Samaritan Befrienders Hong Kong 2389 2222. We have told {doctor}."),
        "zh": ("聽到你咁辛苦，我好難過。你唔需要獨自面對。如果你可能會傷害自己，請即打999。"
               "你隨時都可以搵人傾：撒瑪利亞會 2896 0000（24小時，中英文）或 香港撒瑪利亞防止自殺會 2389 2222。我哋已經通知{doctor}。"),
    },
}

MENU = [  # landing page v3: 10 options, the most one WhatsApp list message holds; titles ≤ 24 characters
    ("book", "預約 / 改期", "Book or change"),
    ("info", "診所資料", "Hours and address"),
    ("staff", "搵職員", "Talk to the clinic"),
    ("results", "我嘅報告", "My results"),
    ("tests", "驗血 / 照X光", "Tests and scans"),
    ("repeat", "覆配藥物", "Repeat prescription"),
    ("plan", "我嘅計劃", "My plan"),
    ("hospital", "出院跟進", "After hospital"),
    ("receipts", "收據 / 文件", "Receipts and letters"),
    ("fees", "收費 / 醫療券", "Fees and vouchers"),
]


def say(key: str, lang: str, **kw) -> str:
    return T[key][lang].format(**kw)


def fixed(key: str, lang: str, **kw) -> str:
    return FIXED[key][lang].format(**kw)
