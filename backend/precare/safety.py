"""Deterministic screens for the precare line. Every inbound message is screened before anything else, whatever
step the conversation is at and whatever model may later help read replies; nothing can turn this off.

Red flags over-trigger on purpose: "no chest pain" still matches. A false alarm costs a phone call from the
doctor; a missed one can cost a life. The lists are English, Cantonese and the code-switched mix HK patients write.

`check_outbound` runs on every message before it is sent; a failure blocks the send and is recorded.
"""
from __future__ import annotations

import difflib
import re

SCREEN_VERSION = "precare-screen-v3"
# v1 missed code-switched chest symptoms, "collapsed", "arm went weak" (LoopBench dev set, 12 Nov).
# v2 missed descriptions without keywords ("elephant sitting on my chest", "can't catch my breath"), typos
# ("chest pian", "metphormin"), a low sugar given as a number, a tight throat after a new drug, and indirect
# Cantonese crisis wording (LoopBench stress set, first run 36/126). v3 adds those, a number check for low sugar
# and fuzzy matching for medicine names.

CRISIS = [r"suicid", r"kill (myself|me)", r"end (my|it) (life|all)", r"want to die", r"don'?t want to (live|be alive)",
          r"self[- ]?harm", r"hurt(ing)? myself", r"better off (if i (was|were) )?dead", r"better off without me",
          r"end(ing)? (my|it) (life|all)", r"no reason to live",
          r"(sleep|go to sleep) and (not|never) wake", r"don'?t want to wake up", r"not want to be here any ?more",
          r"瞓咗唔再醒", r"唔想再醒", r"活落去冇意思", r"冇意思再活", r"死咗(仲|會)好",
          r"自殺", r"想死", r"唔想生存", r"唔想活", r"唔想再活", r"傷害自己", r"了結", r"輕生", r"跳樓", r"𠝹手"]

RED_FLAGS = [
    r"chest (pain|tight|pressure|hurts?|discomfort)", r"pain in (my|the) chest", r"heart attack", r"tight(ness)? (in|of) (my|the) chest",
    r"can'?t breathe", r"cannot breathe", r"(difficulty|trouble|hard to) breath", r"short(ness)? of breath", r"breathless", r"gasping",
    r"(chest|心口|胸口).{0,15}(tight|heavy|pressure|pain|hurt|squeez|crush|壓|翳|焗|悶|痛)", r"cold sweat", r"出冷汗",
    r"(elephant|weight|heavy|heaviness|pressure|squeez\w*|crush\w*|band)\b.{0,30}\bchest", r"\bchest\b.{0,40}\b(left arm|jaw|arm)\b",
    r"\bch(e|a)?st\s*(pain|pian|pane|pn|tite|tight)", r"\bchst\b",
    r"(can'?t|cannot|couldn'?t|struggl\w*|hard to|unable to) (to )?(catch|get) (my )?(breath|air)", r"hard to breathe", r"gasp",
    r"條氣唔順", r"唞氣.{0,4}辛苦", r"唞唔切氣", r"唞唔到",
    r"\b(throat|tongue)\b.{0,20}\b(tight|closing|thick|swell\w*)", r"喉嚨.{0,6}(緊|腫|塞)", r"low (blood )?sugar.{0,30}(shak|sweat|confus|faint|dizzy)",
    r"\bhypo\b", r"低血糖", r"血糖.{0,6}(好低|跌)",
    r"collaps", r"not respond", r"won'?t wake", r"(can'?t|couldn'?t|cannot) (lift|move|feel) (my|his|her) (arm|leg|hand|face)",
    r"\b(arm|leg|hand|face|side)\b.{0,25}\b(went|gone|go|goes|is|was|feels?|felt|became) (weak|numb|limp|dead|heavy)",
    r"speech (was |is |went |became )?(slurred|garbled|strange)", r"slurr",
    r"stroke", r"face (is )?droop", r"slurred speech", r"can'?t (speak|talk) properly", r"weak(ness)? (on|in|of) one side",
    r"numb(ness)? (on|in|of) one side", r"(passed|blacked) out", r"faint(ed|ing)", r"unconscious", r"unresponsive", r"seizure", r"\bfits?\b",
    r"vomit(ing|ed)? blood", r"cough(ing|ed)? (up )?blood", r"black (stool|poo)", r"blood in (my )?(stool|poo|urine|vomit)",
    r"bleeding (heavily|a lot|won'?t stop)", r"severe bleeding", r"(lips?|tongue|throat|face) (is |are )?swell", r"swollen (lips?|tongue|throat)",
    r"anaphyla", r"severe allerg", r"worst headache", r"sudden(ly)? (a )?(severe|bad|terrible) headache", r"thunderclap",
    r"stiff neck", r"overdose", r"took too many (pills|tablets)", r"(very|extremely) (drowsy|confused)", r"can'?t wake", r"blue lips",
    r"severe (abdominal|stomach|belly) pain", r"pregnan\w* .{0,30}bleed", r"bleed\w* .{0,30}pregnan",
    r"胸口痛", r"心口痛", r"胸口翳", r"心口翳", r"胸痛", r"胸悶", r"唞唔到氣", r"抖唔到氣", r"呼吸困難", r"透唔到氣", r"氣促", r"喘唔到氣",
    r"中風", r"口歪", r"講嘢唔清", r"一邊手腳無力", r"半邊身", r"暈低", r"暈倒", r"昏迷", r"叫唔醒", r"抽筋", r"吐血", r"咳血", r"屙血",
    r"黑色大便", r"大量出血", r"血流唔停", r"嘴唇腫", r"喉嚨腫", r"塊面腫", r"嚴重敏感", r"敏感好嚴重", r"食多咗藥", r"食咗好多粒",
    r"頭痛到爆", r"最痛嘅頭痛", r"突然好劇烈頭痛", r"肚痛到", r"劇痛",
]

CLINICAL_Q = [
    r"should i\b", r"can i (take|stop|start|drink|eat|skip|double|have|keep)", r"do i (need|have) to (take|stop|skip|fast)",
    r"is (it|this|that|my \w+) (normal|serious|bad|ok|okay|safe|fine)", r"(are|is) my (results?|reports?|levels?|tests?|bp|blood pressure|sugar|cholesterol)",
    r"what does (my|the|this) (result|report|test|level)", r"\b(results?|reports?)\b.{0,40}\b(normal|fine|ok|okay|good|bad)\b",
    r"\bdose\b", r"\bdosage\b", r"side[- ]?effects?", r"what do i have", r"what is wrong with me", r"\bdiagnos",
    r"使唔使", r"食唔食得", r"可唔可以(食|停|飲|唔食)", r"停唔停得", r"要唔要(停|食|空肚)", r"正唔正常", r"嚴唔嚴重", r"份量", r"劑量",
    r"副作用", r"報告.{0,10}(點|係咪|正常|好唔好)", r"係咪有病", r"咩病",
]

# a message that tries to make the line say or do something it cannot (prompt injection, "tell me it's fine"):
# passed to the doctor like a question, never obeyed
INSTRUCTION = [r"ignore (your|all|the|any|previous)", r"^\s*(system|assistant|developer)\s*:", r"you are (now )?(a|the|my) doctor",
               r"\b(tell|reply|say|confirm)\b.{0,20}\b(that )?(my|everything|it|i'?m|i am)\b.{0,30}\b(normal|fine|ok(ay)?|nothing|no need)",
               r"no need to come", r"唔理(你|之前)", r"話俾我知.{0,10}(正常|冇事)"]

# medicines the doctor must decide about before a visit: the agent never tells the patient what to do with them
GLUCOSE_LOWERING = [r"metformin", r"insulin", r"gliclazide", r"glipizide", r"sitagliptin", r"empagliflozin", r"dapagliflozin",
                    r"diamicron", r"glucophage", r"糖尿藥", r"降血糖", r"胰島素", r"二甲雙胍"]
ANTITHROMBOTIC = [r"warfarin", r"apixaban", r"rivaroxaban", r"dabigatran", r"edoxaban", r"clopidogrel", r"ticagrelor", r"aspirin",
                  r"eliquis", r"xarelto", r"plavix", r"blood thinner", r"薄血藥", r"華法林", r"阿士匹靈", r"薯仔藥"]

STOP = [r"^\s*stop\W*$", r"^\s*unsubscribe\W*$", r"^\s*stop (please|it|now|messaging|texting|this)\b", r"\bplease stop (messag|text|send|contact)",
        r"\bstop (messag|text|send|contact)\w* me", r"\bdon'?t (message|text|contact) me", r"^\s*唔好再(send|傳|搵|發)", r"^\s*停止\s*$", r"唔好再send", r"唔好再傳", r"唔好再搵我"]


def _first(pats, text):
    return next((p for p in pats if re.search(p, text, re.I)), None)


LOW_SUGAR = re.compile(r"(sugar|glucose|血糖|hypo|reading|度數)\D{0,25}?(\d{1,2}(?:\.\d)?)\b", re.I)


def low_sugar(text: str) -> bool:
    """A blood sugar under 4.0 mmol/L given as a number ("sugar dropped to 2.8")."""
    for m in LOW_SUGAR.finditer(text):
        v = float(m.group(2))
        if 0 < v < 4.0:
            return True
    return False


def screen(text: str) -> str | None:
    """'crisis', 'red_flag' or None. Runs on every inbound message."""
    if _first(CRISIS, text):
        return "crisis"
    if _first(RED_FLAGS, text) or low_sugar(text):
        return "red_flag"
    return None


def clinical_question(text: str) -> bool:
    return bool(_first(CLINICAL_Q, text) or _first(INSTRUCTION, text))


def mentions(pats, text: str) -> str | None:
    """The first medicine named, by pattern, or by a close spelling ("metphormin") of a plain name in the list."""
    m = next((re.search(p, text, re.I) for p in pats if re.search(p, text, re.I)), None)
    if m:
        return m.group(0)
    names = [p for p in pats if re.fullmatch(r"[a-z]+", p)]
    for w in re.findall(r"[a-z]{6,}", text.lower()):
        close = difflib.get_close_matches(w, names, n=1, cutoff=0.8)
        if close:
            return w
    return None


def is_stop(text: str) -> bool:
    return bool(_first(STOP, text))


# ---- outbound: hard fails a message would commit ----------------------------------------------------------------
ADVICE = [r"\byou should (take|stop|start|increase|reduce|skip)", r"\b(skip|stop|hold) (your|the) (metformin|insulin|warfarin|medicine|tablets?)",
          r"\btake \d+ ?(mg|tablets?|pills?)", r"\bdouble (the|your) dose", r"\bit'?s (probably|likely|just|only) ",
          r"\bnothing to worry", r"\bdon'?t worry\b", r"\byou (have|probably have|might have) (a|an) ", r"\b(is|are|looks?) (normal|fine|ok|okay)\b",
          r"\bno need to (see|worry|go)", r"\bnot (serious|urgent)\b",
          r"正常", r"唔使擔心", r"冇事", r"應該係", r"唔嚴重", r"唔使去"]
REASSURE = [r"\ball clear\b", r"\bnothing (is )?outstanding\b", r"\bresolved\b", r"\bclosed (your|the) (item|request)"]
PERSON_CLAIM = [r"\bthis is dr\b", r"\bi am (dr|the doctor|a nurse|a person|human)", r"\bi'?m (dr|the doctor|a nurse|a real person|human)",
                r"我係醫生", r"我係真人"]


def check_outbound(text: str, label: str) -> list[str]:
    fails = []
    if not text.startswith(label):
        fails.append("impersonation")
    body = text[len(label):] if text.startswith(label) else text
    if _first(PERSON_CLAIM, body):
        fails.append("impersonation")
    if _first(ADVICE, body):
        fails.append("clinical_advice")
    if _first(REASSURE, body):
        fails.append("authority")
    return sorted(set(fails))
