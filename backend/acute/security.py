"""Security, privacy, RBAC/ABAC, PHI redaction, WhatsApp zero-leakage guard, and FHIR R4 export.

Enforces:
- HK PDPO (Cap. 486) DPP1-DPP6 & PCPD AI Model Framework controls
- MCHK Telemedicine Guidelines (MCHK reg no verification, advertising/inducement ban)
- Section 17 Invariants: zero clinical content or ID numbers in WhatsApp; zero autonomous Rx/cert;
  strict role separation (patient, clinician, clinic_admin, corporate_admin, agent); OTP step-up
- OWASP ASVS L2 headers, rate limiting, constant-time secret verification, PHI-scrubbed logging
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, Request

from .terminology import HK_DIAGNOSES, LOINC_OBSERVATIONS, SNOMED_SYMPTOMS, SYSTEMS, CVX_VACCINES

HKT = timezone(timedelta(hours=8))
JWT_SECRET = os.environ.get("JWT_SECRET", "tmrw-hk-acute-jwt-dev-secret-change-in-prod-32b")
JWT_ALG = "HS256"

# Hong Kong MCHK Registration Number pattern (e.g. M12345 or L01234)
MCHK_REG_RE = re.compile(r"^[ML]\d{5}$")

# --- PHI Scrubbing Patterns (HKID, HK Phone, DOB, Email, Clinical Names) ---
_HKID_RE = re.compile(r"\b[A-Z]{1,2}\d{6}\(?[0-9A]\)?\b", re.I)
_HK_PHONE_RE = re.compile(r"(?:\+?852[-\s]?)?[2356789]\d{3}[-\s]?\d{4}\b")
_DOB_RE = re.compile(r"\b(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class PHIScrubber:
    """Scrubs direct Hong Kong identifiers before stdout logs or cross-border inference."""

    @staticmethod
    def scrub(text: str, extra_names: list[str] | None = None) -> tuple[str, list[str]]:
        if not text:
            return text, []
        redactions: list[str] = []
        out = text
        if _HKID_RE.search(out):
            out = _HKID_RE.sub("[REDACTED_HKID]", out)
            redactions.append("hkid")
        if _HK_PHONE_RE.search(out):
            out = _HK_PHONE_RE.sub("[REDACTED_PHONE]", out)
            redactions.append("phone")
        if _DOB_RE.search(out):
            out = _DOB_RE.sub("[REDACTED_DOB]", out)
            redactions.append("dob")
        if _EMAIL_RE.search(out):
            out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
            redactions.append("email")
        for name in extra_names or []:
            clean = name.strip()
            if len(clean) >= 2 and clean.lower() in out.lower():
                out = re.sub(re.escape(clean), "[REDACTED_NAME]", out, flags=re.I)
                redactions.append("name")
        return out, sorted(set(redactions))


class PHISafeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg, _ = PHIScrubber.scrub(record.msg)
        return True


# --- WhatsApp Zero-Leakage Guard (Section 17 & 19 Hard Invariant) ---
# WhatsApp is the doorbell, NEVER the room: zero clinical terms, symptoms, drugs, diagnoses, or IDs.
_WA_FORBIDDEN_CLINICAL = [
    # English symptoms / diagnoses / drugs / labs / clinical terms
    r"\bfever\b", r"\bcough\b", r"\bsore throat\b", r"\binfluenza\b", r"\bflu\b(?!\s+vaccin|\s+shot|\s+season\s+campaign)",
    r"\bcovid\b", r"\bpneumonia\b", r"\bbronchitis\b", r"\bdiarrh?oea\b", r"\bvomit", r"\bchest pain\b",
    r"\bbreathless", r"\bshortness of breath\b", r"\bdiagnos", r"\bsymptom", r"\bprescription\b",
    r"\bparacetamol\b", r"\bibuprofen\b", r"\boseltamivir\b", r"\btamiflu\b", r"\bamoxicillin\b",
    r"\baugmentin\b", r"\bwarfarin\b", r"\bcodeine\b", r"\bantibiotic", r"\ballerg", r"\bg6pd\b",
    r"\bmg\b", r"\bmmol\b", r"\b38\.\d\b", r"\b39\.\d\b", r"\b40\.\d\b", r"\bsick[- ]?leave\s+for\b",
    r"\bnormal result", r"\babnormal\b", r"\bpositive\b", r"\bnegative\b", r"\bhba1c\b", r"\bpotassium\b",
    # Cantonese / Traditional Chinese clinical terms
    r"發燒", r"咳嗽", r"喉嚨痛", r"流感病毒", r"肺炎", r"氣管炎", r"肚瀉", r"嘔吐", r"胸痛", r"心口痛",
    r"氣促", r"呼吸困難", r"診斷", r"症狀", r"病徵", r"處方", r"藥物", r"抗生素", r"撲熱息痛", r"特敏福",
    r"布洛芬", r"阿莫西林", r"過敏", r"蠶豆症", r"異常", r"陽性", r"陰性", r"血糖", r"血壓",
    # ID numbers & sensitive identity requests (WhatsApp policy bans requesting ID numbers)
    r"\bhkid\b", r"\bid\s*number\b", r"身份證", r"身分證", r"出生日期", r"\bdate of birth\b", r"\bdob\b",
    r"\b[A-Z]{1,2}\d{6}\(?[0-9A]\)?\b",
]
_WA_FORBIDDEN_PROMO = [
    # MCHK Code on Professional Information — no discounts/inducements/claims of superiority
    r"\bdiscount\b", r"\b\d+%\s*off\b", r"\bfree\s+consult", r"\bbest\s+doctor", r"\bspecial\s+offer\b",
    r"折扣", r"優惠價", r"免費診症", r"全港最好",
]


class WhatsAppLeakageError(ValueError):
    """Raised when an outbound WhatsApp message violates the zero-clinical / zero-ID invariant."""


class WhatsAppLeakageGuard:
    """Scans 100% of outbound WhatsApp messages & templates before dispatch."""

    @classmethod
    def scan(cls, text: str) -> list[str]:
        violations: list[str] = []
        for pat in _WA_FORBIDDEN_CLINICAL:
            if re.search(pat, text, re.I):
                violations.append(f"clinical_or_id_leak:{pat}")
        for pat in _WA_FORBIDDEN_PROMO:
            if re.search(pat, text, re.I):
                violations.append(f"mchk_promotion_violation:{pat}")
        return violations

    @classmethod
    def assert_clean(cls, text: str) -> None:
        v = cls.scan(text)
        if v:
            raise WhatsAppLeakageError(f"Blocked WhatsApp outbound message due to invariant violation: {v[:3]}")


# --- Password, Token & OTP Utilities ---
def hash_secret(raw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_secret(raw: str, stored: str) -> bool:
    try:
        scheme, salt, hex_dk = stored.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return hmac.compare_digest(dk.hex(), hex_dk)
    except Exception:
        return False


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"****{digits[-4:]}" if len(digits) >= 4 else "****"


def mask_hkid(hkid: str) -> str:
    clean = re.sub(r"\s+", "", hkid.upper())
    return f"{clean[:4]}XXX(X)" if len(clean) >= 4 else "XXXX(X)"


def valid_mchk_reg(reg_no: str | None) -> bool:
    return bool(reg_no and MCHK_REG_RE.match(reg_no.strip().upper()))


# --- JWT & RBAC / ABAC ---
def mint_jwt(
    *,
    subject_id: str,
    role: str,
    tenant_id: str,
    step_up_verified: bool = False,
    corporate_id: str | None = None,
    mchk_reg_no: str | None = None,
    ttl_minutes: int = 120,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject_id,
        "role": role,
        "tid": tenant_id,
        "step_up": step_up_verified,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    if corporate_id:
        payload["cid"] = corporate_id
    if mchk_reg_no:
        payload["mchk"] = mchk_reg_no
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "Session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Invalid authentication token") from exc


def extract_claims(request: Request, *, allowed_roles: tuple[str, ...], require_step_up: bool = False) -> dict[str, Any]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    claims = decode_jwt(auth.split(" ", 1)[1].strip())
    if claims.get("role") not in allowed_roles:
        raise HTTPException(403, f"Role '{claims.get('role')}' is not permitted for this action")
    if require_step_up and not claims.get("step_up"):
        raise HTTPException(403, "OTP step-up verification required before accessing clinical data")
    return claims


# --- In-Memory Sliding-Window Rate Limiter ---
class SlidingRateLimiter:
    def __init__(self, max_requests: int = 120, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_requests:
            raise HTTPException(429, "Rate limit exceeded; please retry shortly")
        q.append(now)


# --- HL7 FHIR R4 Bundle Serializer (PDPO DPP6 DAR & IPS Alignment) ---
def build_fhir_r4_bundle(
    *,
    patient: dict[str, Any],
    consents: list[dict[str, Any]],
    encounters: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    prescriptions: list[dict[str, Any]],
    immunizations: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Serialize a patient's record into an HL7 FHIR R4 Collection Bundle (IPS-aligned)."""
    entries: list[dict[str, Any]] = []

    def add(resource: dict[str, Any]) -> None:
        entries.append({"fullUrl": f"urn:uuid:{resource['id']}", "resource": resource})

    add({
        "resourceType": "Patient",
        "id": patient["id"],
        "identifier": [
            {"system": "http://tmrw.hk/fhir/hkid-masked", "value": patient.get("hkid_masked") or ""},
        ],
        "active": patient.get("status") == "active",
        "name": [{"text": patient.get("name_en") or ""}, {"text": patient.get("name_zh") or ""}],
        "gender": {"M": "male", "F": "female"}.get(patient.get("sex") or "", "unknown"),
        "birthDate": patient.get("dob"),
        "extension": [
            {"url": "http://tmrw.hk/fhir/StructureDefinition/ehealth-consent", "valueBoolean": bool(patient.get("ehealth_consent"))},
            {"url": "http://tmrw.hk/fhir/StructureDefinition/g6pd-deficient", "valueBoolean": bool(patient.get("g6pd_deficient"))},
        ],
    })

    for allergy in patient.get("allergies", []):
        add({
            "resourceType": "AllergyIntolerance",
            "id": f"alg-{sha256_hex(patient['id'] + allergy)[:12]}",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
            "code": {"text": allergy},
            "patient": {"reference": f"Patient/{patient['id']}"},
        })

    for c in consents:
        add({
            "resourceType": "Consent",
            "id": c["id"],
            "status": "active" if c["granted"] else "rejected",
            "scope": {"coding": [{"system": "http://tmrw.hk/fhir/consent-scope", "code": c["scope"]}]},
            "patient": {"reference": f"Patient/{patient['id']}"},
            "dateTime": c["recorded_at"],
            "policyRule": {"text": c["pics_version"]},
        })

    for enc in encounters:
        diag_codings = []
        if enc.get("icd10_code") in HK_DIAGNOSES:
            d = HK_DIAGNOSES[enc["icd10_code"]]
            diag_codings.append({"system": SYSTEMS["icd10"], "code": d["icd10"], "display": d["icd10_display"]})
            diag_codings.append({"system": SYSTEMS["icpc2"], "code": d["icpc2"], "display": d["icpc2_display"]})
        add({
            "resourceType": "Encounter",
            "id": enc["id"],
            "status": "finished" if enc["status"] == "completed" else "in-progress",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "VR", "display": "virtual"},
            "subject": {"reference": f"Patient/{patient['id']}"},
            "reasonCode": [
                {"coding": [{"system": SYSTEMS["snomed"], "code": sc, "display": SNOMED_SYMPTOMS[sc][0]}]}
                for sc in (enc.get("snomed_codes") or []) if sc in SNOMED_SYMPTOMS
            ],
        })
        if diag_codings:
            add({
                "resourceType": "Condition",
                "id": f"cond-{enc['id']}",
                "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
                "code": {"coding": diag_codings},
                "subject": {"reference": f"Patient/{patient['id']}"},
                "encounter": {"reference": f"Encounter/{enc['id']}"},
            })

    for obs in observations:
        loinc_meta = LOINC_OBSERVATIONS.get(obs["loinc_code"], (obs.get("display", ""), "", obs.get("ucum_unit", "")))
        res: dict[str, Any] = {
            "resourceType": "Observation",
            "id": obs["id"],
            "status": "final",
            "code": {"coding": [{"system": SYSTEMS["loinc"], "code": obs["loinc_code"], "display": loinc_meta[0]}]},
            "subject": {"reference": f"Patient/{patient['id']}"},
            "effectiveDateTime": obs["recorded_at"],
        }
        if obs.get("value_num") is not None:
            res["valueQuantity"] = {"value": obs["value_num"], "unit": obs.get("ucum_unit") or loinc_meta[2], "system": SYSTEMS["ucum"]}
        else:
            res["valueString"] = obs.get("value_text") or ""
        add(res)

    for rx in prescriptions:
        for idx, item in enumerate(rx.get("items") or []):
            add({
                "resourceType": "MedicationRequest",
                "id": f"{rx['id']}-{idx}",
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {
                    "coding": [
                        {"system": SYSTEMS["rxnorm"], "code": str(item.get("rxnorm", "")), "display": item.get("display", item.get("drug", ""))},
                        {"system": SYSTEMS["atc"], "code": str(item.get("atc", "")), "display": item.get("display", item.get("drug", ""))},
                    ],
                },
                "subject": {"reference": f"Patient/{patient['id']}"},
                "authoredOn": rx["signed_at"],
                "requester": {"identifier": {"system": "http://mchk.org.hk/reg", "value": rx["mchk_reg_no"]}},
                "dosageInstruction": [{"text": f"{item.get('dose_mg', '')}mg {item.get('frequency', '')} x {item.get('days', '')}d"}],
            })

    for cert in certificates:
        add({
            "resourceType": "DocumentReference",
            "id": cert["id"],
            "status": "current",
            "type": {"text": "Medical Sick-Leave Certificate (HK Registered Medical Practitioner)"},
            "subject": {"reference": f"Patient/{patient['id']}"},
            "date": cert["signed_at"],
            "author": [{"identifier": {"system": "http://mchk.org.hk/reg", "value": cert["mchk_reg_no"]}}],
            "content": [{"attachment": {"contentType": "application/pdf", "hash": cert["verification_hash"]}}],
        })

    for imm in immunizations:
        vax_disp = CVX_VACCINES.get(imm["cvx_code"], (imm.get("vaccine_display", ""), ""))[0]
        add({
            "resourceType": "Immunization",
            "id": imm["id"],
            "status": "completed" if imm["status"] == "completed" else "not-done",
            "vaccineCode": {"coding": [{"system": SYSTEMS["cvx"], "code": imm["cvx_code"], "display": vax_disp}]},
            "patient": {"reference": f"Patient/{patient['id']}"},
            "occurrenceDateTime": imm["slot_at"],
        })

    for ae in audit_events:
        add({
            "resourceType": "AuditEvent",
            "id": ae["id"],
            "type": {"code": ae["action"]},
            "recorded": ae["created_at"],
            "agent": [{"who": {"identifier": {"value": ae["actor_id"]}}, "role": [{"text": ae["actor_role"]}]}],
        })

    return {
        "resourceType": "Bundle",
        "id": f"dar-{patient['id']}-{int(time.time())}",
        "type": "collection",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "meta": {"profile": ["http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips"]},
        "total": len(entries),
        "entry": entries,
    }
