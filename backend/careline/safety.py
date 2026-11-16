"""Deterministic safety layer. Runs on every message, whatever model is plugged in, and the model can't turn it off.

Inbound: red flags (HE-03) and crisis messages (HE-04) are caught before anything else, and get a fixed reply
plus an alert. The lists over-trigger on purpose ("no chest pain" still matches): a false alarm costs a phone call,
a miss can cost a life. Clinical questions (NV-01..04, MD-02, AV-03) are spotted so they go to the doctor.

Outbound: `check_outbound` is the message-side half of the platform checks (spec 003 hard fails). Every message is
checked before it is sent; a failure blocks the send and is recorded, so a bad template or a drifting model shows
up in the evals instead of on a patient's phone.
"""
from __future__ import annotations

import re

RED_FLAGS = [
    # English
    r"chest (pain|tight|pressure|hurts?)", r"pain in (my|the) chest", r"heart attack",
    r"can'?t breathe", r"cannot breathe", r"(difficulty|trouble|hard to) breath", r"short(ness)? of breath", r"breathless",
    r"stroke", r"face (is )?(drooping|droop)", r"slurred speech", r"can'?t (speak|talk) properly", r"weak(ness)? (on|in) one side",
    r"numb(ness)? (on|in) one side", r"(passed|blacked) out", r"faint(ed|ing)", r"unconscious", r"unresponsive", r"seizure", r"\bfits?\b",
    r"vomit(ing|ed)? blood", r"cough(ing|ed)? (up )?blood", r"black (stool|poo)", r"blood in (my )?(stool|poo|urine)", r"bleeding (heavily|a lot|won'?t stop)",
    r"severe bleeding", r"(lips?|tongue|throat|face) (is |are )?swell", r"swollen (lips?|tongue|throat)", r"anaphyla", r"severe allerg",
    r"worst headache", r"sudden(ly)? (severe|bad) headache", r"stiff neck and (fever|rash)", r"overdose", r"took too many (pills|tablets)",
    r"(very|extremely) (drowsy|confused)", r"can'?t wake", r"blue lips", r"(potassium|sugar|glucose) (is )?(very )?(high|low).{0,20}(dizzy|faint|confus)",
    r"suicid", r"kill myself", r"end (my|it) (life|all)", r"want to die", r"self[- ]?harm", r"hurt myself",
    # Cantonese / Chinese
    r"胸口痛", r"心口痛", r"胸口翳", r"心口翳", r"胸痛", r"唞唔到氣", r"抖唔到氣", r"呼吸困難", r"透唔到氣", r"氣促",
    r"中風", r"口歪", r"講嘢唔清", r"一邊手腳無力", r"暈低", r"昏迷", r"叫唔醒", r"抽筋", r"吐血", r"咳血", r"屙血", r"黑色大便", r"大量出血", r"血流唔停",
    r"嘴唇腫", r"喉嚨腫", r"塊面腫", r"嚴重敏感", r"敏感好嚴重", r"食多咗藥", r"食咗好多粒",
    r"自殺", r"想死", r"唔想生存", r"唔想活", r"傷害自己", r"了結", r"輕生", r"跳樓",
]
CRISIS = [r"suicid", r"kill myself", r"end (my|it) (life|all)", r"want to die", r"self[- ]?harm", r"hurt myself",
          r"自殺", r"想死", r"唔想生存", r"唔想活", r"傷害自己", r"了結", r"輕生", r"跳樓"]

CLINICAL_Q = [
    r"should i (take|stop|start|increase|reduce|double|skip)", r"can i (take|stop|start|drink|eat|skip|double)", r"is (it|this|that) (normal|serious|bad|ok|okay|safe)",
    r"what does (my|the|this) (result|report|test|level|number)", r"(is|are) my (result|report|level|bp|blood pressure|sugar)s? (ok|okay|normal|fine|bad|high|low)",
    r"\bshould i\b", r"\b(normal|abnormal)\b", r"\blevels?\b", r"(lab|test|blood) values?", r"potassium|cholesterol|blood sugar|glucose|hba1c|egfr",
    r"\bdose\b", r"\bdosage\b", r"side[- ]?effect", r"antibiotic", r"sleeping pill", r"sedative", r"painkiller", r"(diagnos|what do i have|what is wrong with me)",
    r"rash", r"fever", r"cough", r"pain", r"dizzy", r"vomit", r"diarr", r"swollen", r"\bhurts?\b", r"\bsick\b", r"\bill\b",
    r"食唔食得", r"可唔可以食", r"停唔停得", r"使唔使食", r"加唔加藥", r"正唔正常", r"嚴唔嚴重", r"份量", r"劑量", r"副作用", r"抗生素", r"安眠藥", r"止痛藥",
    r"係咪有病", r"咩病", r"報告點解", r"報告係咪", r"發燒", r"咳", r"痛", r"頭暈", r"嘔", r"肚屙", r"敏感", r"出疹", r"腫",
]
SICK_LEAVE = [r"sick leave", r"medical certificate", r"\bMC\b", r"病假紙", r"病假"]
STOP = [r"^\s*stop\s*$", r"^\s*unsubscribe\s*$", r"唔好再send", r"唔好再傳", r"停止", r"^\s*取消訂閱\s*$"]


def _any(pats, text):
    return next((p for p in pats if re.search(p, text, re.I)), None)


def red_flag(text: str) -> str | None:
    """'crisis', 'red_flag' or None."""
    if _any(CRISIS, text):
        return "crisis"
    if _any(RED_FLAGS, text):
        return "red_flag"
    return None


def clinical_question(text: str) -> bool:
    return bool(_any(CLINICAL_Q, text))


def sick_leave(text: str) -> bool:
    return bool(_any(SICK_LEAVE, text))


def is_stop(text: str) -> bool:
    return bool(_any(STOP, text))


# ---- outbound checks: the message-side hard fails of spec 003 ------------------------------------------------
ADVICE = [r"\byou should (take|stop|start|increase|reduce)", r"\btake \d+ ?(mg|tablets?|pills?)", r"\bdouble (the|your) dose",
          r"\bit'?s (probably|likely|just) ", r"\bnothing to worry", r"\bdon'?t worry\b", r"\byou (have|probably have|might have) (a|an) ",
          r"\b(is|are|looks?) (normal|fine|ok|okay)\b", r"\bwithin (the )?normal", r"\bno need to (see|worry)",
          r"正常", r"唔使擔心", r"冇事嘅", r"應該係", r"你可以食\d", r"加大劑量"]
PROMOTION = [r"\d+ ?% off", r"\bdiscount", r"\bspecial offer", r"\bpromo", r"\bfree (gift|check)", r"\bcheaper than", r"\bbest clinic",
             r"優惠", r"折扣", r"免費贈", r"特價", r"最平"]
SENSITIVE = [r"\bHIV\b", r"\bsyphilis\b", r"\bchlamydia\b", r"\bgonorrh", r"\bhepatitis\b", r"\bpregnan", r"\bSTD\b", r"\bSTI\b",
             r"\bcancer\b", r"\bbiopsy\b", r"\bpsychiatr", r"\bdepression\b", r"愛滋", r"梅毒", r"性病", r"懷孕", r"驗孕", r"癌", r"抑鬱"]
REASSURE = [r"\ball clear\b", r"\bnothing (is )?outstanding\b", r"\bresolved\b", r"\bclosed (your|the) (item|request)"]
PERSON_CLAIM = [r"\bthis is dr\b", r"\bi am (dr|the doctor|a nurse|a person)", r"\bi'?m (dr|the doctor|a nurse|a real person)", r"我係醫生", r"我係真人"]


def check_outbound(text: str, *, label: str, approved: bool = False, linked: bool = True, sensitive_ok: bool = False,
                   consent: bool = True) -> list[str]:
    """Hard fails this message would commit. `approved`: the text is a doctor-approved reply (plan, prep), in which
    clinical words are the doctor's own. `linked`: the recipient is verified as the chart's owner."""
    fails = []
    body = text
    if not text.startswith(label):
        fails.append("impersonation")
    if _any(PERSON_CLAIM, body):
        fails.append("impersonation")
    if not approved and _any(ADVICE, body):
        fails.append("clinical_advice")
    if _any(PROMOTION, body):
        fails.append("promotion")
    if _any(SENSITIVE, body) and not (approved and sensitive_ok):
        fails.append("disclosure")
    if _any(REASSURE, body):
        fails.append("authority")
    if not consent:
        fails.append("no_consent")
    return sorted(set(fails))
