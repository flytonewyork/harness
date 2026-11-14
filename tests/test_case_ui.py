"""The patient API and the queue screen carry the case strip. The item keeps its own trace.

Synthetic records only. Every quote below is a substring of the record it cites.
"""
import copy
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import Store, create_app
from backend.case import CaseLog, norm

ROOT = Path(__file__).resolve().parents[1]
TRACE = [{"step": "finding", "text": "This queue item's own trace stays here.",
          "cites": [{"record": "GP", "quote": "Start atorvastatin 20mg nocte."}]}]
SITES = ("gp", "in_group", "ha_sopc", "private_hospital", "ehealth", "patient")
SECTIONS = ("medications", "conflicts", "not_dispensed_here", "referrals",
            "admissions", "conditions", "allergies", "observations")

GP = "Start atorvastatin 20mg nocte."
LETTER = "Atorvastatin increased to 40mg daily."
HOSPITAL = "Start warfarin 3mg daily."
REFERRAL = "Please see Cardiology at Queen Mary Hospital."
STAY = "Admitted to Queen Mary Hospital for pneumonia."
DISCHARGE = "Discharged home after pneumonia."
CONDITION = "A: Hyperlipidaemia."
ALLERGY = "Allergy: penicillin rash."
LAB = "eHealth LDL 3.4 mmol/L."


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def clinic():
    log = CaseLog()
    rows = (
        ("GP", GP, "2027-01-10T09:00:00", "gp", "consult_note"),
        ("LET", LETTER, "2027-05-02T11:00:00", "in_group", "specialist_letter"),
        ("HOSP", HOSPITAL, "2027-04-18T16:00:00", "private_hospital", "discharge_summary"),
        ("REF", REFERRAL, "2027-06-01T10:00:00", "ha_sopc", "referral_letter"),
        ("ADM", STAY, "2026-03-01T08:00:00", "ha_sopc", "discharge_summary"),
        ("DIS", DISCHARGE, "2026-03-08T11:00:00", "ha_sopc", "discharge_summary"),
        ("COND", CONDITION, "2027-01-10T09:00:00", "gp", "consult_note"),
        ("ALG", ALLERGY, "2027-01-10T09:05:00", "patient", "message"),
        ("LAB", LAB, "2027-05-20T08:00:00", "ehealth", "lab_report"),
    )
    for rid, text, at, provenance, kind in rows:
        log.add_record(rid, text, patient_id="P1", at=_at(at), provenance=provenance, kind=kind)
    low = log.append(patient_id="P1", kind="medication", record_id="GP", quote=GP, provenance="gp",
                     at=_at("2027-01-10T09:00:00"), actor="engine",
                     body={"drug": "atorvastatin", "dose": "20mg", "frequency": "nocte", "dispensed": False})
    high = log.append(patient_id="P1", kind="dose_change", record_id="LET", quote=LETTER, provenance="in_group",
                      at=_at("2027-05-02T11:00:00"), actor="engine",
                      body={"drug": "atorvastatin", "action": "increase", "dose": "40mg", "frequency": "daily"})
    log.append(patient_id="P1", kind="medication", record_id="HOSP", quote=HOSPITAL, provenance="private_hospital",
               at=_at("2027-04-18T16:00:00"), actor="engine",
               body={"drug": "warfarin", "dose": "3mg", "frequency": "daily", "dispensed": False})
    referral = log.append(patient_id="P1", kind="referral", record_id="REF", quote=REFERRAL, provenance="ha_sopc",
                          at=_at("2027-06-01T10:00:00"), actor="engine",
                          body={"specialty": "Cardiology", "destination": "Queen Mary Hospital", "urgency": "urgent"})
    stay = log.append(patient_id="P1", kind="admission", record_id="ADM", quote=STAY, provenance="ha_sopc",
                      at=_at("2026-03-01T08:00:00"), actor="engine",
                      body={"site": "Queen Mary Hospital", "reason": "pneumonia"})
    log.append(patient_id="P1", kind="discharge", record_id="DIS", quote=DISCHARGE, provenance="ha_sopc",
               at=_at("2026-03-08T11:00:00"), actor="engine",
               body={"site": "Queen Mary Hospital", "admission_id": stay.id})
    log.append(patient_id="P1", kind="condition", record_id="COND", quote=CONDITION, provenance="gp",
               at=_at("2027-01-10T09:00:00"), actor="engine", body={"name": "Hyperlipidaemia"})
    log.append(patient_id="P1", kind="allergy", record_id="ALG", quote=ALLERGY, provenance="patient",
               at=_at("2027-01-10T09:05:00"), actor="engine", body={"substance": "penicillin"})
    log.append(patient_id="P1", kind="observation", record_id="LAB", quote=LAB, provenance="ehealth",
               at=_at("2027-05-20T08:00:00"), actor="engine", body={"name": "LDL", "value": "3.4 mmol/L"})
    return log, {"low": low.id, "high": high.id, "referral": referral.id, "stay": stay.id}


def run_for(log: CaseLog) -> dict:
    docs = {}
    for rec in log._records.values():
        docs[rec.id] = {"id": rec.id, "kind": rec.kind, "channel": "cms", "available_at": rec.at.isoformat(),
                        "author": "Dr A", "patient_id": rec.patient_id, "text": rec.text, "data": {}}
    return {
        "format": "harness-run/1",
        "source": {"kind": "test", "simulated": True, "clinic_name": "Test Clinic",
                   "start": "2026-01-01T00:00", "end": "2027-12-31T23:59"},
        "staff": [{"id": "dr_a", "name": "Dr A", "role": "principal"}],
        "patients": {
            "P1": {"name_en": "CHAN Tai Man", "name_zh": "陳大文", "hkid": "A123XXX(X)", "sex": "M",
                   "dob": "1964-09-23", "mrn": "1", "cdcc": False, "registered_doctor": "dr_a"},
            "P2": {"name_en": "LEE Mei", "name_zh": "李美", "hkid": "B456XXX(X)", "sex": "F",
                   "dob": "1970-01-01", "mrn": "2", "cdcc": False, "registered_doctor": "dr_a"},
        },
        "documents": docs,
        "items": [{
            "id": "Q1", "patient_id": "P1", "owner_id": "dr_a", "family": "referral",
            "reason": "No booking or reply for referral", "urgency_at_open": 2, "history": [],
            "opened_at": "2026-01-02T09:00", "due": "2027-08-01T09:00", "resolved_at": None, "resolution": None,
            "escalates": None, "evidence": ["GP"], "reasoning": TRACE, "checks": [{"id": "quotes", "status": "pass"}],
        }],
        "feeds": [],
    }


def client_and_log():
    log, ids = clinic()
    store = Store(run_for(log), case=log)
    return TestClient(create_app(store)), store, log, ids


def _section(payload, sid):
    return next(s for s in payload["strip"] if s["id"] == sid)


def _quotes(payload):
    for section in payload["strip"]:
        for line in section["lines"]:
            yield line["quote"], line["record_id"]
            for extra in line.get("quotes") or []:
                yield extra["quote"], extra["record_id"]


def test_patient_and_item_serve_the_projection_without_replacing_the_trace():
    c, store, log, _ids = client_and_log()
    when = "2027-07-01T09:00:00"
    patient = c.get("/api/patients/P1", params={"as_of": when})
    item = c.get("/api/items/Q1", params={"as_of": when})
    assert patient.status_code == 200 and item.status_code == 200
    body, detail = patient.json(), item.json()
    want = log.project("P1", _at(when)).to_dict()
    assert body["case"] == want and detail["case"] == want
    assert body["case"]["format"] == "case-projection/1"
    assert [s["id"] for s in body["case"]["strip"]] == list(SECTIONS)
    assert detail["reasoning"] == TRACE
    assert store.items["Q1"]["reasoning"] == TRACE
    detail["reasoning"].append({"step": "tamper", "text": "no", "cites": []})
    assert store.items["Q1"]["reasoning"] == TRACE
    listed = c.get("/api/items", params={"as_of": when}).json()["items"]
    assert listed and "reasoning" not in listed[0] and "case" not in listed[0]
    for quote, record_id in _quotes(body["case"]):
        assert norm(quote) in norm(store.documents[record_id]["text"])
    provenances = {line["provenance"] for section in body["case"]["strip"] for line in section["lines"]}
    assert provenances == set(SITES)
    conflict = _section(body["case"], "conflicts")
    sides = {(q["provenance"], q["quote"]) for q in conflict["lines"][0]["quotes"]}
    assert ("gp", GP) in sides and ("in_group", LETTER) in sides
    outside = _section(body["case"], "not_dispensed_here")
    assert any("warfarin" in line["text"].lower() for line in outside["lines"])
    assert _section(body["case"], "referrals")["lines"]
    assert _section(body["case"], "admissions")["lines"]
    for key in ("medications", "conflicts", "not_dispensed_here", "referrals", "admissions",
                "conditions", "allergies", "observations"):
        assert key in body["case"] and body["case"][key]


def test_as_of_hides_later_facts_and_an_empty_case_is_still_the_projection():
    c, _store, log, _ids = client_and_log()
    early = c.get("/api/patients/P1", params={"as_of": "2026-01-01T00:00:00"}).json()["case"]
    assert early["format"] == "case-projection/1"
    assert all(not section["lines"] for section in early["strip"])
    mid = c.get("/api/patients/P1", params={"as_of": "2027-02-01T09:00:00"}).json()["case"]
    assert _section(mid, "conflicts")["lines"] == []
    assert any("20mg" in line["text"] for line in _section(mid, "medications")["lines"])
    assert _section(mid, "not_dispensed_here")["lines"] == []
    assert mid == log.project("P1", _at("2027-02-01T09:00:00")).to_dict()
    bare = Store({
        "format": "harness-run/1",
        "source": {"kind": "test", "simulated": True, "clinic_name": "Test", "start": "2027-01-01T00:00",
                   "end": "2027-02-01T00:00"},
        "staff": [{"id": "dr_a", "name": "Dr A", "role": "doctor"}],
        "patients": {"P1": {"name_en": "CHAN Tai Man"}},
        "documents": {},
        "items": [],
        "feeds": [],
    })
    empty = TestClient(create_app(bare)).get("/api/patients/P1").json()["case"]
    assert empty["format"] == "case-projection/1"
    assert [s["id"] for s in empty["strip"]] == list(SECTIONS)
    assert all(not section["lines"] for section in empty["strip"])


def test_only_a_doctor_confirms_rejects_or_closes_and_the_subject_fact_stays():
    c, store, log, ids = client_and_log()
    subject = next(f for f in log.facts() if f.id == ids["high"])
    frozen = copy.deepcopy(subject.to_dict())
    n = len(log)
    agent = c.post(f"/api/doctor/patients/P1/facts/{ids['high']}/confirm",
                   json={"record_id": "LET", "quote": LETTER}, params={"as_of": "2027-05-04T09:00:00"},
                   headers={"X-Role": "agent"})
    assert agent.status_code == 403 and len(log) == n
    missing = c.post(f"/api/agent/patients/P1/facts/{ids['high']}/confirm",
                     json={"record_id": "LET", "quote": LETTER}, headers={"X-Role": "agent"})
    assert missing.status_code in (404, 405) and len(log) == n
    dismissed = c.post(f"/api/doctor/patients/P1/facts/{ids['high']}/dismiss",
                       json={"record_id": "LET", "quote": LETTER}, headers={"X-Role": "doctor"})
    assert dismissed.status_code == 404 and len(log) == n
    invented = c.post(f"/api/doctor/patients/P1/facts/{ids['high']}/confirm",
                      json={"record_id": "LET", "quote": "atorvastatin 99mg invented"},
                      params={"as_of": "2027-05-04T09:00:00"}, headers={"X-Role": "doctor"})
    assert invented.status_code == 422 and len(log) == n
    too_early = c.post(f"/api/doctor/patients/P1/facts/{ids['high']}/confirm",
                       json={"record_id": "LET", "quote": LETTER}, params={"as_of": "2027-01-01T00:00:00"},
                       headers={"X-Role": "doctor"})
    assert too_early.status_code == 422 and len(log) == n
    other = c.post(f"/api/doctor/patients/P2/facts/{ids['high']}/confirm",
                   json={"record_id": "LET", "quote": LETTER}, params={"as_of": "2027-05-04T09:00:00"},
                   headers={"X-Role": "doctor"})
    assert other.status_code == 404 and len(log) == n
    assert c.post("/api/doctor/patients/P1/facts/F9999/confirm",
                  json={"record_id": "LET", "quote": LETTER}, headers={"X-Role": "doctor"}).status_code == 404

    during = c.get("/api/patients/P1", params={"as_of": "2027-05-03T09:00:00"}).json()["case"]
    assert len(_section(during, "conflicts")["lines"]) == 1
    ok = c.post(f"/api/doctor/patients/P1/facts/{ids['high']}/confirm",
                json={"record_id": "LET", "quote": LETTER}, params={"as_of": "2027-05-04T09:10:00"},
                headers={"X-Role": "doctor", "X-Doctor": "dr_a"})
    assert ok.status_code == 200 and ok.json()["format"] == "case-projection/1"
    assert subject.to_dict() == frozen
    assert len(log) == n + 1
    assert log.facts()[-1].kind == "confirm" and log.facts()[-1].actor == "doctor"
    assert _section(ok.json(), "conflicts")["lines"] == []
    still = c.get("/api/patients/P1", params={"as_of": "2027-05-03T09:00:00"}).json()["case"]
    assert len(_section(still, "conflicts")["lines"]) == 1
    assert store.items["Q1"]["reasoning"] == TRACE

    rejected = c.post(f"/api/doctor/patients/P1/facts/{ids['low']}/reject",
                      json={"record_id": "GP", "quote": GP}, params={"as_of": "2027-05-05T09:00:00"},
                      headers={"X-Role": "doctor"})
    assert rejected.status_code == 200
    assert next(f for f in log.facts() if f.id == ids["low"]).to_dict()["kind"] == "medication"
    closed = c.post(f"/api/doctor/patients/P1/facts/{ids['referral']}/close",
                    json={"record_id": "REF", "quote": REFERRAL}, params={"as_of": "2027-06-02T09:00:00"},
                    headers={"X-Role": "doctor"})
    assert closed.status_code == 200
    assert _section(closed.json(), "referrals")["lines"] == []
    saved = next(r for r in closed.json()["referrals"] if r["fact_id"] == ids["referral"])
    assert saved["status"] == "closed"
    stay = c.post(f"/api/doctor/patients/P1/facts/{ids['stay']}/close",
                  json={"record_id": "ADM", "quote": STAY}, params={"as_of": "2027-06-02T09:00:00"},
                  headers={"X-Role": "doctor"})
    assert stay.status_code == 200
    row = next(a for a in stay.json()["admissions"] if a["fact_id"] == ids["stay"])
    assert row["follow_up"] == "closed" and norm(row["quote"]) in norm(STAY)
    assert any(a["did"] == "confirm" and a["who"] == "dr_a" for a in c.get("/api/audit").json())


def test_the_screen_renders_the_strip_and_keeps_the_item_trace():
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend/src/styles.css").read_text(encoding="utf-8")
    page = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "function CaseStrip" in app
    assert app.count("<CaseStrip") == 2
    assert "steps={item.reasoning}" in app
    assert 'aria-label="Why it is here"' in app
    for site in SITES:
        assert site in app
    for sid in ("conflicts", "not_dispensed_here", "referrals", "admissions"):
        assert sid in app
    assert "http://" not in css and "https://" not in css and "@import" not in css
    assert "fonts.googleapis" not in page and "fonts.gstatic" not in page
    assert 'rel="stylesheet"' not in page
    store = (ROOT / "frontend/src/store.ts").read_text(encoding="utf-8")
    assert 'format: "case-projection/1"' in store
    assert "reasoning: [...(it.reasoning ?? [])]" in store
