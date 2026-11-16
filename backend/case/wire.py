"""How the journeys share one case log.

Rules v1.0 does not call this. A journey that is not given a log keeps a private
one, cleared on each `reset`. Quotes that do not verify are skipped, never invented.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .log import CaseLog
from .model import CaseError, drug_key
from .quote import QuoteError, norm

_HA = re.compile(r"Hospital Authority|SOPC|\bHA\b")
_DISPENSE = re.compile(r"Dispensed:\s*(?P<drug>.+?)\s+x(?P<days>\d+) days", re.I)


def ensure_case(engine, *, reset: bool = False) -> CaseLog:
    """The log this engine reads. An injected log stays the same object."""
    given = getattr(engine, "_given_case", None)
    # Rules v1.0 does not take the injected log, so a caller's log is left as it was.
    if given is not None and getattr(engine, "version", None) != "1.0":
        if reset:
            given.reset()
        engine.case = given
        return given
    if reset or getattr(engine, "case", None) is None:
        engine.case = CaseLog()
    return engine.case


def provenance_for(record: dict) -> str:
    """Where a clinic record sits on the case. Patient messages stay `patient`."""
    kind = record.get("kind") or ""
    channel = record.get("channel") or ""
    blob = (record.get("text") or "") + str(record.get("data") or "")
    if kind in ("message", "phone_memo") or channel in ("careline", "whatsapp", "phone"):
        return "patient"
    if channel == "ehealth":
        return "ehealth"
    if kind == "specialist_letter":
        return "ha_sopc" if _HA.search(blob) else "in_group"
    if kind in ("discharge_summary", "aed_summary"):
        return "ehealth" if _HA.search(blob) else "private_hospital"
    return "gp"


def is_ha(record: dict) -> bool:
    blob = (record.get("text") or "") + str(record.get("data") or "")
    return _HA.search(blob) is not None


def is_urgent(record: dict) -> bool:
    data = record.get("data") or {}
    return data.get("urgency") == "urgent" or "URGENT" in (record.get("text") or "")


def _quote(text: str) -> str:
    return norm(text)[:240]


def remember(log: CaseLog, record: dict, *, patient_id: str, at: datetime, provenance: str,
             kind: str, quote: str, body: dict[str, Any] | None = None,
             actor: str = "engine", actor_id: str = "engine"):
    """Register the source record and append one fact. Returns None when the quote will not verify."""
    text = record.get("text") or ""
    kept = _quote(quote)
    if len(kept.replace(" ", "")) < 2:
        return None
    rid = record.get("id") or ""
    if not rid or not patient_id:
        return None
    try:
        existing = log.record(rid)
    except CaseError:
        existing = None
    if existing is None:
        try:
            log.add_record(rid, text, patient_id=patient_id, at=at, provenance=provenance,
                           kind=record.get("kind") or "")
        except CaseError:
            return None
    elif existing.provenance != provenance or existing.patient_id != patient_id:
        return None
    payload = dict(body or {})
    for fact in log.facts(patient_id):
        if fact.record_id == rid and fact.kind == kind and fact.quote == kept and _same(fact.body, payload):
            return fact
    try:
        return log.append(patient_id=patient_id, kind=kind, record_id=rid, quote=kept, provenance=provenance,
                          at=at, actor=actor, actor_id=actor_id, body=payload)
    except (CaseError, QuoteError):
        return None


def _same(stored, new: dict) -> bool:
    for key in ("drug", "action", "mx", "name", "substance"):
        if key in stored or key in new:
            if stored.get(key) != new.get(key):
                return False
    return True


def note_dispensing(log: CaseLog, record: dict, patient_id: str, at: datetime, quote: str | None):
    """A GP dispensing line, for the case projection. The medication journey keeps its own replay tag."""
    if not quote:
        return None
    found = _DISPENSE.search(record.get("text") or "")
    drug = found.group("drug").strip() if found else quote
    days = int(found.group("days")) if found else 0
    return remember(log, record, patient_id=patient_id, at=at, provenance="gp", kind="medication", quote=quote,
                    actor_id="rules", body={"drug": drug, "dose": drug, "dispensed": True, "days": days,
                                            "status": "active"})


def note_referral(log: CaseLog, record: dict, patient_id: str, at: datetime, *, ha: bool | None = None):
    """One referral fact per letter. A second call for the same letter returns the fact already stored."""
    if any(f.record_id == record.get("id") and f.kind == "referral" for f in log.facts(patient_id)):
        return next(f for f in log.facts(patient_id) if f.record_id == record.get("id") and f.kind == "referral")
    flagged = is_ha(record) if ha is None else ha
    text = record.get("text") or ""
    quote = _quote(text)
    return remember(
        log, record, patient_id=patient_id, at=at, provenance="ha_sopc" if flagged else "gp",
        kind="referral", quote=quote, actor_id="rules",
        body={"specialty": "SOPC" if flagged else "referral", "destination": "",
              "urgency": "urgent" if is_urgent(record) else "routine",
              "owner": record.get("author") or "", "ha": flagged},
    )


def note_stay(log: CaseLog, record: dict, patient_id: str, at: datetime):
    """An admission, its discharge, and the diagnosis, from a summary the journey has already read."""
    text = record.get("text") or ""
    provenance = provenance_for(record)
    diagnosis = _line(text, r"^(Principal diagnosis|Diagnosis):")
    quote = diagnosis or _line(text, r"^HOSPITAL AUTHORITY") or _quote(text)
    if not quote:
        return None
    site = "hospital"
    header = _line(text, r"^HOSPITAL AUTHORITY")
    if header and "-" in header:
        site = header.split("-", 1)[1].strip()[:80] or site
    stay = remember(log, record, patient_id=patient_id, at=at, provenance=provenance, kind="admission",
                    quote=quote, actor_id="followon", body={"site": site, "reason": _after_colon(diagnosis) or site})
    if stay is not None:
        remember(log, record, patient_id=patient_id, at=at, provenance=provenance, kind="discharge",
                 quote=quote, actor_id="followon", body={"site": site, "admission_id": stay.id})
    if diagnosis:
        remember(log, record, patient_id=patient_id, at=at, provenance=provenance, kind="condition",
                 quote=diagnosis, actor_id="followon",
                 body={"name": _after_colon(diagnosis) or "diagnosis", "status": "active"})
    return stay


def note_medicine_lines(log: CaseLog, record: dict, patient_id: str, at: datetime, lines: list[str]):
    """Hospital or letter medicine lines, when this record does not already carry that drug."""
    provenance = provenance_for(record)
    for line in lines:
        quote = _quote(line)
        if len(quote.replace(" ", "")) < 2:
            continue
        found = re.search(r"[A-Za-z][A-Za-z-]{2,}", quote)
        if not found:
            continue
        drug = found.group(0)
        if _has_drug(log, patient_id, record.get("id") or "", drug):
            continue
        action = "stop" if re.search(r"\b(stopped|discontinued|ceased)\b", quote, re.I) else "start"
        remember(log, record, patient_id=patient_id, at=at, provenance=provenance, kind="dose_change",
                 quote=quote, actor_id="followon",
                 body={"drug": drug, "action": action, "dose": "" if action == "stop" else quote,
                       "dispensed": False})


def note_patient(log: CaseLog, *, patient_id: str, record_id: str, text: str, at: datetime,
                 step: str):
    """A consented pre-visit answer. The quote is the patient's own message."""
    record = {"id": record_id, "text": text, "kind": "message", "channel": "careline"}
    quote = _quote(text)
    if step == "allergies":
        return remember(log, record, patient_id=patient_id, at=at, provenance="patient", kind="allergy",
                        quote=quote, actor_id="precare", body={"substance": quote[:200] or quote, "status": "active"})
    if step == "reason":
        return remember(log, record, patient_id=patient_id, at=at, provenance="patient", kind="condition",
                        quote=quote, actor_id="precare", body={"name": quote[:200] or quote, "status": "active"})
    if step == "meds":
        return remember(log, record, patient_id=patient_id, at=at, provenance="patient", kind="medication",
                        quote=quote, actor_id="precare",
                        body={"drug": quote[:200] or quote, "dose": quote[:200] or quote, "dispensed": False,
                              "status": "active"})
    return None


def referral_rows(log: CaseLog, patient_id: str, as_of: datetime | None = None, *, ha_only: bool = False,
                  open_only: bool = False) -> list[dict]:
    """Referral rows in the shape the journeys already use: rid, t, text, owner, urgent."""
    facts = {f.id: f for f in log.facts(patient_id, as_of)}
    view = log.project(patient_id, as_of)
    chosen = view.open_referrals() if open_only else view.referrals
    rows = []
    for ref in chosen:
        fact = facts.get(ref.fact_id)
        if fact is None:
            continue
        if ha_only and not fact.body.get("ha"):
            continue
        try:
            text = log.record(ref.record_id).text
        except CaseError:
            continue
        rows.append({
            "pid": patient_id, "t": ref.at, "rid": ref.record_id, "text": text,
            "owner": fact.body.get("owner") or None,
            "urgent": ref.urgency == "urgent" or "URGENT" in text,
        })
    return rows


def medication_lines(log: CaseLog, patient_id: str, as_of: datetime | None = None):
    return log.project(patient_id, as_of).medication_lines()


def _has_drug(log: CaseLog, patient_id: str, record_id: str, drug: str) -> bool:
    key = drug_key(drug)
    for fact in log.facts(patient_id):
        if fact.record_id == record_id and fact.kind in ("medication", "dose_change"):
            if drug_key(str(fact.body.get("drug") or "")) == key:
                return True
    return False


def _line(text: str, pattern: str) -> str | None:
    for ln in (text or "").splitlines():
        if re.search(pattern, ln):
            quote = norm(ln)[:240]
            return quote or None
    return None


def _after_colon(line: str | None) -> str:
    if not line or ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()[:120]
