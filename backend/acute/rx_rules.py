"""100% deterministic Hong Kong acute prescribing safety rules engine.

Zero LLM calls. Enforces tiered clinical safety rules locked for the Flu-Season Acute Care MVP:
- hard_block (cannot be overridden in acute teleconsult):
    * HK Cap. 134 Dangerous Drugs (codeine, zolpidem, diazepam, tramadol, opioids, benzodiazepines, z-drugs)
    * Unknown / unlisted drugs outside the HK acute formulary (safety floor)
    * Aspirin in pediatric patients (age < 16 — Reye syndrome risk)
- override_required (requires clinician explicit override reason >= 10 chars):
    * Allergy & cross-reactivity (penicillin -> amoxicillin/Augmentin; NSAID/aspirin -> ibuprofen/naproxen;
      sulfonamide -> co-trimoxazole; exact & group matches)
    * HK G6PD deficiency contraindications (co-trimoxazole, nitrofurantoin, aspirin)
    * Major drug-drug interactions (warfarin + NSAID/clarithromycin/co-trimoxazole; DOAC + NSAID;
      pseudoephedrine + MAOI or uncontrolled hypertension; dextromethorphan + SSRI/MAOI;
      clarithromycin + simvastatin/atorvastatin)
    * Renal & dose ceiling violations (daily/single mg limits, pediatric weight-based mg/kg/dose,
      oseltamivir when eGFR < 60 without <= 75mg/day reduction, nitrofurantoin when eGFR < 45)
- warn (advisory banner, no override needed):
    * Duplicate therapeutic class in same script
    * Sedating antihistamine driving/machinery caution
    * HK Cap. 138 Part I Poison dispensing label reminder
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Sequence

from .terminology import HK_ACUTE_FORMULARY, lookup_drug

Severity = Literal["hard_block", "override_required", "warn"]
ReportStatus = Literal["pass", "warn", "override_required", "hard_block"]

MIN_OVERRIDE_REASON_CHARS = 10

_SEVERITY_RANK: dict[str, int] = {
    "hard_block": 3,
    "override_required": 2,
    "warn": 1,
}

# Cap. 134 Dangerous Drugs not in HK_ACUTE_FORMULARY that must still be explicitly
# classified as Cap. 134 hard blocks (opioids, benzodiazepines, z-drugs).
_UNLISTED_CAP134_TERMS: dict[str, str] = {
    # Opioids
    "morphine": "opioid",
    "oxycodone": "opioid",
    "oxycontin": "opioid",
    "fentanyl": "opioid",
    "pethidine": "opioid",
    "meperidine": "opioid",
    "methadone": "opioid",
    "hydrocodone": "opioid",
    "hydromorphone": "opioid",
    "buprenorphine": "opioid",
    "dihydrocodeine": "opioid",
    "tapentadol": "opioid",
    "opioid": "opioid",
    # Benzodiazepines
    "alprazolam": "benzodiazepine",
    "xanax": "benzodiazepine",
    "lorazepam": "benzodiazepine",
    "ativan": "benzodiazepine",
    "clonazepam": "benzodiazepine",
    "rivotril": "benzodiazepine",
    "midazolam": "benzodiazepine",
    "dormicum": "benzodiazepine",
    "triazolam": "benzodiazepine",
    "temazepam": "benzodiazepine",
    "nitrazepam": "benzodiazepine",
    "bromazepam": "benzodiazepine",
    "lexotan": "benzodiazepine",
    "benzodiazepine": "benzodiazepine",
    # Z-drugs
    "zopiclone": "z_drug",
    "imovane": "z_drug",
    "eszopiclone": "z_drug",
    "zaleplon": "z_drug",
    "z-drug": "z_drug",
    "z_drug": "z_drug",
}

_CAP134_CLASSES = frozenset({"opioid", "benzodiazepine", "z_drug"})

# Canonical allergy group mappings for cross-reactivity detection
_ALLERGY_GROUP_ALIASES: dict[str, str] = {
    # Penicillin / beta-lactam
    "penicillin": "penicillin",
    "penicillins": "penicillin",
    "beta-lactam": "penicillin",
    "beta_lactam": "penicillin",
    "beta lactam": "penicillin",
    "amoxicillin": "penicillin",
    "amoxicillin-clavulanate": "penicillin",
    "augmentin": "penicillin",
    "amoxil": "penicillin",
    "ampicillin": "penicillin",
    "cloxacillin": "penicillin",
    "flucloxacillin": "penicillin",
    "盤尼西林": "penicillin",
    "青黴素": "penicillin",
    "阿莫西林": "penicillin",
    "安滅菌": "penicillin",
    # NSAIDs / Aspirin
    "nsaid": "nsaid",
    "nsaids": "nsaid",
    "aspirin": "nsaid",
    "salicylate": "nsaid",
    "ibuprofen": "nsaid",
    "brufen": "nsaid",
    "nurofen": "nsaid",
    "naproxen": "nsaid",
    "diclofenac": "nsaid",
    "voltaren": "nsaid",
    "mefenamic": "nsaid",
    "celecoxib": "nsaid",
    "消炎止痛藥": "nsaid",
    "阿士匹靈": "nsaid",
    "布洛芬": "nsaid",
    # Sulfonamides
    "sulfonamide": "sulfonamide",
    "sulfonamides": "sulfonamide",
    "sulfa": "sulfonamide",
    "sulpha": "sulfonamide",
    "co-trimoxazole": "sulfonamide",
    "cotrimoxazole": "sulfonamide",
    "sulfamethoxazole": "sulfonamide",
    "bactrim": "sulfonamide",
    "septrin": "sulfonamide",
    "磺胺": "sulfonamide",
    # Macrolides
    "macrolide": "macrolide",
    "macrolides": "macrolide",
    "clarithromycin": "macrolide",
    "klacid": "macrolide",
    "erythromycin": "macrolide",
    "azithromycin": "macrolide",
    "克拉黴素": "macrolide",
    # Paracetamol
    "paracetamol": "paracetamol",
    "acetaminophen": "paracetamol",
    "panadol": "paracetamol",
    "撲熱息痛": "paracetamol",
    "必理痛": "paracetamol",
    # Nitrofurans
    "nitrofuran": "nitrofuran",
    "nitrofurantoin": "nitrofuran",
    # Oseltamivir
    "oseltamivir": "oseltamivir",
    "tamiflu": "oseltamivir",
    "特敏福": "oseltamivir",
    "奧司他韋": "oseltamivir",
    # Antihistamines
    "antihistamine": "antihistamine",
    "antihistamines": "antihistamine",
    "cetirizine": "antihistamine",
    "zyrtec": "antihistamine",
    "chlorpheniramine": "antihistamine",
    "piriton": "antihistamine",
    # Sympathomimetics
    "sympathomimetic": "sympathomimetic",
    "pseudoephedrine": "sympathomimetic",
    "sudafed": "sympathomimetic",
    # Antitussive
    "dextromethorphan": "dextromethorphan",
}

# Drug-drug interaction classes
_WARFARIN_TERMS = ("warfarin", "coumadin", "marevan")
_WARFARIN_INTERACTING_FORMULARY = frozenset(
    {"ibuprofen", "naproxen", "aspirin", "clarithromycin", "co-trimoxazole"}
)

_DOAC_TERMS = (
    "apixaban", "eliquis",
    "rivaroxaban", "xarelto",
    "dabigatran", "pradaxa",
    "edoxaban", "lixiana",
    "doac",
)
_NSAID_FORMULARY = frozenset({"ibuprofen", "naproxen", "aspirin"})

_MAOI_TERMS = (
    "maoi",
    "monoamine oxidase",
    "phenelzine",
    "tranylcypromine",
    "selegiline",
    "rasagiline",
    "moclobemide",
    "isocarboxazid",
    "linezolid",
)

_SSRI_TERMS = (
    "ssri",
    "fluoxetine", "prozac",
    "sertraline", "zoloft",
    "citalopram", "cipramil",
    "escitalopram", "lexapro",
    "paroxetine", "seroxat",
    "fluvoxamine",
    "venlafaxine",
    "duloxetine",
)

_CLARITHROMYCIN_STATIN_TERMS = ("simvastatin", "zocor", "atorvastatin", "lipitor", "lovastatin")

_UNCONTROLLED_HTN_TERMS = (
    "uncontrolled hypertension",
    "uncontrolled htn",
    "severe hypertension",
    "hypertensive crisis",
    "malignant hypertension",
    "未受控高血壓",
)

# Frequency string -> doses per day mapping
_FREQ_MAP: dict[str, float] = {
    "od": 1.0,
    "qd": 1.0,
    "q24h": 1.0,
    "daily": 1.0,
    "once daily": 1.0,
    "on": 1.0,
    "nocte": 1.0,
    "hs": 1.0,
    "stat": 1.0,
    "bd": 2.0,
    "bid": 2.0,
    "q12h": 2.0,
    "twice daily": 2.0,
    "tds": 3.0,
    "tid": 3.0,
    "q8h": 3.0,
    "three times daily": 3.0,
    "qid": 4.0,
    "q6h": 4.0,
    "four times daily": 4.0,
    "q4h": 6.0,
    "q4-6h": 6.0,
    "q6-8h": 4.0,
    "q8-12h": 3.0,
}

# Normalize classes for duplicate therapy warnings
_DUP_THERAPY_CLASS_MAP: dict[str, str] = {
    "sedating_antihistamine": "antihistamine",
}


@dataclass(frozen=True)
class RxFinding:
    severity: Severity
    code: str
    drug: str
    message: str
    message_zh: str = ""
    rule_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "drug": self.drug,
            "message": self.message,
            "message_zh": self.message_zh,
            "rule_ref": self.rule_ref,
        }


@dataclass
class RxSafetyReport:
    status: ReportStatus
    findings: list[RxFinding] = field(default_factory=list)
    enriched_items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def hard_blocks(self) -> list[RxFinding]:
        return [f for f in self.findings if f.severity == "hard_block"]

    @property
    def overrides_required(self) -> list[RxFinding]:
        return [f for f in self.findings if f.severity == "override_required"]

    @property
    def warnings(self) -> list[RxFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def can_sign_without_override(self) -> bool:
        return not self.hard_blocks and not self.overrides_required

    def validate_override(self, override_reason: str | None) -> tuple[bool, str | None]:
        """Validate whether a prescription with this safety report may be signed by a clinician."""
        if self.hard_blocks:
            drugs = ", ".join(sorted({f.drug for f in self.hard_blocks}))
            return (
                False,
                f"Hard-blocked prescription item(s) ({drugs}) cannot be overridden in acute teleconsult.",
            )
        if self.overrides_required:
            reason = (override_reason or "").strip()
            if len(reason) < MIN_OVERRIDE_REASON_CHARS:
                return (
                    False,
                    f"Explicit documented clinical override reason (>= {MIN_OVERRIDE_REASON_CHARS} characters) is required.",
                )
        return True, None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_sign_without_override": self.can_sign_without_override,
            "counts": {
                "hard_block": len(self.hard_blocks),
                "override_required": len(self.overrides_required),
                "warn": len(self.warnings),
            },
            "hard_blocks": [f.to_dict() for f in self.hard_blocks],
            "overrides_required": [f.to_dict() for f in self.overrides_required],
            "warnings": [f.to_dict() for f in self.warnings],
            "findings": [f.to_dict() for f in self.findings],
            "enriched_items": self.enriched_items,
        }


def _get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            val = getattr(obj, name, None)
            if val is not None:
                return val
    return default


def _parse_age_years(patient_profile: Any, as_of: date | None = None) -> float | None:
    explicit_age = _get_attr(patient_profile, "age_years", "age")
    if explicit_age is not None:
        return float(explicit_age)
    dob = _get_attr(patient_profile, "dob", "birth_date")
    if not dob:
        return None
    if isinstance(dob, datetime):
        dob_date = dob.date()
    elif isinstance(dob, date):
        dob_date = dob
    else:
        try:
            dob_date = date.fromisoformat(str(dob).strip()[:10])
        except ValueError:
            return None
    ref = as_of or date.today()
    years = ref.year - dob_date.year - ((ref.month, ref.day) < (dob_date.month, dob_date.day))
    return float(years)


def _parse_numeric_mg(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    m = re.search(r"(\d+(?:\.\d+)?)", str(val))
    return float(m.group(1)) if m else None


def _parse_doses_per_day(item: dict[str, Any]) -> float | None:
    explicit = item.get("doses_per_day")
    if explicit is not None:
        return float(explicit)
    freq = str(item.get("frequency") or "").strip().lower()
    if not freq:
        return None
    # Strip leading "prn" qualifier while preserving the interval/rate
    cleaned = re.sub(r"\bprn\b", "", freq).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned in _FREQ_MAP:
        return _FREQ_MAP[cleaned]
    for token, rate in _FREQ_MAP.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", cleaned):
            return rate
    # Match "q<N>h" e.g. q4h -> 6, q6h -> 4
    m_qh = re.search(r"\bq\s*(\d+(?:\.\d+)?)\s*h(?:r|ours?)?\b", cleaned)
    if m_qh:
        hours = float(m_qh.group(1))
        if hours > 0:
            return 24.0 / hours
    # Match "<N>x/day", "<N> times daily", "<N>/day", "<N> times a day"
    m_times = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:x\s*/?\s*day|/\s*day|times?\s*(?:daily|a\s*day|per\s*day))\b", cleaned)
    if m_times:
        return float(m_times.group(1))
    return None


def _normalize_patient(patient_profile: Any, as_of: date | None = None) -> dict[str, Any]:
    allergies_raw = _get_attr(patient_profile, "allergies", "allergies_json", default=[]) or []
    meds_raw = _get_attr(patient_profile, "current_meds", "current_meds_json", default=[]) or []
    conditions_raw = _get_attr(patient_profile, "conditions", "conditions_json", default=[]) or []

    allergies = [str(a).strip() for a in allergies_raw if str(a).strip()]
    current_meds = [str(m).strip() for m in meds_raw if str(m).strip()]
    conditions = [str(c).strip() for c in conditions_raw if str(c).strip()]

    uncontrolled_htn = bool(_get_attr(patient_profile, "uncontrolled_hypertension", default=False))
    if not uncontrolled_htn:
        for cond in conditions:
            c_low = cond.lower()
            if any(term in c_low for term in _UNCONTROLLED_HTN_TERMS):
                uncontrolled_htn = True
                break
    systolic_bp = _get_attr(patient_profile, "systolic_bp")
    diastolic_bp = _get_attr(patient_profile, "diastolic_bp")
    if (systolic_bp is not None and float(systolic_bp) >= 160) or (
        diastolic_bp is not None and float(diastolic_bp) >= 100
    ):
        uncontrolled_htn = True

    pregnant = bool(_get_attr(patient_profile, "pregnant", "is_pregnant", default=False))
    if not pregnant:
        for cond in conditions:
            c_low = cond.lower()
            if any(term in c_low for term in ("pregnant", "pregnancy", "懷孕", "大肚", "孕婦")):
                pregnant = True
                break

    weight_raw = _get_attr(patient_profile, "weight_kg")
    egfr_raw = _get_attr(patient_profile, "egfr")

    return {
        "age_years": _parse_age_years(patient_profile, as_of=as_of),
        "weight_kg": float(weight_raw) if weight_raw is not None else None,
        "egfr": float(egfr_raw) if egfr_raw is not None else None,
        "g6pd_deficient": bool(_get_attr(patient_profile, "g6pd_deficient", default=False)),
        "pregnant": pregnant,
        "allergies": allergies,
        "current_meds": current_meds,
        "conditions": conditions,
        "uncontrolled_hypertension": uncontrolled_htn,
    }


def _resolve_allergy_groups(allergies: list[str]) -> tuple[set[str], set[str]]:
    """Return (exact_drug_names_lower, allergy_groups)."""
    exact: set[str] = set()
    groups: set[str] = set()
    for raw in allergies:
        norm = raw.strip().lower()
        if not norm or norm in {"none", "nkda", "nka", "nil", "no known drug allergies"}:
            continue
        exact.add(norm)
        if norm in _ALLERGY_GROUP_ALIASES:
            groups.add(_ALLERGY_GROUP_ALIASES[norm])
        for alias, grp in _ALLERGY_GROUP_ALIASES.items():
            if alias in norm:
                groups.add(grp)
        looked = lookup_drug(norm)
        if looked:
            canon, entry = looked
            exact.add(canon)
            if entry.get("allergy_group"):
                groups.add(entry["allergy_group"])
    return exact, groups


def _matches_any_term(med_list: list[str], terms: tuple[str, ...]) -> str | None:
    for med in med_list:
        m_low = med.lower()
        for term in terms:
            if term in m_low:
                return med
    return None


def _detect_unlisted_cap134(raw_name: str) -> str | None:
    norm = raw_name.strip().lower()
    for term, cls in _UNLISTED_CAP134_TERMS.items():
        if term in norm:
            return cls
    return None


def check_prescription_safety(
    patient_profile: Any,
    items: Sequence[dict[str, Any]],
    *,
    as_of: date | None = None,
    diagnosis_icd10: str | None = None,
) -> RxSafetyReport:
    """Run deterministic HK clinical safety checks on a proposed prescription.

    Args:
        patient_profile: Dict or ORM/dataclass object containing patient clinical attributes
            (`age`/`dob`, `weight_kg`, `egfr`, `g6pd_deficient`, `allergies`, `current_meds`,
            `conditions`, `uncontrolled_hypertension`, `systolic_bp`, `diastolic_bp`).
        items: Sequence of prescription item dicts (`drug`, `dose_mg`, `frequency`, `days`,
            optional `doses_per_day`, `daily_mg`).
        as_of: Optional reference date for computing age from `dob` (defaults to today).

    Returns:
        RxSafetyReport with tiered findings (`hard_block`, `override_required`, `warn`) and
        formulary-enriched items (`rxnorm`, `atc`, `display`, `display_zh`, `hk_schedule`).
    """
    patient = _normalize_patient(patient_profile, as_of=as_of)
    allergy_exact, allergy_groups = _resolve_allergy_groups(patient["allergies"])

    findings: list[RxFinding] = []
    enriched_items: list[dict[str, Any]] = []

    # Pre-resolve items so we can also detect co-prescribed drug interactions & duplicate classes
    resolved_records: list[tuple[dict[str, Any], str, str | None, dict[str, Any] | None]] = []
    co_prescribed_names: list[str] = []
    for raw_item in items:
        item = dict(raw_item)
        raw_drug = str(item.get("drug") or item.get("name") or item.get("drug_name") or "").strip()
        looked = lookup_drug(raw_drug) if raw_drug else None
        if looked:
            canon, entry = looked
            resolved_records.append((item, raw_drug, canon, entry))
            co_prescribed_names.append(canon)
        else:
            resolved_records.append((item, raw_drug, None, None))
            if raw_drug:
                co_prescribed_names.append(raw_drug.lower())

    seen_classes: dict[str, str] = {}
    seen_drugs: set[str] = set()

    for idx, (item, raw_drug, canon, entry) in enumerate(resolved_records):
        # 1. Formulary & HK Cap. 134 Dangerous Drug safety floor
        if canon is None or entry is None:
            unlisted_cls = _detect_unlisted_cap134(raw_drug) if raw_drug else None
            if unlisted_cls:
                findings.append(
                    RxFinding(
                        severity="hard_block",
                        code="cap134_dangerous_drug",
                        drug=raw_drug,
                        message=(
                            f"'{raw_drug}' is a Hong Kong Cap. 134 Dangerous Drug ({unlisted_cls}) "
                            "and is hard-blocked from acute teleconsultation prescribing."
                        ),
                        message_zh=f"「{raw_drug}」屬香港《危險藥物條例》（第134章）管制藥物，禁止於急性視像診症處方。",
                        rule_ref="HK Cap. 134",
                    )
                )
                enriched_items.append(
                    {
                        **item,
                        "drug": raw_drug,
                        "canonical_drug": None,
                        "rxnorm": "",
                        "atc": "",
                        "display": raw_drug,
                        "display_zh": "",
                        "hk_schedule": "DANGEROUS_DRUG",
                    }
                )
            else:
                findings.append(
                    RxFinding(
                        severity="hard_block",
                        code="unknown_formulary_drug",
                        drug=raw_drug or "<empty>",
                        message=(
                            f"'{raw_drug or '<empty>'}' is not in the approved Hong Kong acute "
                            "teleconsult formulary and cannot be prescribed."
                        ),
                        message_zh=f"「{raw_drug or '未指定藥物'}」不在核准的香港急性視像診症藥物名冊內，禁止處方。",
                        rule_ref="HK Acute Formulary Floor",
                    )
                )
                enriched_items.append(
                    {
                        **item,
                        "drug": raw_drug,
                        "canonical_drug": None,
                        "rxnorm": "",
                        "atc": "",
                        "display": raw_drug,
                        "display_zh": "",
                        "hk_schedule": "UNLISTED",
                    }
                )
            continue

        # Parse single and daily doses
        dose_mg = _parse_numeric_mg(item.get("dose_mg"))
        doses_per_day = _parse_doses_per_day(item)
        explicit_daily = _parse_numeric_mg(item.get("daily_mg"))
        if explicit_daily is not None:
            daily_mg: float | None = explicit_daily
        elif dose_mg is not None and doses_per_day is not None:
            daily_mg = round(dose_mg * doses_per_day, 4)
        elif dose_mg is not None:
            daily_mg = dose_mg
        else:
            daily_mg = None

        enriched = {
            **item,
            "drug": canon,
            "canonical_drug": canon,
            "rxnorm": entry["rxnorm"],
            "atc": entry["atc"],
            "display": entry["display"],
            "display_zh": entry["display_zh"],
            "hk_schedule": entry["hk_schedule"],
            "drug_class": entry["class"],
        }
        if dose_mg is not None:
            enriched["dose_mg"] = dose_mg
        if daily_mg is not None:
            enriched["daily_mg"] = daily_mg
        enriched_items.append(enriched)

        # 2. HK Cap. 134 Dangerous Drugs in formulary -> hard_block
        if entry["hk_schedule"] == "DANGEROUS_DRUG" or entry["class"] in _CAP134_CLASSES:
            findings.append(
                RxFinding(
                    severity="hard_block",
                    code="cap134_dangerous_drug",
                    drug=canon,
                    message=(
                        f"{entry['display']} is a Hong Kong Cap. 134 Dangerous Drug ({entry['class']}) "
                        "and is hard-blocked in acute teleconsultations."
                    ),
                    message_zh=f"{entry['display_zh']} 屬香港《危險藥物條例》（第134章）管制藥物，禁止於急性視像診症處方。",
                    rule_ref="HK Cap. 134",
                )
            )
            continue

        # 3. Pediatric Aspirin (age < 16 — Reye syndrome risk) -> hard_block
        age_years = patient["age_years"]
        if (canon == "aspirin" or entry.get("pediatric_contraindicated")) and age_years is not None and age_years < 16:
            findings.append(
                RxFinding(
                    severity="hard_block",
                    code="pediatric_aspirin_reye",
                    drug=canon,
                    message=(
                        f"Aspirin is hard-blocked for patients under 16 years old (age {age_years:.0f}) "
                        "due to risk of Reye syndrome."
                    ),
                    message_zh="16歲以下兒童禁用阿士匹靈，以免引發雷氏症候群（Reye syndrome）。",
                    rule_ref="HK Paediatric Safety / Reye Syndrome",
                )
            )

        # 4. Allergy & Cross-Reactivity -> override_required
        item_allergy_group = entry.get("allergy_group", "")
        if (
            canon in allergy_exact
            or raw_drug.lower() in allergy_exact
            or (item_allergy_group and item_allergy_group in allergy_groups)
        ):
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="allergy_contraindication",
                    drug=canon,
                    message=(
                        f"Allergy / cross-reactivity alert: {entry['display']} (group: {item_allergy_group}) "
                        f"conflicts with documented patient allergies ({', '.join(patient['allergies'])})."
                    ),
                    message_zh=f"藥物過敏／交叉過敏警示：{entry['display_zh']} 與病人過敏史衝突。",
                    rule_ref="Allergy & Cross-Reactivity",
                )
            )

        # 5. HK G6PD Deficiency Contraindications -> override_required
        if patient["g6pd_deficient"] and entry.get("g6pd_risk"):
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="g6pd_contraindication",
                    drug=canon,
                    message=(
                        f"HK G6PD deficiency contraindication: {entry['display']} carries haemolysis risk "
                        "in G6PD-deficient patients."
                    ),
                    message_zh=f"香港蠶豆症（G6PD缺乏症）禁忌：{entry['display_zh']} 可誘發急性溶血。",
                    rule_ref="HK DH G6PD Guidance",
                )
            )

        # 6. Major Drug-Drug Interactions -> override_required
        # Check against patient's active medications AND other items co-prescribed in this script
        other_script_meds = [m for j, m in enumerate(co_prescribed_names) if j != idx]
        concurrent_meds = patient["current_meds"] + other_script_meds

        warfarin_hit = _matches_any_term(concurrent_meds, _WARFARIN_TERMS)
        if warfarin_hit and canon in _WARFARIN_INTERACTING_FORMULARY:
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="major_drug_interaction",
                    drug=canon,
                    message=(
                        f"Major drug interaction: {entry['display']} with {warfarin_hit} significantly "
                        "increases bleeding risk and/or INR elevation."
                    ),
                    message_zh=f"嚴重藥物相互作用：{entry['display_zh']} 與華法林（Warfarin）同服會顯著增加出血風險。",
                    rule_ref="Major DDI / Anticoagulant",
                )
            )

        doac_hit = _matches_any_term(concurrent_meds, _DOAC_TERMS)
        if doac_hit and canon in _NSAID_FORMULARY:
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="major_drug_interaction",
                    drug=canon,
                    message=(
                        f"Major drug interaction: NSAID ({entry['display']}) with DOAC ({doac_hit}) "
                        "carries high risk of gastrointestinal and systemic bleeding."
                    ),
                    message_zh=f"嚴重藥物相互作用：非類固醇消炎止痛藥與新型抗凝血藥（{doac_hit}）同服增加嚴重出血風險。",
                    rule_ref="Major DDI / DOAC + NSAID",
                )
            )

        if canon == "pseudoephedrine":
            maoi_hit = _matches_any_term(concurrent_meds, _MAOI_TERMS)
            if maoi_hit:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="major_drug_interaction",
                        drug=canon,
                        message=(
                            f"Major drug interaction: Pseudoephedrine with MAOI ({maoi_hit}) risks "
                            "hypertensive crisis."
                        ),
                        message_zh="嚴重藥物相互作用：偽麻黃鹼與單胺氧化酶抑制劑（MAOI）同服可引致高血壓危象。",
                        rule_ref="Major DDI / Sympathomimetic + MAOI",
                    )
                )
            if patient["uncontrolled_hypertension"]:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="uncontrolled_hypertension_contraindication",
                        drug=canon,
                        message=(
                            "Pseudoephedrine is contraindicated in patients with uncontrolled hypertension "
                            "due to pressor effects."
                        ),
                        message_zh="未受控高血壓病人應避免使用偽麻黃鹼，以免血壓進一步升高。",
                        rule_ref="Cardiovascular Safety",
                    )
                )

        if canon == "dextromethorphan":
            sero_hit = _matches_any_term(concurrent_meds, _SSRI_TERMS + _MAOI_TERMS)
            if sero_hit:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="major_drug_interaction",
                        drug=canon,
                        message=(
                            f"Major drug interaction: Dextromethorphan with serotonergic agent ({sero_hit}) "
                            "carries risk of serotonin syndrome."
                        ),
                        message_zh=f"嚴重藥物相互作用：右美沙芬與 {sero_hit} 同服有血清素症候群風險。",
                        rule_ref="Major DDI / Serotonin Syndrome",
                    )
                )

        if canon == "clarithromycin":
            statin_hit = _matches_any_term(concurrent_meds, _CLARITHROMYCIN_STATIN_TERMS)
            if statin_hit:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="major_drug_interaction",
                        drug=canon,
                        message=(
                            f"Major drug interaction: Clarithromycin inhibits CYP3A4 metabolism of {statin_hit}, "
                            "creating severe myopathy and rhabdomyolysis risk."
                        ),
                        message_zh=f"嚴重藥物相互作用：克拉黴素與 {statin_hit} 同服可致橫紋肌溶解症風險。",
                        rule_ref="Major DDI / CYP3A4 Statin",
                    )
                )

        # 7. Dose Ceilings, Pediatric Weight-Based Dosing & Renal Adjustments -> override_required
        max_single = entry.get("max_single_mg")
        if dose_mg is not None and max_single is not None and dose_mg > float(max_single) + 1e-6:
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="single_dose_ceiling_exceeded",
                    drug=canon,
                    message=(
                        f"Single dose ceiling exceeded for {entry['display']}: "
                        f"{dose_mg:g}mg exceeds maximum single dose of {max_single}mg."
                    ),
                    message_zh=f"{entry['display_zh']} 單次劑量（{dose_mg:g}mg）超出上限（{max_single}mg）。",
                    rule_ref="Dose Ceiling",
                )
            )

        max_daily = entry.get("max_daily_mg")
        if daily_mg is not None and max_daily is not None and daily_mg > float(max_daily) + 1e-6:
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="daily_dose_ceiling_exceeded",
                    drug=canon,
                    message=(
                        f"Daily dose ceiling exceeded for {entry['display']}: "
                        f"{daily_mg:g}mg/day exceeds maximum daily dose of {max_daily}mg/day."
                    ),
                    message_zh=f"{entry['display_zh']} 每日總劑量（{daily_mg:g}mg）超出上限（{max_daily}mg）。",
                    rule_ref="Dose Ceiling",
                )
            )

        weight_kg = patient["weight_kg"]
        is_pediatric = (age_years is not None and age_years < 16) or (
            age_years is None and weight_kg is not None and weight_kg < 40.0
        )
        ped_mg_per_kg = entry.get("pediatric_mg_per_kg_dose")
        if is_pediatric and weight_kg is not None and weight_kg > 0 and ped_mg_per_kg and dose_mg is not None:
            ped_ceiling = float(ped_mg_per_kg) * weight_kg
            if dose_mg > ped_ceiling + 1e-6:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="pediatric_weight_dose_exceeded",
                        drug=canon,
                        message=(
                            f"Pediatric weight-based dose exceeded for {entry['display']}: "
                            f"{dose_mg:g}mg exceeds {ped_mg_per_kg}mg/kg x {weight_kg:g}kg = {ped_ceiling:g}mg/dose."
                        ),
                        message_zh=(
                            f"兒童按體重劑量超標：{entry['display_zh']} 單次 {dose_mg:g}mg "
                            f"超過 {ped_ceiling:g}mg 上限。"
                        ),
                        rule_ref="Pediatric Weight Dosing",
                    )
                )

        # 7b. Pregnancy Safety Rules (BNF / HK DH / RCOG) -> override_required
        if patient["pregnant"]:
            if canon in _NSAID_FORMULARY:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="pregnancy_nsaid_contraindication",
                        drug=canon,
                        message=(
                            f"Pregnancy contraindication: NSAID ({entry['display']}) carries risks of premature "
                            "ductus arteriosus closure and oligohydramnios (>=20 weeks); prefer Paracetamol."
                        ),
                        message_zh=f"懷孕期禁忌：孕婦應避免使用非類固醇消炎止痛藥（{entry['display_zh']}），以免影響胎兒動脈導管或羊水；建議改用撲熱息痛。",
                        rule_ref="Pregnancy Safety / NSAIDs",
                    )
                )
            elif canon == "pseudoephedrine":
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="pregnancy_decongestant_caution",
                        drug=canon,
                        message=(
                            "Pregnancy caution: Oral pseudoephedrine is discouraged in pregnancy due to "
                            "vasoconstriction and first-trimester fetal risk; prefer saline nasal spray."
                        ),
                        message_zh="懷孕期警示：孕婦應避免口服偽麻黃鹼，建議改用生理鹽水洗鼻。",
                        rule_ref="Pregnancy Safety / Decongestants",
                    )
                )
            elif canon == "co-trimoxazole":
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="pregnancy_folate_kernicterus_risk",
                        drug=canon,
                        message=(
                            "Pregnancy contraindication: Co-trimoxazole carries folate-antagonist risk in the 1st "
                            "trimester and neonatal kernicterus risk near term."
                        ),
                        message_zh="懷孕期禁忌：復方新諾明於懷孕早期具葉酸拮抗風險，後期具新生兒核黃疸風險。",
                        rule_ref="Pregnancy Safety / Sulfonamides",
                    )
                )
            elif canon == "clarithromycin":
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="pregnancy_macrolide_caution",
                        drug=canon,
                        message=(
                            "Pregnancy caution: Clarithromycin should be avoided in pregnancy where safer "
                            "alternatives exist (consult BNF/RCOG)."
                        ),
                        message_zh="懷孕期警示：孕婦一般不建議使用克拉黴素，請醫生評估更安全替代方案。",
                        rule_ref="Pregnancy Safety / Macrolides",
                    )
                )

        # 7c. Pediatric < 6y Dextromethorphan (HK DH / FDA cough-suppressant safety warning) -> override_required
        if canon == "dextromethorphan" and age_years is not None and age_years < 6:
            findings.append(
                RxFinding(
                    severity="override_required",
                    code="pediatric_antitussive_under_6",
                    drug=canon,
                    message=(
                        f"Pediatric safety alert: Dextromethorphan is not recommended for children under 6 years "
                        f"(age {age_years:.0f}) per HK Department of Health OTC cough medicine guidance."
                    ),
                    message_zh="兒童用藥警示：香港衞生署不建議 6 歲以下兒童使用右美沙芬止咳藥。",
                    rule_ref="HK DH Paediatric Cough Safety (<6y)",
                )
            )

        # 7d. Pediatric Oseltamivir weight-band check (IDSA / HK CHP) -> override_required
        if canon == "oseltamivir" and is_pediatric and weight_kg is not None and weight_kg > 0 and dose_mg is not None:
            if weight_kg <= 15.0:
                band_max = 30.0
            elif weight_kg <= 23.0:
                band_max = 45.0
            elif weight_kg <= 40.0:
                band_max = 60.0
            else:
                band_max = 75.0
            if dose_mg > band_max + 1e-6:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="pediatric_oseltamivir_weight_band",
                        drug=canon,
                        message=(
                            f"Pediatric Oseltamivir weight-band exceeded: {dose_mg:g}mg/dose exceeds "
                            f"{band_max:g}mg/dose ceiling for weight {weight_kg:g}kg (HK CHP / IDSA)."
                        ),
                        message_zh=f"兒童特敏福體重分層超標：{weight_kg:g}kg 單次劑量上限為 {band_max:g}mg（現處方 {dose_mg:g}mg）。",
                        rule_ref="HK CHP / IDSA Pediatric Oseltamivir",
                    )
                )

        egfr = patient["egfr"]
        if egfr is not None:
            if canon == "oseltamivir" and egfr < 30:
                if daily_mg is None or daily_mg > 30.0 + 1e-6:
                    findings.append(
                        RxFinding(
                            severity="override_required",
                            code="renal_dose_adjustment_required",
                            drug=canon,
                            message=(
                                f"Severe renal adjustment required for Oseltamivir at eGFR {egfr:g} (< 30): "
                                f"daily dose ({daily_mg if daily_mg is not None else 'unspecified'}mg/day) "
                                "must be reduced to <= 30mg/day (30mg OD)."
                            ),
                            message_zh=f"嚴重腎功能調整警示：eGFR {egfr:g}（<30）時，特敏福每日劑量需減至 <= 30mg（每日一次 30mg）。",
                            rule_ref="Renal Dosing / eGFR < 30",
                        )
                    )
            elif canon == "oseltamivir" and egfr < 60:
                if daily_mg is None or daily_mg > 75.0 + 1e-6:
                    findings.append(
                        RxFinding(
                            severity="override_required",
                            code="renal_dose_adjustment_required",
                            drug=canon,
                            message=(
                                f"Renal dose adjustment required for Oseltamivir at eGFR {egfr:g} (< 60): "
                                f"daily dose ({daily_mg if daily_mg is not None else 'unspecified'}mg/day) "
                                "must be reduced to <= 75mg/day."
                            ),
                            message_zh=f"腎功能調整警示：eGFR {egfr:g}（<60）時，特敏福每日劑量需減至 <= 75mg。",
                            rule_ref="Renal Dosing / eGFR < 60",
                        )
                    )
            if canon == "nitrofurantoin" and egfr < 45:
                findings.append(
                    RxFinding(
                        severity="override_required",
                        code="renal_contraindication",
                        drug=canon,
                        message=(
                            f"Nitrofurantoin is contraindicated when eGFR < 45 (patient eGFR {egfr:g}) "
                            "due to reduced urinary efficacy and toxicity risk."
                        ),
                        message_zh=f"腎功能禁忌：eGFR {egfr:g}（<45）禁用呋喃妥因。",
                        rule_ref="Renal Contraindication / eGFR < 45",
                    )
                )

        # 8. Advisory Warnings (warn — no override required)
        # HK CHP IMPACT / NICE NG84 Antibiotic Stewardship for uncomplicated viral influenza/URTI
        if (
            diagnosis_icd10
            and diagnosis_icd10.strip().upper() in {"J10.1", "J11.1", "J06.9"}
            and canon in {"amoxicillin", "amoxicillin-clavulanate", "clarithromycin", "co-trimoxazole", "nitrofurantoin"}
        ):
            findings.append(
                RxFinding(
                    severity="warn",
                    code="antibiotic_stewardship_viral_illness",
                    drug=canon,
                    message=(
                        f"HK CHP IMPACT / NICE NG84 Antibiotic Stewardship: {entry['display']} prescribed for "
                        f"viral diagnosis ({diagnosis_icd10}). Routine empirical antibiotics are not indicated "
                        "for uncomplicated influenza/URTI unless secondary bacterial infection is documented."
                    ),
                    message_zh=f"香港衞生防護中心 IMPACT 抗生素管理提示：病毒性診斷（{diagnosis_icd10}）一般無需常規處方抗生素（{entry['display_zh']}），請記錄繼發細菌感染依據。",
                    rule_ref="HK CHP IMPACT / NICE NG84",
                )
            )

        norm_class = _DUP_THERAPY_CLASS_MAP.get(entry["class"], entry["class"])
        if canon in seen_drugs or norm_class in seen_classes:
            other = canon if canon in seen_drugs else seen_classes[norm_class]
            findings.append(
                RxFinding(
                    severity="warn",
                    code="duplicate_therapeutic_class",
                    drug=canon,
                    message=(
                        f"Duplicate therapeutic class ({norm_class}): {canon} co-prescribed with {other} "
                        "in the same prescription."
                    ),
                    message_zh=f"重複藥理類別提示（{norm_class}）：同一處方同時包含 {canon} 及 {other}。",
                    rule_ref="Duplicate Therapy",
                )
            )
        else:
            seen_classes[norm_class] = canon
        seen_drugs.add(canon)

        if canon == "chlorpheniramine" or entry["class"] == "sedating_antihistamine":
            findings.append(
                RxFinding(
                    severity="warn",
                    code="sedating_antihistamine_caution",
                    drug=canon,
                    message=(
                        f"Sedation advisory: {entry['display']} may cause drowsiness; "
                        "advise patient against driving or operating machinery."
                    ),
                    message_zh=f"嗜睡提示：{entry['display_zh']} 可能引致昏昏欲睡，服後切勿駕駛或操作機械。",
                    rule_ref="Sedation Caution",
                )
            )

        if entry["hk_schedule"] == "PART_I_POISON":
            findings.append(
                RxFinding(
                    severity="warn",
                    code="cap138_part1_poison_label",
                    drug=canon,
                    message=(
                        f"HK Cap. 138 Part I Poison reminder: {entry['display']} requires statutory "
                        "dispensing label (patient name, dosage, clinic name/address, date)."
                    ),
                    message_zh=f"香港第138章第一部毒藥提示：{entry['display_zh']} 須附法定配藥標籤。",
                    rule_ref="HK Cap. 138 Part I Poison",
                )
            )

    # Sort findings by severity descending (hard_block -> override_required -> warn)
    findings.sort(key=lambda f: -_SEVERITY_RANK[f.severity])

    if any(f.severity == "hard_block" for f in findings):
        status: ReportStatus = "hard_block"
    elif any(f.severity == "override_required" for f in findings):
        status = "override_required"
    elif any(f.severity == "warn" for f in findings):
        status = "warn"
    else:
        status = "pass"

    return RxSafetyReport(status=status, findings=findings, enriched_items=enriched_items)


_CROSS_CHART_THERAPEUTIC_CLASSES: dict[str, tuple[str, tuple[str, ...]]] = {
    "statin": ("statin", ("atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "lovastatin", "fluvastatin", "lipitor", "zocor", "crestor")),
    "nsaid": ("NSAID", ("ibuprofen", "naproxen", "diclofenac", "mefenamic", "celecoxib", "etoricoxib", "indomethacin", "meloxicam", "brufen", "voltaren")),
    "ras_blocker": ("ACEi/ARB", ("lisinopril", "perindopril", "enalapril", "ramipril", "losartan", "valsartan", "telmisartan", "irbesartan", "candesartan")),
    "anticoagulant": ("anticoagulant", ("warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "marevan", "eliquis", "xarelto", "pradaxa")),
    "ccb": ("calcium-channel blocker", ("amlodipine", "nifedipine", "felodipine", "diltiazem", "verapamil", "norvasc")),
}


def check_duplicate_therapeutic_classes(medications: list[str]) -> list[RxFinding]:
    """Detect duplicate drugs from the same therapeutic class across active/discharge medication lists."""
    by_class: dict[str, list[tuple[str, str]]] = {}
    for raw in medications:
        low = str(raw or "").lower()
        for cls_key, (cls_label, members) in _CROSS_CHART_THERAPEUTIC_CLASSES.items():
            for member in members:
                if member in low:
                    by_class.setdefault(cls_key, []).append((member, str(raw).strip()))
                    break

    findings: list[RxFinding] = []
    for cls_key, entries in by_class.items():
        unique_members = []
        for member, orig in entries:
            if not any(m == member for m, _ in unique_members):
                unique_members.append((member, orig))
        if len(unique_members) >= 2:
            cls_label = _CROSS_CHART_THERAPEUTIC_CLASSES[cls_key][0]
            names = " + ".join(orig for _, orig in unique_members)
            findings.append(
                RxFinding(
                    severity="override_required",
                    code=f"duplicate_class_{cls_key}",
                    drug=unique_members[0][0],
                    message=(
                        f"Duplicate {cls_label} on file: {names}. "
                        "Stop the superseded agent before signing repeats."
                    ),
                    message_zh=f"重複{cls_label}類別藥物：{names}。簽發處方前請確認停用舊藥。",
                    rule_ref=f"Duplicate Class Reconciliation ({cls_label})",
                )
            )
    return findings
