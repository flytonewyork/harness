"""The case as it stood at a moment.

Pure function of the facts visible then. A rejected fact drops out of the active sections and stays in
the log. A doctor's confirm of a regimen selects that dose: older disagreeing lines are superseded, not
edited. A later unconfirmed line from another site opens the conflict again. A close marks a referral
done and marks an admission's follow-up done; it does not stop a medicine and it does not delete the event.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .model import Fact, dose_key, drug_key, frequency_key


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat()


@dataclass(frozen=True, slots=True)
class Regimen:
    fact_id: str
    drug: str
    drug_key: str
    dose: str
    dose_key: str
    frequency: str
    frequency_key: str
    provenance: str
    record_id: str
    quote: str
    at: datetime
    dispensed: bool
    confirmed: bool = False

    def text(self) -> str:
        bits = [self.drug, self.dose]
        if self.frequency:
            bits.append(self.frequency)
        return " ".join(b for b in bits if b)

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "drug": self.drug, "dose": self.dose, "frequency": self.frequency,
            "provenance": self.provenance, "record_id": self.record_id, "quote": self.quote,
            "at": self.at.isoformat(), "dispensed": self.dispensed, "confirmed": self.confirmed,
            "text": self.text(),
        }


@dataclass(frozen=True, slots=True)
class DoseConflict:
    drug: str
    drug_key: str
    sides: tuple[Regimen, ...]

    def text(self) -> str:
        return f"{self.drug}: " + " vs ".join(f"{s.text()} ({s.provenance})" for s in self.sides)

    def to_dict(self) -> dict:
        return {"drug": self.drug, "text": self.text(),
                "sides": [s.to_dict() for s in self.sides]}


@dataclass(frozen=True, slots=True)
class ReferralView:
    fact_id: str
    at: datetime
    specialty: str
    destination: str
    urgency: str
    status: str
    quote: str
    record_id: str
    provenance: str

    def text(self) -> str:
        where = f" to {self.destination}" if self.destination else ""
        return f"{self.specialty}{where}, {self.urgency}, {self.status}"

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "at": self.at.isoformat(), "specialty": self.specialty,
            "destination": self.destination, "urgency": self.urgency, "status": self.status,
            "quote": self.quote, "record_id": self.record_id, "provenance": self.provenance,
            "text": self.text(),
        }


@dataclass(frozen=True, slots=True)
class AdmissionView:
    fact_id: str
    at: datetime
    site: str
    reason: str
    quote: str
    record_id: str
    provenance: str
    status: str
    discharged_at: datetime | None
    discharge_quote: str
    discharge_record_id: str
    follow_up: str

    def text(self) -> str:
        why = f", {self.reason}" if self.reason else ""
        tail = f", discharged {self.discharged_at:%d %b %Y}" if self.discharged_at else ", still admitted"
        return f"{self.site}{why}{tail}"

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "at": self.at.isoformat(), "site": self.site, "reason": self.reason,
            "quote": self.quote, "record_id": self.record_id, "provenance": self.provenance,
            "status": self.status, "discharged_at": _iso(self.discharged_at),
            "discharge_quote": self.discharge_quote, "discharge_record_id": self.discharge_record_id,
            "follow_up": self.follow_up, "text": self.text(),
        }


@dataclass(frozen=True, slots=True)
class NamedView:
    fact_id: str
    kind: str
    name: str
    status: str
    value: str
    quote: str
    record_id: str
    provenance: str
    at: datetime

    def text(self) -> str:
        if self.kind == "observation":
            return f"{self.name}: {self.value}"
        return self.name if self.status == "active" else f"{self.name} ({self.status})"

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "kind": self.kind, "name": self.name, "status": self.status,
            "value": self.value, "quote": self.quote, "record_id": self.record_id,
            "provenance": self.provenance, "at": self.at.isoformat(), "text": self.text(),
        }


def _line(text: str, quote: str, record_id: str, provenance: str, at: datetime, fact_id: str,
          quotes: list[dict] | None = None) -> dict:
    row = {"text": text, "quote": quote, "record_id": record_id, "provenance": provenance,
           "at": at.isoformat(), "fact_id": fact_id}
    if quotes:
        row["quotes"] = quotes
    return row


def _disagrees(a: Regimen, b: Regimen) -> bool:
    if a.dose_key != b.dose_key:
        return True
    # An omitted frequency does not contradict a stated one. Two stated frequencies can.
    return bool(a.frequency_key and b.frequency_key and a.frequency_key != b.frequency_key)


def _regimen(fact: Fact) -> Regimen | None:
    body = fact.body
    if fact.kind == "medication":
        if str(body.get("status") or "active") == "stopped":
            return None
        drug, dose = str(body["drug"]), str(body.get("dose") or "")
        freq = str(body.get("frequency") or "")
        dispensed = bool(body.get("dispensed"))
    elif fact.kind == "dose_change":
        if str(body.get("action")) == "stop":
            return None
        drug, dose = str(body["drug"]), str(body.get("dose") or "")
        freq = str(body.get("frequency") or "")
        dispensed = bool(body.get("dispensed"))
    else:
        return None
    return Regimen(
        fact_id=fact.id, drug=drug, drug_key=drug_key(drug), dose=dose, dose_key=dose_key(dose),
        frequency=freq, frequency_key=frequency_key(freq), provenance=fact.provenance,
        record_id=fact.record_id, quote=fact.quote, at=fact.at, dispensed=dispensed,
    )


def _live_regimens(facts: tuple[Fact, ...], rejected: set[str]) -> list[Regimen]:
    grouped: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.kind in ("medication", "dose_change") and fact.id not in rejected:
            grouped[(drug_key(str(fact.body.get("drug") or "")), fact.provenance)].append(fact)
    live = []
    for group in grouped.values():
        regimen = _regimen(group[-1])
        if regimen is not None:
            live.append(regimen)
    return live


def _apply_confirms(live: list[Regimen], facts: tuple[Fact, ...], rejected: set[str]) -> list[Regimen]:
    """Per drug, the latest confirm of a line that is still live selects it.

    Older lines for that drug drop out of the active view. A line dated after the confirmed line
    stays, so a newer unconfirmed dose from another site is still a conflict. A reject is not undone.
    """
    by_id = {f.id: f for f in facts}
    chosen_for: dict[str, str] = {}
    chosen_at: dict[str, tuple[datetime, int]] = {}
    for fact in facts:
        if fact.kind != "confirm" or not fact.subject_id or fact.subject_id in rejected:
            continue
        subject = by_id.get(fact.subject_id)
        if subject is None or subject.kind not in ("medication", "dose_change"):
            continue
        if not any(line.fact_id == subject.id for line in live):
            continue
        key = drug_key(str(subject.body.get("drug") or ""))
        stamp = (fact.at, fact.seq)
        if key not in chosen_at or stamp >= chosen_at[key]:
            chosen_at[key] = stamp
            chosen_for[key] = subject.id
    if not chosen_for:
        return live
    by_fact = {line.fact_id: line for line in live}
    kept: list[Regimen] = []
    for line in live:
        subject_id = chosen_for.get(line.drug_key)
        if subject_id is None:
            kept.append(line)
            continue
        chosen = by_fact[subject_id]
        if line.fact_id == subject_id or line.at > chosen.at:
            kept.append(replace(line, confirmed=line.fact_id == subject_id))
    return kept


def _conflicts(lines: list[Regimen]) -> list[DoseConflict]:
    by_drug: dict[str, list[Regimen]] = defaultdict(list)
    for line in lines:
        by_drug[line.drug_key].append(line)
    out = []
    for drug, sides in by_drug.items():
        ordered = tuple(sorted(sides, key=lambda s: (s.at, s.provenance, s.fact_id)))
        if len(ordered) >= 2 and any(_disagrees(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]):
            out.append(DoseConflict(drug=ordered[-1].drug, drug_key=drug, sides=ordered))
    return out


def _named(facts: tuple[Fact, ...], kind: str, field: str, rejected: set[str]) -> list[NamedView]:
    latest: dict[str, Fact] = {}
    for fact in facts:
        if fact.kind == kind and fact.id not in rejected:
            latest[str(fact.body.get(field) or "").strip().lower()] = fact
    rows = []
    for fact in latest.values():
        status = str(fact.body.get("status") or "active")
        if kind != "observation" and status == "resolved":
            continue
        rows.append(NamedView(
            fact_id=fact.id, kind=kind, name=str(fact.body.get(field) or ""), status=status,
            value=str(fact.body.get("value") or ""), quote=fact.quote, record_id=fact.record_id,
            provenance=fact.provenance, at=fact.at,
        ))
    rows.sort(key=lambda n: (n.name.lower(), n.at, n.fact_id))
    return rows


def _link_discharges(admissions: list[Fact], discharges: list[Fact]) -> dict[str, Fact]:
    linked: dict[str, Fact] = {}
    used: set[str] = set()
    for discharge in discharges:
        target = str(discharge.body.get("admission_id") or "")
        if target and any(a.id == target for a in admissions):
            linked[target] = discharge
            used.add(discharge.id)
    for index, admission in enumerate(admissions):
        if admission.id in linked:
            continue
        site = " ".join(str(admission.body.get("site") or "").lower().split())
        later = [a.at for a in admissions[index + 1:]
                 if " ".join(str(a.body.get("site") or "").lower().split()) == site]
        horizon = min(later) if later else None
        for discharge in discharges:
            if discharge.id in used or discharge.at < admission.at:
                continue
            if horizon is not None and discharge.at >= horizon:
                continue
            if str(discharge.body.get("admission_id") or ""):
                continue
            other = " ".join(str(discharge.body.get("site") or "").lower().split())
            if other != site:
                continue
            linked[admission.id] = discharge
            used.add(discharge.id)
            break
    return linked


@dataclass(frozen=True, slots=True)
class CaseView:
    """What case/wire reads, and what case/ui renders from `to_dict()['strip']`.

    `medication_lines()` is `(at, quote, record_id)`, oldest first, the shape `context_step` already walks.
    `open_referrals()` is the referral list with doctor-closed rows removed. The strip is the projection,
    not the queue item's own trace.
    """

    patient_id: str
    as_of: datetime
    medications: tuple[Regimen, ...]
    conflicts: tuple[DoseConflict, ...]
    not_dispensed_here: tuple[Regimen, ...]
    referrals: tuple[ReferralView, ...]
    admissions: tuple[AdmissionView, ...]
    conditions: tuple[NamedView, ...]
    allergies: tuple[NamedView, ...]
    observations: tuple[NamedView, ...]
    facts: tuple[Fact, ...]

    def open_referrals(self) -> tuple[ReferralView, ...]:
        return tuple(r for r in self.referrals if r.status == "open")

    def medication_lines(self) -> tuple[tuple[datetime, str, str], ...]:
        ordered = sorted(self.medications, key=lambda m: (m.at, m.fact_id))
        return tuple((m.at, m.quote, m.record_id) for m in ordered)

    def to_dict(self) -> dict[str, Any]:
        def section(sid: str, title: str, lines: list[dict]) -> dict:
            return {"id": sid, "title": title, "lines": lines}

        meds = [_line(m.text(), m.quote, m.record_id, m.provenance, m.at, m.fact_id) for m in self.medications]
        conflicts = []
        for c in self.conflicts:
            side_quotes = [{"fact_id": s.fact_id, "quote": s.quote, "record_id": s.record_id,
                            "provenance": s.provenance, "text": s.text()} for s in c.sides]
            last = c.sides[-1]
            conflicts.append(_line(c.text(), last.quote, last.record_id, last.provenance, last.at,
                                   last.fact_id, side_quotes))
        outside = [_line(m.text(), m.quote, m.record_id, m.provenance, m.at, m.fact_id)
                   for m in self.not_dispensed_here]
        refs = [_line(r.text(), r.quote, r.record_id, r.provenance, r.at, r.fact_id)
                for r in self.open_referrals()]
        stays = []
        for a in self.admissions:
            row = _line(a.text(), a.quote, a.record_id, a.provenance, a.at, a.fact_id)
            if a.discharge_quote:
                row["quotes"] = [
                    {"fact_id": a.fact_id, "quote": a.quote, "record_id": a.record_id, "provenance": a.provenance},
                    {"quote": a.discharge_quote, "record_id": a.discharge_record_id, "provenance": a.provenance},
                ]
            stays.append(row)
        named = lambda rows: [_line(n.text(), n.quote, n.record_id, n.provenance, n.at, n.fact_id) for n in rows]
        return {
            "format": "case-projection/1",
            "patient_id": self.patient_id,
            "as_of": self.as_of.isoformat(),
            "medications": [m.to_dict() for m in self.medications],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "not_dispensed_here": [m.to_dict() for m in self.not_dispensed_here],
            "referrals": [r.to_dict() for r in self.referrals],
            "admissions": [a.to_dict() for a in self.admissions],
            "conditions": [n.to_dict() for n in self.conditions],
            "allergies": [n.to_dict() for n in self.allergies],
            "observations": [n.to_dict() for n in self.observations],
            "facts": [f.to_dict() for f in self.facts],
            "strip": [
                section("medications", "Medicines", meds),
                section("conflicts", "Dose conflicts", conflicts),
                section("not_dispensed_here", "Not dispensed here", outside),
                section("referrals", "Referrals", refs),
                section("admissions", "Admissions", stays),
                section("conditions", "Conditions", named(self.conditions)),
                section("allergies", "Allergies", named(self.allergies)),
                section("observations", "Observations", named(self.observations)),
            ],
        }


def project_case(facts: tuple[Fact, ...], patient_id: str, as_of: datetime) -> CaseView:
    ordered = tuple(sorted(facts, key=lambda f: (f.at, f.seq)))
    rejected = {f.subject_id for f in ordered if f.kind == "reject" and f.subject_id}
    closed = {f.subject_id for f in ordered if f.kind == "close" and f.subject_id}
    live = _apply_confirms(_live_regimens(ordered, rejected), ordered, rejected)
    live.sort(key=lambda m: (m.drug_key, m.provenance, m.at, m.fact_id))
    medications = tuple(live)
    conflicts = tuple(_conflicts(live))
    dispensed = {m.drug_key for m in live if m.dispensed and m.provenance == "gp"}
    outside = tuple(m for m in live if m.provenance != "gp" and m.drug_key not in dispensed)
    referrals = []
    for fact in ordered:
        if fact.kind != "referral" or fact.id in rejected:
            continue
        referrals.append(ReferralView(
            fact_id=fact.id, at=fact.at, specialty=str(fact.body.get("specialty") or ""),
            destination=str(fact.body.get("destination") or ""),
            urgency=str(fact.body.get("urgency") or "routine"),
            status="closed" if fact.id in closed else "open", quote=fact.quote, record_id=fact.record_id,
            provenance=fact.provenance,
        ))
    admissions = [f for f in ordered if f.kind == "admission" and f.id not in rejected]
    discharges = [f for f in ordered if f.kind == "discharge" and f.id not in rejected]
    linked = _link_discharges(admissions, discharges)
    stays = []
    for admission in admissions:
        discharge = linked.get(admission.id)
        stays.append(AdmissionView(
            fact_id=admission.id, at=admission.at, site=str(admission.body.get("site") or ""),
            reason=str(admission.body.get("reason") or ""), quote=admission.quote,
            record_id=admission.record_id, provenance=admission.provenance,
            status="discharged" if discharge else "admitted",
            discharged_at=discharge.at if discharge else None,
            discharge_quote=discharge.quote if discharge else "",
            discharge_record_id=discharge.record_id if discharge else "",
            follow_up="closed" if admission.id in closed else "open",
        ))
    return CaseView(
        patient_id=patient_id, as_of=as_of, medications=medications, conflicts=conflicts,
        not_dispensed_here=outside, referrals=tuple(referrals), admissions=tuple(stays),
        conditions=tuple(_named(ordered, "condition", "name", rejected)),
        allergies=tuple(_named(ordered, "allergy", "substance", rejected)),
        observations=tuple(_named(ordered, "observation", "name", rejected)),
        facts=ordered,
    )
