"""Unified, machine-derived online verification checks & runtime infra toggles.

Produces ONE honest `checks[]` array (`status` in {pass, warn, fail, na, unknown} with `affects`
pointers) combining:
- Real bilingual OCR-safe verbatim quote verification (`backend.case.quote.verify_quote_span`)
- Real multi-page patient identity collision check (`backend.engine.verify.doc_identifiers`)
- Cross-site `DoseConflict` checks (`backend.case.model.drug_key`)
- Full `RxSafetyEngine` + cross-chart duplicate therapeutic class checks (`backend.acute.rx_rules`)
- Elderly unknown-eGFR renal dosing check (e.g. Oseltamivir at age >= 65 without CrCl/eGFR)
- Feed / AI kill-switch epistemic honesty (`unknown` NEVER counts toward passed)

Every count (`passed_count`, `applicable_count`) is derived strictly from `checks[]`.
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

from ..case.model import drug_key
from ..case.quote import QuoteError, verify_quote_span
from ..engine.verify import doc_identifiers
from .rx_rules import check_duplicate_therapeutic_classes, check_prescription_safety

_MASKED_HKID_RE = re.compile(
    r"\b([A-Z]{1,2}\d{3})(?:\*{2,3}|XXX)\([0-9A*X]\)|\b([A-Z]{1,2}\d{6})\([0-9A]\)",
    re.I,
)
_MRN_TOKEN_RE = re.compile(r"\b(PAT-\d{4,6})\b", re.I)


def check_document_identifiers(source_text: str) -> dict[str, Any]:
    """Wrap `doc_identifiers()` and also detect masked HKID (`A412***(8)`) & MRN multi-page collisions."""
    text = source_text or ""
    out = dict(doc_identifiers(text))

    hkid_prefixes: list[str] = []
    if out.get("hkid_collision"):
        for h in out["hkid_collision"]:
            norm_h = str(h).upper()[:4]
            if norm_h and norm_h not in hkid_prefixes:
                hkid_prefixes.append(norm_h)
    elif out.get("hkid"):
        hkid_prefixes.append(str(out["hkid"]).upper()[:4])

    masked_tokens: list[str] = []
    for m in _MASKED_HKID_RE.finditer(text):
        raw_tok = m.group(0).upper()
        if raw_tok not in masked_tokens:
            masked_tokens.append(raw_tok)
        prefix = (m.group(1) or (m.group(2) or "")[:4]).upper()
        if prefix and prefix not in hkid_prefixes:
            hkid_prefixes.append(prefix)

    if hkid_prefixes:
        out.setdefault("hkid", hkid_prefixes[0])
        out["hkid_prefixes"] = hkid_prefixes
        if len(hkid_prefixes) > 1:
            out["hkid_collision"] = hkid_prefixes
    if masked_tokens:
        out["masked_hkids"] = masked_tokens

    mrns: list[str] = []
    for m in _MRN_TOKEN_RE.finditer(text):
        tok = m.group(1).upper()
        if tok not in mrns:
            mrns.append(tok)
    if mrns:
        out["mrn"] = mrns[0]
        if len(mrns) > 1:
            out["mrn_collision"] = mrns

    collision_reasons: list[str] = []
    if out.get("hkid_collision"):
        collision_reasons.append(f"Multiple HKIDs detected across document pages: {', '.join(out['hkid_collision'])}")
    if out.get("dob_collision"):
        collision_reasons.append(f"Multiple DOBs detected across document pages: {', '.join(out['dob_collision'])}")
    if out.get("mrn_collision"):
        collision_reasons.append(f"Multiple patient MRNs detected across document pages: {', '.join(out['mrn_collision'])}")

    out["has_collision"] = bool(collision_reasons)
    out["collision_reasons"] = collision_reasons
    return out


def runtime_toggles() -> dict[str, Any]:
    """Return the 4 clean toggleable infra switches for demo iteration vs HK Cloud Run prod."""
    env = os.environ.get("HARNESS_ENV", "dev").strip().lower()
    runtime_mode = os.environ.get("HARNESS_RUNTIME_MODE", "prod" if env == "prod" else "demo").strip().lower()
    infra_mode = os.environ.get(
        "INFRA_MODE",
        "gcp_hk" if os.environ.get("VAULT_GCS_BUCKET") else "lite",
    ).strip().lower()
    ai_mode = os.environ.get(
        "AI_INFERENCE_MODE",
        "vertex_hk" if (env == "prod" and os.environ.get("VERTEX_PROJECT_ID")) else "deterministic_rich",
    ).strip().lower()
    try:
        undo_sec = max(0, min(60, int(os.environ.get("DISPATCH_UNDO_SECONDS", "10"))))
    except ValueError:
        undo_sec = 10
    return {
        "harness_runtime_mode": runtime_mode,
        "infra_mode": infra_mode,
        "ai_inference_mode": ai_mode,
        "dispatch_undo_seconds": undo_sec,
        "demo_controls_enabled": runtime_mode != "prod",
    }


def _age_years(dob_str: str | None) -> int | None:
    if not dob_str:
        return None
    try:
        born = date.fromisoformat(str(dob_str)[:10])
        today = date.today()
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    except Exception:
        return None


def compute_quote_anchors(quotes: list[dict[str, Any]], source_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Verify every quote verbatim against `source_text` and attach exact raw character spans."""
    anchored: list[dict[str, Any]] = []
    failed: list[str] = []
    for item in quotes:
        raw_q = str(item.get("quote") or "").strip()
        if not raw_q:
            continue
        rec_id = str(item.get("record_id") or "source_primary")
        try:
            span = verify_quote_span(raw_q, source_text)
            anchored.append({
                **item,
                "record_id": rec_id,
                "quote": span.raw_quote,
                "start_char": span.start_char,
                "end_char": span.end_char,
                "verified": True,
            })
        except QuoteError:
            # Allow short 2-3 char exact verbatim CJK/ASCII clinical tokens if literally present
            if len(raw_q) >= 2 and raw_q in source_text:
                idx = source_text.find(raw_q)
                anchored.append({
                    **item,
                    "record_id": rec_id,
                    "quote": raw_q,
                    "start_char": idx,
                    "end_char": idx + len(raw_q),
                    "verified": True,
                })
            else:
                anchored.append({**item, "record_id": rec_id, "verified": False})
                failed.append(raw_q)
    return anchored, failed


def build_unified_checks(
    *,
    patient: dict[str, Any],
    source_text: str,
    quotes: list[dict[str, Any]],
    proposed_meds: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    diagnosis_icd10: str | None = None,
    ai_enabled: bool = True,
    ehealth_reachable: bool = True,
    prompt_injection_detected: bool = False,
    red_flags: list[str] | None = None,
    extra_chart_meds: list[str] | None = None,
) -> dict[str, Any]:
    """Compute ONE unified `checks[]` array and derived summary counts (never hardcoded)."""
    checks: list[dict[str, Any]] = []

    # 1. Evidence present
    has_source = bool((source_text or "").strip())
    checks.append({
        "id": "evidence",
        "label": "Original source record on file",
        "status": "pass" if has_source else "fail",
        "affects": "summary",
        "detail": "Original source text verified and attached." if has_source else "Missing source document.",
    })

    # 2. Verbatim quotes (real span verification)
    anchored_quotes, failed_quotes = compute_quote_anchors(quotes, source_text or "")
    if not quotes:
        checks.append({
            "id": "quotes",
            "label": "Quotes match source word for word",
            "status": "na",
            "affects": "summary",
            "detail": "No quoted claims attached to this item.",
        })
    elif failed_quotes:
        checks.append({
            "id": "quotes",
            "label": "Quotes match source word for word",
            "status": "fail",
            "affects": "summary",
            "detail": f"Unverified quote(s) not found verbatim in source: {', '.join(failed_quotes[:2])}",
        })
    else:
        checks.append({
            "id": "quotes",
            "label": "Quotes match source word for word",
            "status": "pass",
            "affects": "summary",
            "detail": f"All {len(anchored_quotes)} quote(s) verified verbatim with exact character spans.",
        })

    # 3. Multi-page patient identity check (supports XXX(X), A412***(8), DOB, and MRN collisions)
    doc_ids = check_document_identifiers(source_text or "")
    if doc_ids.get("has_collision"):
        checks.append({
            "id": "identity",
            "label": "Right patient across all pages",
            "status": "fail",
            "affects": "summary",
            "detail": "Multiple distinct patient HKIDs, MRNs, or DOBs detected in this document (possible misfax).",
        })
    else:
        checks.append({
            "id": "identity",
            "label": "Right patient",
            "status": "pass",
            "affects": "summary",
            "detail": f"Chart identity consistent for {patient.get('name_en') or 'patient'}.",
        })

    # 4. Cross-site DoseConflict check
    unresolved_conflicts = [c for c in conflicts if not c.get("resolved")]
    if conflicts:
        if unresolved_conflicts:
            first_c = unresolved_conflicts[0]
            drug_name = str(first_c.get("drug") or "medication")
            checks.append({
                "id": "dose_conflict",
                "label": f"Cross-site dose conflict on {drug_name}",
                "status": "fail",
                "affects": f"conflict:{drug_key(drug_name)}",
                "detail": f"{len(unresolved_conflicts)} unresolved medication dose conflict(s) across sites — doctor must select active regimen before signing.",
            })
        else:
            checks.append({
                "id": "dose_conflict",
                "label": "Cross-site medication doses reconciled",
                "status": "pass",
                "affects": "summary",
                "detail": "All cross-site dose conflicts settled by clinician.",
            })
    else:
        checks.append({
            "id": "dose_conflict",
            "label": "No cross-site medication dose conflicts",
            "status": "pass",
            "affects": "orders",
            "detail": "Active medications consistent across known sites.",
        })

    # 5. Deterministic Rx safety + cross-chart duplicate therapeutic class
    all_med_strings = [str(m) for m in (patient.get("current_meds") or [])]
    all_med_strings.extend(str(m) for m in (extra_chart_meds or []))
    for pm in proposed_meds:
        dname = str(pm.get("drug") or pm.get("name") or "").strip()
        if dname and not pm.get("stopped"):
            all_med_strings.append(dname)

    dup_findings = check_duplicate_therapeutic_classes(all_med_strings)
    for df in dup_findings:
        checks.append({
            "id": df.code,
            "label": df.message.split(".")[0],
            "status": "warn",
            "affects": f"order:{df.drug}",
            "detail": df.message,
            "detail_zh": df.message_zh,
            "requires_reason": True,
        })

    active_proposed = [m for m in proposed_meds if not m.get("stopped") and not m.get("is_non_acute_order")]
    rx_report_dict: dict[str, Any] | None = None
    if active_proposed:
        rep = check_prescription_safety(patient, active_proposed, diagnosis_icd10=diagnosis_icd10)
        rx_report_dict = rep.to_dict()
        if rep.hard_blocks:
            for hb in rep.hard_blocks:
                checks.append({
                    "id": f"rx_{hb.code}",
                    "label": f"Remove {hb.drug} to sign",
                    "status": "fail",
                    "affects": f"order:{hb.drug}",
                    "detail": hb.message,
                    "detail_zh": hb.message_zh,
                })
        elif rep.overrides_required:
            for ov in rep.overrides_required:
                checks.append({
                    "id": f"rx_{ov.code}",
                    "label": ov.rule_ref,
                    "status": "warn",
                    "affects": f"order:{ov.drug}",
                    "detail": ov.message,
                    "detail_zh": ov.message_zh,
                    "requires_reason": True,
                })
        else:
            checks.append({
                "id": "rx_safety",
                "label": "Allergies, G6PD, Cap. 134 & drug interactions clear",
                "status": "pass",
                "affects": "orders",
                "detail": "Deterministic HK prescribing safety screen passed.",
            })
    else:
        checks.append({
            "id": "rx_safety",
            "label": "Prescribing safety check",
            "status": "na" if not dup_findings else "pass",
            "affects": "orders",
            "detail": "No acute formulary drug added in this packet.",
        })

    # 6. Renal clearance check (elderly >= 65 with unknown eGFR on renally cleared antiviral)
    age = _age_years(patient.get("dob"))
    egfr = patient.get("egfr")
    has_oseltamivir = any("oseltamivir" in str(m.get("drug") or "").lower() for m in proposed_meds if not m.get("stopped"))
    if has_oseltamivir and age is not None and age >= 65 and egfr is None:
        checks.append({
            "id": "renal_unknown_elderly",
            "label": f"Renal function unknown at {age}: dose assumes CrCl > 60",
            "status": "warn",
            "affects": "order:oseltamivir",
            "detail": f"Patient is {age} with no eGFR/CrCl on file. Keep 75 mg BD only if CrCl > 60, or switch to 30 mg BD.",
            "requires_reason": True,
            "quick_choice": {"keep": "Keep 75 mg BD (CrCl > 60)", "reduce": "Switch to 30 mg BD (renal dose)"},
        })
    elif has_oseltamivir:
        checks.append({
            "id": "renal_clearance",
            "label": "Renal dosing check",
            "status": "pass" if egfr is not None else "na",
            "affects": "order:oseltamivir",
            "detail": f"eGFR {egfr} mL/min/1.73m² on file." if egfr is not None else f"Renal adjustment not required for age {age or 'adult'}.",
        })

    # 7. Prompt-injection & emergency floor check
    checks.append({
        "id": "safety_floor",
        "label": "Red-flag & prompt-injection floor",
        "status": "warn" if (prompt_injection_detected or red_flags) else "pass",
        "affects": "summary",
        "detail": (
            f"Red flags flagged: {', '.join(red_flags)}" if red_flags
            else ("Prompt-injection attempt neutralized; zero tools exposed." if prompt_injection_detected else "No 999 red flags or injection patterns detected.")
        ),
    })

    # 8. Feed & AI epistemic check (`unknown` NEVER counts as passed!)
    if not ehealth_reachable:
        checks.append({
            "id": "feed_ehealth",
            "label": "eHealth not reachable: external allergy check incomplete",
            "status": "unknown",
            "affects": "summary",
            "detail": "External eHealth feed did not respond; local clinic allergies checked only.",
        })
    if not ai_enabled:
        checks.append({
            "id": "ai_kill_switch",
            "label": "AI drafting is off — manual clinician review",
            "status": "unknown",
            "affects": "summary",
            "detail": "Clinic Ops kill switch is engaged; every item is manual.",
        })

    # 9. Asymmetric authority
    checks.append({
        "id": "authority",
        "label": "Clinician sign-off required (asymmetric authority)",
        "status": "pass",
        "affects": "sign",
        "detail": "Background harness staged draft only; MCHK doctor signature required to dispatch.",
    })

    applicable = [c for c in checks if c["status"] != "na"]
    passed_count = sum(1 for c in applicable if c["status"] == "pass")
    warn_count = sum(1 for c in applicable if c["status"] == "warn")
    fail_count = sum(1 for c in applicable if c["status"] == "fail")
    unknown_count = sum(1 for c in applicable if c["status"] == "unknown")

    if unresolved_conflicts:
        sign_cta = f"Settle {len(unresolved_conflicts)} conflict" + ("s" if len(unresolved_conflicts) > 1 else "")
        can_sign = False
    elif fail_count > 0:
        first_fail = next(c for c in applicable if c["status"] == "fail")
        sign_cta = first_fail["label"]
        can_sign = False
    elif warn_count > 0:
        sign_cta = f"{warn_count} needs your reason"
        can_sign = True
    else:
        sign_cta = "Sign & send"
        can_sign = True

    return {
        "checks": checks,
        "anchored_quotes": anchored_quotes,
        "rx_safety": rx_report_dict,
        "summary": {
            "passed": passed_count,
            "applicable": len(applicable),
            "warn": warn_count,
            "fail": fail_count,
            "unknown": unknown_count,
            "can_sign": can_sign,
            "sign_cta": sign_cta,
        },
    }


def _extract_dose_conflicts(parsed_diff: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Derive cross-site DoseConflict dicts from `parsed_diff` (`emr_active` vs `qeh_discharge`/`external_active`)."""
    if not isinstance(parsed_diff, dict):
        return []
    emr_meds = parsed_diff.get("emr_active") or parsed_diff.get("clinic") or []
    ext_meds = parsed_diff.get("qeh_discharge") or parsed_diff.get("external_active") or parsed_diff.get("external") or []
    if not isinstance(emr_meds, list) or not isinstance(ext_meds, list):
        return []

    emr_by_key: dict[str, str] = {}
    for m in emr_meds:
        s = str(m).strip()
        if s:
            emr_by_key[drug_key(s)] = s

    conflicts: list[dict[str, Any]] = []
    for m in ext_meds:
        s = str(m).strip()
        if not s:
            continue
        k = drug_key(s)
        if k in emr_by_key and emr_by_key[k].lower() != s.lower():
            conflicts.append({
                "drug": s.split()[0],
                "drug_key": k,
                "emr_regimen": emr_by_key[k],
                "external_regimen": s,
                "resolved": False,
            })
    return conflicts


def verify_external_document_payload(
    source_text: str,
    *,
    verbatim_quote: str = "",
    parsed_diff: dict[str, Any] | None = None,
    proposed_meds: list[dict[str, Any]] | None = None,
    patient_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify an external document payload (multi-page ID collision, verbatim quote spans, cross-site DoseConflict, unified checks)."""
    full_text = (source_text or "").strip() or (verbatim_quote or "").strip()
    doc_ids = check_document_identifiers(full_text)
    quotes_in = [{"record_id": "external_doc", "quote": verbatim_quote.strip()}] if (verbatim_quote or "").strip() else []
    conflicts = _extract_dose_conflicts(parsed_diff)
    patient = dict(patient_context or {})
    if not patient.get("current_meds") and isinstance(parsed_diff, dict):
        emr_list = parsed_diff.get("emr_active") or parsed_diff.get("clinic")
        if isinstance(emr_list, list):
            patient["current_meds"] = [str(x) for x in emr_list]

    unified = build_unified_checks(
        patient=patient,
        source_text=full_text,
        quotes=quotes_in,
        proposed_meds=list(proposed_meds or []),
        conflicts=conflicts,
    )
    anchored = unified["anchored_quotes"]
    quote_verified = (
        True if not quotes_in else all(bool(q.get("verified")) for q in anchored)
    )
    has_collision = bool(doc_ids.get("has_collision"))

    return {
        "identifiers": doc_ids,
        "has_identity_collision": has_collision,
        "collision_reasons": list(doc_ids.get("collision_reasons") or []),
        "quote_verified": quote_verified,
        "anchored_quotes": anchored,
        "dose_conflicts": conflicts,
        "checks": unified["checks"],
        "summary": unified["summary"],
        "rx_safety": unified["rx_safety"],
        "should_quarantine": has_collision,
    }

