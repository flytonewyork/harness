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


# --- LOINC + UCUM Vitals, Point-of-Care Acute Labs & HK Primary Care Panels (Pathways 1–8) ---
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
    # Pathways 1, 3, 4, 5 chronic & post-discharge labs
    "6301-6": ("INR in Platelet poor plasma by Coagulation assay", "國際標準化比值 (INR)", "{INR}"),
    "2276-4": ("Ferritin [Mass/volume] in Serum or Plasma", "血清鐵蛋白", "ng/mL"),
    "718-7": ("Hemoglobin [Mass/volume] in Blood", "血紅素 (Hb)", "g/dL"),
    "33914-3": ("Glomerular filtration rate/1.73 sq M.predicted [Volume Rate/Area] in Serum, Plasma or Blood", "腎絲球過濾率 (eGFR)", "mL/min/1.73m2"),
    "2160-0": ("Creatinine [Moles/volume] in Serum or Plasma", "血清肌酸酐", "umol/L"),
    "2823-3": ("Potassium [Moles/volume] in Serum or Plasma", "血鉀 (K+)", "mmol/L"),
    "2951-2": ("Sodium [Moles/volume] in Serum or Plasma", "血鈉 (Na+)", "mmol/L"),
    "13457-7": ("Cholesterol in LDL [Moles/volume] in Serum or Plasma", "低密度脂蛋白膽固醇 (LDL-C)", "mmol/L"),
    "2085-9": ("Cholesterol in HDL [Moles/volume] in Serum or Plasma", "高密度脂蛋白膽固醇 (HDL-C)", "mmol/L"),
    "2571-8": ("Triglyceride [Moles/volume] in Serum or Plasma", "三酸甘油酯", "mmol/L"),
    "1558-6": ("Fasting glucose [Moles/volume] in Serum or Plasma", "空腹血糖", "mmol/L"),
    "29771-3": ("Hemoglobin.gastrointestinal [Presence] in Stool by Immunologic method (FIT)", "大便隱血免疫化學測試 (FIT)", "{presence}"),
    # Pathway 6: T2DM / HbA1c
    "4548-4": ("Hemoglobin A1c/Hemoglobin.total in Blood", "糖化血紅素 (HbA1c)", "%"),
    "14959-1": ("Microalbumin/Creatinine [Molar Ratio] in Urine", "尿液白蛋白肌酸酐比值 (uACR)", "mg/mmol"),
    # Pathway 7: Thyroid / Fatigue
    "3016-3": ("Thyrotropin [Units/volume] in Serum or Plasma (TSH)", "促甲狀腺激素 (TSH)", "mIU/L"),
    "3024-7": ("Thyroxine (T4) free [Moles/volume] in Serum or Plasma", "游離甲狀腺素 (Free T4)", "pmol/L"),
    # Pathway 8: Acute UTI
    "5799-2": ("Leukocyte esterase [Presence] in Urine by Test strip", "尿液白血球酯酶試紙", "{presence}"),
    "5802-4": ("Nitrite [Presence] in Urine by Test strip", "尿液亞硝酸鹽試紙", "{presence}"),
    "630-4": ("Bacteria identified in Urine by Culture (MSU)", "中段尿液細菌培養 (MSU)", "{presence}"),
    # Preventive Care (HK HBV screening)
    "5196-1": ("Hepatitis B virus surface Ag [Presence] in Serum", "乙型肝炎表面抗原 (HBsAg)", "{presence}"),
}

VALID_UCUM = {
    "Cel", "%", "/min", "mm[Hg]", "{presence}", "mg", "mg/kg", "mL", "mmol/L", "g/dL", "U/L",
    "ng/mL", "mL/min/1.73m2", "{INR}", "mIU/L", "pmol/L", "umol/L", "ug", "g", "mg/mmol", "10*9/L", "{score}", "kg/m2",
}

# --- SNOMED CT Acute & Primary Care Symptoms & Red-Flag Findings (Pathways 1–8) ---
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
    # Pathway 6: T2DM symptoms
    "28442001": ("Polyuria", "多尿"),
    "17173007": ("Polydipsia", "口渴多飲"),
    "89362005": ("Weight loss", "體重減輕"),
    # Pathway 7: Thyroid symptoms
    "267032009": ("Cold intolerance", "怕冷"),
    "698862001": ("Heat intolerance", "怕熱"),
    "80313002": ("Palpitations", "心悸"),
    "77554004": ("Neck swelling / Goitre", "頸部腫脹／甲狀腺腫大"),
    "39156008": ("Unintentional weight gain", "體重增加"),
    # Pathway 8: Acute UTI & pyelonephritis red flags
    "49650001": ("Dysuria", "排尿灼痛"),
    "162116003": ("Urinary frequency", "尿頻"),
    "248595008": ("Suprapubic pain", "下腹恥骨上痛"),
    "247355005": ("Flank pain", "腰側痛（腎盂腎炎紅旗）"),
    "165232002": ("Haematuria", "血尿"),
}

# --- Dual ICD-10 + ICPC-2 Hong Kong Primary Care Diagnoses (Pathways 1–8) ---
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
    # Pathways 1, 3, 4, 5 chronic / post-discharge GP diagnoses
    "I48.9": {
        "icd10": "I48.9", "icd10_display": "Atrial fibrillation and atrial flutter, unspecified",
        "icpc2": "K78", "icpc2_display": "Atrial fibrillation/flutter",
        "display_zh": "心房顫動",
    },
    "I10": {
        "icd10": "I10", "icd10_display": "Essential (primary) hypertension",
        "icpc2": "K86", "icpc2_display": "Hypertension uncomplicated",
        "display_zh": "原發性高血壓",
    },
    "E78.0": {
        "icd10": "E78.0", "icd10_display": "Pure hypercholesterolaemia",
        "icpc2": "T93", "icpc2_display": "Lipid disorder",
        "display_zh": "高膽固醇血症",
    },
    "E61.1": {
        "icd10": "E61.1", "icd10_display": "Iron deficiency without anaemia",
        "icpc2": "T91", "icpc2_display": "Vitamin/nutritional deficiency",
        "display_zh": "非貧血性缺鐵",
    },
    "D50.9": {
        "icd10": "D50.9", "icd10_display": "Iron deficiency anaemia, unspecified",
        "icpc2": "B80", "icpc2_display": "Iron deficiency anaemia",
        "display_zh": "缺鐵性貧血",
    },
    # Pathway 6: T2DM / HbA1c
    "E11.9": {
        "icd10": "E11.9", "icd10_display": "Type 2 diabetes mellitus without complications",
        "icpc2": "T90", "icpc2_display": "Diabetes non-insulin dependent",
        "display_zh": "二型糖尿病",
    },
    "E11.65": {
        "icd10": "E11.65", "icd10_display": "Type 2 diabetes mellitus with hyperglycaemia",
        "icpc2": "T90", "icpc2_display": "Diabetes non-insulin dependent",
        "display_zh": "二型糖尿病伴高血糖",
    },
    # Pathway 7: Thyroid / Fatigue
    "E03.9": {
        "icd10": "E03.9", "icd10_display": "Hypothyroidism, unspecified",
        "icpc2": "T86", "icpc2_display": "Hypothyroidism/myxoedema",
        "display_zh": "甲狀腺功能減退",
    },
    "E05.9": {
        "icd10": "E05.9", "icd10_display": "Thyrotoxicosis, unspecified",
        "icpc2": "T85", "icpc2_display": "Hyperthyroidism/thyrotoxicosis",
        "display_zh": "甲狀腺功能亢進",
    },
    # Pathway 8: Acute UTI
    "N39.0": {
        "icd10": "N39.0", "icd10_display": "Urinary tract infection, site not specified",
        "icpc2": "U71", "icpc2_display": "Cystitis/urinary infection other",
        "display_zh": "泌尿道感染",
    },
    "N30.0": {
        "icd10": "N30.0", "icd10_display": "Acute cystitis",
        "icpc2": "U71", "icpc2_display": "Cystitis/urinary infection other",
        "display_zh": "急性膀胱炎",
    },
    "N10": {
        "icd10": "N10", "icd10_display": "Acute tubulo-interstitial nephritis (acute pyelonephritis)",
        "icpc2": "U70", "icpc2_display": "Pyelonephritis/pyelitis",
        "display_zh": "急性腎盂腎炎",
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

# --- HK Chronic & Primary Care GP Formulary (Pathways 1–8) ---
HK_CHRONIC_GP_FORMULARY: dict[str, dict[str, Any]] = {
    "warfarin": {
        "rxnorm": "11289", "atc": "B01AA03", "display": "Warfarin 3mg Tablet", "display_zh": "華法林 3毫克",
        "class": "anticoagulant", "hk_schedule": "PART_I_POISON", "max_daily_mg": 15, "max_single_mg": 15,
        "g6pd_risk": False, "allergy_group": "warfarin", "teratogenic": True,
    },
    "atorvastatin": {
        "rxnorm": "83367", "atc": "C10AA05", "display": "Atorvastatin 20mg Tablet", "display_zh": "阿托伐他汀 20毫克",
        "class": "statin", "hk_schedule": "PART_I_POISON", "max_daily_mg": 80, "max_single_mg": 80,
        "g6pd_risk": False, "allergy_group": "statin", "teratogenic": True,
    },
    "simvastatin": {
        "rxnorm": "36567", "atc": "C10AA01", "display": "Simvastatin 20mg Tablet", "display_zh": "辛伐他汀 20毫克",
        "class": "statin", "hk_schedule": "PART_I_POISON", "max_daily_mg": 40, "max_single_mg": 40,
        "g6pd_risk": False, "allergy_group": "statin", "teratogenic": True,
    },
    "rosuvastatin": {
        "rxnorm": "301542", "atc": "C10AA07", "display": "Rosuvastatin 10mg Tablet", "display_zh": "瑞舒伐他汀 10毫克",
        "class": "statin", "hk_schedule": "PART_I_POISON", "max_daily_mg": 40, "max_single_mg": 40,
        "g6pd_risk": False, "allergy_group": "statin", "teratogenic": True,
    },
    "amlodipine": {
        "rxnorm": "17767", "atc": "C08CA01", "display": "Amlodipine 5mg Tablet", "display_zh": "氨氯地平 5毫克",
        "class": "ccb", "hk_schedule": "PART_I_POISON", "max_daily_mg": 10, "max_single_mg": 10,
        "g6pd_risk": False, "allergy_group": "ccb",
    },
    "losartan": {
        "rxnorm": "52175", "atc": "C09CA01", "display": "Losartan 50mg Tablet", "display_zh": "氯沙坦 50毫克",
        "class": "ras_blocker", "hk_schedule": "PART_I_POISON", "max_daily_mg": 100, "max_single_mg": 100,
        "g6pd_risk": False, "allergy_group": "arb", "teratogenic": True,
    },
    "perindopril": {
        "rxnorm": "54552", "atc": "C09AA04", "display": "Perindopril 4mg Tablet", "display_zh": "培哚普利 4毫克",
        "class": "ras_blocker", "hk_schedule": "PART_I_POISON", "max_daily_mg": 8, "max_single_mg": 8,
        "g6pd_risk": False, "allergy_group": "ace_inhibitor", "teratogenic": True,
    },
    "ferrous bisglycinate": {
        "rxnorm": "2047996", "atc": "B03AA07", "display": "Ferrous Bisglycinate 25mg Capsule", "display_zh": "甘氨酸亞鐵 25毫克",
        "class": "oral_iron", "hk_schedule": "OTC", "max_daily_mg": 100, "max_single_mg": 50,
        "g6pd_risk": False, "allergy_group": "iron",
    },
    "ferrous sulfate": {
        "rxnorm": "24947", "atc": "B03AA07", "display": "Ferrous Sulfate 200mg Tablet", "display_zh": "硫酸亞鐵 200毫克",
        "class": "oral_iron", "hk_schedule": "OTC", "max_daily_mg": 600, "max_single_mg": 200,
        "g6pd_risk": False, "allergy_group": "iron",
    },
    "metformin": {
        "rxnorm": "6809", "atc": "A10BA02", "display": "Metformin 500mg Tablet", "display_zh": "二甲雙胍 500毫克",
        "class": "biguanide", "hk_schedule": "PART_I_POISON", "max_daily_mg": 2550, "max_single_mg": 1000,
        "renal_adjust_egfr": 45, "renal_contraindicated_egfr": 30, "g6pd_risk": False, "allergy_group": "biguanide",
    },
    "empagliflozin": {
        "rxnorm": "1545653", "atc": "A10BK03", "display": "Empagliflozin 10mg Tablet", "display_zh": "恩格列淨 10毫克",
        "class": "sglt2_inhibitor", "hk_schedule": "PART_I_POISON", "max_daily_mg": 25, "max_single_mg": 25,
        "renal_adjust_egfr": 30, "g6pd_risk": False, "allergy_group": "sglt2_inhibitor",
    },
    "gliclazide": {
        "rxnorm": "4816", "atc": "A10BB09", "display": "Gliclazide MR 30mg Tablet", "display_zh": "格列齊特緩釋片 30毫克",
        "class": "sulfonylurea", "hk_schedule": "PART_I_POISON", "max_daily_mg": 120, "max_single_mg": 120,
        "g6pd_risk": True, "allergy_group": "sulfonamide",
    },
    "levothyroxine": {
        "rxnorm": "10582", "atc": "H03AA01", "display": "Levothyroxine 50mcg Tablet", "display_zh": "左甲狀腺素 50微克",
        "class": "thyroid_hormone", "hk_schedule": "PART_I_POISON", "max_daily_mg": 0.3, "max_single_mg": 0.3,
        "g6pd_risk": False, "allergy_group": "levothyroxine",
    },
    "carbimazole": {
        "rxnorm": "2047", "atc": "H03BB01", "display": "Carbimazole 5mg Tablet", "display_zh": "卡比馬唑 5毫克",
        "class": "antithyroid", "hk_schedule": "PART_I_POISON", "max_daily_mg": 60, "max_single_mg": 40,
        "g6pd_risk": False, "allergy_group": "thionamide", "agranulocytosis_warning": True, "teratogenic": True,
    },
    "fosfomycin": {
        "rxnorm": "4550", "atc": "J01XX01", "display": "Fosfomycin Trometamol 3g Sachet", "display_zh": "磷黴素 3克沖劑",
        "class": "phosphonic_antibiotic", "hk_schedule": "PART_I_POISON", "max_daily_mg": 3000, "max_single_mg": 3000,
        "g6pd_risk": False, "allergy_group": "fosfomycin",
    },
    "cephalexin": {
        "rxnorm": "2231", "atc": "J01DB01", "display": "Cephalexin 500mg Capsule", "display_zh": "頭孢氨苄 500毫克",
        "class": "cephalosporin", "hk_schedule": "PART_I_POISON", "max_daily_mg": 4000, "max_single_mg": 1000,
        "g6pd_risk": False, "allergy_group": "cephalosporin",
    },
}

HK_PRIMARY_CARE_FORMULARY: dict[str, dict[str, Any]] = {
    **HK_ACUTE_FORMULARY,
    **HK_CHRONIC_GP_FORMULARY,
}

# --- CVX HK Primary Care & Preventive Vaccines ---
CVX_VACCINES: dict[str, tuple[str, str]] = {
    "150": ("Influenza, injectable, quadrivalent, preservative free (IIV4)", "四價滅活季節性流感疫苗（無防腐劑）"),
    "158": ("Influenza, injectable, quadrivalent, contains preservative (IIV4)", "四價滅活季節性流感疫苗"),
    "171": ("Influenza, injectable, Madin Darby Canine Kidney, preservative free (ccIIV4)", "四價細胞培養季節性流感疫苗"),
    "185": ("Influenza, recombinant, quadrivalent, injectable, preservative free (RIV4)", "四價重組季節性流感疫苗"),
    "216": ("Pneumococcal conjugate vaccine 20-valent (PCV20)", "二十價肺炎球菌結合疫苗 (PCV20)"),
    "187": ("Zoster vaccine recombinant (RZV / Shingrix)", "重組帶狀疱疹疫苗（蛇針）"),
    "165": ("Human Papillomavirus 9-valent vaccine (HPV9)", "九價人類乳頭瘤病毒疫苗"),
    "43": ("Hepatitis B vaccine, adult", "成人乙型肝炎疫苗"),
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


_ACUTE_ALIASES: dict[str, str] = {
    "panadol": "paracetamol", "acetaminophen": "paracetamol", "撲熱息痛": "paracetamol", "必理痛": "paracetamol",
    "brufen": "ibuprofen", "nurofen": "ibuprofen", "布洛芬": "ibuprofen",
    "tamiflu": "oseltamivir", "特敏福": "oseltamivir", "奧司他韋": "oseltamivir",
    "augmentin": "amoxicillin-clavulanate", "安滅菌": "amoxicillin-clavulanate",
    "amoxil": "amoxicillin", "阿莫西林": "amoxicillin",
    "klacid": "clarithromycin", "克拉黴素": "clarithromycin",
    "bactrim": "co-trimoxazole", "septrin": "co-trimoxazole", "磺胺": "co-trimoxazole",
    "macrobid": "nitrofurantoin", "macrodantin": "nitrofurantoin", "呋喃妥因": "nitrofurantoin",
    "zyrtec": "cetirizine", "西替利嗪": "cetirizine",
    "piriton": "chlorpheniramine",
    "sudafed": "pseudoephedrine", "偽麻黃鹼": "pseudoephedrine",
    "stilnox": "zolpidem", "valium": "diazepam",
    "codeine linctus": "codeine", "可待因": "codeine",
}

_CHRONIC_ALIASES: dict[str, str] = {
    "coumadin": "warfarin", "marevan": "warfarin", "華法林": "warfarin",
    "lipitor": "atorvastatin", "立普妥": "atorvastatin", "阿托伐他汀": "atorvastatin",
    "zocor": "simvastatin", "辛伐他汀": "simvastatin",
    "crestor": "rosuvastatin", "瑞舒伐他汀": "rosuvastatin",
    "norvasc": "amlodipine", "脈優": "amlodipine", "氨氯地平": "amlodipine",
    "cozaar": "losartan", "氯沙坦": "losartan",
    "coversyl": "perindopril", "培哚普利": "perindopril",
    "iron bisglycinate": "ferrous bisglycinate", "gentle oral iron": "ferrous bisglycinate", "甘氨酸亞鐵": "ferrous bisglycinate",
    "ferrous sulphate": "ferrous sulfate", "硫酸亞鐵": "ferrous sulfate",
    "glucophage": "metformin", "二甲雙胍": "metformin",
    "jardiance": "empagliflozin", "恩格列淨": "empagliflozin",
    "diamicron": "gliclazide", "格列齊特": "gliclazide",
    "eltroxin": "levothyroxine", "euthyrox": "levothyroxine", "synthroid": "levothyroxine", "thyroxine": "levothyroxine", "左甲狀腺素": "levothyroxine",
    "neomercazole": "carbimazole", "卡比馬唑": "carbimazole",
    "monurol": "fosfomycin", "磷黴素": "fosfomycin",
    "keflex": "cephalexin", "cefalexin": "cephalexin", "頭孢氨苄": "cephalexin",
}


def lookup_drug(drug_name: str, *, include_chronic: bool = True) -> tuple[str, dict[str, Any]] | None:
    """Resolve generic/brand/Cantonese name to canonical HK primary-care formulary entry.

    Unlisted drugs (e.g. ivermectin) fail closed and return None.
    """
    norm = (drug_name or "").strip().lower()
    if not norm:
        return None
    formulary = HK_PRIMARY_CARE_FORMULARY if include_chronic else HK_ACUTE_FORMULARY
    aliases = {**_ACUTE_ALIASES, **_CHRONIC_ALIASES} if include_chronic else _ACUTE_ALIASES

    if norm in formulary:
        return norm, formulary[norm]
    for alias, canon in sorted(aliases.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in norm and canon in formulary:
            return canon, formulary[canon]
    for canon in sorted(formulary, key=len, reverse=True):
        if canon in norm:
            return canon, formulary[canon]
    return None
