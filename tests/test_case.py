"""The case log: quotes, append-only facts, provenance, as-of projection, doctor acts.

Synthetic records only. The log never edits a fact; a doctor confirm, reject or close is a new fact.
"""
import dataclasses
import json
from datetime import datetime

import pytest

from backend.case import (
    PROVENANCE, AuthorityError, CaseError, CaseLog, QuoteError, dose_key, drug_key, norm,
)
from backend.engine.verify import norm as platform_norm

P = "P1"
JAN = datetime(2027, 1, 10, 9, 0)
MAY = datetime(2027, 5, 2, 11, 0)
JUN = datetime(2027, 6, 1, 9, 0)


def _log() -> CaseLog:
    return CaseLog()


def _rec(log, rid, text, at=JAN, provenance="gp", patient=P, kind="consult_note"):
    return log.add_record(rid, text, patient_id=patient, at=at, provenance=provenance, kind=kind)


def _med(log, rid, quote, at=JAN, provenance="gp", patient=P, **body):
    fields = {"drug": "Amlodipine", "dose": "5mg", "frequency": "daily", "dispensed": False}
    fields.update(body)
    return log.append(patient_id=patient, kind="medication", record_id=rid, quote=quote, provenance=provenance,
                      at=at, body=fields)


def test_quote_is_verified_and_whitespace_collapsed():
    log = _log()
    text = "P: Start  atorvastatin\n20mg nocte."
    _rec(log, "N1", text)
    fact = log.append(patient_id=P, kind="medication", record_id="N1", quote="Start atorvastatin 20mg nocte.",
                      provenance="gp", at=JAN, body={"drug": "Atorvastatin", "dose": "20mg", "frequency": "nocte"})
    assert fact.quote == "Start atorvastatin 20mg nocte."
    assert fact.record_id == "N1" and fact.provenance == "gp"
    assert fact.quote in norm(text)


def test_quote_norm_matches_the_platform_checker():
    for sample in ["a  b", "Start  warfarin\n3mg daily.", "  ", "Continue Amlodipine 5mg daily."]:
        assert norm(sample) == platform_norm(sample)


def test_fabricated_quote_is_rejected_and_the_log_is_unchanged():
    log = _log()
    _rec(log, "N1", "P: Start atorvastatin 20mg nocte.")
    with pytest.raises(QuoteError, match="verbatim"):
        log.append(patient_id=P, kind="medication", record_id="N1", quote="Start atorvastatin 80mg nocte.",
                   provenance="gp", at=JAN, body={"drug": "Atorvastatin", "dose": "80mg"})
    assert len(log) == 0 and log.facts() == ()


def test_quote_shorter_than_two_non_space_characters_is_rejected():
    log = _log()
    _rec(log, "N1", "P: Start atorvastatin 20mg nocte.")
    for quote in ("", " ", "A", "5"):
        with pytest.raises(QuoteError, match="2 non-space"):
            log.append(patient_id=P, kind="medication", record_id="N1", quote=quote, provenance="gp", at=JAN,
                       body={"drug": "Atorvastatin", "dose": "20mg"})
    assert len(log) == 0
    kept = log.append(patient_id=P, kind="medication", record_id="N1", quote="20mg", provenance="gp", at=JAN,
                      body={"drug": "Atorvastatin", "dose": "20mg"})
    assert kept.quote == "20mg"


def test_facts_are_append_only_and_immutable():
    log = _log()
    _rec(log, "N1", "Continue Amlodipine 5mg daily.")
    body = {"drug": "Amlodipine", "dose": "5mg", "frequency": "daily"}
    fact = _med(log, "N1", "Continue Amlodipine 5mg daily.", **body)
    body["dose"] = "80mg"
    assert fact.body["dose"] == "5mg"
    with pytest.raises(TypeError):
        fact.body["dose"] = "80mg"
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.quote = "Continue Amlodipine 80mg daily."
    assert not any(hasattr(log, name) for name in ("delete", "update", "pop", "dismiss", "downgrade"))
    _rec(log, "N2", "Continue Metformin 500mg bd.")
    second = _med(log, "N2", "Continue Metformin 500mg bd.", drug="Metformin", dose="500mg", frequency="bd")
    assert log.facts()[0] is fact and fact.quote == "Continue Amlodipine 5mg daily."
    assert log.facts() == (fact, second)
    with pytest.raises(TypeError):
        log.facts()[0] = second


def test_doctor_acts_are_new_facts_and_do_not_edit_the_subject():
    log = _log()
    _rec(log, "N1", "Continue Amlodipine 5mg daily.")
    _rec(log, "A1", "Reject amlodipine: not what the note says he takes.", kind="action_note")
    fact = _med(log, "N1", "Continue Amlodipine 5mg daily.")
    rejected = log.reject(doctor_id="dr_wong", fact_id=fact.id, record_id="A1",
                          quote="Reject amlodipine: not what the note says he takes.", at=datetime(2027, 1, 11, 9, 0),
                          note="misread")
    assert rejected.kind == "reject" and rejected.actor == "doctor" and rejected.actor_id == "dr_wong"
    assert rejected.subject_id == fact.id and rejected is not fact
    assert log.facts()[0] is fact and fact.body["dose"] == "5mg" and fact.kind == "medication"
    view = log.project(P, datetime(2027, 1, 12))
    assert view.medications == ()
    assert any(f.id == fact.id for f in view.facts)


@pytest.mark.parametrize("kind", ["confirm", "reject", "close", "dismiss", "downgrade", "reassure", "delete", "update"])
def test_agent_cannot_confirm_reject_close_or_soften(kind):
    log = _log()
    _rec(log, "N1", "Continue Amlodipine 5mg daily.")
    with pytest.raises(AuthorityError):
        log.append(patient_id=P, kind=kind, record_id="N1", quote="Continue Amlodipine 5mg daily.",
                   provenance="gp", at=JAN, actor="agent")
    with pytest.raises(AuthorityError):
        log.append(patient_id=P, kind="medication", record_id="N1", quote="Continue Amlodipine 5mg daily.",
                   provenance="gp", at=JAN, actor="doctor", actor_id="dr_wong",
                   body={"drug": "Amlodipine", "dose": "5mg"})
    assert len(log) == 0


def test_a_failed_doctor_quote_does_not_append():
    log = _log()
    _rec(log, "N1", "Continue Amlodipine 5mg daily.")
    fact = _med(log, "N1", "Continue Amlodipine 5mg daily.")
    with pytest.raises(QuoteError):
        log.confirm(doctor_id="dr_wong", fact_id=fact.id, record_id="N1", quote="Accept amlodipine 80mg daily.",
                    at=datetime(2027, 1, 11, 9, 0))
    assert len(log) == 1 and log.facts()[0] is fact


def test_every_provenance_is_kept_and_an_unknown_one_is_refused():
    log = _log()
    texts = {
        "gp": "GP note: hypertension.",
        "in_group": "In-group letter: hypertension.",
        "ha_sopc": "HA SOPC letter: hypertension.",
        "private_hospital": "Private hospital summary: hypertension.",
        "ehealth": "eHealth summary: hypertension.",
        "patient": "Patient says: hypertension.",
    }
    for provenance, text in texts.items():
        _rec(log, provenance, text, provenance=provenance, kind="note")
        fact = log.append(patient_id=P, kind="condition", record_id=provenance, quote=text, provenance=provenance,
                          at=JAN, body={"name": provenance, "status": "active"})
        assert fact.provenance == provenance
    view = log.project(P, JAN)
    assert {c.provenance for c in view.conditions} == set(PROVENANCE)
    with pytest.raises(CaseError, match="provenance"):
        log.add_record("bad", "Somewhere else: hypertension.", patient_id=P, at=JAN, provenance="hospital")
    with pytest.raises(CaseError, match="different patient"):
        log.append(patient_id="P2", kind="condition", record_id="gp", quote=texts["gp"], provenance="gp",
                   at=JAN, body={"name": "hypertension"})


def test_provenance_must_match_the_source_record():
    log = _log()
    _rec(log, "H", "Start warfarin 3mg daily.", provenance="private_hospital", kind="discharge_summary")
    with pytest.raises(CaseError, match="provenance"):
        log.append(patient_id=P, kind="medication", record_id="H", quote="Start warfarin 3mg daily.",
                   provenance="gp", at=JAN, body={"drug": "warfarin", "dose": "3mg"})
    assert len(log) == 0


def test_as_of_hides_facts_that_had_not_joined_yet():
    log = _log()
    _rec(log, "MAR", "Admitted 01/03/2026 for pneumonia.", at=datetime(2026, 3, 1, 8, 0), provenance="ehealth")
    _rec(log, "JUN", "Admitted 02/03/2027 for chest pain.", at=datetime(2027, 3, 2, 8, 0), provenance="ehealth")
    later = log.append(patient_id=P, kind="admission", record_id="JUN", quote="Admitted 02/03/2027 for chest pain.",
                       provenance="ehealth", at=datetime(2027, 3, 2, 8, 0),
                       body={"site": "Queen Mary Hospital", "reason": "chest pain"})
    earlier = log.append(patient_id=P, kind="admission", record_id="MAR", quote="Admitted 01/03/2026 for pneumonia.",
                         provenance="ehealth", at=datetime(2026, 3, 1, 8, 0),
                         body={"site": "Queen Mary Hospital", "reason": "pneumonia"})
    assert [f.id for f in log.facts()] == [later.id, earlier.id]
    april = log.project(P, datetime(2026, 4, 1))
    assert [a.reason for a in april.admissions] == ["pneumonia"]
    both = log.project(P, datetime(2027, 3, 3))
    assert [a.reason for a in both.admissions] == ["pneumonia", "chest pain"]
    assert log.project(P, datetime(2026, 2, 1)).admissions == ()
    assert log.project("someone-else", datetime(2027, 4, 1)).facts == ()


def test_dose_conflict_across_sites_confirm_and_a_later_change():
    log = _log()
    gp = "Start atorvastatin 20mg nocte."
    letter = "Atorvastatin increased to 40mg daily."
    _rec(log, "G", f"P: {gp}", at=JAN)
    _rec(log, "L", letter, at=MAY, provenance="in_group", kind="specialist_letter")
    log.append(patient_id=P, kind="medication", record_id="G", quote=gp, provenance="gp", at=JAN,
               body={"drug": "Atorvastatin", "dose": "20mg", "frequency": "nocte"})
    early = log.project(P, datetime(2027, 2, 1))
    assert early.conflicts == () and [m.dose_key for m in early.medications] == ["20mg"]
    changed = log.append(patient_id=P, kind="dose_change", record_id="L", quote=letter, provenance="in_group",
                         at=MAY, body={"drug": "atorvastatin", "action": "increase", "dose": "40 mg",
                                       "frequency": "daily"})
    mid = log.project(P, datetime(2027, 5, 3))
    assert len(mid.conflicts) == 1
    assert {s.dose_key for s in mid.conflicts[0].sides} == {"20mg", "40mg"}
    assert {s.provenance for s in mid.conflicts[0].sides} == {"gp", "in_group"}
    assert {s.quote for s in mid.conflicts[0].sides} == {gp, letter}
    agreed = log.confirm(doctor_id="dr_wong", fact_id=changed.id, record_id="L", quote=letter,
                         at=datetime(2027, 5, 4, 9, 0))
    assert agreed.actor == "doctor" and agreed.subject_id == changed.id and agreed.provenance == "in_group"
    after = log.project(P, datetime(2027, 5, 5))
    assert after.conflicts == ()
    assert [(m.dose_key, m.confirmed, m.provenance) for m in after.medications] == [("40mg", True, "in_group")]
    assert len(log.project(P, datetime(2027, 5, 3)).conflicts) == 1
    assert log.facts()[1] is changed and changed.body["dose"] == "40 mg"
    later = "Reduce atorvastatin to 10mg daily."
    _rec(log, "G2", later, at=JUN)
    log.append(patient_id=P, kind="dose_change", record_id="G2", quote=later, provenance="gp", at=JUN,
               body={"drug": "atorvastatin", "action": "decrease", "dose": "10mg", "frequency": "daily"})
    reopened = log.project(P, datetime(2027, 6, 2))
    assert {s.dose_key for s in reopened.conflicts[0].sides} == {"10mg", "40mg"}


def test_same_dose_is_not_a_conflict_and_a_different_frequency_is():
    log = _log()
    gp = "Continue atorvastatin 20mg daily."
    sopc = "Continue atorvastatin 20 mg daily."
    _rec(log, "G", gp)
    _rec(log, "S", sopc, provenance="ha_sopc", kind="specialist_letter")
    log.append(patient_id=P, kind="medication", record_id="G", quote=gp, provenance="gp", at=JAN,
               body={"drug": "atorvastatin", "dose": "20mg", "frequency": "daily"})
    log.append(patient_id=P, kind="medication", record_id="S", quote=sopc, provenance="ha_sopc", at=MAY,
               body={"drug": "Atorvastatin", "dose": "20 mg", "frequency": "daily"})
    assert log.project(P, MAY).conflicts == ()
    assert dose_key("20 mg") == dose_key("20mg") == "20mg"
    assert drug_key("Warfarin Sodium") == drug_key("warfarin") == "warfarin"
    night = "Atorvastatin 20mg nocte."
    _rec(log, "N", night, at=JUN, provenance="in_group")
    log.append(patient_id=P, kind="medication", record_id="N", quote=night, provenance="in_group", at=JUN,
               body={"drug": "atorvastatin", "dose": "20mg", "frequency": "nocte"})
    clash = log.project(P, JUN)
    assert len(clash.conflicts) == 1
    assert {s.frequency_key for s in clash.conflicts[0].sides} == {"daily", "nocte"}


def test_reject_drops_the_regimen_and_a_later_confirm_does_not_restore_it():
    log = _log()
    line = "Start warfarin 3mg daily."
    _rec(log, "H", line, provenance="private_hospital", kind="discharge_summary")
    _rec(log, "A", "Reject warfarin: this discharge is for another patient.", kind="action_note",
         at=datetime(2027, 1, 12, 9, 0))
    fact = log.append(patient_id=P, kind="medication", record_id="H", quote=line, provenance="private_hospital",
                      at=JAN, body={"drug": "warfarin", "dose": "3mg", "frequency": "daily"})
    log.reject(doctor_id="dr_wong", fact_id=fact.id, record_id="A",
               quote="Reject warfarin: this discharge is for another patient.", at=datetime(2027, 1, 12, 9, 0))
    log.confirm(doctor_id="dr_wong", fact_id=fact.id, record_id="H", quote=line, at=datetime(2027, 1, 13, 9, 0))
    view = log.project(P, datetime(2027, 1, 14))
    assert view.medications == () and view.not_dispensed_here == ()
    assert [f.kind for f in log.facts()] == ["medication", "reject", "confirm"]
    assert log.facts()[0] is fact


def test_hospital_warfarin_is_not_dispensed_at_the_gp_clinic():
    log = _log()
    start = "Start warfarin 3mg daily."
    cont = "Continue bisoprolol 2.5mg daily."
    dispensed = "Dispensed: Bisoprolol 2.5 MG Oral Tablet x28 days"
    _rec(log, "H", f"{start} {cont}", provenance="private_hospital", kind="discharge_summary")
    _rec(log, "D", dispensed, at=datetime(2027, 1, 20, 10, 0), kind="dispensing")
    log.append(patient_id=P, kind="medication", record_id="H", quote=start, provenance="private_hospital", at=JAN,
               body={"drug": "warfarin", "dose": "3mg", "frequency": "daily", "dispensed": False})
    log.append(patient_id=P, kind="medication", record_id="H", quote=cont, provenance="private_hospital", at=JAN,
               body={"drug": "bisoprolol", "dose": "2.5mg", "frequency": "daily", "dispensed": False})
    log.append(patient_id=P, kind="medication", record_id="D", quote=dispensed, provenance="gp",
               at=datetime(2027, 1, 20, 10, 0),
               body={"drug": "Bisoprolol", "dose": "2.5mg", "frequency": "daily", "dispensed": True})
    view = log.project(P, datetime(2027, 1, 21))
    assert {m.drug_key for m in view.not_dispensed_here} == {"warfarin"}
    assert any(m.drug_key == "bisoprolol" and m.provenance == "gp" and m.dispensed for m in view.medications)
    assert view.conflicts == ()


def test_doctor_close_closes_a_referral_and_does_not_stop_a_medicine():
    log = _log()
    med = "Continue Amlodipine 5mg daily."
    ref = "Referral to cardiology at Queen Mary SOPC."
    done = "Close the cardiology referral: the patient was seen."
    _rec(log, "N1", med)
    _rec(log, "R1", ref, kind="referral_letter")
    _rec(log, "A1", done, at=datetime(2027, 2, 1, 9, 0), kind="action_note")
    medicine = _med(log, "N1", med)
    referral = log.append(patient_id=P, kind="referral", record_id="R1", quote=ref, provenance="gp", at=JAN,
                          body={"specialty": "cardiology", "destination": "Queen Mary SOPC", "urgency": "urgent"})
    assert len(log.project(P, JAN).open_referrals()) == 1
    closed = log.close(doctor_id="dr_wong", fact_id=referral.id, record_id="A1", quote=done,
                       at=datetime(2027, 2, 1, 9, 0))
    assert closed.kind == "close" and closed.actor == "doctor" and closed.subject_id == referral.id
    view = log.project(P, datetime(2027, 2, 2))
    assert view.open_referrals() == ()
    assert view.referrals[0].status == "closed" and view.referrals[0].quote == ref
    assert [m.fact_id for m in view.medications] == [medicine.id]
    log.close(doctor_id="dr_wong", fact_id=medicine.id, record_id="A1", quote=done, at=datetime(2027, 2, 3, 9, 0))
    still = log.project(P, datetime(2027, 2, 4))
    assert [m.fact_id for m in still.medications] == [medicine.id]
    assert log.facts()[0] is medicine and log.facts()[1] is referral


def test_two_admissions_a_year_apart_do_not_collapse():
    log = _log()
    rows = [
        ("A1", "Admitted 01/03/2026 for pneumonia.", datetime(2026, 3, 1, 8, 0), "admission",
         {"site": "Queen Mary Hospital", "reason": "pneumonia"}),
        ("D1", "Discharged 08/03/2026.", datetime(2026, 3, 8, 11, 0), "discharge", {"site": "Queen Mary Hospital"}),
        ("A2", "Admitted 02/03/2027 for chest pain.", datetime(2027, 3, 2, 8, 0), "admission",
         {"site": "Queen Mary Hospital", "reason": "chest pain"}),
        ("D2", "Discharged 06/03/2027.", datetime(2027, 3, 6, 11, 0), "discharge", {"site": "Queen Mary Hospital"}),
    ]
    ids = {}
    for rid, quote, at, kind, body in rows:
        _rec(log, rid, quote, at=at, provenance="ehealth", kind="ehealth")
        if kind == "discharge":
            body = {**body, "admission_id": ids["A1" if rid == "D1" else "A2"]}
        fact = log.append(patient_id=P, kind=kind, record_id=rid, quote=quote, provenance="ehealth", at=at, body=body)
        ids[rid] = fact.id
    during = log.project(P, datetime(2026, 3, 4))
    assert [(a.reason, a.status) for a in during.admissions] == [("pneumonia", "admitted")]
    between = log.project(P, datetime(2026, 6, 1))
    assert [(a.reason, a.status) for a in between.admissions] == [("pneumonia", "discharged")]
    assert between.admissions[0].discharge_quote == "Discharged 08/03/2026."
    second = log.project(P, datetime(2027, 3, 4))
    assert [(a.reason, a.status) for a in second.admissions] == [("pneumonia", "discharged"), ("chest pain", "admitted")]
    done = log.project(P, datetime(2027, 4, 1))
    assert [(a.reason, a.status) for a in done.admissions] == [("pneumonia", "discharged"), ("chest pain", "discharged")]
    assert done.admissions[0].fact_id != done.admissions[1].fact_id
    assert (done.admissions[1].at - done.admissions[0].at).days >= 360
    note = "Close the pneumonia admission follow-up."
    _rec(log, "C", note, at=datetime(2027, 4, 2, 9, 0), kind="action_note")
    log.close(doctor_id="dr_wong", fact_id=ids["A1"], record_id="C", quote=note, at=datetime(2027, 4, 2, 9, 0))
    closed = log.project(P, datetime(2027, 4, 3))
    assert (closed.admissions[0].follow_up, closed.admissions[1].follow_up) == ("closed", "open")
    assert closed.admissions[0].quote == "Admitted 01/03/2026 for pneumonia."


def test_discharge_without_an_id_links_to_the_open_stay_at_that_site():
    log = _log()
    for rid, quote, at, kind in (
        ("A1", "Admitted 01/03/2026 for pneumonia.", datetime(2026, 3, 1), "admission"),
        ("D1", "Discharged 08/03/2026.", datetime(2026, 3, 8), "discharge"),
        ("A2", "Admitted 02/03/2027 for chest pain.", datetime(2027, 3, 2), "admission"),
        ("D2", "Discharged 06/03/2027.", datetime(2027, 3, 6), "discharge"),
    ):
        _rec(log, rid, quote, at=at, provenance="ehealth")
        body = {"site": "Queen Mary Hospital"}
        if kind == "admission":
            body["reason"] = "pneumonia" if rid == "A1" else "chest pain"
        log.append(patient_id=P, kind=kind, record_id=rid, quote=quote, provenance="ehealth", at=at, body=body)
    view = log.project(P, datetime(2027, 4, 1))
    assert [(a.reason, a.discharge_quote) for a in view.admissions] == [
        ("pneumonia", "Discharged 08/03/2026."),
        ("chest pain", "Discharged 06/03/2027."),
    ]


def test_conditions_allergies_and_observations():
    log = _log()
    _rec(log, "N1", "A: Hypertension. NKDA. BP 138/82.")
    _rec(log, "N2", "A: Hypertension resolved after the notes were corrected.", at=MAY)
    log.append(patient_id=P, kind="condition", record_id="N1", quote="A: Hypertension.", provenance="gp", at=JAN,
               body={"name": "Hypertension"})
    log.append(patient_id=P, kind="allergy", record_id="N1", quote="NKDA", provenance="gp", at=JAN,
               body={"substance": "NKDA"})
    log.append(patient_id=P, kind="observation", record_id="N1", quote="BP 138/82", provenance="gp", at=JAN,
               body={"name": "BP", "value": "138/82"})
    view = log.project(P, JAN)
    assert [c.name for c in view.conditions] == ["Hypertension"]
    assert [a.name for a in view.allergies] == ["NKDA"]
    assert [(o.name, o.value) for o in view.observations] == [("BP", "138/82")]
    log.append(patient_id=P, kind="condition", record_id="N2",
               quote="Hypertension resolved after the notes were corrected.", provenance="gp", at=MAY,
               body={"name": "Hypertension", "status": "resolved"})
    assert log.project(P, MAY).conditions == ()
    assert log.project(P, JAN).conditions[0].name == "Hypertension"


def test_a_doctor_cannot_act_before_the_fact_exists():
    log = _log()
    _rec(log, "N1", "Continue Amlodipine 5mg daily.")
    fact = _med(log, "N1", "Continue Amlodipine 5mg daily.")
    with pytest.raises(CaseError, match="before"):
        log.confirm(doctor_id="dr_wong", fact_id=fact.id, record_id="N1", quote="Continue Amlodipine 5mg daily.",
                    at=datetime(2027, 1, 9, 9, 0))
    assert len(log) == 1


def test_projection_strip_quotes_are_the_stored_facts_and_json_ready():
    log = _log()
    line = "Continue Metformin 500mg bd."
    _rec(log, "N1", line)
    fact = _med(log, "N1", line, drug="Metformin", dose="500mg", frequency="bd")
    view = log.project(P, JAN)
    assert view.medication_lines() == ((fact.at, fact.quote, fact.record_id),)
    payload = view.to_dict()
    json.dumps(payload)
    assert payload["format"] == "case-projection/1"
    sections = {s["id"]: s for s in payload["strip"]}
    assert set(sections) == {"medications", "conflicts", "not_dispensed_here", "referrals", "admissions",
                             "conditions", "allergies", "observations"}
    row = sections["medications"]["lines"][0]
    assert row["quote"] == fact.quote and row["record_id"] == "N1" and row["provenance"] == "gp"
    assert sections["conflicts"]["lines"] == []


def test_confirming_one_drug_does_not_clear_another():
    log = _log()
    statin = "Start atorvastatin 20mg nocte."
    warfarin = "Start warfarin 3mg daily."
    higher = "Atorvastatin increased to 40mg daily."
    _rec(log, "G", f"{statin} {warfarin}")
    _rec(log, "L", higher, at=MAY, provenance="in_group", kind="specialist_letter")
    log.append(patient_id=P, kind="medication", record_id="G", quote=statin, provenance="gp", at=JAN,
               body={"drug": "atorvastatin", "dose": "20mg", "frequency": "nocte"})
    log.append(patient_id=P, kind="medication", record_id="G", quote=warfarin, provenance="gp", at=JAN,
               body={"drug": "warfarin", "dose": "3mg", "frequency": "daily", "dispensed": True})
    changed = log.append(patient_id=P, kind="dose_change", record_id="L", quote=higher, provenance="in_group",
                         at=MAY, body={"drug": "atorvastatin", "action": "increase", "dose": "40mg",
                                       "frequency": "daily"})
    log.confirm(doctor_id="dr_wong", fact_id=changed.id, record_id="L", quote=higher, at=datetime(2027, 5, 4, 9, 0))
    view = log.project(P, datetime(2027, 5, 5))
    assert {m.drug_key for m in view.medications} == {"atorvastatin", "warfarin"}
    assert view.conflicts == ()
    assert next(m for m in view.medications if m.drug_key == "warfarin").dose_key == "3mg"
