"""PHI-scrubbed clinical context builder for Stage-2 LLM extraction, drafting, and companion replies.

Extracts structured clinical facts (active medications, dose conflicts, allergies, conditions,
recent vitals/observations, G6PD, eGFR, pregnancy) from either a longitudinal `CaseLog`
or a patient/observations dict while guaranteeing zero direct Hong Kong PII (name, HKID, DOB,
phone, email) ever enters the model prompt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.acute.security import PHIScrubber


def _scrub_str(val: Any, extra_names: list[str] | None = None) -> str:
    text = str(val or "").strip()
    if not text:
        return ""
    scrubbed, _ = PHIScrubber.scrub(text, extra_names=extra_names)
    return scrubbed


def build_scrubbed_clinical_context(
    *,
    patient: dict[str, Any] | None = None,
    observations: list[dict[str, Any]] | None = None,
    case_log: Any | None = None,
    patient_id: str | None = None,
    as_of: datetime | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a strictly PHI-free clinical summary dict suitable for L1 LLM prompts."""
    pat = patient or {}
    extra_names = [
        str(pat.get(k) or "").strip()
        for k in ("name_en", "name_zh", "name")
        if pat.get(k)
    ]

    active_meds: list[str] = []
    allergies: list[str] = []
    conditions: list[str] = []
    dose_conflicts: list[dict[str, Any]] = []
    recent_obs: list[dict[str, Any]] = []

    # 1. Pull longitudinal facts from CaseLog projection if supplied
    if case_log is not None and patient_id:
        try:
            view = case_log.project(patient_id, as_of)
            for m in getattr(view, "medications", ()) or ():
                label = _scrub_str(m.text() if hasattr(m, "text") else getattr(m, "drug", ""), extra_names)
                if label and label not in active_meds:
                    active_meds.append(label)
            for a in getattr(view, "allergies", ()) or ():
                label = _scrub_str(a.text() if hasattr(a, "text") else getattr(a, "substance", ""), extra_names)
                if label and label not in allergies:
                    allergies.append(label)
            for c in getattr(view, "conditions", ()) or ():
                label = _scrub_str(c.text() if hasattr(c, "text") else getattr(c, "name", ""), extra_names)
                if label and label not in conditions:
                    conditions.append(label)
            for cf in getattr(view, "conflicts", ()) or ():
                dose_conflicts.append({
                    "drug": _scrub_str(getattr(cf, "drug_key", ""), extra_names),
                    "summary": _scrub_str(cf.text() if hasattr(cf, "text") else str(cf), extra_names),
                    "sides": [
                        {
                            "provenance": getattr(s, "provenance", ""),
                            "text": _scrub_str(s.text() if hasattr(s, "text") else str(s), extra_names),
                        }
                        for s in (getattr(cf, "sides", ()) or ())
                    ],
                })
        except Exception:
            pass

    # 2. Merge structured patient clinical flags (NEVER name, HKID, DOB, phone, email)
    for med in pat.get("current_meds") or []:
        clean = _scrub_str(med, extra_names)
        if clean and clean not in active_meds:
            active_meds.append(clean)

    for alg in pat.get("allergies") or []:
        clean = _scrub_str(alg, extra_names)
        if clean and clean not in allergies:
            allergies.append(clean)

    for cond in pat.get("comorbidities") or []:
        clean = _scrub_str(cond, extra_names)
        if clean and clean not in conditions:
            conditions.append(clean)

    for cf in conflicts or []:
        if isinstance(cf, dict):
            dose_conflicts.append({
                "drug": _scrub_str(cf.get("drug") or cf.get("title") or "", extra_names),
                "summary": _scrub_str(cf.get("summary") or cf.get("detail") or cf.get("title") or "", extra_names),
            })

    # 3. Merge recent clinical observations (LOINC, display, value only)
    for obs in (observations or [])[:10]:
        if not isinstance(obs, dict):
            continue
        entry: dict[str, Any] = {
            "loinc_code": str(obs.get("loinc_code") or ""),
            "display": _scrub_str(obs.get("display") or "", extra_names),
        }
        if obs.get("value_text"):
            entry["value"] = _scrub_str(obs["value_text"], extra_names)
        elif obs.get("value_num") is not None:
            entry["value"] = f"{obs['value_num']} {obs.get('ucum_unit') or ''}".strip()
        if entry.get("value") or entry.get("display"):
            recent_obs.append(entry)

    ctx: dict[str, Any] = {
        "active_medications": active_meds,
        "allergies": allergies,
        "conditions": conditions,
        "dose_conflicts": dose_conflicts,
        "recent_observations": recent_obs,
        "g6pd_deficient": bool(pat.get("g6pd_deficient")),
        "pregnant": bool(pat.get("pregnant")),
    }
    if pat.get("age") is not None:
        try:
            ctx["age_years"] = int(pat["age"])
        except (TypeError, ValueError):
            pass
    if pat.get("egfr") is not None:
        try:
            ctx["egfr"] = float(pat["egfr"])
        except (TypeError, ValueError):
            pass
    if pat.get("weight_kg") is not None:
        try:
            ctx["weight_kg"] = float(pat["weight_kg"])
        except (TypeError, ValueError):
            pass
    return ctx
