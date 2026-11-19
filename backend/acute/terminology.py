"""Official Hong Kong primary-care clinical terminologies and HL7 FHIR R4 coding validators.

Supports:
- SNOMED CT (http://snomed.info/sct): symptoms, red-flag clinical findings, allergies
- ICD-10 (http://hl7.org/fhir/sid/icd-10) & ICPC-2 (http://hl7.org/fhir/sid/icpc-2): HK primary-care working diagnoses
- RxNorm (http://www.nlm.nih.gov/research/umls/rxnorm) & ATC (http://www.whocc.no/atc): medications + HK Cap. 134/138 class
- LOINC (http://loinc.org) & UCUM (http://unitsofmeasure.org): vitals, rapid antigen tests, lab observations
- CVX (http://hl7.org/fhir/sid/cvx): seasonal influenza vaccinations
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SYSTEMS = {
    "snomed": "http://snomed.info/sct",
    "icd10": "http://hl7.org/fhir/sid/icd-10",
    "icpc2": "http://hl7.org/fhir/sid/icpc-2",
    "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "atc": "http://www.whocc.no/atc",
    "loinc": "http://loinc.org",
    "ucum": "http://unitsofmeasure.org",
    "cvx": "http://hl7.org/fhir/sid/cvx",
}


@dataclass(frozen=True)
class CodedConcept:
    system: str
    code: str
    display: str
    display_zh: str = ""

    def to_fhir(self) -> dict[str, Any]:
        coding: dict[str, Any] = {
            "system": self.system,
            "code": self.code,
            "display": self.display,
        }
        if self.display_zh:
            coding["extension"] = [{
                "url": "http://tmrw.hk/fhir/StructureDefinition/display-zh-HK",
                "valueString": self.display_zh,
            }]
        return {"coding": [coding], "text": self.display}

    def to_dict(self) -> dict[str, str]:
        return {
            "system": self.system,
            "code": self.code,
            "display": self.display,
            "display_zh": self.display_zh,
        }


# --- LOINC + UCUM Vitals & Point-of-Care Acute Flu Labs ---
LOINC_OBSERVATIONS: dict[str, tuple[str, str, str]] = {
    # code -> (display, display_zh, ucum_unit)
    "8310-5": ("Body temperature", "體溫", "Cel"),
    "2708-6": ("Oxygen saturation in Arterial blood by Pulse oximetry", "血氧飽和度", "%"),
    "8867-4": ("Heart rate", "心率", "/min"),
    "9279-1": ("Respiratory rate", "呼吸頻率", "/min"),
    "8480-6": ("Systolic blood pressure", "收縮壓", "mm[Hg]"),
    "8462-4": ("Diastolic blood pressure", "舒張壓", "mm[Hg]"),
    "80382-5": ("Influenza virus A Ag [Presence] in Upper respiratory specimen by Rapid immunoassay", "甲型流感快速測試", "{presence}"),
    "80383-3": ("Influenza virus B Ag [Presence] in Upper respiratory specimen by Rapid immunoassay", "乙型流感快速測試", "{presence}"),
    "94558-4": ("SARS-CoV-2 Ag [Presence] in Respiratory specimen by Rapid immunoassay", "新冠快速抗原測試", "{presence}"),
}

VALID_UCUM = {"Cel", "%", "/min", "mm[Hg]", "{presence}", "mg", "mg/kg", "mL", "mmol/L", "g/dL", "U/L"}

# --- SNOMED CT Acute Flu-Season Symptoms & Red-Flag Findings ---
SNOMED_SYMPTOMS: dict[str, tuple[str, str]] = {
    "386661006": ("Fever", "發燒"),
    "49727002": ("Cough", "咳嗽"),
    "162397003": ("Sore throat", "喉嚨痛"),
    "64531003": ("Nasal discharge", "流鼻水"),
    "68962001": ("Myalgia", "肌肉痠痛"),
    "25064002": ("Headache", "頭痛"),
    "84229001": ("Fatigue", "疲倦"),
    "422400008": ("Vomiting", "嘔吐"),
    "62315008": ("Diarrhoea", "腹瀉"),
    # Red-flag findings
    "267036007": ("Dyspnoea", "呼吸困難"),
    "29857009": ("Chest pain", "胸痛"),
    "40917007": ("Clouded consciousness / Confusion", "意識混亂"),
    "66857006": ("Haemoptysis", "咳血"),
    "3415004": ("Cyanosis", "發紺"),
    "39579001": ("Anaphylaxis", "嚴重過敏反應"),
    "271594007": ("Syncope", "暈厥"),
    "161882006": ("Stiff neck", "頸部僵硬"),
    "6471006": ("Suicidal thoughts", "自殺念頭"),
}

# --- Dual ICD-10 + ICPC-2 Hong Kong Primary Care Diagnoses ---
HK_DIAGNOSES: dict[str, dict[str, str]] = {
    "J11.1": {
        "icd10": "J11.1", "icd10_display": "Influenza with other respiratory manifestations, virus not identified",
        "icpc2": "R80", "icpc2_display": "Influenza",
        "display_zh": "流行性感冒",
    },
    "J10.1": {
        "icd10": "J10.1", "icd10_display": "Influenza with other respiratory manifestations, seasonal virus identified",
        "icpc2": "R80", "icpc2_display": "Influenza",
        "display_zh": "季節性流行性感冒（已確診）",
    },
    "J06.9": {
        "icd10": "J06.9", "icd10_display": "Acute upper respiratory infection, unspecified",
        "icpc2": "R74", "icpc2_display": "Upper respiratory infection acute",
        "display_zh": "急性上呼吸道感染",
    },
    "J02.9": {
        "icd10": "J02.9", "icd10_display": "Acute pharyngitis, unspecified",
        "icpc2": "R74", "icpc2_display": "Acute pharyngitis / URTI",
        "display_zh": "急性咽喉炎",
    },
    "J20.9": {
        "icd10": "J20.9", "icd10_display": "Acute bronchitis, unspecified",
        "icpc2": "R78", "icpc2_display": "Acute bronchitis/bronchiolitis",
        "display_zh": "急性支氣管炎",
    },
    "R50.9": {
        "icd10": "R50.9", "icd10_display": "Fever, unspecified",
        "icpc2": "A03", "icpc2_display": "Fever",
        "display_zh": "發燒（待查）",
    },
    "A09": {
        "icd10": "A09", "icd10_display": "Infectious gastroenteritis and colitis, unspecified",
        "icpc2": "D73", "icpc2_display": "Gastroenteritis presumed infection",
        "display_zh": "急性腸胃炎",
    },
}

# --- RxNorm + ATC + Hong Kong Cap. 134 (Dangerous Drugs) / Cap. 138 (Part I Poisons) Formulary ---
# hk_schedule: "OTC" | "PART_I_POISON" (Cap. 138) | "DANGEROUS_DRUG" (Cap. 134 - hard-blocked in acute teleconsult)
HK_ACUTE_FORMULARY: dict[str, dict[str, Any]] = {
    "paracetamol": {
        "rxnorm": "161", "atc": "N02BE01", "display": "Paracetamol 500mg Tablet", "display_zh": "撲熱息痛 500毫克",
        "class": "analgesic", "hk_schedule": "OTC", "max_daily_mg": 4000, "max_single_mg": 1000,
        "pediatric_mg_per_kg_dose": 15, "g6pd_risk": False, "allergy_group": "paracetamol",
    },
    "ibuprofen": {
        "rxnorm": "5640", "atc": "M01AE01", "display": "Ibuprofen 400mg Tablet", "display_zh": "布洛芬 400毫克",
        "class": "nsaid", "hk_schedule": "PART_I_POISON", "max_daily_mg": 2400, "max_single_mg": 800,
        "pediatric_mg_per_kg_dose": 10, "g6pd_risk": False, "allergy_group": "nsaid",
    },
    "naproxen": {
        "rxnorm": "7258", "atc": "M01AE02", "display": "Naproxen 250mg Tablet", "display_zh": "萘普生 250毫克",
        "class": "nsaid", "hk_schedule": "PART_I_POISON", "max_daily_mg": 1250, "max_single_mg": 500,
        "pediatric_mg_per_kg_dose": 5, "g6pd_risk": False, "allergy_group": "nsaid",
    },
    "aspirin": {
        "rxnorm": "1191", "atc": "N02BA01", "display": "Aspirin 300mg Tablet", "display_zh": "阿士匹靈 300毫克",
        "class": "nsaid", "hk_schedule": "OTC", "max_daily_mg": 4000, "max_single_mg": 900,
        "pediatric_contraindicated": True, "g6pd_risk": True, "allergy_group": "nsaid",
    },
    "oseltamivir": {
        "rxnorm": "261315", "atc": "J05AH02", "display": "Oseltamivir (Tamiflu) 75mg Capsule", "display_zh": "奧司他韋（特敏福）75毫克",
        "class": "antiviral", "hk_schedule": "PART_I_POISON", "max_daily_mg": 150, "max_single_mg": 75,
        "renal_adjust_egfr": 60, "g6pd_risk": False, "allergy_group": "oseltamivir",
    },
    "amoxicillin": {
        "rxnorm": "723", "atc": "J01CA04", "display": "Amoxicillin 500mg Capsule", "display_zh": "阿莫西林 500毫克",
        "class": "beta_lactam", "hk_schedule": "PART_I_POISON", "max_daily_mg": 3000, "max_single_mg": 1000,
        "g6pd_risk": False, "allergy_group": "penicillin",
    },
    "amoxicillin-clavulanate": {
        "rxnorm": "617995", "atc": "J01CR02", "display": "Amoxicillin-Clavulanate (Augmentin) 625mg Tablet", "display_zh": "阿莫西林克拉維酸（安滅菌）625毫克",
        "class": "beta_lactam", "hk_schedule": "PART_I_POISON", "max_daily_mg": 3000, "max_single_mg": 1000,
        "g6pd_risk": False, "allergy_group": "penicillin",
    },
    "clarithromycin": {
        "rxnorm": "21212", "atc": "J01FA09", "display": "Clarithromycin 500mg Tablet", "display_zh": "克拉黴素 500毫克",
        "class": "macrolide", "hk_schedule": "PART_I_POISON", "max_daily_mg": 1000, "max_single_mg": 500,
        "g6pd_risk": False, "allergy_group": "macrolide",
    },
    "co-trimoxazole": {
        "rxnorm": "10831", "atc": "J01EE01", "display": "Co-trimoxazole (Sulfamethoxazole/Trimethoprim) 480mg", "display_zh": "復方新諾明（磺胺類）480毫克",
        "class": "sulfonamide", "hk_schedule": "PART_I_POISON", "max_daily_mg": 1920, "max_single_mg": 960,
        "g6pd_risk": True, "allergy_group": "sulfonamide",
    },
    "nitrofurantoin": {
        "rxnorm": "7454", "atc": "J01XE01", "display": "Nitrofurantoin 100mg Capsule", "display_zh": "呋喃妥因 100毫克",
        "class": "nitrofuran", "hk_schedule": "PART_I_POISON", "max_daily_mg": 400, "max_single_mg": 100,
        "renal_adjust_egfr": 45, "g6pd_risk": True, "allergy_group": "nitrofuran",
    },
    "cetirizine": {
        "rxnorm": "20610", "atc": "R06AE07", "display": "Cetirizine 10mg Tablet", "display_zh": "西替利嗪 10毫克",
        "class": "antihistamine", "hk_schedule": "OTC", "max_daily_mg": 10, "max_single_mg": 10,
        "g6pd_risk": False, "allergy_group": "antihistamine",
    },
    "chlorpheniramine": {
        "rxnorm": "2400", "atc": "R06AB04", "display": "Chlorpheniramine 4mg Tablet", "display_zh": "氯苯那敏 4毫克",
        "class": "sedating_antihistamine", "hk_schedule": "OTC", "max_daily_mg": 24, "max_single_mg": 4,
        "g6pd_risk": False, "allergy_group": "antihistamine",
    },
    "pseudoephedrine": {
        "rxnorm": "8896", "atc": "R01BA02", "display": "Pseudoephedrine 60mg Tablet", "display_zh": "偽麻黃鹼 60毫克",
        "class": "decongestant", "hk_schedule": "PART_I_POISON", "max_daily_mg": 240, "max_single_mg": 60,
        "g6pd_risk": False, "allergy_group": "sympathomimetic",
    },
    "dextromethorphan": {
        "rxnorm": "3289", "atc": "R05DA09", "display": "Dextromethorphan 15mg Tablet", "display_zh": "右美沙芬 15毫克",
        "class": "antitussive", "hk_schedule": "PART_I_POISON", "max_daily_mg": 120, "max_single_mg": 30,
        "g6pd_risk": False, "allergy_group": "dextromethorphan",
    },
    # Cap. 134 Dangerous Drugs — hard-blocked from acute teleconsult e-scripts
    "codeine": {
        "rxnorm": "2670", "atc": "R05DA04", "display": "Codeine Phosphate 15mg", "display_zh": "可待因（危險藥物）",
        "class": "opioid", "hk_schedule": "DANGEROUS_DRUG", "max_daily_mg": 240, "max_single_mg": 60,
        "g6pd_risk": False, "allergy_group": "opioid",
    },
    "zolpidem": {
        "rxnorm": "39993", "atc": "N05CF02", "display": "Zolpidem 10mg", "display_zh": "唑吡坦（危險藥物）",
        "class": "z_drug", "hk_schedule": "DANGEROUS_DRUG", "max_daily_mg": 10, "max_single_mg": 10,
        "g6pd_risk": False, "allergy_group": "z_drug",
    },
    "diazepam": {
        "rxnorm": "3322", "atc": "N05BA01", "display": "Diazepam 5mg", "display_zh": "地西泮（危險藥物）",
        "class": "benzodiazepine", "hk_schedule": "DANGEROUS_DRUG", "max_daily_mg": 40, "max_single_mg": 10,
        "g6pd_risk": False, "allergy_group": "benzodiazepine",
    },
    "tramadol": {
        "rxnorm": "10689", "atc": "N02AX02", "display": "Tramadol 50mg", "display_zh": "曲馬多（危險藥物）",
        "class": "opioid", "hk_schedule": "DANGEROUS_DRUG", "max_daily_mg": 400, "max_single_mg": 100,
        "g6pd_risk": False, "allergy_group": "opioid",
    },
}

# --- CVX Seasonal Influenza Vaccines ---
CVX_VACCINES: dict[str, tuple[str, str]] = {
    "150": ("Influenza, injectable, quadrivalent, preservative free (IIV4)", "四價滅活季節性流感疫苗（無防腐劑）"),
    "158": ("Influenza, injectable, quadrivalent, contains preservative (IIV4)", "四價滅活季節性流感疫苗"),
    "171": ("Influenza, injectable, Madin Darby Canine Kidney, preservative free (ccIIV4)", "四價細胞培養季節性流感疫苗"),
    "185": ("Influenza, recombinant, quadrivalent, injectable, preservative free (RIV4)", "四價重組季節性流感疫苗"),
}


def validate_observation_coding(loinc_code: str, ucum_unit: str | None = None) -> list[str]:
    errors: list[str] = []
    if loinc_code not in LOINC_OBSERVATIONS:
        errors.append(f"Unknown LOINC code: {loinc_code}")
    elif ucum_unit is not None:
        expected = LOINC_OBSERVATIONS[loinc_code][2]
        if ucum_unit not in VALID_UCUM:
            errors.append(f"Invalid UCUM unit: {ucum_unit}")
        elif ucum_unit != expected:
            errors.append(f"UCUM unit {ucum_unit} does not match expected {expected} for LOINC {loinc_code}")
    return errors


def validate_diagnosis_coding(icd10_code: str, icpc2_code: str | None = None) -> list[str]:
    errors: list[str] = []
    if icd10_code not in HK_DIAGNOSES:
        errors.append(f"Unknown HK acute ICD-10 code: {icd10_code}")
    elif icpc2_code and HK_DIAGNOSES[icd10_code]["icpc2"] != icpc2_code:
        errors.append(f"ICPC-2 {icpc2_code} does not match ICD-10 {icd10_code} (expected {HK_DIAGNOSES[icd10_code]['icpc2']})")
    return errors


def validate_snomed_codes(codes: list[str]) -> list[str]:
    return [f"Unknown SNOMED CT code: {c}" for c in codes if c not in SNOMED_SYMPTOMS]


def validate_cvx_code(cvx_code: str) -> list[str]:
    return [] if cvx_code in CVX_VACCINES else [f"Unknown CVX vaccine code: {cvx_code}"]


def lookup_drug(drug_name: str) -> tuple[str, dict[str, Any]] | None:
    """Resolve generic/brand/Cantonese name to canonical HK acute formulary entry."""
    norm = drug_name.strip().lower()
    aliases = {
        "panadol": "paracetamol", "acetaminophen": "paracetamol", "撲熱息痛": "paracetamol", "必理痛": "paracetamol",
        "brufen": "ibuprofen", "nurofen": "ibuprofen", "布洛芬": "ibuprofen",
        "tamiflu": "oseltamivir", "特敏福": "oseltamivir", "奧司他韋": "oseltamivir",
        "augmentin": "amoxicillin-clavulanate", "安滅菌": "amoxicillin-clavulanate",
        "amoxil": "amoxicillin", "阿莫西林": "amoxicillin",
        "klacid": "clarithromycin", "克拉黴素": "clarithromycin",
        "bactrim": "co-trimoxazole", "septrin": "co-trimoxazole", "磺胺": "co-trimoxazole",
        "zyrtec": "cetirizine", "西替利嗪": "cetirizine",
        "piriton": "chlorpheniramine",
        "sudafed": "pseudoephedrine", "偽麻黃鹼": "pseudoephedrine",
        "stilnox": "zolpidem", "valium": "diazepam",
        "codeine linctus": "codeine", "可待因": "codeine",
    }
    if norm in HK_ACUTE_FORMULARY:
        return norm, HK_ACUTE_FORMULARY[norm]
    for alias, canon in aliases.items():
        if alias in norm:
            return canon, HK_ACUTE_FORMULARY[canon]
    for canon in sorted(HK_ACUTE_FORMULARY, key=len, reverse=True):
        if canon in norm:
            return canon, HK_ACUTE_FORMULARY[canon]
    return None
