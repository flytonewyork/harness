"""What the agent knows about medicines: class, whether a change is high risk, whether it is controlled, and
which blood tests a repeat should have on file. Product knowledge, not patient data, and never a dosing table:
the agent uses it only to decide what to tell the doctor, never what to tell the patient.

Monitoring intervals are conservative defaults for a primary-care repeat check (flag, never block); a clinic sets
its own in `MONITORING` before go-live.
"""
from __future__ import annotations

import re

# class -> generic names (lower case). Brand names and Cantonese names a patient uses are in ALIASES.
CLASSES: dict[str, tuple[str, ...]] = {
    "anticoagulant": ("warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban"),
    "antiplatelet": ("aspirin", "clopidogrel", "ticagrelor"),
    "insulin": ("insulin", "insulin glargine", "insulin aspart", "insulin detemir"),
    "sulfonylurea": ("gliclazide", "glipizide", "glibenclamide", "glimepiride"),
    "metformin": ("metformin",),
    "sglt2": ("empagliflozin", "dapagliflozin", "canagliflozin"),
    "dpp4": ("sitagliptin", "linagliptin", "vildagliptin"),
    "acei": ("lisinopril", "enalapril", "perindopril", "ramipril", "captopril"),
    "arb": ("losartan", "valsartan", "telmisartan", "irbesartan", "candesartan"),
    "ccb": ("amlodipine", "nifedipine", "felodipine", "lercanidipine"),
    "beta_blocker": ("bisoprolol", "metoprolol", "atenolol", "propranolol", "carvedilol"),
    "diuretic": ("hydrochlorothiazide", "indapamide", "furosemide", "spironolactone"),
    "statin": ("atorvastatin", "simvastatin", "rosuvastatin", "pravastatin"),
    "thyroid": ("levothyroxine",),
    "xanthine_oxidase": ("allopurinol", "febuxostat"),
    "antibiotic": ("amoxicillin", "amoxicillin-clavulanate", "doxycycline", "clarithromycin", "cefuroxime",
                   "nitrofurantoin", "levofloxacin"),
    "nsaid": ("ibuprofen", "naproxen", "diclofenac", "etoricoxib", "celecoxib", "mefenamic acid"),
    "ssri": ("sertraline", "fluoxetine", "escitalopram", "citalopram", "paroxetine"),
    "benzodiazepine": ("diazepam", "lorazepam", "alprazolam", "clonazepam"),
    "z_drug": ("zolpidem", "zopiclone"),
    "opioid": ("codeine", "tramadol", "dihydrocodeine"),
    "ppi": ("omeprazole", "esomeprazole", "pantoprazole", "lansoprazole"),
    "antihistamine": ("cetirizine", "loratadine", "fexofenadine", "chlorpheniramine"),
}
ALIASES: dict[str, str] = {
    "augmentin": "amoxicillin-clavulanate", "lipitor": "atorvastatin", "crestor": "rosuvastatin", "zocor": "simvastatin",
    "norvasc": "amlodipine", "glucophage": "metformin", "diamicron": "gliclazide", "jardiance": "empagliflozin",
    "forxiga": "dapagliflozin", "januvia": "sitagliptin", "eliquis": "apixaban", "xarelto": "rivaroxaban",
    "pradaxa": "dabigatran", "plavix": "clopidogrel", "cozaar": "losartan", "diovan": "valsartan",
    "micardis": "telmisartan", "concor": "bisoprolol", "eltroxin": "levothyroxine", "zyloric": "allopurinol",
    "zoloft": "sertraline", "prozac": "fluoxetine", "lexapro": "escitalopram", "valium": "diazepam",
    "ativan": "lorazepam", "xanax": "alprazolam", "stilnox": "zolpidem", "imovane": "zopiclone",
    "nexium": "esomeprazole", "losec": "omeprazole", "brufen": "ibuprofen", "voltaren": "diclofenac",
    "arcoxia": "etoricoxib", "panadol": "paracetamol",
    "thyroxine": "levothyroxine", "華法林": "warfarin", "薄血丸": "warfarin", "二甲雙胍": "metformin", "阿司匹林": "aspirin", "亞士匹靈": "aspirin",
    "甲狀腺素": "levothyroxine", "胰島素": "insulin",
}
# words a patient uses for a medicine's purpose -> classes it may mean
PURPOSE: dict[str, tuple[str, ...]] = {
    "blood pressure": ("acei", "arb", "ccb", "beta_blocker", "diuretic"), "bp": ("acei", "arb", "ccb", "beta_blocker", "diuretic"),
    "血壓": ("acei", "arb", "ccb", "beta_blocker", "diuretic"),
    "diabetes": ("metformin", "sulfonylurea", "sglt2", "dpp4", "insulin"), "sugar": ("metformin", "sulfonylurea", "sglt2", "dpp4", "insulin"),
    "糖尿": ("metformin", "sulfonylurea", "sglt2", "dpp4", "insulin"), "血糖": ("metformin", "sulfonylurea", "sglt2", "dpp4", "insulin"),
    "cholesterol": ("statin",), "膽固醇": ("statin",), "blood thinner": ("anticoagulant", "antiplatelet"),
    "薄血": ("anticoagulant", "antiplatelet"), "thyroid": ("thyroid",), "甲狀腺": ("thyroid",), "gout": ("xanthine_oxidase",),
    "痛風": ("xanthine_oxidase",), "sleeping": ("benzodiazepine", "z_drug"), "sleep": ("benzodiazepine", "z_drug"),
    "安眠藥": ("benzodiazepine", "z_drug"), "瞓覺": ("benzodiazepine", "z_drug"), "stomach": ("ppi",), "胃藥": ("ppi",),
    "antidepressant": ("ssri",), "抗抑鬱": ("ssri",), "pain": ("nsaid", "opioid"), "antibiotic": ("antibiotic",), "antibiotics": ("antibiotic",), "抗生素": ("antibiotic",), "止痛": ("nsaid", "opioid"),
}

# a change to one of these gets an earlier check-in and a chase if the patient does not answer
HIGH_RISK = {"anticoagulant", "insulin", "sulfonylurea", "ssri", "xanthine_oxidase", "opioid", "benzodiazepine", "z_drug"}
# Hong Kong Dangerous Drugs / psychotropic: the doctor sees these repeats flagged, every time
CONTROLLED = {"benzodiazepine", "z_drug", "opioid"}
# short courses: a repeat is unusual, so the item says so
SHORT_COURSE = {"antibiotic"}
# a gap is dangerous: running out moves the request to same-day
NO_GAP = {"anticoagulant", "insulin", "thyroid", "beta_blocker"}

# class -> (tests that count, how recent in days) for a repeat to be routine
MONITORING: dict[str, tuple[tuple[str, ...], int]] = {
    "metformin": (("eGFR", "Creatinine"), 365),
    "acei": (("Creatinine", "Potassium", "eGFR"), 365),
    "arb": (("Creatinine", "Potassium", "eGFR"), 365),
    "diuretic": (("Sodium", "Potassium", "Creatinine"), 365),
    "sglt2": (("eGFR", "Creatinine"), 365),
    "anticoagulant": (("INR",), 42),           # warfarin only; DOACs use RENAL below
    "thyroid": (("TSH",), 365),
    "statin": (("LDL cholesterol", "ALT"), 450),
}
DOAC_RENAL = (("Creatinine", "eGFR"), 365)
REVIEW_DAYS = 365          # a repeat with no consultation in this long gets flagged for a review


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def generic(name: str) -> str:
    """'Amlodipine 5mg' -> 'amlodipine'; brand and Cantonese names resolve through ALIASES."""
    n = _norm(name)
    for a, g in ALIASES.items():
        if a in n:
            return g
    for gens in CLASSES.values():
        for g in sorted(gens, key=len, reverse=True):
            if g in n:
                return g
    return n.split(" ")[0] if n else n


def drug_class(name: str) -> str | None:
    g = generic(name)
    for c, gens in CLASSES.items():
        if g in gens:
            return c
    return None


def monitoring_for(name: str) -> tuple[tuple[str, ...], int] | None:
    c = drug_class(name)
    if c == "anticoagulant" and generic(name) != "warfarin":
        return DOAC_RENAL
    return MONITORING.get(c) if c else None


def mentions(text: str) -> list[str]:
    """Generic names the text names directly (by generic, brand or Cantonese name)."""
    t = _norm(text)
    out = []
    for a, g in ALIASES.items():
        if a in t and g not in out:
            out.append(g)
    for gens in CLASSES.values():
        for g in gens:
            if re.search(rf"(?<![a-z]){re.escape(g)}(?![a-z])", t) and g not in out:
                out.append(g)
    return out


def purposes(text: str) -> set[str]:
    t = _norm(text)
    out: set[str] = set()
    for w, cs in PURPOSE.items():
        if (re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t) if w.isascii() else w in t):
            out |= set(cs)
    return out
