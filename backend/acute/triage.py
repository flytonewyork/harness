"""Hong Kong Flu-Season Acute Care Triage, Bilingual Extraction, Consult Draft & Validators.

Implements L1 clinical harness intelligence:
1. Union-Only Bilingual Red-Flag Triage (`screen_acute_message`):
   - Deterministic rules floor FIRST: reuses `backend.precare.safety.screen` plus expanded HK acute
     flu-season red flags (EN, Cantonese, code-switched, colloquialisms, pediatric/elderly/caregiver
     presentations, SpO2 < 94%, temp >= 40.0 Cel, cyanosis, inability to speak full sentences,
     confusion, non-blanching rash, calf pain + sudden dyspnoea, sudden unilateral vision loss,
     indirect self-harm/suicide wording).
   - Safe clause-scoped negation, remote third-party history, and Cantonese hyperbole idiom handling
     ("no chest pain", "咳到想死", "flu jab", "husband had heart attack in 2019") while preserving
     >99% recall on genuine emergencies.
   - Optional Model Gateway screen in STRICT union-only mode: can add red flags or raise urgency,
     NEVER lower urgency or suppress a rules-floor flag.
   - Any emergency immediately returns a fixed non-LLM bilingual 999 / A&E (+ Samaritans 24-hour
     crisis hotline) banner with `urgency=4`.
2. Structured Bilingual Symptom & Observation Extraction (`extract_intake`):
   - Maps symptoms to official `SNOMED_SYMPTOMS` codes, vitals/RAT to LOINC + UCUM, duration/onset,
     comorbidities, allergies, current medications, with 100% verbatim substring quotes.
   - Detects and neutralizes prompt-injection attempts (flags `prompt_injection_detected=True`,
     never obeys, never reassures).
3. Consult Draft Proposal & Deterministic Validators (`draft_consult_proposal`, `validate_proposal`):
   - Dual ICD-10 + ICPC-2 working diagnosis from `HK_DIAGNOSES`, SOAP draft clinical note,
     proposed 1-2 day sick-leave draft (never autonomously issued), allergy/G6PD-safe non-dangerous
     OTC/antiviral draft, plain-language patient summary draft.
   - Validated against JSON schema, 100% verbatim quote integrity, official terminology codes,
     formulary/allergy/G6PD/Cap. 134 rules, and `backend.precare.safety.check_outbound` banned claims.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from backend.acute.terminology import (
    HK_ACUTE_FORMULARY,
    HK_DIAGNOSES,
    LOINC_OBSERVATIONS,
    SNOMED_SYMPTOMS,
    lookup_drug,
    validate_diagnosis_coding,
    validate_observation_coding,
    validate_snomed_codes,
)
from backend.gateway.gateway import (
    GatewayError,
    GatewayRequest,
    ModelGateway,
    ProtocolBundle,
    ResidencyViolationError,
    load_protocol,
)
from backend.precare import safety as precare_safety

# --- Fixed Non-LLM Bilingual Emergency & Crisis Banners ---
AUTOMATED_LABEL_EN = "Care team (automated)"
AUTOMATED_LABEL_ZH = "護理團隊（自動回覆）"

EMERGENCY_BANNER_EN = (
    f"{AUTOMATED_LABEL_EN}\n"
    "EMERGENCY ALERT: What you described may be a medical emergency. "
    "Please call 999 or go to the nearest A&E department immediately. "
    "Do not wait for a teleconsultation. Our on-duty clinician has been alerted."
)
EMERGENCY_BANNER_ZH = (
    f"{AUTOMATED_LABEL_ZH}\n"
    "緊急提示：你描述嘅情況可能屬於嚴重緊急醫療狀況。"
    "請即刻致電 999 或前往最近嘅急症室，切勿等待視像診症。我哋已通知當值醫生。"
)
EMERGENCY_BANNER_BILINGUAL = f"{EMERGENCY_BANNER_EN}\n\n---\n\n{EMERGENCY_BANNER_ZH}"

CRISIS_BANNER_EN = (
    f"{AUTOMATED_LABEL_EN}\n"
    "URGENT SUPPORT: Thank you for reaching out. If you are in immediate danger or might hurt yourself, "
    "please call 999 or go to the nearest A&E now. "
    "You can also speak confidentially 24/7 with The Samaritans Hong Kong on 2896 0000 "
    "(or Samaritan Befrienders HK on 2389 2222). Our on-duty clinician has been alerted."
)
CRISIS_BANNER_ZH = (
    f"{AUTOMATED_LABEL_ZH}\n"
    "緊急支援：多謝你話俾我哋知。如果你目前有即時危險或者可能會傷害自己，"
    "請即刻致電 999 或前往最近嘅急症室。"
    "你亦可以隨時致電撒瑪利亞會 24 小時多語言熱線 2896 0000（或香港撒瑪利亞防止自殺會 2389 2222）。"
    "我哋已通知當值醫生。"
)
CRISIS_BANNER_BILINGUAL = f"{CRISIS_BANNER_EN}\n\n---\n\n{CRISIS_BANNER_ZH}"

KIND_ORDER: dict[str | None, int] = {None: 0, "red_flag": 1, "crisis": 2}


# --- Safe Negation, Remote Third-Party History, & Idiom Specificity Helpers ---
# Tightly scoped Cantonese hyperbole idioms ONLY when attached to benign flu/cold symptoms.
# Never matches chest pain, severe headache, suicidal context, or overdose.
_BENIGN_HYPERBOLE_RE = re.compile(
    r"(咳|流鼻水|鼻塞|喉嚨痛|喉嚨痕|攰|眼瞓|周身痛|骨痛|發燒)到(想死|死咁滯|快死)"
)

# Remote past third-party history (e.g. "husband had heart attack in 2019", "brother had severe allergy when he was a kid", "grandmother fainted last summer")
_REMOTE_THIRD_PARTY_HISTORY_RE = re.compile(
    r"\b(?:my\s+)?(?:husband|wife|spouse|father|mother|dad|mum|mom|grandpa|grandma|"
    r"grandfather|grandmother|uncle|aunt|brother|sister|friend|colleague|boss)\b"
    r"[^.,;!?]{0,60}\b(?:had|suffered(?:\s+from)?|died\s+of|history\s+of|fainted|collapsed)\b"
    r"[^.,;!?]{0,60}\b(?:in\s+(?:19\d{2}|20[0-2]\d)|\d+\s+years?\s+ago|years?\s+ago|previously|before|"
    r"last\s+(?:year|summer|winter|month)|when\s+(?:he|she|they)\s+(?:was|were)\s+(?:a\s+)?(?:kid|child|young))\b",
    re.I,
)
_REMOTE_THIRD_PARTY_ZH_RE = re.compile(
    r"(?:老公|老婆|爸爸|媽媽|阿爸|阿媽|公公|婆婆|爺爺|嫲嫲|朋友|同事)"
    r"(?:喺)?(?:(?:19\d{2}|20[0-2]\d)年|\d+年前|以前|之前|上年|去年)[^，。；！？!?]{0,20}"
    r"(?:中過風|試過心臟病發|心臟病發過|有過心臟病|中風過|試過暈低|暈過)"
)

# Clause-scoped explicit English & Cantonese symptom negation (supports HK code-switching!)
_CLAUSE_SPLIT_RE = re.compile(r"([.,;!?，。；！？]|\b(?:but|however|except|although|though|yet)\b|但係|但是|不過|但|只是|淨係)", re.I)

_EN_NEG_TERM = (
    r"(?:chest\s+(?:pain|tightness|pressure|discomfort)|short(?:ness)?\s+of\s+breath|breathlessness|"
    r"difficulty\s+breathing|trouble\s+breathing|stiff\s+neck|blue\s+lips|cyanosis|confusion|"
    r"fainting|syncope|seizures?|palpitations?|rash|vomiting(?:\s+blood)?|coughing\s+(?:up\s+)?blood|"
    r"black\s+(?:tarry\s+)?stools?|tarry\s+stools?|suicidal(?:\s+thoughts?)?|self[- ]?harm|"
    r"haemoptysis|bleeding|fever|cough|diarrh?oea|headache|dizziness|heart\s+attack|stroke)"
)
_EN_NEGATED_SYMPTOM_RE = re.compile(
    r"(?<![A-Za-z])(?:no|not(?:\s+having)?|without|denies|denied|deny|negative\s+for|neither|never\s+had|"
    r"haven'?t\s+had|don'?t\s+have|doesn'?t\s+have|didn'?t\s+have|zero|none\s+of)\s+"
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
_ZH_NEGATED_SYMPTOM_RE = re.compile(
    r"(?:冇|無|沒有|唔覺有|並無|否認)(?:任何)?\s*"
    + _ZH_NEG_TERM
    + r"(?:(?:\s*[、，,同或及和]\s*(?:冇|無|沒有)?\s*|\s*(?:冇|無|沒有)\s*)" + _ZH_NEG_TERM + r")*",
    re.I,
)


def _normalize_for_rules_floor(text: str) -> tuple[str, list[str]]:
    """Produce a clause-normalized copy for red-flag screening while recording suppressed benign phrases."""
    suppressed: list[str] = []

    def _mask_idiom(m: re.Match[str]) -> str:
        suppressed.append(f"idiom:{m.group(0)}")
        return f"{m.group(1)}得好犀利"

    work = _BENIGN_HYPERBOLE_RE.sub(_mask_idiom, text)

    def _mask_hist(m: re.Match[str]) -> str:
        suppressed.append(f"remote_history:{m.group(0)}")
        return " [remote_history] "

    work = _REMOTE_THIRD_PARTY_HISTORY_RE.sub(_mask_hist, work)
    work = _REMOTE_THIRD_PARTY_ZH_RE.sub(_mask_hist, work)

    # Clause-by-clause negation masking (never crosses 'but', 'however', punctuation, '但', '不過')
    parts = _CLAUSE_SPLIT_RE.split(work)
    cleaned_parts: list[str] = []
    for part in parts:
        if not part or _CLAUSE_SPLIT_RE.fullmatch(part):
            cleaned_parts.append(part)
            continue

        def _mask_neg(m: re.Match[str]) -> str:
            suppressed.append(f"negation:{m.group(0)}")
            return " [negated_symptom] "

        c = _EN_NEGATED_SYMPTOM_RE.sub(_mask_neg, part)
        c = _ZH_NEGATED_SYMPTOM_RE.sub(_mask_neg, c)
        cleaned_parts.append(c)

    return "".join(cleaned_parts), suppressed


# --- Expanded Hong Kong Acute Flu-Season Red Flags ---
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
    # Cyanosis
    ("cyanosis", r"\b(blue|bluish|purple|grey|gray)\s+(lips?|tongue|fingers?|fingernails?|face|around\s+(?:the\s+)?mouth)\b"),
    ("cyanosis", r"\b(lips?|fingers?|fingernails?|face|skin)\s+(?:have\s+|has\s+|had\s+)?(are|is|turned|turning|went|gone|looking|look)\s+(blue|bluish|purple|grey|gray)\b"),
    ("cyanosis", r"\bcyanosis\b|\bcyanotic\b"),
    ("cyanosis", r"(嘴唇|口唇|手指|塊面|咀唇)(發紫|變紫|變藍|發青|變青)|發紺"),
    # Haemoptysis (including descriptive modifiers like "coughed up bright red blood")
    ("haemoptysis", r"\bcough(?:ed|ing)?\s+(?:up\s+)?(?:\w+\s+){0,3}blood\b|咳(?:出|咗)?(?:啲|鮮紅|好多)?血"),
    # Inability to speak full sentences / severe respiratory distress
    ("cannot_speak_full_sentences", r"(can'?t|cannot|unable\s+to)\s+(speak|talk|say|finish|complete)\s+(in\s+)?(full\s+)?sentences?"),
    ("cannot_speak_full_sentences", r"too\s+(breathless|short\s+of\s+breath)\s+to\s+(talk|speak)"),
    ("cannot_speak_full_sentences", r"(ribs?|chest\s+wall)\s+(pulling\s+in|indrawing|sucking\s+in)|\bretractions?\b"),
    ("cannot_speak_full_sentences", r"講唔到成句|講幾隻字就要唞氣|唞氣好似拉風箱|肋骨凹陷|吸氣凹陷"),
    # Confusion / altered mental status / delirium
    ("confusion_ams", r"\b(confused|disoriented|delirious|hallucinating|incoherent)\b"),
    ("confusion_ams", r"\b(talking\s+nonsense|not\s+making\s+sense|doesn'?t\s+know\s+where\s+(?:he|she|they)\s+(?:is|are))\b"),
    ("confusion_ams", r"(意識混亂|神志不清|神智不清|神智迷糊|胡言亂語|認唔到人|答非所問|呆滯叫唔醒)"),
    # Non-blanching / petechial / purpuric rash (meningitis/sepsis)
    ("non_blanching_rash", r"\bnon[- ]?blanching\b|\bpetechia\w*|\bpurpur(?:a|ic)\b"),
    ("non_blanching_rash", r"\b(rash|spots?)\b.{0,35}\b(doesn'?t|does\s+not|won'?t|did\s+not)\s+(fade|blanch|disappear)\b"),
    ("non_blanching_rash", r"\bglass\s+test\b.{0,25}\b(doesn'?t|does\s+not|still\s+visible|positive|failed)\b"),
    ("non_blanching_rash", r"(按落去|玻璃杯按壓).{0,8}(唔褪色|唔退色)|唔會?(?:褪色|退色).{0,8}(紅點|紅疹|紫斑|紫紅色?皮疹|皮疹)|瘀點|紫癜"),
    # Meningitis red flags (stiff neck + photophobia / severe headache / fever)
    ("meningitis_red_flag", r"\b(?:stiff\s+neck|neck\s+stiffness)\b.{0,50}\b(?:photophobia|bright\s+lights?|can'?t\s+look\s+at\s+light|fever|worst\s+headache|rash)\b"),
    ("meningitis_red_flag", r"\b(?:photophobia|bright\s+lights?\s+hurt)\b.{0,50}\b(?:stiff\s+neck|neck\s+stiffness|fever|severe\s+headache)\b"),
    ("meningitis_red_flag", r"(?:頸硬|頸梗|頸部僵硬).{0,25}(?:畏光|怕光|發燒|高燒|劇烈頭痛|紅疹|皮疹)|(?:畏光|怕光).{0,25}(?:頸硬|頸梗|頸部僵硬|發燒|高燒)"),
    # Sudden unilateral vision loss
    ("sudden_unilateral_vision_loss", r"\bsudden(?:ly)?\s+(?:lost|loss\s+of)\s+(?:all\s+|my\s+|the\s+)?(?:vision|sight)\b"),
    ("sudden_unilateral_vision_loss", r"\b(?:went\s+blind|lost\s+(?:all\s+|my\s+)?(?:sight|vision)|can'?t\s+see(?:\s+anything)?)\s+(?:in|out\s+of)\s+(?:one|my\s+left|my\s+right|the\s+left|the\s+right)\s+eye\b"),
    ("sudden_unilateral_vision_loss", r"\bcurtain\s+(?:came\s+)?over\s+(?:my\s+)?(?:eye|vision)\b"),
    ("sudden_unilateral_vision_loss", r"(一隻眼|單眼|左眼|右眼).{0,8}突然.{0,4}(睇唔到|失明|盲咗|黑晒|黑咗)|突然(單眼)?失明"),
    # Pediatric / elderly caregiver acute deterioration
    ("pediatric_deterioration", r"\b(baby|infant|toddler|child|kid|son|daughter|newborn)\b.{0,50}\b(floppy|limp|lethargic|no\s+wet\s+(?:nappies|diapers)|not\s+drinking|refusing\s+fluids|sunken\s+eyes|grunting|won'?t\s+wake|hard\s+to\s+wake|seizure|fitting|blue|stridor|bulging\s+fontanelle)\b"),
    ("pediatric_deterioration", r"(bb|嬰兒|小朋友|個仔|個女|阿仔|阿女).{0,25}(軟晒|叫唔醒|冇反應|唔肯飲水|冇尿|唔肯食奶|抽筋|發紫|吸氣凹陷|呼吸好急|喘鳴)"),
    ("elderly_deterioration", r"\b(elderly|grandma|grandpa|grandmother|grandfather|mother|father|mum|mom|dad)\b.{0,45}\b(confused|delirious|collapsed|won'?t\s+wake|unresponsive|gasping|blue\s+lips|not\s+making\s+sense|fell\s+and\s+hit)\b"),
    ("elderly_deterioration", r"(婆婆|公公|阿媽|阿爸|老人家|爺爺|嫲嫲).{0,25}(意識混亂|神志不清|胡言亂語|暈低|叫唔醒|冇反應|唞唔到氣|嘴唇發紫)"),
]

# Composite guideline red-flag detectors (NICE NG143 / HK HA A&E / RCOG / IDSA)
# 1. Calf pain / unilateral leg swelling + sudden dyspnoea / chest pain -> suspected PE
_CALF_DVT_RE = re.compile(
    r"\b(?:calf\s+(?:pain|ache|swollen|swelling|tender|tight|cramp)|"
    r"(?:left|right|one)\s+(?:calf|leg)\s+(?:is\s+)?(?:swollen|painful|hot|red|tender)|swollen\s+calf)\b|"
    r"(?:小腿(?:痛|腫|脹|紅腫)|單腳腫|一隻腳腫)",
    re.I,
)
_ACUTE_DYSPNOEA_PE_RE = re.compile(
    r"\b(?:sudden(?:ly)?\s+(?:short(?:ness)?\s+of\s+breath|breathless|dyspnoea|dyspnea|can'?t\s+breathe)|"
    r"short(?:ness)?\s+of\s+breath|breathless|dyspnoea|dyspnea|chest\s+pain|pleuritic)\b|"
    r"(?:突然(?:氣促|唞唔到氣|呼吸困難|唞氣辛苦)|氣促|唞唔到氣|呼吸困難|胸痛|心口痛)",
    re.I,
)

# 2. Neutropenic sepsis (chemotherapy / neutropenia / transplant / immunosuppressant + fever >= 38C)
# Note: use (?<![A-Za-z]) so HK code-switched CJK+EN like "食緊immunosuppressant" still matches cleanly.
_NEUTROPENIC_HOST_RE = re.compile(
    r"(?<![A-Za-z])(?:chemo(?:therapy)?|neutropeni[a-z]*|(?:bone\s+marrow|stem\s+cell|kidney|liver|organ)\s+transplant|"
    r"active\s+cancer\s+treatment|immunosuppress[a-z]*)(?![A-Za-z])|"
    r"(?:化療|中性粒細胞減少|骨髓移植|器官移植|免疫抑制劑|免疫力嚴重受抑)",
    re.I,
)
_FEVER_MENTION_RE = re.compile(
    r"(?<![A-Za-z])(?:fever|febrile|chills|rigors|high\s+temp(?:erature)?)(?![A-Za-z])|發燒|高燒|發冷|打冷震",
    re.I,
)

# 3. Febrile infant < 3 months (NICE NG143 / HK HA Paediatrics mandatory A&E sepsis workup)
_INFANT_UNDER_3M_RE = re.compile(
    r"\b(?:newborn|neonate|(?:[1-9]|1[0-2])\s*[- ]?weeks?[- ]?old\s+(?:baby|infant|son|daughter)|"
    r"(?:1|2)\s*[- ]?months?[- ]?old\s+(?:baby|infant|son|daughter)|under\s+3\s+months?\s+old)\b|"
    r"(?:初生(?:嬰兒|BB)|(?:1|2|一|兩)個月大(?:嬰兒|BB)|未滿(?:3|三)個月(?:嬰兒|BB))",
    re.I,
)

# 4. Pregnancy emergency red flags (vaginal bleeding, reduced fetal movements, severe pre-eclampsia signs)
_PREGNANT_HOST_RE = re.compile(
    r"\b(?:pregnant|pregnancy|\d+\s*weeks?\s+pregnant|expecting\s+a\s+baby)\b|懷孕|大肚|有咗BB|孕婦",
    re.I,
)
_OBSTETRIC_DANGER_RE = re.compile(
    r"\b(?:vaginal\s+bleeding|bleeding\s+down\s+below|spotting\s+heavily|"
    r"(?:reduced|decreased|less|no)\s+(?:fetal|baby)\s+movements?|baby\s+(?:stopped|not)\s+moving|"
    r"(?:severe\s+headache|blinding\s+headache).{0,35}(?:blurr?y?\s+vision|flashing\s+lights?|swollen\s+face|upper\s+abdominal|epigastric)|"
    r"pre[- ]?eclampsia|water\s+broke)\b|"
    r"(?:陰道出血|下體流血|見紅好多|胎動減少|冇胎動|BB唔郁|劇烈頭痛.{0,15}(?:視力模糊|眼花|上腹痛)|穿羊水)",
    re.I,
)

# 5. Secondary bacterial pneumonia ("double-sickening" on day 4-7 after initial viral improvement)
_DOUBLE_SICKENING_RE = re.compile(
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

# --- Prompt-Injection Detection ---
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


# --- Dataclasses ---
@dataclass
class TriageResult:
    kind: str | None  # "crisis" | "red_flag" | None
    is_emergency: bool
    urgency: int  # 1=routine, 2=priority, 3=urgent, 4=emergency
    red_flags: list[str] = field(default_factory=list)
    rules_flags: list[str] = field(default_factory=list)
    model_flags: list[str] = field(default_factory=list)
    evidence_quotes: list[str] = field(default_factory=list)
    banner: str | None = None
    banner_en: str | None = None
    banner_zh: str | None = None
    screened_by: str = "rules"
    prompt_injection_detected: bool = False
    suppressed_negations: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "is_emergency": self.is_emergency,
            "urgency": self.urgency,
            "red_flags": list(self.red_flags),
            "rules_flags": list(self.rules_flags),
            "model_flags": list(self.model_flags),
            "evidence_quotes": list(self.evidence_quotes),
            "banner": self.banner,
            "banner_en": self.banner_en,
            "banner_zh": self.banner_zh,
            "screened_by": self.screened_by,
            "prompt_injection_detected": self.prompt_injection_detected,
            "suppressed_negations": list(self.suppressed_negations),
            "audit": dict(self.audit),
        }


@dataclass
class IntakeExtraction:
    source_text: str
    lang: str
    symptoms: list[dict[str, Any]] = field(default_factory=list)
    snomed_codes: list[str] = field(default_factory=list)
    negated_symptoms: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    duration_text: str | None = None
    onset_hours: float | None = None
    comorbidities: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    current_medications: list[str] = field(default_factory=list)
    evidence_quotes: list[str] = field(default_factory=list)
    dropped_quotes: list[str] = field(default_factory=list)
    prompt_injection_detected: bool = False
    prompt_injection_matches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "lang": self.lang,
            "symptoms": list(self.symptoms),
            "snomed_codes": list(self.snomed_codes),
            "negated_symptoms": list(self.negated_symptoms),
            "observations": list(self.observations),
            "duration_text": self.duration_text,
            "onset_hours": self.onset_hours,
            "comorbidities": list(self.comorbidities),
            "allergies": list(self.allergies),
            "current_medications": list(self.current_medications),
            "evidence_quotes": list(self.evidence_quotes),
            "dropped_quotes": list(self.dropped_quotes),
            "prompt_injection_detected": self.prompt_injection_detected,
            "prompt_injection_matches": list(self.prompt_injection_matches),
        }


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


@dataclass
class DraftBundle:
    status: str
    autonomy_level: str
    clinician_signoff_required: bool
    working_diagnosis: dict[str, str]
    clinical_note: dict[str, Any]
    suggested_sick_leave: dict[str, Any]
    suggested_medications: list[dict[str, Any]]
    patient_summary_draft: str
    evidence_quotes: list[str]
    contraindications_noted: list[str] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(valid=True))
    protocol_id: str = "acute_consult_draft_v1"
    protocol_hash: str = ""
    gateway_audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "autonomy_level": self.autonomy_level,
            "clinician_signoff_required": self.clinician_signoff_required,
            "working_diagnosis": dict(self.working_diagnosis),
            "clinical_note": dict(self.clinical_note),
            "suggested_sick_leave": dict(self.suggested_sick_leave),
            "suggested_medications": list(self.suggested_medications),
            "contraindications_noted": list(self.contraindications_noted),
            "patient_summary_draft": self.patient_summary_draft,
            "evidence_quotes": list(self.evidence_quotes),
            "protocol_id": self.protocol_id,
            "protocol_hash": self.protocol_hash,
            "gateway_audit": dict(self.gateway_audit),
            "validation": {
                "valid": self.validation.valid,
                "errors": list(self.validation.errors),
                "warnings": list(self.validation.warnings),
                "checks": dict(self.validation.checks),
            },
        }


# --- Language Detection ---
def detect_language(text: str) -> str:
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
    has_en = bool(re.search(r"[A-Za-z]{3,}", text))
    if has_cjk and has_en:
        return "mixed"
    if has_cjk:
        return "zh"
    return "en"


# --- Vitals Helper ---
def _extract_numeric_vitals(text: str, vitals: dict[str, Any] | None = None) -> tuple[dict[str, float], list[dict[str, Any]]]:
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

    # Structured vitals first (authoritative device / form readings)
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
            ("dbp", "8462-4"),
        ):
            if key in vitals and vitals[key] is not None and loinc not in seen_loinc:
                val = float(vitals[key])
                if loinc == "8310-5" and val > 50.0:  # Fahrenheit passed in
                    val = round((val - 32.0) * 5.0 / 9.0, 2)
                parsed[loinc] = val
                _add_obs(loinc, val, "vitals")

    # Free-text vitals
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


# --- 1. Union-Only Bilingual Red-Flag Triage ---
def _banner_for_kind(kind: str | None, lang: str) -> tuple[str | None, str | None, str | None]:
    if kind == "crisis":
        en, zh = CRISIS_BANNER_EN, CRISIS_BANNER_ZH
        primary = zh if lang == "zh" else (CRISIS_BANNER_BILINGUAL if lang in ("mixed", "bilingual") else CRISIS_BANNER_BILINGUAL)
        return primary, en, zh
    if kind == "red_flag":
        en, zh = EMERGENCY_BANNER_EN, EMERGENCY_BANNER_ZH
        primary = zh if lang == "zh" else EMERGENCY_BANNER_BILINGUAL
        return primary, en, zh
    return None, None, None


def screen_acute_message(
    text: str,
    lang: str = "en",
    gateway: ModelGateway | None = None,
    *,
    vitals: dict[str, Any] | None = None,
    enable_model_screen: bool = True,
    kill_switch: bool = False,
    tenant_id: str = "default",
    model_id: str = "gemini-2.5-flash",
    provider: str = "vertex",
    protocol_id: str = "flu_intake_v1",
) -> TriageResult:
    """Union-only bilingual red-flag triage.

    1. Deterministic rules floor FIRST (reuses `backend.precare.safety.screen` + HK acute red flags + vitals).
    2. Optional Model Gateway screen in STRICT union-only mode: may add red flags or raise urgency,
       NEVER remove a rules-floor flag or lower urgency.
    3. Any emergency returns fixed non-LLM bilingual 999 / A&E (+ Samaritans 24h) banner and urgency=4.
    """
    raw_text = text or ""
    norm_text, suppressed = _normalize_for_rules_floor(raw_text)
    inj_detected, inj_matches = detect_prompt_injection(raw_text)

    rules_flags: list[str] = []
    evidence_quotes: list[str] = []
    rules_kind: str | None = None

    def _add_quote(q: str) -> None:
        if q and q in raw_text and q not in evidence_quotes:
            evidence_quotes.append(q)

    def _upgrade_kind(current: str | None, new_kind: str | None) -> str | None:
        return new_kind if KIND_ORDER[new_kind] > KIND_ORDER[current] else current

    # Step 1A: Reuse backend.precare.safety.screen on specificity-normalized text
    precare_kind = precare_safety.screen(norm_text)
    if precare_kind == "crisis":
        rules_kind = _upgrade_kind(rules_kind, "crisis")
        rules_flags.append("precare_crisis_floor")
        for pat in precare_safety.CRISIS:
            m = re.search(pat, norm_text, re.I)
            if m:
                _add_quote(m.group(0))
                break
    elif precare_kind == "red_flag":
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        rules_flags.append("precare_red_flag_floor")
        for pat in precare_safety.RED_FLAGS:
            m = re.search(pat, norm_text, re.I)
            if m:
                _add_quote(m.group(0))
                break
        if precare_safety.low_sugar(norm_text):
            rules_flags.append("hypoglycaemia_lt_4")

    # Step 1B: Expanded indirect crisis patterns
    for code, pat in ACUTE_CRISIS_PATTERNS:
        m = re.search(pat, norm_text, re.I)
        if m:
            rules_kind = _upgrade_kind(rules_kind, "crisis")
            if code not in rules_flags:
                rules_flags.append(code)
            _add_quote(m.group(0))

    # Step 1C: Expanded HK acute flu-season red flags
    for code, pat in ACUTE_RED_FLAG_PATTERNS:
        m = re.search(pat, norm_text, re.I)
        if m:
            rules_kind = _upgrade_kind(rules_kind, "red_flag")
            if code not in rules_flags:
                rules_flags.append(code)
            _add_quote(m.group(0))

    # Step 1D: Composite clinical guideline red flags (PE, neutropenic sepsis, febrile infant <3m, obstetric emergency, double-sickening)
    calf_m = _CALF_DVT_RE.search(norm_text)
    dys_m = _ACUTE_DYSPNOEA_PE_RE.search(norm_text)
    if calf_m and dys_m:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        if "suspected_pe_calf_dyspnoea" not in rules_flags:
            rules_flags.append("suspected_pe_calf_dyspnoea")
        _add_quote(calf_m.group(0))
        _add_quote(dys_m.group(0))

    # Step 1E: Numeric vitals thresholds (text + structured vitals — NEWS2 / HK HA A&E Cat 1-2)
    vitals_map, vitals_obs = _extract_numeric_vitals(raw_text, vitals)
    rules_urgency = 1

    temp_c = vitals_map.get("8310-5")
    has_febrile = (temp_c is not None and temp_c >= 38.0) or bool(_FEVER_MENTION_RE.search(norm_text))

    # Neutropenic sepsis: chemo / neutropenia / immunosuppressed + fever >=38C -> immediate 999/A&E
    neut_m = _NEUTROPENIC_HOST_RE.search(norm_text)
    if neut_m and has_febrile:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        if "neutropenic_sepsis_risk" not in rules_flags:
            rules_flags.append("neutropenic_sepsis_risk")
        _add_quote(neut_m.group(0))
        fm = _FEVER_MENTION_RE.search(norm_text)
        if fm:
            _add_quote(fm.group(0))

    # Febrile infant < 3 months (NICE NG143 / HK HA Paediatrics mandatory A&E sepsis workup)
    inf_m = _INFANT_UNDER_3M_RE.search(norm_text)
    if inf_m and has_febrile:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        if "febrile_infant_under_3m" not in rules_flags:
            rules_flags.append("febrile_infant_under_3m")
        _add_quote(inf_m.group(0))
        fm = _FEVER_MENTION_RE.search(norm_text)
        if fm:
            _add_quote(fm.group(0))

    # Obstetric red flag (pregnancy + bleeding / reduced fetal movement / pre-eclampsia signs)
    preg_m = _PREGNANT_HOST_RE.search(norm_text)
    ob_m = _OBSTETRIC_DANGER_RE.search(norm_text)
    if preg_m and ob_m:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        if "obstetric_red_flag" not in rules_flags:
            rules_flags.append("obstetric_red_flag")
        _add_quote(preg_m.group(0))
        _add_quote(ob_m.group(0))

    # Secondary bacterial pneumonia ("double-sickening") -> priority urgent clinician review (urgency 3)
    ds_m = _DOUBLE_SICKENING_RE.search(norm_text)
    if ds_m:
        rules_urgency = max(rules_urgency, 3)
        _add_quote(ds_m.group(0))

    spo2 = vitals_map.get("2708-6")
    if spo2 is not None:
        if spo2 < 94.0:
            rules_kind = _upgrade_kind(rules_kind, "red_flag")
            rules_flags.append("hypoxia_spo2_lt_94")
        elif spo2 == 94.0:
            rules_urgency = max(rules_urgency, 3)

    if temp_c is not None:
        if temp_c >= 40.0:
            rules_kind = _upgrade_kind(rules_kind, "red_flag")
            rules_flags.append("hyperpyrexia_temp_gte_40c")
        elif temp_c <= 35.0:
            rules_kind = _upgrade_kind(rules_kind, "red_flag")
            rules_flags.append("hypothermia_sepsis_temp_lte_35c")
        elif temp_c >= 39.5:
            rules_urgency = max(rules_urgency, 3)
        elif temp_c >= 38.5:
            rules_urgency = max(rules_urgency, 2)

    rr = vitals_map.get("9279-1")
    if rr is not None and rr >= 25.0:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        rules_flags.append("tachypnoea_rr_gte_25")

    hr = vitals_map.get("8867-4")
    if hr is not None and hr >= 130.0:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        rules_flags.append("tachycardia_hr_gte_130")

    sbp = vitals_map.get("8480-6")
    if sbp is not None and sbp < 90.0:
        rules_kind = _upgrade_kind(rules_kind, "red_flag")
        rules_flags.append("hypotension_sbp_lt_90")

    for obs in vitals_obs:
        if obs.get("quote"):
            _add_quote(obs["quote"])

    if rules_kind in ("crisis", "red_flag"):
        rules_urgency = 4

    # Step 2: Model Gateway Screen (STRICT UNION-ONLY MODE)
    model_flags: list[str] = []
    model_kind: str | None = None
    model_urgency = 1
    screened_by = f"rules:{precare_safety.SCREEN_VERSION}+hk_acute_v1"
    dropped_model_quotes: list[str] = []
    gateway_audit: dict[str, Any] = {}

    if gateway is not None and enable_model_screen and not kill_switch:
        req = GatewayRequest(
            prompt=raw_text,
            protocol_id=protocol_id,
            model_id=model_id,
            provider=provider,
            tenant_id=tenant_id,
            task_type="triage",
            tools=None,  # Explicit zero-tools isolation
            response_json=True,
            fence_untrusted_input=True,
        )
        try:
            resp = gateway.complete(req)
        except ResidencyViolationError:
            raise
        except GatewayError as exc:
            gateway_audit = {
                "provider": provider,
                "model_id": model_id,
                "protocol_id": protocol_id,
                "fallback_used": True,
                "error": str(exc)[:160],
            }
            resp = None

        if resp is not None:
            gateway_audit = {
                "provider": resp.provider,
                "model_id": resp.model_id,
                "region": resp.region,
                "protocol_id": resp.protocol_id,
                "protocol_hash": resp.protocol_hash,
                "redacted": resp.redacted,
                "fallback_used": resp.fallback_used,
                "tools_enabled": resp.tools_enabled,
                "latency_ms": resp.latency_ms,
                "validation_errors": list(resp.validation_errors),
            }
            screened_by = f"union({screened_by}, gateway:{resp.provider}:{resp.model_id})"

            parsed = resp.parsed or {}
            if isinstance(parsed, dict) and not parsed.get("fallback"):
                raw_m_kind = parsed.get("kind")
                if raw_m_kind in ("crisis", "red_flag"):
                    model_kind = raw_m_kind
                elif parsed.get("recovery_trajectory") == "emergency":
                    model_kind = "red_flag"
                raw_m_flags = parsed.get("red_flags") or []
                if isinstance(raw_m_flags, list):
                    model_flags = [str(f) for f in raw_m_flags if f]
                try:
                    raw_u = int(parsed.get("urgency", 1))
                    model_urgency = max(1, min(4, raw_u))
                except (TypeError, ValueError):
                    model_urgency = 1
                if parsed.get("recovery_trajectory") == "worsening":
                    model_urgency = max(model_urgency, 3)

                # Verify any model evidence quotes are exact verbatim substrings
                cand_quotes: list[str] = []
                if isinstance(parsed.get("evidence_quote"), str) and parsed["evidence_quote"]:
                    cand_quotes.append(parsed["evidence_quote"])
                if isinstance(parsed.get("evidence_quotes"), list):
                    cand_quotes.extend(str(q) for q in parsed["evidence_quotes"] if isinstance(q, str))
                kept_q, dropped_q = filter_verbatim_quotes(cand_quotes, raw_text)
                dropped_model_quotes.extend(dropped_q)
                for q in kept_q:
                    _add_quote(q)

                if model_kind in ("crisis", "red_flag"):
                    model_urgency = 4

    # UNION-ONLY MERGE: model can ONLY add flags or raise urgency, NEVER suppress or lower
    final_kind = _upgrade_kind(rules_kind, model_kind)
    final_flags = list(dict.fromkeys([*rules_flags, *model_flags]))
    final_urgency = max(rules_urgency, model_urgency)
    if final_kind in ("crisis", "red_flag"):
        final_urgency = 4
    elif final_urgency == 4:
        final_kind = "red_flag"

    is_emergency = final_kind in ("crisis", "red_flag")
    eff_lang = detect_language(raw_text) if lang == "auto" else lang
    banner, banner_en, banner_zh = _banner_for_kind(final_kind, eff_lang)

    return TriageResult(
        kind=final_kind,
        is_emergency=is_emergency,
        urgency=final_urgency,
        red_flags=final_flags,
        rules_flags=list(dict.fromkeys(rules_flags)),
        model_flags=list(dict.fromkeys(model_flags)),
        evidence_quotes=evidence_quotes,
        banner=banner,
        banner_en=banner_en,
        banner_zh=banner_zh,
        screened_by=screened_by,
        prompt_injection_detected=inj_detected,
        suppressed_negations=suppressed,
        audit={
            "gateway": gateway_audit,
            "dropped_model_quotes": dropped_model_quotes,
            "prompt_injection_matches": inj_matches,
        },
    )


# --- 2. Structured Bilingual Symptom & Observation Extraction ---
# Symptom patterns mapped strictly to official SNOMED_SYMPTOMS codes
_SYMPTOM_PATTERNS: dict[str, list[str]] = {
    "386661006": [r"(?<![A-Za-z])fever(?![A-Za-z])", r"(?<![A-Za-z])febrile(?![A-Za-z])", r"(?<![A-Za-z])high\s+temp(?:erature)?(?![A-Za-z])", r"發燒", r"低燒", r"高燒", r"燒到", r"發熱"],
    "49727002": [r"(?<![A-Za-z])cough(?:ing)?(?![A-Za-z])", r"咳嗽", r"乾咳", r"有痰咳", r"咳到", r"咳"],
    "162397003": [r"(?<![A-Za-z])sore\s+throat(?![A-Za-z])", r"(?<![A-Za-z])throat\s+(?:pain|hurts?|aching)(?![A-Za-z])", r"喉嚨痛", r"咽喉痛", r"喉嚨痕痛"],
    "64531003": [r"(?<![A-Za-z])runny\s+nose(?![A-Za-z])", r"(?<![A-Za-z])nasal\s+(?:discharge|congestion)(?![A-Za-z])", r"(?<![A-Za-z])stuffy\s+nose(?![A-Za-z])", r"(?<![A-Za-z])blocked\s+nose(?![A-Za-z])", r"流鼻水", r"鼻水", r"鼻塞"],
    "68962001": [r"(?<![A-Za-z])body\s+aches?(?![A-Za-z])", r"(?<![A-Za-z])muscle\s+aches?(?![A-Za-z])", r"(?<![A-Za-z])myalgia(?![A-Za-z])", r"(?<![A-Za-z])aching\s+all\s+over(?![A-Za-z])", r"周身骨痛", r"肌肉痠痛", r"肌肉痛", r"骨痛"],
    "25064002": [r"(?<![A-Za-z])headache(?![A-Za-z])", r"(?<![A-Za-z])head\s+hurts?(?![A-Za-z])", r"頭痛", r"頭赤"],
    "84229001": [r"(?<![A-Za-z])fatigue(?![A-Za-z])", r"(?<![A-Za-z])tired(?![A-Za-z])", r"(?<![A-Za-z])exhausted(?![A-Za-z])", r"(?<![A-Za-z])malaise(?![A-Za-z])", r"疲倦", r"好攰", r"冇精神", r"全身無力"],
    "422400008": [r"(?<![A-Za-z])vomit(?:ing|ed)?(?![A-Za-z])", r"(?<![A-Za-z])threw\s+up(?![A-Za-z])", r"(?<![A-Za-z])throwing\s+up(?![A-Za-z])", r"嘔吐", r"嘔咗"],
    "62315008": [r"(?<![A-Za-z])diarrh?oea(?![A-Za-z])", r"(?<![A-Za-z])loose\s+stools?(?![A-Za-z])", r"(?<![A-Za-z])watery\s+stools?(?![A-Za-z])", r"腹瀉", r"肚瀉", r"屙水"],
    # Red-flag SNOMED findings
    "267036007": [r"(?<![A-Za-z])short(?:ness)?\s+of\s+breath(?![A-Za-z])", r"(?<![A-Za-z])breathless(?![A-Za-z])", r"(?<![A-Za-z])dyspnoea(?![A-Za-z])", r"(?<![A-Za-z])can'?t\s+breathe(?![A-Za-z])", r"呼吸困難", r"氣促", r"唞唔到氣"],
    "29857009": [r"(?<![A-Za-z])chest\s+pain(?![A-Za-z])", r"(?<![A-Za-z])chest\s+tight(?:ness)?(?![A-Za-z])", r"胸痛", r"心口痛", r"胸口痛"],
    "40917007": [r"(?<![A-Za-z])confus(?:ed|ion)(?![A-Za-z])", r"(?<![A-Za-z])disoriented(?![A-Za-z])", r"意識混亂", r"神志不清"],
    "66857006": [r"(?<![A-Za-z])cough(?:ing|ed)?\s+(?:up\s+)?(?:\w+\s+){0,3}blood(?![A-Za-z])", r"(?<![A-Za-z])haemoptysis(?![A-Za-z])", r"咳血"],
    "3415004": [r"(?<![A-Za-z])blue\s+lips(?![A-Za-z])", r"(?<![A-Za-z])lips\s+(?:have\s+)?turned\s+blue(?![A-Za-z])", r"(?<![A-Za-z])cyanosis(?![A-Za-z])", r"嘴唇發紫", r"發紺"],
    "39579001": [r"(?<![A-Za-z])anaphylax\w*(?![A-Za-z])", r"(?<![A-Za-z])swollen\s+(?:lips|tongue|throat)(?![A-Za-z])", r"嚴重過敏", r"喉嚨腫"],
    "271594007": [r"(?<![A-Za-z])faint(?:ed|ing)?(?![A-Za-z])", r"(?<![A-Za-z])passed\s+out(?![A-Za-z])", r"(?<![A-Za-z])syncope(?![A-Za-z])", r"暈厥", r"暈低", r"暈倒"],
    "161882006": [r"(?<![A-Za-z])stiff\s+neck(?![A-Za-z])", r"頸部僵硬", r"條頸好硬"],
    "6471006": [r"(?<![A-Za-z])suicid\w*(?![A-Za-z])", r"(?<![A-Za-z])want\s+to\s+die(?![A-Za-z])", r"自殺", *precare_safety.CRISIS, *[p for _, p in ACUTE_CRISIS_PATTERNS]],
}

_RAT_PATTERNS: list[tuple[str, str, str]] = [
    # (loinc_code, value_text, regex)
    ("80382-5", "positive", r"(?:flu\s*a|influenza\s*a)[^.,;!?，。]{0,30}(?:positive|\+|(?<![A-Za-z])pos(?![A-Za-z]))|(?:rat|rapid[^.,;!?]{0,12}|快測)[^.,;!?，。]{0,20}(?:flu\s*a|influenza\s*a)[^.,;!?，。]{0,25}(?:positive|\+|(?<![A-Za-z])pos(?![A-Za-z]))|(?:甲型流感|甲流)(?:快測|快速測試)?[^.,;!?，。]{0,8}(?:陽性|中咗|兩條線|\+)"),
    ("80382-5", "negative", r"(?:flu\s*a?|influenza\s*a?)[^.,;!?，。]{0,30}(?:negative|(?<![A-Za-z])neg(?![A-Za-z])|陰性|一條線)|(?:甲型流感|甲流|流感)(?:快測|快速測試)[^.,;!?，。]{0,8}(?:陰性|一條線)"),
    ("80383-3", "positive", r"(?:flu\s*b|influenza\s*b)[^.,;!?，。]{0,30}(?:positive|\+|(?<![A-Za-z])pos(?![A-Za-z]))|(?:乙型流感|乙流)(?:快測|快速測試)?[^.,;!?，。]{0,8}(?:陽性|中咗|兩條線|\+)"),
    ("80383-3", "negative", r"(?:flu\s*b|influenza\s*b)[^.,;!?，。]{0,30}(?:negative|(?<![A-Za-z])neg(?![A-Za-z])|陰性|一條線)|(?:乙型流感|乙流)(?:快測|快速測試)?[^.,;!?，。]{0,8}(?:陰性|一條線)"),
    ("94558-4", "positive", r"(?:covid(?:-?19)?|sars-cov-2)[^.,;!?，。]{0,30}(?:positive|\+|(?<![A-Za-z])pos(?![A-Za-z]))|(?:新冠|covid)(?:快測|快速測試)?[^.,;!?，。]{0,8}(?:陽性|中咗|兩條線|\+)"),
    ("94558-4", "negative", r"(?:covid(?:-?19)?|sars-cov-2)[^.,;!?，。]{0,30}(?:negative|(?<![A-Za-z])neg(?![A-Za-z])|陰性|一條線)|(?:新冠|covid)(?:快測|快速測試)?[^.,;!?，。]{0,8}(?:陰性|一條線)"),
]

_DURATION_PATTERNS: list[tuple[str, Any]] = [
    (r"(?:for\s+|since\s+|started\s+|past\s+)?(\d{1,2})\s*(?:hours?|hrs?)(?:\s+ago)?", lambda m: float(m.group(1))),
    (r"(?:for\s+|past\s+|started\s+)?(\d{1,2})\s*days?(?:\s+ago)?", lambda m: float(m.group(1)) * 24.0),
    (r"(?:since\s+)?(yesterday|last\s+night|this\s+morning|today)", lambda m: 24.0 if "yesterday" in m.group(1).lower() else 12.0),
    (r"(\d{1,2})\s*(?:小時|個鐘)(?:前)?", lambda m: float(m.group(1))),
    (r"(\d{1,2}|一|兩|二|三|四|五|六|七)\s*(?:日|天)(?:前|了|啦|喇)?", lambda m: float({"一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}.get(m.group(1), m.group(1))) * 24.0),
    (r"(尋日|琴日|琴晚|昨晚|今朝|今日)(?:開始)?", lambda m: 24.0 if m.group(1) in ("尋日", "琴日") else 12.0),
]

_COMORBIDITY_PATTERNS: dict[str, str] = {
    "asthma": r"\basthma\b|哮喘",
    "diabetes": r"\bdiabet(?:es|ic)\b|\bdm\b|糖尿病",
    "hypertension": r"\bhypertension\b|\bhigh\s+blood\s+pressure\b|高血壓",
    "copd": r"\bcopd\b|慢阻肺",
    "heart_disease": r"\b(?:heart\s+disease|coronary|atrial\s+fibrillation|heart\s+failure)\b|心臟病",
    "ckd": r"\b(?:ckd|kidney\s+disease|renal\s+impairment)\b|腎病",
    "pregnancy": r"\bpregnant\b|\bpregnancy\b|懷孕|有咗bb",
    "g6pd_deficiency": r"\bg6pd\b|蠶豆症",
    "immunocompromised": r"\b(?:immunocompromised|chemotherapy|transplant)\b|免疫力低下|化療",
}

_ALLERGY_EXTRACT_PATTERNS: list[tuple[str, str]] = [
    ("penicillin", r"(?:allerg\w*\s+to|敏感|過敏)[^.,;!?]{0,20}(?:penicillin|amoxicillin|augmentin|盤尼西林|青黴素|阿莫西林)|(?:penicillin|盤尼西林|青黴素)\s*(?:allergy|敏感|過敏)"),
    ("nsaid", r"(?:allerg\w*\s+to|敏感|過敏)[^.,;!?]{0,20}(?:nsaid|ibuprofen|naproxen|aspirin|布洛芬|阿士匹靈|消炎止痛藥)|(?:nsaid|ibuprofen|aspirin|布洛芬|阿士匹靈)\s*(?:allergy|敏感|過敏)"),
    ("paracetamol", r"(?:allerg\w*\s+to|敏感|過敏)[^.,;!?]{0,20}(?:paracetamol|panadol|acetaminophen|撲熱息痛|必理痛)|(?:paracetamol|panadol|撲熱息痛|必理痛)\s*(?:allergy|敏感|過敏)"),
    ("sulfonamide", r"(?:allerg\w*\s+to|敏感|過敏)[^.,;!?]{0,20}(?:sulfa|sulfonamide|bactrim|co-trimoxazole|磺胺)|(?:sulfa|磺胺)\s*(?:allergy|敏感|過敏)"),
    ("oseltamivir", r"(?:allerg\w*\s+to|敏感|過敏)[^.,;!?]{0,20}(?:oseltamivir|tamiflu|特敏福|奧司他韋)"),
    ("antihistamine", r"(?:allerg\w*\s+to|敏感|過敏)[^.,;!?]{0,20}(?:cetirizine|chlorpheniramine|antihistamine|抗組織胺)"),
]


def extract_intake(
    text: str,
    vitals: dict[str, Any] | None = None,
    *,
    lang: str = "auto",
    gateway: ModelGateway | None = None,
    tenant_id: str = "default",
    model_id: str = "gemini-2.5-flash",
    provider: str = "vertex",
) -> IntakeExtraction:
    """Extract structured bilingual clinical intake with SNOMED/LOINC/UCUM codes and verbatim quotes."""
    raw_text = text or ""
    detected_lang = detect_language(raw_text) if lang == "auto" else lang
    norm_text, suppressed = _normalize_for_rules_floor(raw_text)
    inj_detected, inj_matches = detect_prompt_injection(raw_text)

    evidence_quotes: list[str] = []
    dropped_quotes: list[str] = []

    def _add_quote(q: str) -> None:
        if q and q in raw_text and q not in evidence_quotes:
            evidence_quotes.append(q)

    # 1. Negated symptoms noted
    negated_symptoms = [s.split(":", 1)[1] for s in suppressed if s.startswith("negation:")]

    # 2. Extract present symptoms (scanning clause-negation-masked text so negated symptoms are not added)
    symptoms: list[dict[str, Any]] = []
    snomed_codes: list[str] = []
    for code, pats in _SYMPTOM_PATTERNS.items():
        for pat in pats:
            m = re.search(pat, norm_text, re.I)
            if m:
                q = m.group(0)
                # Ensure verbatim quote from raw_text
                if q not in raw_text:
                    m_raw = re.search(pat, raw_text, re.I)
                    q = m_raw.group(0) if m_raw else ""
                if q and q in raw_text:
                    disp_en, disp_zh = SNOMED_SYMPTOMS[code]
                    symptoms.append({
                        "snomed_code": code,
                        "display": disp_en,
                        "display_zh": disp_zh,
                        "quote": q,
                    })
                    snomed_codes.append(code)
                    _add_quote(q)
                    break

    # 3. Extract LOINC + UCUM vitals & RAT observations
    _, observations = _extract_numeric_vitals(raw_text, vitals)
    seen_rat: set[str] = set()

    # Structured RAT inputs in vitals dict if provided
    if vitals:
        for key, loinc in (("rat_flu_a", "80382-5"), ("rat_flu_b", "80383-3"), ("rat_covid", "94558-4")):
            if key in vitals and vitals[key] is not None:
                val = str(vitals[key]).strip().lower()
                if val in ("positive", "negative", "pos", "neg", "+", "-"):
                    norm_val = "positive" if val in ("positive", "pos", "+") else "negative"
                    meta = LOINC_OBSERVATIONS[loinc]
                    observations.append({
                        "loinc_code": loinc,
                        "display": meta[0],
                        "display_zh": meta[1],
                        "ucum_unit": meta[2],
                        "value_text": norm_val,
                        "source": "vitals",
                    })
                    seen_rat.add(loinc)

    for loinc, val_text, pat in _RAT_PATTERNS:
        if loinc in seen_rat:
            continue
        m = re.search(pat, raw_text, re.I)
        if m:
            meta = LOINC_OBSERVATIONS[loinc]
            q = m.group(0)
            observations.append({
                "loinc_code": loinc,
                "display": meta[0],
                "display_zh": meta[1],
                "ucum_unit": meta[2],
                "value_text": val_text,
                "source": "text",
                "quote": q,
            })
            seen_rat.add(loinc)
            _add_quote(q)

    # Also ensure fever SNOMED if objective temp >= 37.8 Cel recorded
    for obs in observations:
        if obs.get("quote"):
            _add_quote(obs["quote"])
        if obs["loinc_code"] == "8310-5" and float(obs.get("value_num", 0)) >= 37.8 and "386661006" not in snomed_codes:
            q = obs.get("quote") or ""
            if q and q in raw_text:
                disp_en, disp_zh = SNOMED_SYMPTOMS["386661006"]
                symptoms.append({
                    "snomed_code": "386661006",
                    "display": disp_en,
                    "display_zh": disp_zh,
                    "quote": q,
                })
                snomed_codes.append("386661006")

    # 4. Duration / onset
    duration_text: str | None = None
    onset_hours: float | None = None
    for pat, hrs_fn in _DURATION_PATTERNS:
        m = re.search(pat, raw_text, re.I)
        if m:
            duration_text = m.group(0)
            onset_hours = float(hrs_fn(m))
            _add_quote(duration_text)
            break

    # 5. Comorbidities
    comorbidities: list[str] = []
    for name, pat in _COMORBIDITY_PATTERNS.items():
        m = re.search(pat, norm_text, re.I)
        if m:
            comorbidities.append(name)
            _add_quote(m.group(0))

    # 6. Allergies
    allergies: list[str] = []
    for grp, pat in _ALLERGY_EXTRACT_PATTERNS:
        m = re.search(pat, raw_text, re.I)
        if m:
            allergies.append(grp)
            _add_quote(m.group(0))

    # 7. Current medications (skip drugs mentioned only inside prompt-injection commands)
    med_scan_text = raw_text
    for inj_m in inj_matches:
        med_scan_text = med_scan_text.replace(inj_m, " ")

    current_meds: list[str] = []
    for drug_key in HK_ACUTE_FORMULARY:
        if re.search(rf"\b{re.escape(drug_key)}\b", med_scan_text, re.I):
            # Don't confuse allergy mention with current medication
            if drug_key not in allergies and not re.search(rf"allerg\w*[^.,;!?]{{0,20}}{re.escape(drug_key)}", med_scan_text, re.I):
                current_meds.append(drug_key)
    for alias in ("panadol", "必理痛", "撲熱息痛", "brufen", "nurofen", "tamiflu", "特敏福", "augmentin", "zyrtec", "piriton"):
        if re.search(re.escape(alias), med_scan_text, re.I):
            found = lookup_drug(alias)
            if found and found[0] not in current_meds and found[0] not in allergies:
                current_meds.append(found[0])
    for pats in (precare_safety.GLUCOSE_LOWERING, precare_safety.ANTITHROMBOTIC):
        hit = precare_safety.mentions(pats, med_scan_text)
        if hit and hit.lower() not in current_meds and hit.lower() not in allergies:
            current_meds.append(hit.lower())

    # 8. Optional Model Gateway enrichment (strict zero tools + verbatim quote filter)
    if gateway is not None:
        req = GatewayRequest(
            prompt=raw_text,
            protocol_id="flu_intake_v1",
            model_id=model_id,
            provider=provider,
            tenant_id=tenant_id,
            task_type="extraction",
            tools=None,
            response_json=True,
            fence_untrusted_input=True,
        )
        resp = gateway.complete(req)
        parsed = resp.parsed or {}
        if isinstance(parsed, dict) and not parsed.get("fallback"):
            # Filter any LLM-proposed quotes strictly to verbatim substrings
            raw_quotes = parsed.get("evidence_quotes") or []
            if isinstance(raw_quotes, list):
                kept, dropped = filter_verbatim_quotes([str(q) for q in raw_quotes if isinstance(q, str)], raw_text)
                dropped_quotes.extend(dropped)
                for q in kept:
                    _add_quote(q)

            # Merge LLM-extracted symptoms ONLY if valid SNOMED AND verbatim quote
            for sym in parsed.get("symptoms") or []:
                if not isinstance(sym, dict):
                    continue
                code = str(sym.get("snomed_code") or "")
                q = str(sym.get("quote") or "")
                if q and q not in raw_text:
                    dropped_quotes.append(q)
                    continue
                if code in SNOMED_SYMPTOMS and code not in snomed_codes and q and q in raw_text:
                    disp_en, disp_zh = SNOMED_SYMPTOMS[code]
                    symptoms.append({
                        "snomed_code": code,
                        "display": disp_en,
                        "display_zh": disp_zh,
                        "quote": q,
                    })
                    snomed_codes.append(code)
                    _add_quote(q)

    return IntakeExtraction(
        source_text=raw_text,
        lang=detected_lang,
        symptoms=symptoms,
        snomed_codes=snomed_codes,
        negated_symptoms=negated_symptoms,
        observations=observations,
        duration_text=duration_text,
        onset_hours=onset_hours,
        comorbidities=comorbidities,
        allergies=allergies,
        current_medications=current_meds,
        evidence_quotes=evidence_quotes,
        dropped_quotes=dropped_quotes,
        prompt_injection_detected=inj_detected,
        prompt_injection_matches=inj_matches,
    )


# --- 3. Consult Draft Proposal & Deterministic Validators ---
_ACUTE_BANNED_OUTBOUND_PATTERNS: list[tuple[str, str]] = [
    ("autonomous_sick_leave", r"\bsick[- ]?leave\s+(?:has\s+been|is|was)\s+(?:issued|approved|granted|signed)\b"),
    ("autonomous_sick_leave", r"\b(?:issued|approved|granted)\s+(?:you\s+)?(?:\d+\s*days?\s+)?(?:sick[- ]?leave|medical\s+certificate)\b"),
    ("autonomous_sick_leave", r"已(?:發出|批出|簽發)(?:\d+日)?病假"),
    ("autonomous_prescription", r"\bprescription\s+(?:has\s+been|is|was)\s+(?:issued|approved|signed|dispensed)\b"),
    ("autonomous_prescription", r"\b(?:prescribed|approved)\s+(?:you\s+)?(?:amoxicillin|augmentin|oseltamivir|antibiotics?|codeine)\b"),
    ("autonomous_prescription", r"已(?:處方|批出處方|開藥)"),
    ("diagnostic_certainty", r"\byou\s+definitely\s+have\b|\bconfirmed\s+diagnosis\b"),
]


def _select_working_diagnosis(intake: dict[str, Any]) -> dict[str, str]:
    snomed = set(intake.get("snomed_codes") or [])
    obs_list = intake.get("observations") or []

    flu_rat_pos = any(
        o.get("loinc_code") in ("80382-5", "80383-3") and o.get("value_text") == "positive"
        for o in obs_list
    )
    if flu_rat_pos:
        return dict(HK_DIAGNOSES["J10.1"])

    has_fever = "386661006" in snomed
    has_cough = "49727002" in snomed
    has_throat = "162397003" in snomed
    has_nose = "64531003" in snomed
    has_myalgia = "68962001" in snomed
    has_gi = ("422400008" in snomed or "62315008" in snomed) and not (has_cough or has_throat or has_nose)

    if has_fever and (has_myalgia or has_cough):
        return dict(HK_DIAGNOSES["J11.1"])
    if has_gi:
        return dict(HK_DIAGNOSES["A09"])
    if has_throat and not has_cough and not has_nose:
        return dict(HK_DIAGNOSES["J02.9"])
    if has_cough and not has_nose and not has_throat:
        return dict(HK_DIAGNOSES["J20.9"])
    if has_fever and not (has_cough or has_throat or has_nose):
        return dict(HK_DIAGNOSES["R50.9"])
    return dict(HK_DIAGNOSES["J06.9"])


def _normalized_allergy_groups(patient: dict[str, Any], intake: dict[str, Any]) -> set[str]:
    raw_allergies: list[str] = []
    for src in (patient.get("allergies") or [], intake.get("allergies") or []):
        if isinstance(src, str):
            raw_allergies.append(src)
        else:
            raw_allergies.extend(str(a) for a in src)

    groups: set[str] = set()
    for a in raw_allergies:
        low = a.strip().lower()
        if not low or low in ("none", "nkda", "no known drug allergies"):
            continue
        groups.add(low)
        found = lookup_drug(low)
        if found:
            groups.add(found[0])
            groups.add(str(found[1].get("allergy_group", "")).lower())
        if any(k in low for k in ("nsaid", "ibuprofen", "naproxen", "aspirin")):
            groups.add("nsaid")
        if any(k in low for k in ("penicillin", "amoxicillin", "augmentin", "beta_lactam")):
            groups.add("penicillin")
        if any(k in low for k in ("sulfa", "bactrim", "co-trimoxazole")):
            groups.add("sulfonamide")
        if any(k in low for k in ("paracetamol", "panadol", "acetaminophen")):
            groups.add("paracetamol")
    return groups


def _is_drug_safe_for_patient(
    drug_key: str,
    *,
    allergy_groups: set[str],
    g6pd_deficient: bool,
    age: int | None,
) -> tuple[bool, str | None]:
    meta = HK_ACUTE_FORMULARY.get(drug_key)
    if meta is None:
        return False, f"Drug '{drug_key}' not in HK acute formulary"
    if meta.get("hk_schedule") == "DANGEROUS_DRUG":
        return False, f"Cap. 134 Dangerous Drug '{drug_key}' is hard-blocked in acute teleconsult"
    allergy_grp = str(meta.get("allergy_group", "")).lower()
    if drug_key in allergy_groups or allergy_grp in allergy_groups:
        return False, f"Excluded '{drug_key}' due to documented {allergy_grp} allergy"
    if g6pd_deficient and meta.get("g6pd_risk"):
        return False, f"Excluded '{drug_key}' due to Hong Kong G6PD deficiency risk"
    if age is not None and age < 16 and meta.get("pediatric_contraindicated"):
        return False, f"Excluded '{drug_key}' due to pediatric contraindication (age < 16)"
    return True, None


def draft_consult_proposal(
    intake: IntakeExtraction | dict[str, Any],
    patient: dict[str, Any],
    gateway: ModelGateway | None = None,
    *,
    lang: str | None = None,
    tenant_id: str = "default",
    model_id: str = "gemini-2.5-flash",
    provider: str = "vertex",
) -> DraftBundle:
    """Produce an L1 clinician-reviewed acute consult draft bundle and run all deterministic validators."""
    intake_dict = intake.to_dict() if isinstance(intake, IntakeExtraction) else dict(intake)
    source_text = str(intake_dict.get("source_text") or "")
    eff_lang = lang or str(patient.get("preferred_lang") or intake_dict.get("lang") or "en")
    if eff_lang == "mixed":
        eff_lang = "zh"

    bundle_proto: ProtocolBundle | None = None
    try:
        bundle_proto = load_protocol("acute_consult_draft_v1")
    except Exception:
        bundle_proto = None

    working_dx = _select_working_diagnosis(intake_dict)
    snomed_codes = list(intake_dict.get("snomed_codes") or [])
    observations = list(intake_dict.get("observations") or [])
    evidence_quotes, _ = filter_verbatim_quotes(list(intake_dict.get("evidence_quotes") or []), source_text)

    # Patient safety attributes & high-risk host stratification (HK CHP / CDC / NICE)
    allergy_groups = _normalized_allergy_groups(patient, intake_dict)
    all_comorbs = set(intake_dict.get("comorbidities") or []) | set(patient.get("comorbidities") or [])
    g6pd_deficient = bool(patient.get("g6pd_deficient")) or ("g6pd_deficiency" in all_comorbs)
    pregnancy_detected = (
        bool(patient.get("pregnant"))
        or ("pregnancy" in all_comorbs)
        or bool(_PREGNANT_HOST_RE.search(source_text))
    )
    age = int(patient["age"]) if patient.get("age") is not None else None
    if age is None and patient.get("dob"):
        try:
            dob_year = int(str(patient["dob"])[:4])
            from datetime import date as _date
            age = max(0, _date.today().year - dob_year)
        except Exception:
            age = None
    egfr = float(patient["egfr"]) if patient.get("egfr") is not None else None

    high_risk_factors = sorted(
        all_comorbs & {"asthma", "diabetes", "copd", "heart_disease", "ckd", "pregnancy", "immunocompromised"}
    )
    if pregnancy_detected and "pregnancy" not in high_risk_factors:
        high_risk_factors.append("pregnancy")
    if age is not None and age >= 65:
        high_risk_factors.append("age_gte_65")
    if age is not None and age < 5:
        high_risk_factors.append("child_lt_5")
    high_risk_comorb = bool(high_risk_factors)
    double_sickening_flag = bool(_DOUBLE_SICKENING_RE.search(source_text))

    contraindications_noted: list[str] = []
    if g6pd_deficient:
        contraindications_noted.append("G6PD deficiency documented: avoid aspirin, sulfonamides (co-trimoxazole), nitrofurantoin.")
    if allergy_groups:
        contraindications_noted.append(f"Allergies documented: {', '.join(sorted(allergy_groups))}.")
    if pregnancy_detected:
        contraindications_noted.append("Pregnancy documented: avoid NSAIDs (ibuprofen/naproxen/aspirin), pseudoephedrine, and co-trimoxazole.")

    # Suggested safe OTC / antiviral draft proposals (never Cap. 134, never autonomously issued)
    suggested_meds: list[dict[str, Any]] = []

    def _propose_drug(
        drug_key: str,
        dose_guidance: str,
        rationale: str,
        *,
        dose_mg: int,
        frequency: str,
        days: int,
    ) -> None:
        if pregnancy_detected and drug_key in ("ibuprofen", "naproxen", "aspirin", "pseudoephedrine", "co-trimoxazole"):
            msg = f"Excluded '{drug_key}' due to documented pregnancy contraindication."
            if msg not in contraindications_noted:
                contraindications_noted.append(msg)
            return
        safe, reason = _is_drug_safe_for_patient(
            drug_key,
            allergy_groups=allergy_groups,
            g6pd_deficient=g6pd_deficient,
            age=age,
        )
        if not safe:
            if reason and reason not in contraindications_noted:
                contraindications_noted.append(reason)
            return
        meta = HK_ACUTE_FORMULARY[drug_key]
        suggested_meds.append({
            "drug": drug_key,
            "rxnorm": meta["rxnorm"],
            "atc": meta["atc"],
            "display": meta["display"],
            "display_zh": meta["display_zh"],
            "hk_schedule": meta["hk_schedule"],
            "dose_mg": dose_mg,
            "frequency": frequency,
            "days": days,
            "dose_guidance": dose_guidance,
            "rationale": rationale,
            "status": "PROPOSED_DRAFT_ONLY",
            "clinician_signoff_required": True,
        })

    # Analgesic / antipyretic for fever, myalgia, headache, or sore throat
    if any(c in snomed_codes for c in ("386661006", "68962001", "25064002", "162397003")):
        safe_para, _ = _is_drug_safe_for_patient(
            "paracetamol", allergy_groups=allergy_groups, g6pd_deficient=g6pd_deficient, age=age
        )
        if safe_para:
            _propose_drug(
                "paracetamol",
                "500mg-1000mg PO q4-6h PRN (max 4000mg/day; weight-based 15mg/kg if pediatric)",
                "First-line symptomatic antipyretic/analgesic for acute febrile illness (safe in G6PD & pregnancy)",
                dose_mg=500,
                frequency="QDS",
                days=3,
            )
        else:
            contraindications_noted.append("Excluded 'paracetamol' due to documented paracetamol allergy")
            _propose_drug(
                "ibuprofen",
                "400mg PO TDS PRN with food (max 1200-2400mg/day; verify no GI/renal/asthma/pregnancy contraindication)",
                "Alternative antipyretic/analgesic (paracetamol allergic)",
                dose_mg=400,
                frequency="TDS",
                days=3,
            )

    # Antiviral guideline evaluation (HK CHP / IDSA / CDC): Oseltamivir within 48h or high-risk host
    onset_hours = intake_dict.get("onset_hours")
    is_influenza_dx = working_dx["icd10"] in ("J10.1", "J11.1")
    if working_dx["icd10"] == "J10.1":
        if onset_hours is None or onset_hours <= 48.0:
            oseltamivir_window = "eligible_within_48h"
        elif high_risk_comorb:
            oseltamivir_window = "eligible_high_risk_beyond_48h"
        else:
            oseltamivir_window = "outside_48h_low_risk"
    elif is_influenza_dx:
        oseltamivir_window = "consider_if_confirmed_or_high_risk"
    else:
        oseltamivir_window = "not_indicated"

    if working_dx["icd10"] == "J10.1" and oseltamivir_window in ("eligible_within_48h", "eligible_high_risk_beyond_48h"):
        osel_dose, osel_freq = 75, "BD"
        dose_note = "75mg PO BD x 5 days (HK CHP / IDSA guideline; clinician review required)"
        if egfr is not None and egfr < 30.0:
            osel_dose, osel_freq = 30, "OD"
            dose_note = "Severe renal adjustment (eGFR < 30): 30mg PO OD x 5 days (clinician verify)"
            contraindications_noted.append(f"Severe renal impairment (eGFR={egfr}): reduce oseltamivir to 30mg OD.")
        elif egfr is not None and egfr < 60.0:
            osel_dose, osel_freq = 30, "BD"
            dose_note = "Moderate renal adjustment (eGFR 30-59): 30mg PO BD x 5 days (clinician verify)"
            contraindications_noted.append(f"Renal impairment (eGFR={egfr}): reduce oseltamivir to 30mg BD (<=75mg/day).")
        _propose_drug(
            "oseltamivir",
            dose_note,
            f"Confirmed seasonal influenza ({oseltamivir_window.replace('_', ' ')})",
            dose_mg=osel_dose,
            frequency=osel_freq,
            days=5,
        )

    # Non-sedating antihistamine for nasal discharge
    if "64531003" in snomed_codes:
        _propose_drug(
            "cetirizine",
            "10mg PO daily PRN",
            "Symptomatic relief of rhinorrhoea / nasal symptoms",
            dose_mg=10,
            frequency="QD",
            days=5,
        )

    # Suggested sick-leave days (1-2 days for flu/URTI; strictly proposal only)
    temp_val = next((float(o["value_num"]) for o in observations if o.get("loinc_code") == "8310-5" and o.get("value_num") is not None), None)
    if working_dx["icd10"] in ("J10.1", "J11.1") and (temp_val is None or temp_val >= 38.0 or "386661006" in snomed_codes):
        sl_days = 2
        sl_rationale = "Proposed 2 days rest for suspected/confirmed acute influenza pending MCHK clinician assessment."
    else:
        sl_days = 1
        sl_rationale = "Proposed 1 day rest for acute URTI/viral illness pending MCHK clinician assessment."

    suggested_sick_leave = {
        "days": sl_days,
        "days_off": sl_days,
        "rationale": sl_rationale,
        "status": "PROPOSED_DRAFT_ONLY",
        "autonomously_issued": False,
        "clinician_signoff_required": True,
    }

    # Guideline-driven clinical decision support (HK CHP IMPACT / NICE NG84 / IDSA)
    abx_stewardship = (
        "HK CHP IMPACT / NICE NG84: Viral respiratory illness — routine empirical antibiotics are NOT indicated "
        "unless secondary bacterial infection (e.g. double-sickening pneumonia, Centor/FeverPAIN >=4) is confirmed."
        if working_dx["icd10"] in ("J10.1", "J11.1", "J06.9", "J20.9")
        else "HK CHP IMPACT: Verify bacterial indication and local susceptibility before any antibiotic prescribing."
    )
    if double_sickening_flag:
        abx_stewardship = (
            "PRIORITY REVIEW — Double-sickening pattern detected (worsening after initial improvement): "
            "clinician to evaluate for secondary bacterial pneumonia (auscultation / CXR / face-to-face referral)."
        )

    clinical_decision_support = {
        "oseltamivir_window": oseltamivir_window,
        "onset_hours": onset_hours,
        "antibiotic_stewardship": abx_stewardship,
        "high_risk_host_factors": high_risk_factors,
        "pregnancy_detected": pregnancy_detected,
        "double_sickening_suspected": double_sickening_flag,
        "safety_netting_en": (
            "Seek immediate 999 / A&E care for breathlessness, chest pain, SpO2 <94%, confusion, persistent fever >72h, "
            "or sudden worsening after initial improvement (days 4-7)."
        ),
        "safety_netting_zh": (
            "如出現氣促、心口痛、血氧低於94%、意識混亂、持續高燒超過72小時，或好轉後突然再度惡化，請立即致電999或前往急症室。"
        ),
    }

    # Structured SOAP clinical note
    sym_displays = [s.get("display", "") for s in (intake_dict.get("symptoms") or [])]
    obs_summary = [
        f"{o['display']} ({o['loinc_code']}): {o.get('value_num', o.get('value_text', ''))} {o['ucum_unit']}"
        for o in observations
    ]
    clinical_note: dict[str, Any] = {
        "subjective": (
            f"Symptoms reported: {', '.join(sym_displays) if sym_displays else 'Acute respiratory/viral presentation'}. "
            f"Duration: {intake_dict.get('duration_text') or 'Not specified'}."
            + (f" High-risk host factors: {', '.join(high_risk_factors)}." if high_risk_factors else "")
        ),
        "objective": "; ".join(obs_summary) if obs_summary else "No objective vitals or RAT recorded yet.",
        "assessment": (
            f"Working diagnosis draft: {working_dx['icd10_display']} "
            f"(ICD-10: {working_dx['icd10']}, ICPC-2: {working_dx['icpc2']}). "
            f"Antiviral window: {oseltamivir_window.replace('_', ' ')}. "
            f"{abx_stewardship} Clinician verification required."
        ),
        "plan": (
            "1. Clinician teleconsultation review and verification of history, allergies, and red flags. "
            f"2. Review proposed medications ({len(suggested_meds)} item(s)) and sick-leave draft ({sl_days} day(s)). "
            f"3. Safety-netting: {clinical_decision_support['safety_netting_en']}"
        ),
        "clinical_decision_support": clinical_decision_support,
        "snomed_codes": snomed_codes,
        "observations": observations,
        "prompt_injection_flagged": bool(intake_dict.get("prompt_injection_detected")),
    }

    # Plain-language patient summary draft (compliant with check_outbound and zero autonomous claims)
    if eff_lang == "zh":
        patient_summary_draft = (
            f"{AUTOMATED_LABEL_ZH}\n"
            "多謝你提供症狀資料。我哋已將你嘅資料整理好，交俾香港註冊醫生於視像診症時審閱及決定跟進方案"
            "（包括任何藥物或病假紙安排，均須由醫生親自作決定）。\n"
            "如喺睇症前覺得呼吸困難、胸口痛、高燒不退或情況明顯轉差，請即刻致電 999 或前往最近嘅急症室。"
        )
    else:
        patient_summary_draft = (
            f"{AUTOMATED_LABEL_EN}\n"
            "Thank you for sharing your symptoms. We have prepared your intake summary for a Hong Kong "
            "registered doctor to review during your teleconsultation. Your doctor will decide on any "
            "assessment, treatment, or medical certificate after speaking with you.\n"
            "If your symptoms get worse before your visit—such as trouble breathing, chest pain, or "
            "feeling much worse—please call 999 or go to the nearest A&E immediately."
        )

    draft = DraftBundle(
        status="PROPOSED_DRAFT_ONLY",
        autonomy_level="L1",
        clinician_signoff_required=True,
        working_diagnosis=working_dx,
        clinical_note=clinical_note,
        suggested_sick_leave=suggested_sick_leave,
        suggested_medications=suggested_meds,
        contraindications_noted=contraindications_noted,
        patient_summary_draft=patient_summary_draft,
        evidence_quotes=evidence_quotes,
        protocol_id=bundle_proto.protocol_id if bundle_proto else "acute_consult_draft_v1",
        protocol_hash=bundle_proto.protocol_hash if bundle_proto else "",
    )

    # Governed zero-tools gateway refinement: safely merge LLM SOAP text refinements ONLY when
    # they pass 100% of deterministic L1 validators (verbatim quotes, zero banned claims, terminology).
    if gateway is not None:
        draft_prompt_payload = {
            "source_text": source_text,
            "working_diagnosis": working_dx,
            "snomed_codes": snomed_codes,
            "observations": observations,
            "evidence_quotes": evidence_quotes,
            "contraindications_noted": contraindications_noted,
            "lang": eff_lang,
        }
        try:
            resp = gateway.complete(
                GatewayRequest(
                    prompt=json.dumps(draft_prompt_payload, ensure_ascii=False),
                    protocol_id="acute_consult_draft_v1",
                    model_id=model_id,
                    provider=provider,
                    tenant_id=tenant_id,
                    task_type="consult_draft",
                    tools=None,
                    response_json=True,
                    fence_untrusted_input=True,
                )
            )
        except ResidencyViolationError:
            raise
        except Exception as exc:
            draft.gateway_audit = {
                "provider": provider,
                "model_id": model_id,
                "protocol_id": "acute_consult_draft_v1",
                "fallback_used": True,
                "error": str(exc)[:160],
                "llm_merge_applied": False,
            }
            resp = None

        if resp is not None:
            draft.protocol_hash = resp.protocol_hash or draft.protocol_hash
            draft.gateway_audit = {
                "provider": resp.provider,
                "model_id": resp.model_id,
                "region": resp.region,
                "protocol_id": resp.protocol_id,
                "protocol_hash": resp.protocol_hash,
                "redacted": resp.redacted,
                "fallback_used": resp.fallback_used,
                "tools_enabled": resp.tools_enabled,
                "latency_ms": resp.latency_ms,
                "validation_errors": list(resp.validation_errors),
                "llm_merge_applied": False,
            }
            parsed = resp.parsed if isinstance(resp.parsed, dict) else None
            has_banned = any(e.startswith("banned_claim:") for e in resp.validation_errors)
            if parsed and not resp.fallback_used and not parsed.get("fallback") and not has_banned:
                cand_note = dict(draft.clinical_note)
                llm_note = parsed.get("clinical_note")
                changed = False
                if isinstance(llm_note, dict):
                    for sec in ("subjective", "assessment", "plan"):
                        val = llm_note.get(sec)
                        if isinstance(val, str) and val.strip():
                            cand_note[sec] = val.strip()
                            changed = True

                cand_quotes = list(draft.evidence_quotes)
                raw_llm_quotes = parsed.get("evidence_quotes")
                if isinstance(raw_llm_quotes, list):
                    kept_q, dropped_q = filter_verbatim_quotes(
                        [str(q) for q in raw_llm_quotes if isinstance(q, str)],
                        source_text,
                    )
                    if dropped_q:
                        draft.gateway_audit["dropped_llm_quotes"] = dropped_q
                    for q in kept_q:
                        if q not in cand_quotes:
                            cand_quotes.append(q)
                            changed = True

                if changed:
                    candidate = DraftBundle(
                        status=draft.status,
                        autonomy_level=draft.autonomy_level,
                        clinician_signoff_required=draft.clinician_signoff_required,
                        working_diagnosis=dict(draft.working_diagnosis),
                        clinical_note=cand_note,
                        suggested_sick_leave=dict(draft.suggested_sick_leave),
                        suggested_medications=list(draft.suggested_medications),
                        contraindications_noted=list(draft.contraindications_noted),
                        patient_summary_draft=draft.patient_summary_draft,
                        evidence_quotes=cand_quotes,
                        protocol_id=draft.protocol_id,
                        protocol_hash=draft.protocol_hash,
                        gateway_audit=dict(draft.gateway_audit),
                    )
                    cand_val = validate_proposal(candidate, source_text, patient=patient, lang=eff_lang)
                    if cand_val.valid:
                        draft.clinical_note = cand_note
                        draft.evidence_quotes = cand_quotes
                        draft.gateway_audit["llm_merge_applied"] = True
                    else:
                        draft.gateway_audit["llm_merge_rejected"] = list(cand_val.errors)
            elif has_banned:
                draft.gateway_audit["llm_merge_rejected"] = [
                    e for e in resp.validation_errors if e.startswith("banned_claim:")
                ]

    draft.validation = validate_proposal(draft, source_text, patient=patient, lang=eff_lang)
    return draft


def validate_proposal(
    proposal: DraftBundle | dict[str, Any],
    source_text: str,
    *,
    patient: dict[str, Any] | None = None,
    lang: str | None = None,
) -> ValidationResult:
    """Run deterministic L1 validators: JSON schema, verbatim quote integrity, terminology, safety/banned claims."""
    data = proposal.to_dict() if isinstance(proposal, DraftBundle) else dict(proposal)
    errors: list[str] = []
    warnings: list[str] = []

    # 1. JSON Schema & L1 Autonomy Invariants
    schema_errors: list[str] = []
    required_top = (
        "status",
        "autonomy_level",
        "clinician_signoff_required",
        "working_diagnosis",
        "clinical_note",
        "suggested_sick_leave",
        "suggested_medications",
        "patient_summary_draft",
        "evidence_quotes",
    )
    for k in required_top:
        if k not in data:
            schema_errors.append(f"Missing required field: {k}")

    if data.get("status") != "PROPOSED_DRAFT_ONLY":
        schema_errors.append(f"Invalid status '{data.get('status')}'; must be 'PROPOSED_DRAFT_ONLY'")
    if data.get("autonomy_level") != "L1":
        schema_errors.append(f"Invalid autonomy_level '{data.get('autonomy_level')}'; must be 'L1'")
    if data.get("clinician_signoff_required") is not True:
        schema_errors.append("clinician_signoff_required must be True")

    sl = data.get("suggested_sick_leave")
    if isinstance(sl, dict):
        if sl.get("autonomously_issued") is not False:
            schema_errors.append("suggested_sick_leave.autonomously_issued must be False")
        if sl.get("clinician_signoff_required") is not True:
            schema_errors.append("suggested_sick_leave.clinician_signoff_required must be True")
        days = sl.get("days")
        if not isinstance(days, int) or not (0 <= days <= 2):
            schema_errors.append(f"suggested_sick_leave.days must be int in [0, 2], got {days!r}")
    else:
        schema_errors.append("suggested_sick_leave must be an object")

    note = data.get("clinical_note")
    if isinstance(note, dict):
        for nk in ("subjective", "objective", "assessment", "plan"):
            if not note.get(nk):
                schema_errors.append(f"clinical_note.{nk} is required")
    else:
        schema_errors.append("clinical_note must be an object")

    errors.extend(schema_errors)

    # 2. Verbatim Quote Integrity (100% verbatim substring in source_text)
    quote_errors: list[str] = []
    quotes = data.get("evidence_quotes")
    if not isinstance(quotes, list):
        quote_errors.append("evidence_quotes must be a list")
    else:
        for q in quotes:
            if not isinstance(q, str) or not q:
                quote_errors.append(f"Empty or non-string quote: {q!r}")
            elif source_text and q not in source_text:
                quote_errors.append(f"Non-verbatim quote not found in source text: {q!r}")
    errors.extend(quote_errors)

    # 3. Official Terminology & Clinical Formulary Checks
    term_errors: list[str] = []
    dx = data.get("working_diagnosis")
    if isinstance(dx, dict):
        term_errors.extend(validate_diagnosis_coding(str(dx.get("icd10") or ""), dx.get("icpc2")))
    else:
        term_errors.append("working_diagnosis must be an object")

    if isinstance(note, dict):
        snomed_codes = [str(c) for c in (note.get("snomed_codes") or [])]
        term_errors.extend(validate_snomed_codes(snomed_codes))
        for obs in note.get("observations") or []:
            if isinstance(obs, dict):
                term_errors.extend(
                    validate_observation_coding(str(obs.get("loinc_code") or ""), obs.get("ucum_unit"))
                )

    allergy_groups = _normalized_allergy_groups(patient or {}, {})
    g6pd_def = bool((patient or {}).get("g6pd_deficient"))
    age = int(patient["age"]) if patient and patient.get("age") is not None else None

    for med in data.get("suggested_medications") or []:
        if not isinstance(med, dict):
            term_errors.append("Medication entry must be an object")
            continue
        drug_name = str(med.get("drug") or "")
        found = lookup_drug(drug_name)
        if found is None:
            term_errors.append(f"Unknown formulary drug: {drug_name}")
            continue
        canon, meta = found
        if meta["hk_schedule"] == "DANGEROUS_DRUG":
            term_errors.append(f"Cap. 134 Dangerous Drug prohibited in draft: {canon}")
        safe, reason = _is_drug_safe_for_patient(
            canon, allergy_groups=allergy_groups, g6pd_deficient=g6pd_def, age=age
        )
        if not safe and reason:
            term_errors.append(reason)
        if med.get("rxnorm") and str(med["rxnorm"]) != str(meta["rxnorm"]):
            term_errors.append(f"RxNorm mismatch for {canon}: {med['rxnorm']} != {meta['rxnorm']}")
        if med.get("atc") and str(med["atc"]) != str(meta["atc"]):
            term_errors.append(f"ATC mismatch for {canon}: {med['atc']} != {meta['atc']}")

    errors.extend(term_errors)

    # 4. Banned-Claims & Outbound Reassurance Check
    claim_errors: list[str] = []
    summary = str(data.get("patient_summary_draft") or "")
    if not summary:
        claim_errors.append("patient_summary_draft is empty")
    else:
        if summary.startswith(AUTOMATED_LABEL_ZH):
            label = AUTOMATED_LABEL_ZH
        elif summary.startswith(AUTOMATED_LABEL_EN):
            label = AUTOMATED_LABEL_EN
        else:
            label = AUTOMATED_LABEL_ZH if lang == "zh" else AUTOMATED_LABEL_EN

        outbound_fails = precare_safety.check_outbound(summary, label)
        for f in outbound_fails:
            claim_errors.append(f"check_outbound_violation:{f}")

        for code, pat in _ACUTE_BANNED_OUTBOUND_PATTERNS:
            if re.search(pat, summary, re.I):
                claim_errors.append(f"banned_claim:{code}")

    errors.extend(claim_errors)

    checks = {
        "json_schema": len(schema_errors) == 0,
        "verbatim_quotes": len(quote_errors) == 0,
        "terminology_and_safety": len(term_errors) == 0,
        "banned_claims": len(claim_errors) == 0,
    }
    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings, checks=checks)


# --- 4. AcuteHarnessRuntime Orchestrator ---
class AcuteHarnessRuntime:
    """High-level L1 runtime orchestrating protocol-governed triage, extraction, and consult drafting."""

    def __init__(
        self,
        *,
        gateway: ModelGateway | None = None,
        enable_model_screen: bool = True,
        kill_switch: bool = False,
        default_model_id: str = "gemini-2.5-flash",
        default_provider: str = "vertex",
    ) -> None:
        self.gateway = gateway or ModelGateway()
        self.enable_model_screen = enable_model_screen
        self.kill_switch = kill_switch
        self.default_model_id = default_model_id
        self.default_provider = default_provider
        self.protocols: dict[str, ProtocolBundle] = {
            pid: load_protocol(pid)
            for pid in ("flu_intake_v1", "acute_consult_draft_v1", "followup_48h_v1")
        }

    def set_kill_switch(self, active: bool) -> None:
        """Disable all LLM screening immediately (reverts strictly to deterministic rules floor)."""
        self.kill_switch = active

    def screen_message(
        self,
        text: str,
        *,
        lang: str = "auto",
        vitals: dict[str, Any] | None = None,
        tenant_id: str = "default",
        kill_switch: bool | None = None,
        protocol_id: str = "flu_intake_v1",
    ) -> TriageResult:
        eff_kill = self.kill_switch if kill_switch is None else bool(kill_switch)
        return screen_acute_message(
            text,
            lang=lang,
            gateway=self.gateway,
            vitals=vitals,
            enable_model_screen=self.enable_model_screen,
            kill_switch=eff_kill,
            tenant_id=tenant_id,
            model_id=self.default_model_id,
            provider=self.default_provider,
            protocol_id=protocol_id,
        )

    def extract(
        self,
        text: str,
        *,
        vitals: dict[str, Any] | None = None,
        lang: str = "auto",
        tenant_id: str = "default",
        use_gateway: bool = False,
        kill_switch: bool | None = None,
    ) -> IntakeExtraction:
        eff_kill = self.kill_switch if kill_switch is None else bool(kill_switch)
        return extract_intake(
            text,
            vitals=vitals,
            lang=lang,
            gateway=self.gateway if (use_gateway and not eff_kill) else None,
            tenant_id=tenant_id,
            model_id=self.default_model_id,
            provider=self.default_provider,
        )

    def draft(
        self,
        intake: IntakeExtraction | dict[str, Any],
        patient: dict[str, Any],
        *,
        lang: str | None = None,
        tenant_id: str = "default",
        kill_switch: bool | None = None,
    ) -> DraftBundle:
        eff_kill = self.kill_switch if kill_switch is None else bool(kill_switch)
        return draft_consult_proposal(
            intake,
            patient,
            gateway=None if eff_kill else self.gateway,
            lang=lang,
            tenant_id=tenant_id,
            model_id=self.default_model_id,
            provider=self.default_provider,
        )

    def process_intake(
        self,
        text: str,
        *,
        vitals: dict[str, Any] | None = None,
        patient: dict[str, Any] | None = None,
        lang: str = "auto",
        tenant_id: str = "default",
        kill_switch: bool | None = None,
    ) -> dict[str, Any]:
        """Run full L1 acute intake pipeline: union-only red-flag screen -> short-circuit emergency OR extract+draft."""
        triage = self.screen_message(text, lang=lang, vitals=vitals, tenant_id=tenant_id, kill_switch=kill_switch)
        if triage.is_emergency:
            return {
                "triage": triage,
                "intake": None,
                "draft": None,
                "patient_reply": triage.banner,
                "escalated": True,
            }
        intake = self.extract(text, vitals=vitals, lang=lang, tenant_id=tenant_id, kill_switch=kill_switch)
        draft = self.draft(intake, patient or {}, lang=None if lang == "auto" else lang, tenant_id=tenant_id, kill_switch=kill_switch)
        return {
            "triage": triage,
            "intake": intake,
            "draft": draft,
            "patient_reply": draft.patient_summary_draft,
            "escalated": False,
        }
