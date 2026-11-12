"""Reading a patient's message about their medicines.

The reader turns one message into findings: {kind, label, urgency, quote, classes}. `kind` is one of
    red_flag     a symptom that may be a serious reaction (urgency 3-4)
    side_effect  a symptom that may be a side effect (urgency 1-3, raised by the medicine's class)
    crisis       thoughts of self-harm (urgency 4)
    confusion    unsure what to take, taking old and new together, wrong dose taken
    adherence    stopped or skipping a medicine on their own
    dose_question asks to change, stop, double or skip a dose
    repeat       asks for more of a medicine, or has run out
    question     any other question for the doctor
Every quote is a verbatim slice of the message, so the platform's word-for-word check holds.

RulesReader is the floor: a bilingual (Cantonese / English) lexicon with negation, third-party and history
handling. ModelReader wraps any model (`run(text, context) -> [finding dicts]`); its findings are kept only if
their quote is verbatim in the message, it can add findings or raise their urgency, and it can never remove a
floor finding or lower its urgency. Anything the model gets wrong falls back to the floor and is counted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import formulary as fx


@dataclass(frozen=True)
class Term:
    label: str
    kind: str
    urgency: int
    pattern: str
    bump: dict = field(default_factory=dict)    # class -> urgency when the patient takes that class


ANY_BP = ("acei", "arb", "ccb", "beta_blocker", "diuretic")
HYPO = ("sulfonylurea", "insulin")
BLEED = ("anticoagulant", "antiplatelet", "nsaid")

TERMS: list[Term] = [
    # ---- emergencies: urgency 4 whatever the medicine
    Term("swelling of lips, tongue, face or throat", "red_flag", 4,
         r"(lips?|tongue|throat|face|eyes?)\s+((is|are|got|getting|went|gone|going|has|have|been) ){0,2}(very |really |a bit |all )?(swollen|swelling|puffy|bigger|fat|thick|swell(ing)? up|blown up|ballooned)|(hard|difficult|difficulty|trouble|can'?t|cannot) (to )?swallow|吞(嘢|野)(好|有啲)?(難|困難|唔落)|喉(嚨)?.{0,4}(收窄|好緊|腫)|swollen (lips?|tongue|face|throat)|swelling (of|in) (my )?(lips?|tongue|face|throat)"
         r"|(嘴唇|脷|舌頭|喉嚨|塊面|(?<!塊)面|(?<!腳)眼)(好|有啲|有D|開始)?腫"),
    Term("trouble breathing", "red_flag", 4,
         r"(can'?t|cannot|hard to|difficult(y)? to|struggling to|trouble) breath|(can'?t|cannot|couldn'?t) catch (my |his |her )?breath|short(ness)? of breath|breathless|wheez"
         r"|唞唔到氣|抖唔到氣|呼吸困難|氣促|喘"),
    Term("chest pain", "red_flag", 4, r"chest (pain|tight|hurt)|pain in (my |his |her |the )?chest|chest (is |feels |has been )?(hurting|painful|heavy|tight|crushing)|心口(好)?(痛|翳|悶)|胸口(好)?(痛|翳|悶)"),
    Term("fainted or collapsed", "red_flag", 4, r"faint(ed)?|passed out|collapsed?|black(ed)? out|暈(低|咗|倒)|昏迷"),
    Term("vomiting blood or black stools", "red_flag", 4,
         r"vomit(ing|ed)? blood|(threw|throw(ing)?|brought|bringing) up blood|(cough(ed|ing)?|spit(ting)?|spat) (up )?(some |a bit of |a little |bits of )?blood|咳(出|咗)?(啲)?血|like tar|tar-?like|blood in (my )?vomit|black (stool|poo|poop)s?|(stool|poo|poop)s? (is|are|was|were|has been|have been|looks?|looking) (very |really |all )?black|tarry|嘔血|黑色(嘅)?大便|大便(好)?黑|屙黑"),
    Term("blistering or peeling skin, mouth sores", "red_flag", 4,
         r"blister|peeling|起(晒|哂)?(水)?泡|skin (is )?coming off|mouth (ulcer|sore)s? .*rash|rash .*mouth (ulcer|sore)|起水泡|甩(晒|哂)?皮|口(爛|腔潰瘍).{0,8}(疹|紅)"),
    Term("very drowsy or confused", "red_flag", 4,
         r"(can'?t|cannot) wake|very (drowsy|sleepy|confused)|not making sense|叫唔醒|神志不清|胡言亂語|slurr|face (is )?droop|can'?t (move|lift) (my|his|her) (arm|leg)|口齒不清|講嘢(唔清楚|含糊)|半邊(身|面)"),
    Term("seizure", "red_flag", 4, r"seizure|\bfit(ting)?\b(?! ?(test|check|well|better))|發羊吊|抽搐"),
    Term("took far too many", "red_flag", 4, r"took ([5-9]|\d\d|five|six|seven|eight|nine|ten|several|lots|loads) (of them|of my|tablets|pills|doses)|(whole|entire|all the|all of the) (box|pack|bottle|strip|tablets|pills)|成(盒|排|樽)(藥)?(都)?食(晒|咗)"),
    Term("thoughts of self-harm", "crisis", 4,
         r"kill myself|(easier|better( off)?) (if|when) i (was|were|wasn'?t|weren'?t|am not)( not)? (here|around|alive)|better off without me|(don'?t|do not|no longer|can'?t) see (the|any) point|what'?s the point( anymore| of (living|anything))|take (them|all of them|all my (pills|tablets|meds)) (all )?at once|no point (in )?(living|going on)|wish i (was|were) dead|not (want to )?wake up|end (my|it all)|suicid|want to die|don'?t want to (live|be here)|hurt myself|self[- ]harm|唔想(再)?生存|想死|自殺|一次過(食|吞)|(撐|頂)唔落去|唔想再(撐|頂)|活(落去)?冇意思|傷害自己|唔想活"),
    # ---- urgent: same day
    Term("rash", "side_effect", 2, r"\brash|hives|(itchy|red) (skin|spots|bumps|patches|blotches)|spots all over|出(紅)?疹|紅疹|風疹|痕癢|好痕|身痕|紅點|紅斑",
         bump={"xanthine_oxidase": 3, "antibiotic": 3, "sulfonylurea": 2}),
    Term("bleeding or bruising", "side_effect", 2,
         r"bleed|bruis|blood in (the )?(toilet|stool|poo|urine|pee|wee|bowl)|bleeding from (my )?(bottom|back passage)|屙血|便血|nose ?bleed|blood in (my )?(urine|pee|stool)|gums? bleed|流血|瘀|血尿|流鼻血|牙肉出血",
         bump={c: 3 for c in BLEED}),
    Term("low sugar symptoms", "side_effect", 2,
         r"\bhypo\b|shak(y|ing)|trembl|cold sweat|sweating|sugar (was |is |of )?(2|3)\.\d|\b(bg|glucose|sugar|blood sugar|血糖)\s*(was |is |of |reading |得)?(1|2|3)\.\d|\b(1|2|3)\.\d ?mmol|手震|出冷汗|冒汗|血糖低|好肚餓.{0,6}(震|汗)",
         bump={c: 3 for c in HYPO}),
    Term("muscle pain or weakness", "side_effect", 1,
         r"muscle (pain|ache|cramp|weak)|muscles? (are |is )?(killing|aching|sore|painful|hurt|weak)|(legs?|arms?|thighs?) (ache|aching|pain|weak)|(legs?|arms?|thighs?) (feel|feels|are|is)( so| very| really)? (weak|heavy|achy|sore)|肌肉(痛|酸|無力)|(腳|手|大髀)(好)?(酸|痛|冇力)",
         bump={"statin": 2}),
    Term("dark urine", "side_effect", 2, r"(dark|brown|cola)[- ]colou?red (urine|pee)|(urine|pee|wee) (is |was |looks |looking )?(very |really )?(dark|brown|cola|tea)|小便(好)?(深色|啡色|茶色)|尿(好)?(深色|啡色|茶色)",
         bump={"statin": 3}),
    Term("yellow skin or eyes", "side_effect", 3, r"yellow (skin|eyes)|(skin|eyes) (looks?|is|are|turned|turning|gone|went|seems?) (a bit |slightly |quite )?yellow|jaundice|(皮膚|眼)(好)?黃|黃疸"),
    Term("dizzy or light-headed", "side_effect", 1, r"dizz|light[- ]?headed|giddy|woozy|wobbly|頭(好|有啲|有D)?暈|頭重腳輕|天旋地轉",
         bump={**{c: 2 for c in ANY_BP}, **{c: 2 for c in HYPO}}),
    Term("fall", "side_effect", 2, r"\bfell\b|\bfall(en)?\b(?! asleep)|跌(低|親|倒)|跌(咗|左)?(一)?(跤|交)|仆(低|親)", bump={c: 3 for c in ANY_BP + HYPO + BLEED}),
    Term("nearly fainted", "side_effect", 3, r"nearly (fainted|passed out|blacked out)|差啲暈|好似要暈|就嚟暈|虛脫|企唔穩"),
    Term("headache", "side_effect", 1, r"headache|頭痛"),
    Term("fast or pounding heartbeat", "side_effect", 2, r"palpitat|skipp(ing|ed) (a )?beats?|missed beats|flutter|heart (is )?(racing|pounding)|心跳(好)?(快|亂)|心悸"),
    Term("slow pulse", "side_effect", 2, r"pulse (is |of )?(4\d|3\d)\b|heart ?rate (is |of )?(4\d|3\d)\b|心跳(好)?慢", bump={"beta_blocker": 3}),
    Term("vomiting and stomach pain", "side_effect", 2, r"vomit|throwing up|嘔吐|嘔(咗|左)|(作)?嘔",
         bump={"sglt2": 3, "metformin": 2}),
    Term("stomach upset or diarrhoea", "side_effect", 1, r"diarrh|loose (stool|motion)|upset stomach|stomach (is |feels |has been )?(upset|bad|sore|funny|off)|nause|肚屙|肚瀉|屙(水|肚)|作悶|胃(好)?唔舒服|肚痛"),
    Term("ankle or leg swelling", "side_effect", 1, r"(ankles?|feet|legs?) (are |is )?(swollen|swelling|puffy|swell)|swollen (ankles?|feet|legs?)|(腳|腳眼|腳踭)(好|有啲|有D)?腫"),
    Term("dry cough", "side_effect", 1, r"(dry|tickly|persistent|annoying) cough|\bcough (is |has been )?(driving|bothering|keeps|won'?t|getting)|\bcoughing (a lot|all the time|non ?stop)|cough(ing)? (since|non ?stop|all (day|night))|乾咳|咳(個)?(不停|唔停)"),
    Term("low mood or anxiety", "side_effect", 2, r"low mood|depress|anxious|agitat|panick|情緒(好)?低落|好焦慮|好驚|好唔開心"),
    Term("pregnant", "side_effect", 3, r"pregnan|有(咗|左)(BB|bb|bb仔|身孕)|懷孕"),
    Term("feels unwell since the change", "side_effect", 2,
         r"side ?effect|reaction|feel(ing)? (a bit |quite |really |very )?(off|strange|weird|funny|unwell|terrible|awful|not right|rough)|副作用|過敏|唔舒服|周身唔聚財"),
    # ---- confusion, adherence, dose questions, repeats
    Term("unsure what to take", "confusion", 2,
         r"(which|what) (one|tablet|pill|medicine)s? (should|do|to)|which (one )?(do|should) i (use|take)|not sure (which|what|how|if)|confus|mixed up|muddled|(looks?|looking|seems?) different|different[- ]looking|both the (old|new)|(old|new) (one|tablet|pill)s? (as well|too|and)"
         r"|(old|new|previous) [a-z]+ (as well as|and|together with|too)\b|as well as the (old|new)|still taking the (old|previous)|唔知(要)?食(邊|幾)|兩(隻|樣)(藥)?都(有)?食|仲(有)?食(緊)?(舊|之前)|(新|舊)(嗰|個)?(隻|粒|款).{0,6}(新|舊)(嗰|個)?(隻|粒|款)|唔知.{0,10}(一齊食|食邊|點食)|新舊(藥)?(一齊|都)",),
    Term("took the wrong dose", "confusion", 3,
         r"took (two|2|double|an extra|extra|too many|twice)|double(d)? (dose|up)|took .{0,20} twice|食(多|錯)(咗|左)|食(咗|左)兩(次|粒)|重覆食"),
    Term("stopped or skipping on their own", "adherence", 2,
         r"(stopped|quit|not|haven'?t been|didn'?t|don'?t) (taking|take|took)|\b(stopped|quit) (the|my|it|them|that)\b|\b(haven'?t|have not|hasn'?t|didn'?t|did not|not) (taken|take|took)\b|gave up (on )?(the|my|taking)|(skip|skipped|skipping|missed)( a| my| some)? (dose|tablet|pill)s?|停(咗|左)(食)?|冇(再)?食|唔(敢)?食(咗|左)?"),
    Term("asks to change a dose", "dose_question", 2,
         r"(can|could|should|may) i (take|have|increase|reduce|cut|halve|double|skip|stop|change|try)|(take|have) (two|2|half|more|less|an extra)|increase the dose|reduce the dose"
         r"|可(唔可以|以)(食|加|減|停|轉|飛)|(想|要)?(加|減)(多|少)?(一)?(粒|半粒|劑量)|使唔使(停|加|減|食)|食(多|少)(啲|D|粒|半粒)(得唔得|好唔好|得嗎)?|(加|減)(藥|劑量)"),
    Term("asks for a repeat", "repeat", 1,
         r"\b(repeat|refill|renew|top ?up|(another|new|a) (script|prescription)|more (of )?(my )?(medicine|meds|tablets|pills))\b|(run|ran|running) out|\b(none|no more|nothing) left\b|\b(\d+|a few|few|only( \d+)?|couple of) (tablets?|pills?|days?|doses?) left\b|\b(running|getting|am|i'?m) (a bit |quite )?low\b|\blow on\b|\bout of (my )?[a-z]+ (by|on|soon|next|tomorrow)\b|\bbe out of\b|finish(ed)? (my|the|all)|need (some )?more"
         r"|\b(can|could|may) i (get|have) (my|some|a|another|more)\b|\b(another|second) (course|box|bottle|pack)\b|\b(get|have) (my )?[a-z]+ again\b|\bnew supply\b|prescription (has )?(expired|ran out|run out)|redo (it|my prescription|the prescription|my script)|about to (finish|run out)|(再|多)開.{0,6}藥|開.{0,6}(個月|星期)(嘅|啲)?藥|覆配|配(返|多)(次|啲)?|(開|配|攞|拎)(返)?(上次|平時)?(嘅|啲)?(嘅|啲)?藥|(拎|攞|配|開)(多|返)(一)?(次|啲|D)|藥(食|用)(晒|完)|食(晒|得七七八八)|冇(晒)?藥|就快(冇|食晒)"),
]

NEGATORS_EN = re.compile(r"\b(no|not|never|without|none|nor|don'?t|doesn'?t|didn'?t|isn'?t|aren'?t|wasn'?t|haven'?t|hasn'?t|no more|any)\b\W*(\w+\W+){0,2}$", re.I)
NEGATORS_ZH = re.compile(r"(冇|無|沒有|唔係|未有|未見|冇再|唔再|唔會|冇乜)[^，,。.!！?？]{0,3}$")
THIRD_PARTY = re.compile(r"\b(my|our) (husband|wife|mum|mom|mother|dad|father|son|daughter|friend|neighbou?r|colleague|brother|sister|kid|child|baby)\b|\bhe\b|\bshe\b"
                         r"|老公|老婆|太太|先生|阿媽|媽媽|阿爸|爸爸|個仔|個女|朋友|同事|鄰居|佢", re.I)
HISTORY = re.compile(r"\b(last year|years ago|months ago|used to|in the past|as a child|before (i|the) (started|new))\b|以前|舊年|幾年前|細個|之前(試過|有過)", re.I)
CLAUSE = re.compile(r"(?<!\d)\.(?!\d)|[!?;。！？；\n]+|,\s*(?:but|though)\b|但係|不過|，(?=但|不過)")
QUESTION = re.compile(r"\?|？|嗎|呢$|點算|點樣|好唔好|得唔得|使唔使|可唔可以|係咪", re.M)
RESOLVED_ONLY = re.compile(r"\b(all good|fine|ok(ay)?|better|no problems?|no issues)\b|冇問題|好返|好(咗|左)(好)?多|OK|ok", re.I)


def clauses(text: str) -> list[tuple[int, str]]:
    """(offset, clause) pieces split on sentence ends and 'but'; offsets index the original text."""
    out, pos = [], 0
    for m in CLAUSE.finditer(text):
        if m.start() > pos:
            out.append((pos, text[pos:m.start()]))
        pos = m.end()
    if pos < len(text):
        out.append((pos, text[pos:]))
    return out


def _negated(clause: str, start: int) -> bool:
    before = clause[:start]
    return bool(NEGATORS_EN.search(before) or NEGATORS_ZH.search(before))


def _quote(text: str, off: int, clause: str) -> str:
    q = clause.strip()
    return q if q and q in text else text[off: off + len(clause)]


class RulesReader:
    name = "rules"

    def read(self, text: str, classes: set[str] | None = None) -> list[dict]:
        classes = classes or set()
        text = text or ""
        found: dict[str, dict] = {}
        for off, cl in clauses(text):
            other = bool(THIRD_PARTY.search(cl))
            past = bool(HISTORY.search(cl))
            for t in TERMS:
                for m in re.finditer(t.pattern, cl, re.I):
                    if t.kind not in ("repeat", "dose_question", "confusion") and _negated(cl, m.start()):
                        continue
                    if t.kind == "adherence" and _negated(cl, m.start()) and not re.search(r"not|didn|don|haven|冇|唔", m.group(0)):
                        continue
                    u = max([t.urgency] + [v for c, v in t.bump.items() if c in classes])
                    note = ""
                    if t.kind in ("red_flag", "side_effect", "crisis") and (other or past):
                        if t.urgency < 4:
                            continue                        # someone else's, or long ago: not this patient's reaction now
                        note = "about someone else" if other else "in the past"
                        u = 3                               # still a message the doctor must see today
                    q = _quote(text, off, cl)
                    f = found.get(t.label)
                    if f is None or u > f["urgency"]:
                        found[t.label] = {"kind": t.kind, "label": t.label, "urgency": u, "quote": q, "note": note,
                                          "classes": sorted(set(t.bump) & classes), "via": self.name}
                    break
        if not found and QUESTION.search(text) and not RESOLVED_ONLY.fullmatch(text.strip()):
            found["question"] = {"kind": "question", "label": "question for the doctor", "urgency": 1,
                                 "quote": text.strip()[:240], "note": "", "classes": [], "via": self.name}
        out = sorted(found.values(), key=lambda f: (-f["urgency"], f["label"]))
        return out


class ModelReader:
    """A model reads the message too; it can only add to the floor. Pass any object with
    `name` and `run(text, context) -> list[dict]` (keys as above). Unverified quotes are dropped and counted."""

    def __init__(self, model, floor: RulesReader | None = None):
        self.model = model
        self.floor = floor or RulesReader()
        self.name = f"model[{getattr(model, 'name', 'model')}]+rules"
        self.stats = {"model_findings": 0, "dropped_unverified": 0, "dropped_bad": 0, "errors": 0, "raised_floor": 0}

    def read(self, text: str, classes: set[str] | None = None) -> list[dict]:
        floor = {f["label"]: f for f in self.floor.read(text, classes)}
        try:
            got = list(self.model.run(text, {"classes": sorted(classes or ())}) or [])
        except Exception:                                    # noqa: BLE001 - a model failure falls back to the floor
            self.stats["errors"] += 1
            got = []
        kinds = {t.kind for t in TERMS} | {"question"}
        for g in got:
            self.stats["model_findings"] += 1
            q = str(g.get("quote") or "")
            if not q or " ".join(q.split()) not in " ".join(text.split()):
                self.stats["dropped_unverified"] += 1
                continue
            try:
                u = max(1, min(4, int(g.get("urgency", 1))))
            except (TypeError, ValueError):
                self.stats["dropped_bad"] += 1
                continue
            if g.get("kind") not in kinds:
                self.stats["dropped_bad"] += 1
                continue
            label = str(g.get("label") or g["kind"])[:80]
            f = floor.get(label)
            if f is None:
                floor[label] = {"kind": g["kind"], "label": label, "urgency": u, "quote": q, "note": "",
                                "classes": [], "via": getattr(self.model, "name", "model")}
            elif u > f["urgency"]:                           # the model may raise the floor, never lower it
                f["urgency"] = u
                f["via"] = f"{f['via']}+{getattr(self.model, 'name', 'model')}"
                self.stats["raised_floor"] += 1
        return sorted(floor.values(), key=lambda f: (-f["urgency"], f["label"]))


def classes_of(meds: list[str]) -> set[str]:
    return {c for c in (fx.drug_class(m) for m in meds) if c}
