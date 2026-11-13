"""Facts in the longitudinal case log.

A fact is one extracted line, frozen at append time. Confirming, rejecting or closing it adds a new
fact; nothing here rewrites an earlier one. Dose conflicts are not a stored kind: the projection
derives them from live regimens that disagree across sites.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

PROVENANCE = ("gp", "in_group", "ha_sopc", "private_hospital", "ehealth", "patient")
CLINICAL_KINDS = (
    "medication", "dose_change", "referral", "admission", "discharge",
    "condition", "allergy", "observation",
)
DOCTOR_KINDS = ("confirm", "reject", "close")
# The engine port already forbids these writes. They are not fact kinds either.
FORBIDDEN_KINDS = ("dismiss", "downgrade", "reassure", "delete", "update")
AGENT_ACTORS = ("agent", "engine")
DOSE_ACTIONS = ("start", "stop", "increase", "decrease", "change")

_REQUIRED = {
    "medication": ("drug", "dose"),
    "dose_change": ("drug", "action"),
    "referral": ("specialty",),
    "admission": ("site",),
    "discharge": ("site",),
    "condition": ("name",),
    "allergy": ("substance",),
    "observation": ("name", "value"),
}
_DROP = {
    "sodium", "calcium", "potassium", "tartrate", "besylate", "hydrochloride", "hcl",
    "tablet", "tablets", "capsule", "capsules", "oral", "mg", "mcg", "ml",
    "daily", "nocte", "bd", "tds", "od", "mane",
}
_FREQ = {
    "nocte": "nocte", "nocturnal": "nocte", "at night": "nocte", "night": "nocte",
    "daily": "daily", "od": "daily", "once daily": "daily", "every day": "daily",
    "bd": "bd", "bid": "bd", "twice daily": "bd",
    "tds": "tds", "tid": "tds", "three times daily": "tds",
    "om": "om", "mane": "om", "every morning": "om", "morning": "om",
}
_DOSE = re.compile(r"(\d+(?:\.\d+)?)(mcg|mg|ml|iu|units|unit|g)?")


class CaseError(ValueError):
    """The fact or the record is not usable."""


class AuthorityError(CaseError):
    """This actor may not append that kind of fact."""


def drug_key(name: str) -> str:
    """Fold salts and dose words so 'Warfarin Sodium' and 'warfarin' are one drug."""
    tokens = re.findall(r"[a-z]+", str(name).lower())
    kept = [t for t in tokens if t not in _DROP]
    return " ".join(kept) if kept else " ".join(tokens)


def dose_key(dose: str) -> str:
    """'40 mg' and '40mg' compare equal. Empty when the line does not state a dose."""
    m = _DOSE.search(str(dose).lower().replace(" ", ""))
    if not m:
        return ""
    num = m.group(1)
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    unit = m.group(2) or ""
    if unit == "units":
        unit = "unit"
    return f"{num}{unit}"


def frequency_key(freq: str) -> str:
    s = " ".join(str(freq).lower().replace(".", "").split())
    return _FREQ.get(s, s)


def freeze_body(kind: str, body: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """A plain, immutable copy. Callers cannot change a stored fact by mutating the dict they passed."""
    raw = dict(body or {})
    missing = [k for k in _REQUIRED.get(kind, ()) if k not in raw or raw[k] in ("", None)]
    if kind == "dose_change":
        action = str(raw.get("action") or "")
        if action not in DOSE_ACTIONS:
            raise CaseError(f"dose_change action must be one of {DOSE_ACTIONS}")
        if action != "stop" and raw.get("dose") in ("", None):
            missing.append("dose")
    if missing:
        raise CaseError(f"{kind} needs {', '.join(missing)}")
    if kind == "medication" and str(raw.get("status") or "active") not in ("active", "stopped"):
        raise CaseError("medication status is active or stopped")
    if kind == "referral" and str(raw.get("urgency") or "routine") not in ("routine", "urgent"):
        raise CaseError("referral urgency is routine or urgent")
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise CaseError("body keys must be strings")
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        else:
            raise CaseError(f"body values must be plain values, not {type(value).__name__}")
    if kind == "medication" and "status" not in clean:
        clean["status"] = "active"
    if kind == "medication" and "dispensed" not in clean:
        clean["dispensed"] = False
    if kind == "dose_change" and "dispensed" not in clean:
        clean["dispensed"] = False
    if kind == "referral" and "urgency" not in clean:
        clean["urgency"] = "routine"
    if kind == "referral" and "destination" not in clean:
        clean["destination"] = ""
    if kind == "condition" and "status" not in clean:
        clean["status"] = "active"
    if kind == "allergy" and "status" not in clean:
        clean["status"] = "active"
    return MappingProxyType(clean)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One source the log can quote. Text is fixed once the record is added."""

    id: str
    patient_id: str
    text: str
    at: datetime
    kind: str
    provenance: str

    def to_dict(self) -> dict:
        return {"id": self.id, "patient_id": self.patient_id, "text": self.text,
                "at": self.at.isoformat(), "kind": self.kind, "provenance": self.provenance}


@dataclass(frozen=True, slots=True)
class Fact:
    """One append. `subject_id` is set only on a doctor's confirm, reject or close."""

    id: str
    patient_id: str
    kind: str
    record_id: str
    quote: str
    provenance: str
    at: datetime
    actor: str
    actor_id: str
    subject_id: str | None
    body: Mapping[str, Any]
    seq: int

    def to_dict(self) -> dict:
        return {
            "id": self.id, "patient_id": self.patient_id, "kind": self.kind, "record_id": self.record_id,
            "quote": self.quote, "provenance": self.provenance, "at": self.at.isoformat(),
            "actor": self.actor, "actor_id": self.actor_id, "subject_id": self.subject_id,
            "body": dict(self.body), "seq": self.seq,
        }


__all__ = [
    "AGENT_ACTORS", "AuthorityError", "CLINICAL_KINDS", "CaseError", "DOCTOR_KINDS", "DOSE_ACTIONS",
    "FORBIDDEN_KINDS", "Fact", "PROVENANCE", "QuoteError", "SourceRecord",
    "dose_key", "drug_key", "freeze_body", "frequency_key",
]
