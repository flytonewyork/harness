"""Deterministic screens for the precare line. Every inbound message is screened before anything else, whatever
step the conversation is at and whatever model may later help read replies; nothing can turn this off.

Red flags over-trigger on purpose: "no chest pain" still matches. A false alarm costs a phone call from the
doctor; a missed one can cost a life. The lists are English, Cantonese and the code-switched mix HK patients write.

`check_outbound` runs on every message before it is sent; a failure blocks the send and is recorded.
"""
from __future__ import annotations

import difflib
import re

SCREEN_VERSION = "precare-screen-v5"
# v1 missed code-switched chest symptoms, "collapsed", "arm went weak" (LoopBench dev set, 12 Nov).
# v2 missed descriptions without keywords ("elephant sitting on my chest", "can't catch my breath"), typos
# ("chest pian", "metphormin"), a low sugar given as a number, a tight throat after a new drug, and indirect
# Cantonese crisis wording (LoopBench stress set, first run 36/126). v3 adds those, a number check for low sugar
# and fuzzy matching for medicine names.
# v3 missed all 7 red-flag phrasings of the first blind set (palpitations with near-fainting, thunderclap headache,
# Cantonese stiff neck, tarry stool, reduced fetal movements, a box of paracetamol, goodbye letters). v4 adds those
# families of wording.
# v4 then missed all 9 red-flag phrasings of blind set 2 (coffee-ground vomit, droopy face, left arm numb with
# sweating, a floppy toddler, rigors with confusion, an inhaler not helping, "a burden to my family", "cut myself",
# puffy lips). v5 adds them. Two blind sets say the same thing: word lists catch the phrasings they were written
# for and almost nothing new. The model screen (screens.py) has to be on before real patients use the line.

CRISIS = [r"suicid", r"kill (myself|me)", r"end (my|it) (life|all)", r"want to die", r"don'?t want to (live|be alive)",
          r"self[- ]?harm", r"hurt(ing)? myself", r"better off (if i (was|were) )?dead", r"better off without me",
          r"end(ing)? (my|it) (life|all)", r"no reason to live",
          r"(sleep|go to sleep) and (not|never) wake", r"don'?t want to wake up", r"not want to be here any ?more",
          r"(goodbye|farewell|suicide) (letter|note|message)", r"(written|wrote|writing) (letters|notes) to (my )?(family|kids|children|wife|husband|parents)",
          r"giv(en|ing|e) .{0,25}(things|stuff|belongings|possessions) away", r"giv(en|ing|e) away (most of |all of )?my", r"affairs in order",
          r"(whole|entire|full) (box|bottle|pack|packet|strip|tub) of", r"took (all|a lot|loads|lots|a handful) of (my )?(pills|tablets|medicine|panadol|paracetamol)",
          r"overdose", r"took too many (pills|tablets)", r"成(盒|樽|排|板)藥", r"食咗成(盒|樽|排|板)", r"遺書", r"寫(咗|好)信俾屋企人", r"將.{0,6}(嘢|東西)送(晒)?俾人",
          r"\bburden\b", r"better (off )?without me", r"負擔", r"冇咗我.{0,4}(會)?好(啲|過)", r"cut(ting)? myself", r"burn(ed|ing)? myself",
          r"𠝹(手|自己|親自己)", r"割(手|脈|自己)", r"鎅(手|自己)",
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
    r"heart (is )?(racing|pounding|beating (really |very |so )?(fast|hard))", r"palpitat", r"(going to|gonna|about to|might|feel like i.{0,6}) (pass out|faint|black out|collapse)",
    r"心跳(好快|得好快|好亂|亂)", r"心慌", r"就(嚟|快)暈", r"好似就嚟暈",
    r"thunder", r"never had (one|a headache|a pain) (this|that|so) bad", r"headache.{0,40}\b(sudden|suddenly|came on|worst)\b", r"頭痛.{0,10}(爆|突然|從來未)",
    r"neck (is |feels )?(stiff|rigid)", r"(條頸|頸).{0,4}(好硬|硬|僵)",
    r"black.{0,25}\b(tar|tarry|stool|poo|poop|motion)", r"\btarry\b", r"like tar", r"大便.{0,4}黑", r"屙.{0,3}黑",
    r"(baby|bb|fetus|foetus).{0,30}\b(not|hasn'?t|has not|stopped|less|isn'?t|no longer) (been )?(mov|kick)", r"(reduced|fewer|less|no) (fetal |foetal |baby )?movements?",
    r"bb.{0,6}(唔郁|冇郁|少咗郁)", r"胎動.{0,4}(少|冇)",
    r"vomit\w*.{0,30}(coffee|black|blood|bloody)", r"coffee.?grounds?", r"嘔.{0,6}(咖啡|黑色|血)",
    r"\bface\b.{0,20}(droop|lopsided|crooked|fallen)", r"dribbl", r"\b(left|right)\b (arm|hand|side).{0,20}\b(numb|tingl|weak|heavy)",
    r"(左|右)(手|邊).{0,4}(痹|麻|冇力)", r"手痹.{0,6}(膊頭|心口)", r"(心口|胸口|手).{0,20}出汗",
    r"(baby|toddler|infant|child|kid|son|daughter|\b\d+ ?(year|yr|month|mth)s?[- ]old).{0,50}(floppy|limp|not drinking|not feeding|very sleepy|hard to wake|won'?t wake|blue|grey)",
    r"(bb|小朋友|個仔|個女).{0,15}(冇反應|好眼瞓|唔肯飲|軟晒|叫極唔醒)",
    r"shiver\w* (uncontrollabl|non.?stop|violently)", r"\brigou?rs?\b", r"\b(feel|feeling|felt|seems?|very|really|so|getting|became|is|was) confused\b", r"\bconfusion\b", r"(fever|temp\w*|發燒|燒到).{0,10}\b(39\.[5-9]|4[0-2])", r"(震到停唔到|發冷震)",
    r"inhaler.{0,25}(not|isn'?t|doesn'?t|no longer|stopped) (help|work)", r"(only|can only) (say|speak|talk in) (a few|few|single) words", r"can'?t (finish|complete) (a |my )?sentences?",
    r"噴劑.{0,6}(冇用|唔work)", r"講唔到成句",
    r"\blips?\b.{0,20}(puff|swell|swollen|big|fat)", r"嘴唇.{0,4}(腫|脹)",
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
    r"(discuss|talk about|ask about|thinking (of|about)|want to|wanna|like to) (stopping|changing|reducing|coming off|starting|switching|stop|change|reduce|switch)",
    r"\bcome off (my|the)\b", r"想(停|減|轉|換)藥",
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
GLUCOSE_LOWERING = [r"metformin", r"insulin", r"gliclazide", r"glipizide", r"glimepiride", r"sitagliptin", r"linagliptin", r"vildagliptin",
                    r"empagliflozin", r"dapagliflozin", r"canagliflozin", r"pioglitazone", r"repaglinide",
                    r"diamicron", r"glucophage", r"amaryl", r"januvia", r"janumet", r"galvus", r"trajenta", r"jardiance", r"forxiga",
                    r"lantus", r"levemir", r"toujeo", r"tresiba", r"novorapid", r"humalog", r"apidra", r"mixtard", r"humulin", r"novomix",
                    r"打針.{0,4}(糖|血糖)", r"糖尿針", r"糖尿藥", r"降血糖", r"胰島素", r"二甲雙胍"]
ANTITHROMBOTIC = [r"warfarin", r"apixaban", r"rivaroxaban", r"dabigatran", r"edoxaban", r"clopidogrel", r"ticagrelor", r"prasugrel",
                  r"aspirin", r"eliquis", r"xarelto", r"plavix", r"pradaxa", r"lixiana", r"brilinta", r"coumadin", r"marevan", r"cartia",
                  r"blood thinner", r"保栓通", r"拜瑞妥", r"艾必克凝", r"倍林達", r"可邁丁", r"阿斯匹靈", r"薄血藥", r"華法林", r"阿士匹靈", r"薯仔藥"]

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
