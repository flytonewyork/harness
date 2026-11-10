"""Every word the line sends on its own, in English and Chinese (Cantonese where it's spoken).

Three kinds of message reach a patient, and each starts with a label that says which it is (AC-05, `impersonation`):
- fixed texts from this file, filled with facts (the doctor's name, hours, a time, a fee);
- doctor-approved texts: the plan, drafted from the doctor's own words and approved by the doctor;
- the doctor's own words, typed by the doctor, sent exactly as written.
Red-flag and crisis replies are spec 004's, word for word (`backend.careline.texts.FIXED`).
All of it is a proposal until Thomas approves the wording (`APPROVED_BY`).
"""
from __future__ import annotations

from backend.careline.texts import FIXED

TEXT_VERSION = "line-texts-v1"
APPROVED_BY = "PENDING: Thomas approves every text here before the first real patient"

T: dict[str, dict[str, str]] = {
    # labels: who is speaking
    "label": {"en": "{doctor}'s care line (automated, by tmrw)", "zh": "{doctor}護理熱線（自動回覆，由 tmrw 提供）"},
    "label_approved": {"en": "{doctor}'s care line (approved by {doctor})", "zh": "{doctor}護理熱線（經{doctor}批核）"},
    "label_doctor": {"en": "{doctor} (written by the doctor)", "zh": "{doctor}（醫生親自回覆）"},

    # someone writes without a doctor's code
    "no_code": {
        "en": ("Hello. This is the care line for doctors who use tmrw. To join, please scan the QR code your doctor "
               "gave you.\nIf this is an emergency, call 999 or go to A&E."),
        "zh": "你好，呢度係使用 tmrw 嘅醫生嘅護理熱線。如想加入，請掃描你嘅醫生俾你嘅二維碼。\n如有緊急情況，請即打999或去急症室。",
    },

    # join (AC-01)
    "welcome": {
        "en": ("Hello, you've reached {doctor}'s care line, an automated service by tmrw that works for {doctor}.\n\n"
               "If you join, {doctor} can send your plan here, check how you're going, and answer your questions. "
               "{doctor} reads messages {hours}. For an emergency, call 999 or go to A&E: don't wait for a reply here.\n\n"
               "How we keep your information: {privacy}\n\nWould you like to join?"),
        "zh": ("你好，呢度係{doctor}護理熱線，係 tmrw 為{doctor}提供嘅自動服務。\n\n"
               "加入後，{doctor}可以喺度send你嘅治療計劃、了解你嘅情況同回答你嘅問題。{doctor}嘅回覆時間：{hours}。"
               "如有緊急情況，請即打999或去急症室，唔好等呢度回覆。\n\n我哋點樣處理你嘅資料：{privacy}\n\n你想唔想加入？"),
    },
    "consent_wording": {
        "en": "Join {doctor}'s care line: {doctor} and tmrw's automated care line may message me about my care on WhatsApp, as the privacy notice describes.",
        "zh": "加入{doctor}護理熱線：{doctor}同 tmrw 嘅自動護理熱線可以按私隱聲明，用WhatsApp同我聯絡有關我嘅護理。",
    },
    "btn_join": {"en": "Yes, join", "zh": "好，加入"},
    "btn_no": {"en": "Not now", "zh": "暫時唔使"},
    "declined": {"en": "No problem. Nothing has been kept. You can scan the QR code again any time.",
                 "zh": "冇問題，我哋冇保存任何資料。你隨時可以再掃描二維碼。"},
    "ask_name": {"en": "Thank you. What name should {doctor} use for you? (Your full name, e.g. WONG Ka Yan)",
                 "zh": "多謝。{doctor}應該點稱呼你？（請寫全名，例如：黃家欣）"},
    "ask_dob": {"en": "And your date of birth? (e.g. 31/12/1980)", "zh": "你嘅出世日期係？（例如：31/12/1980）"},
    "bad_dob": {"en": "Sorry, I couldn't read that date. Please write it as day/month/year, e.g. 31/12/1980.",
                "zh": "唔好意思，睇唔明呢個日期。請用 日/月/年 嘅格式，例如 31/12/1980。"},
    "joined": {
        "en": ("Thank you, {first}. You've joined. {doctor} will send your plan here shortly.\n"
               "You can write here any time. {doctor} reads messages {hours}. If it can't wait, call 999 or go to A&E."),
        "zh": "多謝你，{first}，已經加入咗。{doctor}好快會喺度send你嘅計劃。\n你隨時可以喺度留言，{doctor}嘅回覆時間：{hours}。如果唔可以等，請即打999或去急症室。",
    },
    "stopped": {"en": "You won't get any more messages from {doctor}'s care line, and {doctor} has been told. To start again, write JOIN.",
                "zh": "你唔會再收到{doctor}護理熱線嘅訊息，我哋已經通知{doctor}。想重新開始，請寫 JOIN。"},

    "removed": {"en": "{doctor} couldn't match this number to a patient, so this care line can't be used from it. If that's a mistake, please tell the clinic at your next visit.\nFor an emergency, call 999 or go to A&E.",
                "zh": "{doctor}未能將呢個號碼對應到病人，所以呢個號碼用唔到護理熱線。如果有誤會，請喺下次覆診話俾診所知。\n如有緊急情況，請即打999或去急症室。"},
    "choose": {"en": "Please choose:", "zh": "請選擇："},

    # the plan (AV-01): headings around the doctor's approved items
    "plan_header": {"en": "Your plan from {doctor}, {date}:", "zh": "{doctor}俾你嘅計劃（{date}）："},
    "sec_take": {"en": "Medicines", "zh": "藥物"},
    "sec_watch": {"en": "Watch for", "zh": "要留意"},
    "sec_next": {"en": "Next steps", "zh": "下一步"},
    "sec_other": {"en": "Also", "zh": "其他"},
    "plan_tail": {"en": "Questions? Write them here. {doctor} reads messages {hours}. If it can't wait, call 999 or go to A&E.",
                  "zh": "有問題可以喺度寫低，{doctor}嘅回覆時間：{hours}。如果唔可以等，請即打999或去急症室。"},
    "plan_none": {"en": "{doctor} hasn't sent a written plan yet. You can write your question here.",
                  "zh": "{doctor}仲未send書面計劃。你可以喺度寫低你嘅問題。"},

    # menu
    "menu_body": {"en": "What do you need?", "zh": "你需要咩？"},
    "menu_button": {"en": "Menu", "zh": "選單"},
    "m_plan": {"en": "My plan", "zh": "我嘅計劃"},
    "m_ask": {"en": "Ask {short}", "zh": "問{short}"},
    "m_repeat": {"en": "Repeat medicine", "zh": "覆配藥物"},
    "m_result": {"en": "Result review", "zh": "睇報告"},
    "m_ask_d": {"en": "Write your question", "zh": "寫低你嘅問題"},
    "m_repeat_d": {"en": "From your plan; collect at the clinic", "zh": "計劃入面嘅藥；喺診所取藥"},
    "m_result_d": {"en": "Send a photo or PDF of your report", "zh": "send報告嘅相或者PDF"},
    "ask_prompt": {"en": "Write your question here and {doctor} will read it.", "zh": "請喺度寫低你嘅問題，{doctor}會睇。"},

    # questions (HE-02, NV-01..04): the line never answers them itself
    "ack_open": {"en": "Thank you. {doctor} will read this and reply by {when}.\nIf it can't wait, call 999 or go to A&E.",
                 "zh": "多謝。{doctor}會睇你嘅訊息，喺{when}之前回覆。\n如果唔可以等，請即打999或去急症室。"},
    "ack_closed": {"en": "Thank you. {doctor} isn't reading messages right now, and will reply by {when}.\nIf it can't wait, call 999 or go to A&E.",
                   "zh": "多謝。{doctor}而家唔係回覆時間，會喺{when}之前回覆。\n如果唔可以等，請即打999或去急症室。"},
    "ack_paused": {"en": "Thank you. {doctor} is away until {until} and will read this then. If it can't wait, call 999 or go to A&E, or see another doctor.",
                   "zh": "多謝。{doctor}暫時離開，{until}返嚟後會睇你嘅訊息。如果唔可以等，請即打999、去急症室或者睇其他醫生。"},
    "thanks": {"en": "You're welcome.", "zh": "唔使客氣。"},
    "not_linked_yet": {"en": "{doctor} hasn't confirmed your record on the line yet, so nothing from it can be shared here. {doctor} will see your message.",
                       "zh": "{doctor}未確認你喺熱線上嘅紀錄，所以暫時唔可以喺度分享任何紀錄內容。{doctor}會睇到你嘅訊息。"},

    # check-in (AV-02)
    "checkin": {"en": "Hello {first}, it's {days} days since your visit with {doctor}. How are you feeling?",
                "zh": "{first}你好，距離你見{doctor}已經{days}日。你感覺點呀？"},
    "btn_better": {"en": "Better", "zh": "好咗"},
    "btn_same": {"en": "About the same", "zh": "差唔多"},
    "btn_worse": {"en": "Worse", "zh": "差咗"},
    "checkin_better": {"en": "Good to hear. {doctor} will see your answer.", "zh": "好高興聽到。{doctor}會睇到你嘅回覆。"},
    "checkin_same": {"en": "Thank you. {doctor} will see your answer. If you have a question, write it here.",
                     "zh": "多謝。{doctor}會睇到你嘅回覆。如果有問題，可以喺度寫低。"},
    "checkin_worse": {"en": "Thank you for telling us. {doctor} will read this and reply by {when}. Tell us what's worse if you can.\nIf it can't wait, call 999 or go to A&E.",
                      "zh": "多謝話俾我哋知。{doctor}會喺{when}之前回覆。如果可以，請講低邊方面差咗。\n如果唔可以等，請即打999或去急症室。"},
    "reminder": {"en": "A reminder from your plan: {text}", "zh": "計劃提提你：{text}"},
    "reminder_generic": {"en": "A reminder: a next step in your plan from {doctor} is due. Choose My plan from the menu to see it.",
                         "zh": "提提你：{doctor}俾你嘅計劃有一項下一步到期。請喺選單揀「我嘅計劃」睇返。"},

    # repeats (MD-01), paid
    "repeat_pick": {"en": "Which medicine do you need again? These are from your plan:", "zh": "你想覆配邊隻藥？以下係你計劃入面嘅藥："},
    "repeat_none": {"en": "There are no medicines in your plan to repeat here. You can write to {doctor} instead.",
                    "zh": "你嘅計劃入面冇可以喺度覆配嘅藥。你可以直接留言俾{doctor}。"},
    "repeat_sent": {"en": "Your request for {med} is with {doctor}, who decides whether it can be repeated without a visit. You'll hear by {when}. Nothing is charged unless {doctor} approves.",
                    "zh": "你覆配{med}嘅要求已經交咗俾{doctor}。醫生會決定唔使覆診可唔可以覆配，你會喺{when}之前收到回覆。醫生批准先會收費。"},
    "repeat_approved": {"en": "{doctor} approved your repeat of {med}.\n{fees}\nPay here to confirm: {url}\nThen choose a time to collect it at {clinic}.",
                        "zh": "{doctor}批准咗覆配{med}。\n{fees}\n請喺呢度付款確認：{url}\n之後揀時間去{clinic}取藥。"},
    "repeat_free": {"en": "{doctor} approved your repeat of {med}. Choose a time to collect it at {clinic}.",
                    "zh": "{doctor}批准咗覆配{med}。請揀時間去{clinic}取藥。"},
    "repeat_visit": {"en": "{doctor} would like to see you before repeating {med}. Please book a visit at {clinic}: {phone}. Nothing has been charged.",
                     "zh": "{doctor}想先見你，再決定係咪覆配{med}。請預約{clinic}：{phone}。冇收任何費用。"},
    "repeat_declined": {"en": "{doctor} can't repeat {med} through the line. {doctor} will tell you more, or you can book a visit at {clinic}: {phone}. Nothing has been charged.",
                        "zh": "{doctor}未能經熱線覆配{med}。醫生會再同你講，或者你可以預約{clinic}：{phone}。冇收任何費用。"},
    "pickup_pick": {"en": "Paid, thank you. When will you collect {med} at {clinic}?", "zh": "已收款，多謝。你幾時去{clinic}取{med}？"},
    "pickup_pick_free": {"en": "When will you collect {med} at {clinic}?", "zh": "你幾時去{clinic}取{med}？"},
    "repeat_sent_member": {"en": "Your request for {med} is with {doctor}, who decides whether it can be repeated without a visit. You'll hear by {when}. It's included in your membership.",
                           "zh": "你覆配{med}嘅要求已經交咗俾{doctor}。醫生會決定唔使覆診可唔可以覆配，你會喺{when}之前收到回覆。呢項已包括喺你嘅會籍入面。"},
    "pickup_set": {"en": "Done: collect {med} at {clinic}, {address}, {when}. Bring your HKID.",
                   "zh": "已安排：{when}去{clinic}（{address}）取{med}。請帶身份證。"},

    # result reviews, paid
    "result_offer": {"en": "Thank you, I've kept your report for {doctor}.\n{doctor} reviews results here: {fees}\nShall I send it to {doctor} for review?",
                     "zh": "多謝，我已經為{doctor}保存咗你嘅報告。\n{doctor}喺度睇報告嘅收費：{fees}\n要唔要交俾{doctor}睇？"},
    "result_ask": {"en": "Please send a photo or PDF of your report here.", "zh": "請喺度send報告嘅相或者PDF。"},
    "btn_review": {"en": "Yes, review it", "zh": "好，請醫生睇"},
    "btn_not_now": {"en": "Not now", "zh": "暫時唔使"},
    "result_pay": {"en": "Pay here and {doctor} will review it: {url}", "zh": "請喺呢度付款，{doctor}就會睇你嘅報告：{url}"},
    "result_paid": {"en": "Paid, thank you. {doctor} will review your report and reply by {when}.\nIf you feel unwell and it can't wait, call 999 or go to A&E.",
                    "zh": "已收款，多謝。{doctor}會喺{when}之前睇報告同回覆。\n如果唔舒服又唔可以等，請即打999或去急症室。"},
    "result_kept": {"en": "OK. I've kept it for {doctor}'s records without a review.", "zh": "好，我已經為{doctor}存檔，暫時唔會睇報告。"},
    "fees_line": {"en": "{doctor}'s fee HK${doctor_fee} + tmrw service fee HK${service_fee} = HK${total}",
                  "zh": "{doctor}診金 HK${doctor_fee} + tmrw 服務費 HK${service_fee} = HK${total}"},

    # the patient membership (offered one to one by the doctor, at most once)
    "membership_offer": {
        "en": ("{doctor} has invited you to a monthly care membership: {fees} a month. Cancel any time.\n"
               "It includes repeat prescriptions and result reviews here, with no per-item charge, on top of everything "
               "the line already does.\nJoin here: {url}"),
        "zh": ("{doctor}邀請你加入每月護理會籍：{fees}，按月收費，可隨時取消。\n"
               "會籍包括喺度覆配藥物同睇報告，唔使逐次收費，另外照用熱線所有服務。\n加入：{url}"),
    },
    "btn_no_thanks": {"en": "No thanks", "zh": "唔使喇"},
    "membership_declined": {"en": "No problem. Nothing changes; the line works as before.", "zh": "冇問題，熱線照常運作。"},
    "membership_joined": {"en": "Welcome to {doctor}'s care membership. Repeats and result reviews here are now included. To stop it, write CANCEL MEMBERSHIP.",
                          "zh": "歡迎加入{doctor}護理會籍。喺度覆配藥物同睇報告已經包括喺會籍入面。如想取消，請寫 CANCEL MEMBERSHIP。"},
    "membership_cancelled": {"en": "Your membership is cancelled; you won't be charged again. The line keeps working as before.",
                             "zh": "你嘅會籍已經取消，唔會再收費。熱線照常運作。"},
    "member_repeat": {"en": "{doctor} approved your repeat of {med}. It's included in your membership. Choose a time to collect it at {clinic}.",
                      "zh": "{doctor}批准咗覆配{med}，已包括喺你嘅會籍入面。請揀時間去{clinic}取藥。"},

    # waiting outside WhatsApp's 24-hour window
    "outbox_flushed": {"en": "Here's what {doctor}'s care line has for you:", "zh": "{doctor}護理熱線有以下訊息俾你："},
}

# the doctor's side: short, practical, always marked as the line speaking
D: dict[str, str] = {
    "prefix": "tmrw ·",
    "welcome": ("Welcome to your care line, {name}. Patients join by scanning your QR code: {qr}\n"
                "Your web page (queue, patients, settings, earnings): send WEB any time for a sign-in link.\n"
                "{stripe}Commands: QUEUE, WEB, QR, PAUSE <days>, RESUME, HELP."),
    "stripe_todo": "To get paid for repeats and result reviews, set up payouts (about 10 minutes): {url}\n",
    "stripe_ok": "",
    "help": ("After a consult: reply to the 'joined' message with a voice note (or text) and I'll draft the plan.\n"
             "Every draft comes back to you with Send / Edit / Skip; nothing clinical reaches a patient until you tap Send.\n"
             "Commands: QUEUE (open items), WEB (sign-in link), QR (your QR card), PAUSE <days>, RESUME."),
    "joined": "{name} (b. {year}) just joined your line{where}. Reply to this message with a voice note or text for their plan.",
    "back": "{name} (b. {year}) is back{where}. Reply to this message with a voice note or text for today's plan.",
    "drafting": "Got it. Drafting…",
    "will_send": "After you tap Send: check-in on day {days}{reminders}.",
    "edited_note": "You edited the plan, so its reminders were dropped (the check-in stays).",
    "message_for": "Message to {name}, exactly as you wrote it",
    "plan_for": "Draft plan for {name}",
    "heard": "Heard: “{transcript}”",
    "dropped": "Left out, because it wasn't in what you said: {items}",
    "numbers": "Check these numbers, they weren't in what you said: {items}",
    "send_q": "Send this plan to {name}?",
    "btn_send": "Send",
    "btn_edit": "Edit",
    "btn_skip": "Skip",
    "edit_prompt": "Send the plan as you want it, as text. It goes out exactly as you write it, after you tap Send.",
    "sent": "Sent to {name}. Check-in on day {days}{reminders}.",
    "skipped": "Skipped. {name} keeps the line without a written plan.",
    "who_for": "Which patient is this for?",
    "no_patients": "No patients have joined yet. Show your QR code at the end of a consult: {qr}",
    "question": "{name} asks{flag}: “{quote}”\nReply by {when}.",
    "draft_reply": "Draft reply (from their plan):\n{draft}",
    "btn_send_draft": "Send draft",
    "btn_write": "I'll write",
    "btn_call": "I'll call",
    "write_prompt": "Your next message (text) goes to {name} exactly as you write it. Or send a voice note and I'll draft it for you to approve.",
    "call_noted": "Noted: you'll call {name}. Their number is in your web page (send WEB). The item stays open until you tap Done there.",
    "reply_sent": "Sent to {name}.",
    "repeat": "{name} asks for a repeat: “{med}”.\nYour fee: HK${fee} (+ HK${service_fee} tmrw fee to the patient).",
    "btn_approve": "Approve",
    "btn_visit": "Needs a visit",
    "btn_decline": "Decline",
    "repeat_done": "Done: {name} {what}.",
    "result": "{name} {how}. See it on your web page: {url}\nReply to this with a voice note or text explaining it; I'll draft it for you to approve.",
    "red_flag": "⚠ {name} wrote something that may be an emergency: “{quote}”\nThey were told to call 999 or go to A&E. Please call them: {phone}",
    "checkin_worse": "{name} answered the day-{days} check-in: Worse.{quote}\nReply by {when}.",
    "pickup": "{name} will collect {med} at {clinic}, {when}. (Paid HK${fee}.)",
    "paid": "{name} paid HK${total} for a {what}.",
    "overdue": "Overdue: {n} item(s) passed their reply-by time. Send QUEUE to see them.",
    "queue_empty": "Nothing waiting. 👍",
    "queue": "Waiting for you:\n{rows}",
    "web": "Your sign-in link (valid 15 minutes, one use): {url}",
    "qr": "Your QR card to print or show: {url}",
    "paused": "Paused until {until}. Patients are told you're away and to call 999 or see another doctor if it can't wait.",
    "resumed": "You're back on. Patients will get your usual hours again.",
    "not_doctor_cmd": "I didn't catch that. Send HELP for what I can do.",
    "model_failed": "Sorry, I couldn't draft that ({why}). Send it again, or type the plan as text.",
    "btn_offer": "Offer membership",
    "membership_off": "Set a monthly membership price on your web page first (send WEB).",
    "membership_already": "{name} {state}.",
    "membership_offered": "Offered {name} your care membership ({fees} a month). You'll hear if they join.",
    "membership_joined": "{name} joined your care membership: HK${fee} a month to you.",
    "membership_ended": "{name}'s care membership has ended.",
}

MENU_KEYS = ("plan", "ask", "repeat", "result")


def say(key: str, lang: str, **kw) -> str:
    return T[key][lang].format(**kw)


def fixed(key: str, lang: str, **kw) -> str:
    return FIXED[key][lang].format(**kw)


def dsay(key: str, **kw) -> str:
    return D[key].format(**kw)
