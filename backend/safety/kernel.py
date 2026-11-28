"""Canonical Hong Kong Clinical Safety Kernel (deterministic floor, <2ms, zero LLM).

Single source of truth for deterministic clinical safety across `precare`, `acute/triage`,
`acute/companion`, and follow-up workflows:
- Explicit policy profiles:
  * `STRICT_FLOOR`: over-triggers intentionally with zero negation masking (used by precare).
  * `CLAUSE_AWARE_FLOOR`: applies clause-scoped EN/Cantonese negation, Cantonese hyperbole
    idioms (`咳到想死`), remote third-party history, and carer-vs-third-party detection
    (`His wife here`, `我係佢個女`) before running the full crisis/red-flag/vitals floor.
- Unified clinical thresholds (`SafetyThresholds`: SpO2 < 94%, Temp >= 40.0C / <= 35.0C,
  RR >= 25, HR >= 130, SBP < 90, hypertensive crisis >= 180/110, glucose < 4.0 mmol/L).
- Unified outbound guard (`check_outbound_safety`) combining `backend.precare.safety.check_outbound`
  with acute autonomous-Rx / autonomous-sick-leave / diagnostic-certainty prohibitions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from backend.acute.terminology import LOINC_OBSERVATIONS
from backend.precare import safety as precare_safety

KERNEL_VERSION = "safety-kernel-v1"
KIND_ORDER: dict[str | None, int] = {None: 0, "red_flag": 1, "crisis": 2}


class SafetyProfile(str, Enum):
    STRICT_FLOOR = "strict_floor"
    CLAUSE_AWARE_FLOOR = "clause_aware_floor"


@dataclass(frozen=True)
class SafetyThresholds:
    spo2_emergency_lt: float = 94.0
    spo2_urgent_eq: float = 94.0
    temp_emergency_gte: float = 40.0
    temp_hypothermia_lte: float = 35.0
    temp_urgent_gte: float = 39.5
    temp_priority_gte: float = 38.5
    rr_emergency_gte: float = 25.0
    hr_emergency_gte: float = 130.0
    sbp_emergency_lt: float = 90.0
    sbp_hypertensive_gte: float = 180.0
    dbp_hypertensive_gte: float = 110.0
    glucose_emergency_lt: float = 4.0


THRESHOLDS = SafetyThresholds()

# Carer writing on behalf of the patient ("His wife here", "我係佢個女", "代佢問"):
# symptoms attributed to he/she/husband/mum in a carer message belong to the patient,
# NOT a remote third-party family history!
CARER_RE = re.compile(
    r"\b(?:his|her)\s+(?:wife|husband|son|daughter|mum|mom|mother|dad|father|carer|helper|maid|brother|sister)\b.{0,12}\b(?:here|speaking|writing|messaging)\b"
    r"|\b(?:i'?m|i am|this is)\s+(?:his|her)\s+(?:wife|husband|son|daughter|mum|mom|mother|dad|father|carer|helper|maid|brother|sister)\b"
    r"|\b(?:on behalf of|writing for)\s+(?:him|her|my\s+(?:husband|wife|mum|mom|mother|dad|father|son|daughter))\b"
    r"|我係佢(?:嘅)?(?:老婆|太太|老公|先生|個仔|個女|仔|女|姐姐|工人|家人)"
    r"|代(?:佢|我(?:老公|老婆|阿媽|阿爸|爸爸|媽媽))(?:問|講|send|發)",
    re.I,
)

# Tightly scoped Cantonese hyperbole idioms ONLY when attached to benign flu/cold symptoms.
BENIGN_HYPERBOLE_RE = re.compile(
    r"(咳|流鼻水|鼻塞|喉嚨痛|喉嚨痕|攰|眼瞓|周身痛|骨痛|發燒)到(想死|死咁滯|快死)"
)

# Remote past third-party history (e.g. "husband had heart attack in 2019")
REMOTE_THIRD_PARTY_HISTORY_RE = re.compile(
    r"\b(?:my\s+)?(?:husband|wife|spouse|father|mother|dad|mum|mom|grandpa|grandma|"
    r"grandfather|grandmother|uncle|aunt|brother|sister|friend|colleague|boss)\b"
    r"[^.,;!?]{0,60}\b(?:had|suffered(?:\s+from)?|died\s+of|history\s+of|fainted|collapsed)\b"
    r"[^.,;!?]{0,60}\b(?:in\s+(?:19\d{2}|20[0-2]\d)|\d+\s+years?\s+ago|years?\s+ago|previously|before|"
    r"last\s+(?:year|summer|winter|month)|when\s+(?:he|she|they)\s+(?:was|were)\s+(?:a\s+)?(?:kid|child|young))\b",
    re.I,
)
REMOTE_THIRD_PARTY_ZH_RE = re.compile(
    r"(?:老公|老婆|爸爸|媽媽|阿爸|阿媽|公公|婆婆|爺爺|嫲嫲|朋友|同事)"
    r"(?:喺)?(?:(?:19\d{2}|20[0-2]\d)年|\d+年前|以前|之前|上年|去年)[^，。；！？!?]{0,20}"
    r"(?:中過風|試過心臟病發|心臟病發過|有過心臟病|中風過|試過暈低|暈過)"
)

CLAUSE_SPLIT_RE = re.compile(
    r"([.,;!?，。；！？]|\b(?:but|however|except|although|though|yet)\b|但係|但是|不過|但|只是|淨係)",
    re.I,
)

_EN_NEG_TERM = (
    r"(?:chest\s+(?:pain|tightness|pressure|discomfort)|short(?:ness)?\s+of\s+breath|breathlessness|"
    r"difficulty\s+breathing|trouble\s+breathing|stiff\s+neck|blue\s+lips|cyanosis|confusion|"
    r"fainting|syncope|seizures?|palpitations?|rash|vomiting(?:\s+blood)?|coughing\s+(?:up\s+)?blood|"
    r"black\s+(?:tarry\s+)?stools?|tarry\s+stools?|suicidal(?:\s+thoughts?)?|self[- ]?harm|"
    r"haemoptysis|bleeding|fever|cough|diarrh?oea|headache|dizziness|heart\s+attack|stroke|wheez\w*)"
)
EN_NEGATED_SYMPTOM_RE = re.compile(
    r"(?<![A-Za-z])(?:no(?:\s+more|\s+longer(?:\s+have)?)?|not(?:\s+having)?|without|denies|denied|deny|"
    r"negative\s+for|neither|never\s+had|haven'?t\s+had|don'?t\s+have|doesn'?t\s+have|didn'?t\s+have|zero|none\s+of)\s+"
    r"(?:any\s+)?"
    + _EN_NEG_TERM
    + r"(?:\s*(?:,|/|or|nor|and)\s*(?:no\s+|any\s+)?" + _EN_NEG_TERM + r")*",
    re.I,
)

_ZH_NEG_TERM = (
    r"(?:胸口痛|心口痛|胸痛|胸悶|心口翳|胸口翳|氣促|呼吸困難|唞唔到氣|抖唔到氣|咳血|吐血|"
    r"暈低|暈倒|發紺|嘴唇發紫|嘴唇腫|喉嚨腫|嚴重敏感|發紫|叫唔醒|抽筋|頸硬|頸部僵硬|意識混亂|"
    r"發燒|咳嗽|咳|嘔吐|嘔|肚瀉|腹瀉|紅疹|"
    r"chest\s+(?:pain|tightness|pressure)|short(?:ness)?\s+of\s+breath|breathlessness|fainting|dizziness)"
)
ZH_NEGATED_SYMPTOM_RE = re.compile(
    r"(?:冇再|唔再|已經冇|冇|無|沒有|唔覺有|並無|否認)(?:任何)?\s*"
    + _ZH_NEG_TERM
    + r"(?:(?:\s*[、，,同或及和]\s*(?:冇再|唔再|冇|無|沒有)?\s*|\s*(?:冇再|唔再|冇|無|沒有)\s*)" + _ZH_NEG_TERM + r")*",
    re.I,
)
# Also catch HK Cantonese post-subject negation ("胸口都唔翳喇", "胸口唔痛")
ZH_POST_NEG_RE = re.compile(
    r"(?:胸口|心口)(?:都)?(?:唔|冇再|已經冇)(?:痛|翳|悶)(?:喇|啦|咗)?",
)

# --- Expanded HK Acute Crisis & Red-Flag Patterns ---
ACUTE_CRISIS_PATTERNS: list[tuple[str, str]] = [
    ("indirect_suicidal_ideation", r"don'?t see the point any ?more"),
    ("indirect_suicidal_ideation", r"can'?t go on any ?more"),
    ("indirect_suicidal_ideation", r"tired of living"),
    ("indirect_suicidal_ideation", r"wish i wasn'?t (here|alive)"),
    ("indirect_suicidal_ideation", r"saying goodbye to everyone"),
    ("indirect_suicidal_ideation", r"everyone (would be|is) better off without me"),
    ("indirect_suicidal_ideation", r"撐唔住.{0,6}(想走|放棄生命|唔想活)"),
    ("indirect_suicidal_ideation", r"唔想再拖累屋企人"),
    ("indirect_suicidal_ideation", r"已經同(大家|屋企人)道別"),
    ("indirect_suicidal_ideation", r"準備好後事"),
    ("indirect_suicidal_ideation", r"想一覺瞓死"),
]

ACUTE_RED_FLAG_PATTERNS: list[tuple[str, str]] = [
    ("cyanosis", r"\b(blue|bluish|purple|grey|gray)\s+(lips?|tongue|fingers?|fingernails?|face|around\s+(?:the\s+)?mouth)\b"),
    ("cyanosis", r"\b(lips?|fingers?|fingernails?|face|skin)\s+(?:have\s+|has\s+|had\s+)?(are|is|turned|turning|went|gone|looking|look)\s+(blue|bluish|purple|grey|gray)\b"),
    ("cyanosis", r"\bcyanosis\b|\bcyanotic\b"),
    ("cyanosis", r"(嘴唇|口唇|手指|塊面|咀唇)(發紫|變紫|變藍|發青|變青)|發紺"),
    ("haemoptysis", r"\bcough(?:ed|ing)?\s+(?:up\s+)?(?:\w+\s+){0,3}blood\b|咳(?:出|咗)?(?:啲|鮮紅|好多)?血"),
    ("cannot_speak_full_sentences", r"(can'?t|cannot|unable\s+to)\s+(speak|talk|say|finish|complete)\s+(in\s+)?(full\s+)?sentences?"),
    ("cannot_speak_full_sentences", r"too\s+(breathless|short\s+of\s+breath)\s+to\s+(talk|speak)"),
    ("cannot_speak_full_sentences", r"(ribs?|chest\s+wall)\s+(pulling\s+in|indrawing|sucking\s+in)|\bretractions?\b"),
    ("cannot_speak_full_sentences", r"講唔到成句|講幾隻字就要唞氣|唞氣好似拉風箱|肋骨凹陷|吸氣凹陷"),
    ("confusion_ams", r"\b(confused|disoriented|delirious|hallucinating|incoherent)\b"),
    ("confusion_ams", r"\b(talking\s+nonsense|not\s+making\s+sense|doesn'?t\s+know\s+where\s+(?:he|she|they)\s+(?:is|are))\b"),
    ("confusion_ams", r"(意識混亂|神志不清|神智不清|神智迷糊|胡言亂語|認唔到人|答非所問|呆滯叫唔醒)"),
    ("non_blanching_rash", r"\bnon[- ]?blanching\b|\bpetechia\w*|\bpurpur(?:a|ic)\b"),
    ("non_blanching_rash", r"\b(rash|spots?)\b.{0,35}\b(doesn'?t|does\s+not|won'?t|did\s+not)\s+(fade|blanch|disappear)\b"),
    ("non_blanching_rash", r"\bglass\s+test\b.{0,25}\b(doesn'?t|does\s+not|still\s+visible|positive|failed)\b"),
    ("non_blanching_rash", r"(按落去|玻璃杯按壓).{0,8}(唔褪色|唔退色)|唔會?(?:褪色|退色).{0,8}(紅點|紅疹|紫斑|紫紅色?皮疹|皮疹)|瘀點|紫癜"),
    ("meningitis_red_flag", r"\b(?:stiff\s+neck|neck\s+stiffness)\b.{0,50}\b(?:photophobia|bright\s+lights?|can'?t\s+look\s+at\s+light|fever|worst\s+headache|rash)\b"),
    ("meningitis_red_flag", r"\b(?:photophobia|bright\s+lights?\s+hurt)\b.{0,50}\b(?:stiff\s+neck|neck\s+stiffness|fever|severe\s+headache)\b"),
    ("meningitis_red_flag", r"(?:頸硬|頸梗|頸部僵硬).{0,25}(?:畏光|怕光|發燒|高燒|劇烈頭痛|紅疹|皮疹)|(?:畏光|怕光).{0,25}(?:頸硬|頸梗|頸部僵硬|發燒|高燒)"),
    ("sudden_unilateral_vision_loss", r"\bsudden(?:ly)?\s+(?:lost|loss\s+of)\s+(?:all\s+|my\s+|the\s+)?(?:vision|sight)\b"),
    ("sudden_unilateral_vision_loss", r"\b(?:went\s+blind|lost\s+(?:all\s+|my\s+)?(?:sight|vision)|can'?t\s+see(?:\s+anything)?)\s+(?:in|out\s+of)\s+(?:one|my\s+left|my\s+right|the\s+left|the\s+right)\s+eye\b"),
    ("sudden_unilateral_vision_loss", r"\bcurtain\s+(?:came\s+)?over\s+(?:my\s+)?(?:eye|vision)\b"),
    ("sudden_unilateral_vision_loss", r"(一隻眼|單眼|左眼|右眼).{0,8}突然.{0,4}(睇唔到|失明|盲咗|黑晒|黑咗)|突然(單眼)?失明"),
    ("pediatric_deterioration", r"\b(baby|infant|toddler|child|kid|son|daughter|newborn)\b.{0,50}\b(floppy|limp|lethargic|no\s+wet\s+(?:nappies|diapers)|not\s+drinking|refusing\s+fluids|sunken\s+eyes|grunting|won'?t\s+wake|hard\s+to\s+wake|seizure|fitting|blue|stridor|bulging\s+fontanelle)\b"),
    ("pediatric_deterioration", r"(bb|嬰兒|小朋友|個仔|個女|阿仔|阿女).{0,25}(軟晒|叫唔醒|冇反應|唔肯飲水|冇尿|唔肯食奶|抽筋|發紫|吸氣凹陷|呼吸好急|喘鳴)"),
    ("elderly_deterioration", r"\b(elderly|grandma|grandpa|grandmother|grandfather|mother|father|mum|mom|dad)\b.{0,45}\b(confused|delirious|collapsed|won'?t\s+wake|unresponsive|gasping|blue\s+lips|not\s+making\s+sense|fell\s+and\s+hit)\b"),
    ("elderly_deterioration", r"(婆婆|公公|阿媽|阿爸|老人家|爺爺|嫲嫲).{0,25}(意識混亂|神志不清|胡言亂語|暈低|叫唔醒|冇反應|唞唔到氣|嘴唇發紫)"),
]

# Composite clinical guideline detectors (NICE NG143 / HK HA A&E / RCOG / IDSA)
CALF_DVT_RE = re.compile(
    r"\b(?:calf\s+(?:pain|ache|swollen|swelling|tender|tight|cramp)|"
    r"(?:left|right|one)\s+(?:calf|leg)\s+(?:is\s+)?(?:swollen|painful|hot|red|tender)|swollen\s+calf)\b|"
    r"(?:小腿(?:痛|腫|脹|紅腫)|單腳腫|一隻腳腫)",
    re.I,
)
ACUTE_DYSPNOEA_PE_RE = re.compile(
    r"\b(?:sudden(?:ly)?\s+(?:short(?:ness)?\s+of\s+breath|breathless|dyspnoea|dyspnea|can'?t\s+breathe)|"
    r"short(?:ness)?\s+of\s+breath|breathless|dyspnoea|dyspnea|chest\s+pain|pleuritic)\b|"
    r"(?:突然(?:氣促|唞唔到氣|呼吸困難|唞氣辛苦)|氣促|唞唔到氣|呼吸困難|胸痛|心口痛)",
    re.I,
)
NEUTROPENIC_HOST_RE = re.compile(
    r"(?<![A-Za-z])(?:chemo(?:therapy)?|neutropeni[a-z]*|(?:bone\s+marrow|stem\s+cell|kidney|liver|organ)\s+transplant|"
    r"active\s+cancer\s+treatment|immunosuppress[a-z]*)(?![A-Za-z])|"
    r"(?:化療|中性粒細胞減少|骨髓移植|器官移植|免疫抑制劑|免疫力嚴重受抑)",
    re.I,
)
FEVER_MENTION_RE = re.compile(
    r"(?<![A-Za-z])(?:fever|febrile|chills|rigors|high\s+temp(?:erature)?)(?![A-Za-z])|發燒|高燒|發冷|打冷震",
    re.I,
)
INFANT_UNDER_3M_RE = re.compile(
    r"\b(?:newborn|neonate|(?:[1-9]|1[0-2])\s*[- ]?weeks?[- ]?old\s+(?:baby|infant|son|daughter)|"
    r"(?:1|2)\s*[- ]?months?[- ]?old\s+(?:baby|infant|son|daughter)|under\s+3\s+months?\s+old)\b|"
    r"(?:初生(?:嬰兒|BB)|(?:1|2|一|兩)個月大(?:嬰兒|BB)|未滿(?:3|三)個月(?:嬰兒|BB))",
    re.I,
)
PREGNANT_HOST_RE = re.compile(
    r"\b(?:pregnant|pregnancy|\d+\s*weeks?\s+pregnant|expecting\s+a\s+baby)\b|懷孕|大肚|有咗BB|孕婦",
    re.I,
)
OBSTETRIC_DANGER_RE = re.compile(
    r"\b(?:vaginal\s+bleeding|bleeding\s+down\s+below|spotting\s+heavily|"
    r"(?:reduced|decreased|less|no)\s+(?:fetal|baby)\s+movements?|baby\s+(?:stopped|not)\s+moving|"
    r"(?:severe\s+headache|blinding\s+headache).{0,35}(?:blurr?y?\s+vision|flashing\s+lights?|swollen\s+face|upper\s+abdominal|epigastric)|"
    r"pre[- ]?eclampsia|water\s+broke)\b|"
    r"(?:陰道出血|下體流血|見紅好多|胎動減少|冇胎動|BB唔郁|劇烈頭痛.{0,15}(?:視力模糊|眼花|上腹痛)|穿羊水)",
    re.I,
)
DOUBLE_SICKENING_RE = re.compile(
    r"\b(?:(?:got|felt|getting)\s+better.{0,30}(?:then|and\s+now|but\s+now).{0,25}(?:got\s+)?(?:much\s+)?(?:worse|worsened|relapsed)|"
    r"fever\s+(?:came|is)\s+back\s+(?:higher|worse)|fever.{0,20}returning|"
    r"rusty\s+sputum|dark\s+green\s+phlegm\s+with\s+(?:high\s+)?fever)\b|"
    r"(?:好返少少.{0,15}(?:又再發燒|突然轉差|變嚴重)|退燒幾日後又再燒|鐵鏽色痰|濃綠痰.{0,10}高燒)",
    re.I,
)

# --- Vitals Extraction Regexes ---
_SPO2_RE = re.compile(
    r"(?:spo2|sp02|o2\s*sat(?:uration)?|oxygen(?:\s+saturation|\s+level)?|oximeter|pulse\s+ox|血氧(?:飽和度)?)"
    r"[^0-9]{0,20}(\d{2,3}(?:\.\d)?)\s*%?",
    re.I,
)
_TEMP_CEL_RE = re.compile(
    r"(?:(?:temp(?:erature)?|fever|體溫|發燒|燒到|探熱)[^0-9]{0,18})?"
    r"(?<!\d)(3[5-9](?:\.\d{1,2})?|4[0-2](?:\.\d{1,2})?)\s*(?:°\s*c\b|℃|deg(?:rees?)?\s*c\b|cel(?:sius)?\b|c(?![A-Za-z])|度)",
    re.I,
)
_TEMP_BARE_CTX_RE = re.compile(
    r"(?:temp(?:erature)?|fever|體溫|發燒|燒到|探熱)\s*(?:is|was|at|of|:|=|去到|有)?\s*"
    r"(?<!\d)(3[5-9](?:\.\d{1,2})?|4[0-2](?:\.\d{1,2})?)(?!\d)",
    re.I,
)
_TEMP_FAHR_RE = re.compile(
    r"(?<!\d)(9[6-9](?:\.\d)?|10[0-8](?:\.\d)?)\s*(?:°\s*f\b|℉|deg(?:rees?)?\s*f\b|f(?![A-Za-z]))",
    re.I,
)
_HR_RE = re.compile(r"(?:heart\s*rate|pulse|\bhr\b|心率|脈搏)[^0-9]{0,12}(\d{2,3})\b", re.I)
_RR_RE = re.compile(r"(?:resp(?:iratory)?\s*rate|\brr\b|呼吸頻率)[^0-9]{0,12}(\d{1,2})\b", re.I)
_BP_RE = re.compile(r"(?:blood\s*pressure|\bbp\b|血壓)[^0-9]{0,12}(\d{2,3})\s*/\s*(\d{2,3})\b", re.I)

# --- Prompt-Injection & Outbound Guards ---
PROMPT_INJECTION_PATTERNS: list[str] = [
    *precare_safety.INSTRUCTION,
    r"ignore\s+(?:all\s+|your\s+|any\s+|the\s+)?(?:previous|prior|above|system)\s+(?:instructions?|rules?|prompts?)",
    r"ignore\s+(?:all\s+)?(?:red\s*flags?|safety|alerts?|warnings?|triage)",
    r"disregard\s+(?:all\s+|your\s+|previous\s+)?(?:instructions?|rules?|prompts?|guidelines?)",
    r"\b(?:developer|system|admin|root|supervisor)\s+(?:override|mode|prompt|command|directive)\b",
    r"\b(?:execute|call|invoke|run)\s+(?:tool|function|endpoint|api)\b",
    r"\bskip\s+(?:pdpo|consent|verification|otp|safety|triage|review)\b",
    r"<!--\s*(?:system|instruction|override|admin)\b",
    r"\b(?:clear|delete|remove|wipe)\s+(?:patient\.)?allergies\b",
    r"\b(?:reply|send|post|message)\s+(?:to\s+)?(?:my\s+)?whatsapp\s+directly\b",
    r"\bdump\s+(?:all\s+)?(?:patients?|records?|database|secrets?|prompt)\b",
    r"\b(?:approve|prescribe|issue|dispense|authorize|authorise)\s+(?:me\s+)?(?:amoxicillin|augmentin|antibiotics?|codeine|oseltamivir|tamiflu|steroids?|prescription|rx)\b",
    r"\b(?:issue|give|grant|write|sign|generate)\s+(?:me\s+)?(?:\d+\s*days?\s+)?(?:sick[- ]?leave|medical\s+certificate|\bmc\b)\b",
    r"\b(?:tell|confirm|say)\s+(?:me\s+)?(?:that\s+)?(?:i'?m|i\s+am|it'?s|everything\s+is)\s+(?:normal|fine|healthy|ok|okay|safe)\b",
    r"\bbypass\s+(?:safety|doctor|clinician|review|triage|rules?)\b",
    r"\[system\]|<\|system\|>|^\s*#\s*system\b",
    r"唔理(?:之前|以上)(?:嘅)?(?:指令|規則)",
    r"忽略(?:之前|以上)(?:嘅)?(?:指令|規則)",
    r"(?:即刻|直接)(?:批准?|開|出)(?:抗生素|阿莫西林|病假紙?|\d+日病假)",
    r"(?:話|講)俾我知我(?:好)?正常|話我正常|當我冇事",
]

ACUTE_BANNED_OUTBOUND_PATTERNS: list[tuple[str, str]] = [
    ("autonomous_sick_leave", r"\bsick[- ]?leave\s+(?:has\s+been|is|was)\s+(?:issued|approved|granted|signed)\b"),
    ("autonomous_sick_leave", r"\b(?:issued|approved|granted)\s+(?:you\s+)?(?:\d+\s*days?\s+)?(?:sick[- ]?leave|medical\s+certificate)\b"),
    ("autonomous_sick_leave", r"已(?:發出|批出|簽發)(?:\d+日)?病假"),
    ("autonomous_prescription", r"\bprescription\s+(?:has\s+been|is|was)\s+(?:issued|approved|signed|dispensed)\b"),
    ("autonomous_prescription", r"\b(?:prescribed|approved)\s+(?:you\s+)?(?:amoxicillin|augmentin|oseltamivir|antibiotics?|codeine)\b"),
    ("autonomous_prescription", r"已(?:處方|批出處方|開藥)"),
    ("diagnostic_certainty", r"\byou\s+definitely\s+have\b|\bconfirmed\s+diagnosis\b"),
]


@dataclass
class NormalizationResult:
    normalized_text: str
    suppressed: list[str] = field(default_factory=list)
    is_carer: bool = False
    negated_phrases: list[str] = field(default_factory=list)


@dataclass
class SafetyVerdict:
    kind: str | None  # "crisis" | "red_flag" | None
    urgency: int
    flags: list[str] = field(default_factory=list)
    evidence_quotes: list[str] = field(default_factory=list)
    suppressed_negations: list[str] = field(default_factory=list)
    is_carer: bool = False
    negated_context_detected: bool = False
    prompt_injection_detected: bool = False
    prompt_injection_matches: list[str] = field(default_factory=list)
    vitals_map: dict[str, float] = field(default_factory=dict)
    vitals_observations: list[dict[str, Any]] = field(default_factory=list)
    profile: str = SafetyProfile.CLAUSE_AWARE_FLOOR.value
    version: str = f"{KERNEL_VERSION}:{precare_safety.SCREEN_VERSION}"


def detect_prompt_injection(text: str) -> tuple[bool, list[str]]:
    """Detect prompt-injection attempts and return exact verbatim matched substrings."""
    matches: list[str] = []
    for pat in PROMPT_INJECTION_PATTERNS:
        m = re.search(pat, text, re.I | re.M)
        if m:
            q = m.group(0)
            if q and q in text and q not in matches:
                matches.append(q)
    return bool(matches), matches


def filter_verbatim_quotes(quotes: list[str], source_text: str) -> tuple[list[str], list[str]]:
    """Keep ONLY exact verbatim substrings of `source_text`; return (kept, dropped)."""
    kept: list[str] = []
    dropped: list[str] = []
    for q in quotes:
        if not isinstance(q, str) or not q.strip():
            continue
        if q in source_text:
            if q not in kept:
                kept.append(q)
        else:
            dropped.append(q)
    return kept, dropped


def normalize_clauses(text: str) -> NormalizationResult:
    """Produce a clause-normalized copy for red-flag screening while recording suppressed phrases.

    Respects `CARER_RE`: when a carer writes on behalf of a patient, remote third-party history
    masking still only strips explicitly historical events (e.g. "in 2019", "years ago"), never
    current symptoms reported for the patient.
    """
    suppressed: list[str] = []
    negated_phrases: list[str] = []
    is_carer = bool(CARER_RE.search(text or ""))

    def _mask_idiom(m: re.Match[str]) -> str:
        suppressed.append(f"idiom:{m.group(0)}")
        return f"{m.group(1)}得好犀利"

    work = BENIGN_HYPERBOLE_RE.sub(_mask_idiom, text or "")

    def _mask_hist(m: re.Match[str]) -> str:
        suppressed.append(f"remote_history:{m.group(0)}")
        return " [remote_history] "

    work = REMOTE_THIRD_PARTY_HISTORY_RE.sub(_mask_hist, work)
    work = REMOTE_THIRD_PARTY_ZH_RE.sub(_mask_hist, work)

    parts = CLAUSE_SPLIT_RE.split(work)
    cleaned_parts: list[str] = []
    for part in parts:
        if not part or CLAUSE_SPLIT_RE.fullmatch(part):
            cleaned_parts.append(part)
            continue

        def _mask_neg(m: re.Match[str]) -> str:
            suppressed.append(f"negation:{m.group(0)}")
            negated_phrases.append(m.group(0))
            return " [negated_symptom] "

        c = EN_NEGATED_SYMPTOM_RE.sub(_mask_neg, part)
        c = ZH_NEGATED_SYMPTOM_RE.sub(_mask_neg, c)
        c = ZH_POST_NEG_RE.sub(_mask_neg, c)
        cleaned_parts.append(c)

    return NormalizationResult(
        normalized_text="".join(cleaned_parts),
        suppressed=suppressed,
        is_carer=is_carer,
        negated_phrases=negated_phrases,
    )


def extract_numeric_vitals(
    text: str,
    vitals: dict[str, Any] | None = None,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Extract LOINC+UCUM numeric observations from structured vitals and free text."""
    parsed: dict[str, float] = {}
    observations: list[dict[str, Any]] = []
    seen_loinc: set[str] = set()

    def _add_obs(loinc: str, val: float, source: str, quote: str | None = None) -> None:
        meta = LOINC_OBSERVATIONS[loinc]
        obs: dict[str, Any] = {
            "loinc_code": loinc,
            "display": meta[0],
            "display_zh": meta[1],
            "ucum_unit": meta[2],
            "value_num": round(val, 2),
            "source": source,
        }
        if quote and quote in text:
            obs["quote"] = quote
        observations.append(obs)
        seen_loinc.add(loinc)

    if vitals:
        for key, loinc in (
            ("temp_c", "8310-5"),
            ("temperature_c", "8310-5"),
            ("temperature", "8310-5"),
            ("spo2", "2708-6"),
            ("spo2_pct", "2708-6"),
            ("hr", "8867-4"),
            ("heart_rate", "8867-4"),
            ("rr", "9279-1"),
            ("respiratory_rate", "9279-1"),
            ("sbp", "8480-6"),
            ("systolic", "8480-6"),
            ("dbp", "8462-4"),
            ("diastolic", "8462-4"),
        ):
            if key in vitals and vitals[key] is not None and loinc not in seen_loinc:
                val = float(vitals[key])
                if loinc == "8310-5" and val > 50.0:
                    val = round((val - 32.0) * 5.0 / 9.0, 2)
                parsed[loinc] = val
                _add_obs(loinc, val, "vitals")

    if "2708-6" not in seen_loinc:
        m = _SPO2_RE.search(text)
        if m:
            val = float(m.group(1))
            if 50.0 <= val <= 100.0:
                parsed["2708-6"] = val
                _add_obs("2708-6", val, "text", m.group(0))

    if "8310-5" not in seen_loinc:
        m = _TEMP_CEL_RE.search(text) or _TEMP_BARE_CTX_RE.search(text)
        if m:
            val = float(m.group(1))
            if 34.0 <= val <= 43.0:
                parsed["8310-5"] = val
                _add_obs("8310-5", val, "text", m.group(0))
        else:
            mf = _TEMP_FAHR_RE.search(text)
            if mf:
                val_c = round((float(mf.group(1)) - 32.0) * 5.0 / 9.0, 2)
                parsed["8310-5"] = val_c
                _add_obs("8310-5", val_c, "text", mf.group(0))

    if "8867-4" not in seen_loinc:
        m = _HR_RE.search(text)
        if m:
            val = float(m.group(1))
            if 30 <= val <= 240:
                parsed["8867-4"] = val
                _add_obs("8867-4", val, "text", m.group(0))

    if "9279-1" not in seen_loinc:
        m = _RR_RE.search(text)
        if m:
            val = float(m.group(1))
            if 6 <= val <= 70:
                parsed["9279-1"] = val
                _add_obs("9279-1", val, "text", m.group(0))

    if "8480-6" not in seen_loinc:
        m = _BP_RE.search(text)
        if m:
            sbp, dbp = float(m.group(1)), float(m.group(2))
            if 50 <= sbp <= 260 and 30 <= dbp <= 160:
                parsed["8480-6"] = sbp
                parsed["8462-4"] = dbp
                _add_obs("8480-6", sbp, "text", m.group(0))
                _add_obs("8462-4", dbp, "text", m.group(0))

    return parsed, observations


def _upgrade_kind(current: str | None, new_kind: str | None) -> str | None:
    return new_kind if KIND_ORDER[new_kind] > KIND_ORDER[current] else current


def screen_safety(
    text: str,
    *,
    profile: SafetyProfile = SafetyProfile.CLAUSE_AWARE_FLOOR,
    vitals: dict[str, Any] | None = None,
    check_hypertensive_crisis: bool = False,
) -> SafetyVerdict:
    """Run the canonical deterministic HK clinical safety floor under the selected profile."""
    raw_text = text or ""
    norm_res = normalize_clauses(raw_text)
    scan_text = raw_text if profile == SafetyProfile.STRICT_FLOOR else norm_res.normalized_text
    inj_detected, inj_matches = detect_prompt_injection(raw_text)

    flags: list[str] = []
    evidence_quotes: list[str] = []
    kind: str | None = None

    def _add_quote(q: str) -> None:
        if q and q in raw_text and q not in evidence_quotes:
            evidence_quotes.append(q)

    # 1A. Precare baseline floor (reuses precare_safety.screen)
    precare_kind = precare_safety.screen(scan_text)
    if precare_kind == "crisis":
        kind = _upgrade_kind(kind, "crisis")
        flags.append("precare_crisis_floor")
        for pat in precare_safety.CRISIS:
            m = re.search(pat, scan_text, re.I)
            if m:
                _add_quote(m.group(0))
                break
    elif precare_kind == "red_flag":
        kind = _upgrade_kind(kind, "red_flag")
        flags.append("precare_red_flag_floor")
        for pat in precare_safety.RED_FLAGS:
            m = re.search(pat, scan_text, re.I)
            if m:
                _add_quote(m.group(0))
                break
        if precare_safety.low_sugar(scan_text):
            flags.append("hypoglycaemia_lt_4")

    # 1B. Expanded indirect crisis patterns
    for code, pat in ACUTE_CRISIS_PATTERNS:
        m = re.search(pat, scan_text, re.I)
        if m:
            kind = _upgrade_kind(kind, "crisis")
            if code not in flags:
                flags.append(code)
            _add_quote(m.group(0))

    # 1C. Expanded HK acute red flags
    for code, pat in ACUTE_RED_FLAG_PATTERNS:
        m = re.search(pat, scan_text, re.I)
        if m:
            kind = _upgrade_kind(kind, "red_flag")
            if code not in flags:
                flags.append(code)
            _add_quote(m.group(0))

    # 1D. Composite guideline detectors
    calf_m = CALF_DVT_RE.search(scan_text)
    dys_m = ACUTE_DYSPNOEA_PE_RE.search(scan_text)
    if calf_m and dys_m:
        kind = _upgrade_kind(kind, "red_flag")
        if "suspected_pe_calf_dyspnoea" not in flags:
            flags.append("suspected_pe_calf_dyspnoea")
        _add_quote(calf_m.group(0))
        _add_quote(dys_m.group(0))

    vitals_map, vitals_obs = extract_numeric_vitals(raw_text, vitals)
    urgency = 1

    temp_c = vitals_map.get("8310-5")
    has_febrile = (temp_c is not None and temp_c >= 38.0) or bool(FEVER_MENTION_RE.search(scan_text))

    neut_m = NEUTROPENIC_HOST_RE.search(scan_text)
    if neut_m and has_febrile:
        kind = _upgrade_kind(kind, "red_flag")
        if "neutropenic_sepsis_risk" not in flags:
            flags.append("neutropenic_sepsis_risk")
        _add_quote(neut_m.group(0))
        fm = FEVER_MENTION_RE.search(scan_text)
        if fm:
            _add_quote(fm.group(0))

    inf_m = INFANT_UNDER_3M_RE.search(scan_text)
    if inf_m and has_febrile:
        kind = _upgrade_kind(kind, "red_flag")
        if "febrile_infant_under_3m" not in flags:
            flags.append("febrile_infant_under_3m")
        _add_quote(inf_m.group(0))
        fm = FEVER_MENTION_RE.search(scan_text)
        if fm:
            _add_quote(fm.group(0))

    preg_m = PREGNANT_HOST_RE.search(scan_text)
    ob_m = OBSTETRIC_DANGER_RE.search(scan_text)
    if preg_m and ob_m:
        kind = _upgrade_kind(kind, "red_flag")
        if "obstetric_red_flag" not in flags:
            flags.append("obstetric_red_flag")
        _add_quote(preg_m.group(0))
        _add_quote(ob_m.group(0))

    ds_m = DOUBLE_SICKENING_RE.search(scan_text)
    if ds_m:
        urgency = max(urgency, 3)
        _add_quote(ds_m.group(0))

    # 1E. Canonical numeric vitals thresholds
    spo2 = vitals_map.get("2708-6")
    if spo2 is not None:
        if spo2 < THRESHOLDS.spo2_emergency_lt:
            kind = _upgrade_kind(kind, "red_flag")
            flags.append("hypoxia_spo2_lt_94")
        elif spo2 == THRESHOLDS.spo2_urgent_eq:
            urgency = max(urgency, 3)

    if temp_c is not None:
        if temp_c >= THRESHOLDS.temp_emergency_gte:
            kind = _upgrade_kind(kind, "red_flag")
            flags.append("hyperpyrexia_temp_gte_40c")
        elif temp_c <= THRESHOLDS.temp_hypothermia_lte:
            kind = _upgrade_kind(kind, "red_flag")
            flags.append("hypothermia_sepsis_temp_lte_35c")
        elif temp_c >= THRESHOLDS.temp_urgent_gte:
            urgency = max(urgency, 3)
        elif temp_c >= THRESHOLDS.temp_priority_gte:
            urgency = max(urgency, 2)

    rr = vitals_map.get("9279-1")
    if rr is not None and rr >= THRESHOLDS.rr_emergency_gte:
        kind = _upgrade_kind(kind, "red_flag")
        flags.append("tachypnoea_rr_gte_25")

    hr = vitals_map.get("8867-4")
    if hr is not None and hr >= THRESHOLDS.hr_emergency_gte:
        kind = _upgrade_kind(kind, "red_flag")
        flags.append("tachycardia_hr_gte_130")

    sbp = vitals_map.get("8480-6")
    dbp = vitals_map.get("8462-4")
    if sbp is not None and sbp < THRESHOLDS.sbp_emergency_lt:
        kind = _upgrade_kind(kind, "red_flag")
        flags.append("hypotension_sbp_lt_90")
    if check_hypertensive_crisis:
        if (sbp is not None and sbp >= THRESHOLDS.sbp_hypertensive_gte) or (
            dbp is not None and dbp >= THRESHOLDS.dbp_hypertensive_gte
        ):
            kind = _upgrade_kind(kind, "red_flag")
            flags.append("hypertensive_crisis_bp_gte_180_110")

    for obs in vitals_obs:
        if obs.get("quote"):
            _add_quote(obs["quote"])

    if kind in ("crisis", "red_flag"):
        urgency = 4

    return SafetyVerdict(
        kind=kind,
        urgency=urgency,
        flags=list(dict.fromkeys(flags)),
        evidence_quotes=evidence_quotes,
        suppressed_negations=norm_res.suppressed,
        is_carer=norm_res.is_carer,
        negated_context_detected=bool(norm_res.negated_phrases),
        prompt_injection_detected=inj_detected,
        prompt_injection_matches=inj_matches,
        vitals_map=vitals_map,
        vitals_observations=vitals_obs,
        profile=profile.value,
    )


def check_outbound_safety(
    text: str,
    label: str = "",
    *,
    include_acute_claims: bool = True,
    extra_banned: Sequence[str] = (),
) -> list[str]:
    """Unified outbound patient-message safety guard (impersonation, advice, reassurance, autonomous claims)."""
    errors: list[str] = []
    if not text or not text.strip():
        return ["empty_outbound_text"]

    if label:
        for fail in precare_safety.check_outbound(text, label):
            errors.append(f"check_outbound_violation:{fail}")
    else:
        # Check advice, reassurance, and impersonation without requiring a fixed prefix
        for fail in precare_safety.check_outbound(f"__prefix__\n{text}", "__prefix__"):
            errors.append(f"check_outbound_violation:{fail}")

    if include_acute_claims:
        for code, pat in ACUTE_BANNED_OUTBOUND_PATTERNS:
            if re.search(pat, text, re.I):
                errors.append(f"banned_claim:{code}")

    low = text.lower()
    for claim in extra_banned:
        c = claim.strip()
        if c and c.lower() in low:
            errors.append(f"banned_claim:{c}")

    return errors
