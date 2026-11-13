"""Append-only store of case facts.

The agent and the engine append extracted facts. A doctor confirms, rejects or closes by appending a new
fact that points at the earlier one. There is no update and no delete. `at` is the time the fact joins the
case (the source record's available time, or the time of the doctor's act). `project(as_of)` shows the case
as it stood at that moment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .model import (
    AGENT_ACTORS, CLINICAL_KINDS, DOCTOR_KINDS, FORBIDDEN_KINDS, PROVENANCE,
    AuthorityError, CaseError, Fact, SourceRecord, freeze_body,
)
from .project import CaseView, project_case
from .quote import verify_quote


class CaseLog:
    """The case store for one clinic. Facts only; opening an item stays on the engine port."""

    def __init__(self) -> None:
        self._records: dict[str, SourceRecord] = {}
        self._facts: list[Fact] = []
        self._seq = 0

    def __len__(self) -> int:
        return len(self._facts)

    def add_record(self, record_id: str, text: str, *, patient_id: str, at: datetime,
                   provenance: str, kind: str = "") -> SourceRecord:
        if not record_id or not patient_id:
            raise CaseError("a record needs an id and a patient")
        if provenance not in PROVENANCE:
            raise CaseError(f"provenance must be one of {PROVENANCE}")
        if record_id in self._records:
            raise CaseError(f"record {record_id} is already in the log")
        if not isinstance(at, datetime):
            raise CaseError("record time must be a datetime")
        rec = SourceRecord(record_id, patient_id, str(text), at, kind, provenance)
        self._records[record_id] = rec
        return rec

    def record(self, record_id: str) -> SourceRecord:
        try:
            return self._records[record_id]
        except KeyError:
            raise CaseError(f"unknown record {record_id}") from None

    def facts(self, patient_id: str | None = None, as_of: datetime | None = None) -> tuple[Fact, ...]:
        """Facts in append order. `as_of` hides anything that joined the case after that moment."""
        rows = self._facts
        if patient_id is not None:
            rows = [f for f in rows if f.patient_id == patient_id]
        if as_of is not None:
            rows = [f for f in rows if f.at <= as_of]
        return tuple(rows)

    def project(self, patient_id: str, as_of: datetime | None = None) -> CaseView:
        visible = self.facts(patient_id, as_of)
        when = as_of if as_of is not None else (visible[-1].at if visible else datetime.min)
        return project_case(visible, patient_id, when)

    def append(self, *, patient_id: str, kind: str, record_id: str, quote: str, provenance: str,
               at: datetime, actor: str = "agent", actor_id: str = "engine",
               body: Mapping[str, Any] | None = None) -> Fact:
        """Append one extracted fact. Refuses doctor operations and any in-place change."""
        if kind in FORBIDDEN_KINDS or kind in DOCTOR_KINDS:
            raise AuthorityError(
                "the agent cannot confirm, reject, close, dismiss, downgrade or reassure; "
                "those are new doctor facts, or they are not facts at all")
        if actor not in AGENT_ACTORS:
            raise AuthorityError("extracted facts are appended by the agent or the engine")
        if kind not in CLINICAL_KINDS:
            raise CaseError(f"unknown fact kind {kind}")
        return self._append(patient_id=patient_id, kind=kind, record_id=record_id, quote=quote,
                            provenance=provenance, at=at, actor=actor, actor_id=actor_id,
                            subject_id=None, body=body)

    def confirm(self, *, doctor_id: str, fact_id: str, record_id: str, quote: str, at: datetime,
                note: str = "") -> Fact:
        return self._doctor("confirm", doctor_id, fact_id, record_id, quote, at, note)

    def reject(self, *, doctor_id: str, fact_id: str, record_id: str, quote: str, at: datetime,
               note: str = "") -> Fact:
        return self._doctor("reject", doctor_id, fact_id, record_id, quote, at, note)

    def close(self, *, doctor_id: str, fact_id: str, record_id: str, quote: str, at: datetime,
              note: str = "") -> Fact:
        """Append a doctor close. The subject fact is left as it was."""
        return self._doctor("close", doctor_id, fact_id, record_id, quote, at, note)

    def _doctor(self, kind: str, doctor_id: str, fact_id: str, record_id: str, quote: str,
                at: datetime, note: str) -> Fact:
        if not doctor_id:
            raise CaseError("a doctor id is required")
        subject = self._find(fact_id)
        if subject.kind not in CLINICAL_KINDS:
            raise CaseError("confirm, reject and close apply to an extracted fact")
        if not isinstance(at, datetime):
            raise CaseError("fact time must be a datetime")
        if at < subject.at:
            raise CaseError("a doctor cannot act on a fact before it joined the case")
        body: dict[str, Any] = {}
        if note:
            body["note"] = note
        return self._append(patient_id=subject.patient_id, kind=kind, record_id=record_id, quote=quote,
                            provenance=self.record(record_id).provenance, at=at, actor="doctor",
                            actor_id=doctor_id, subject_id=subject.id, body=body)

    def _find(self, fact_id: str) -> Fact:
        for fact in self._facts:
            if fact.id == fact_id:
                return fact
        raise CaseError(f"no such fact {fact_id}")

    def _append(self, *, patient_id: str, kind: str, record_id: str, quote: str, provenance: str,
                at: datetime, actor: str, actor_id: str, subject_id: str | None,
                body: Mapping[str, Any] | None) -> Fact:
        if not patient_id or not record_id:
            raise CaseError("a fact needs a patient and a record_id")
        if provenance not in PROVENANCE:
            raise CaseError(f"provenance must be one of {PROVENANCE}")
        if not isinstance(at, datetime):
            raise CaseError("fact time must be a datetime")
        rec = self.record(record_id)
        if rec.patient_id != patient_id:
            raise CaseError("record is filed to a different patient")
        if rec.provenance != provenance:
            raise CaseError("provenance does not match the source record")
        # Verify before the fact exists, so a bad quote leaves the log unchanged.
        kept = verify_quote(quote, rec.text)
        frozen = freeze_body(kind, body)
        self._seq += 1
        fact = Fact(
            id=f"F{self._seq:04d}", patient_id=patient_id, kind=kind, record_id=record_id, quote=kept,
            provenance=provenance, at=at, actor=actor, actor_id=actor_id or actor, subject_id=subject_id,
            body=frozen, seq=self._seq,
        )
        self._facts.append(fact)
        return fact
