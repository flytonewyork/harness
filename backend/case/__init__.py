"""Longitudinal case memory: an append-only, quote-checked fact log.

This is the chart beside the follow-up list. It is not a chat and it does not write to a patient.
The agent appends facts it has extracted. A doctor confirms, rejects or closes by appending another
fact. The projection at `as_of` is what later waves read:

- case/wire: `log.project(patient_id, as_of)` then `medication_lines()`, `open_referrals()`,
  `medications`, `conflicts`, `not_dispensed_here`, `admissions`, `conditions`, `allergies`.
- case/ui: `view.to_dict()` on the patient payload. `strip` is the case strip (each line has `text`,
  `quote`, `record_id`, `provenance`, `at`). The queue item keeps its own trace.

Every fact has a `record_id`, a verbatim `quote`, and a `provenance`
(`gp`, `in_group`, `ha_sopc`, `private_hospital`, `ehealth`, `patient`).
"""
from __future__ import annotations

from .log import CaseLog
from .model import (
    AGENT_ACTORS, CLINICAL_KINDS, DOCTOR_KINDS, FORBIDDEN_KINDS, PROVENANCE,
    AuthorityError, CaseError, Fact, SourceRecord,
    dose_key, drug_key, frequency_key,
)
from .project import AdmissionView, CaseView, DoseConflict, NamedView, ReferralView, Regimen
from .quote import QuoteError, norm, verify_quote

__all__ = [
    "AGENT_ACTORS", "AdmissionView", "AuthorityError", "CLINICAL_KINDS", "CaseError", "CaseLog",
    "CaseView", "DOCTOR_KINDS", "DoseConflict", "FORBIDDEN_KINDS", "Fact", "NamedView", "PROVENANCE",
    "QuoteError", "ReferralView", "Regimen", "SourceRecord",
    "dose_key", "drug_key", "frequency_key", "norm", "verify_quote",
]
