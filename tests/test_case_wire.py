"""The journeys share one case log. Rules v1.0 does not."""
from datetime import datetime, timedelta

from backend.case import CaseLog
from backend.case.wire import note_patient
from backend.engine.rules import RulesHarness, Why
from backend.journeys import suite
from backend.precare import PrecareAgent

T0 = datetime(2027, 3, 1, 9, 0)
T1 = datetime(2027, 4, 13, 11, 0)
T2 = datetime(2027, 4, 20, 15, 0)
PID = "p1"

CONSULT = "Consultation\nStarted amlodipine 5mg daily for blood pressure."
DISPENSE = "Dispensed: Amlodipine 5mg daily x28 days"
DISCHARGE = """HOSPITAL AUTHORITY - Queen Elizabeth Hospital
DISCHARGE SUMMARY

Patient: CHEUNG, Wai Man  HKID G598XXX(X)
Principal diagnosis: Atrial fibrillation
Follow-up: Surgical SOPC in 6 weeks, appointment given. No need for family doctor follow up.
Medications on discharge: Warfarin 3mg daily (NEW); Gliclazide STOPPED."""
REFERRAL = "REFERRAL LETTER\nHospital Authority SOPC\nReason: atrial fibrillation\nURGENT"


class Clinic:
    ServiceUnavailable = ConnectionError

    def __init__(self):
        self.now = T0
        self.recs = []
        self._items = {}

    def add(self, kind, at, text, data=None, channel="scan"):
        rid = f"R{len(self.recs) + 1}"
        self.recs.append({"id": rid, "kind": kind, "channel": channel, "available_at": at.isoformat(timespec="minutes"),
                          "patient_id": PID, "author": "dr_wong", "text": text, "data": data or {}})
        return rid

    def clinic(self):
        return {"labs": [{"id": "pathlab", "name": "PathLab", "portal": False}]}

    def staff(self):
        return [{"id": "dr_wong", "name": "Dr WONG", "role": "doctor"}]

    def roster(self, d):
        return {"date": d.isoformat(), "open": True, "closed_reason": "", "doctors": ["dr_wong"]}

    def patients(self):
        return [{"id": PID, "name_en": "CHEUNG Wai Man", "registered_doctor": "dr_wong", "dob": "1961-04-09",
                 "hkid": "G598XXX(X)", "ehealth_consent": False}]

    def find_patients(self, name=None, dob=None, hkid=None):
        return []

    def chart(self, pid):
        return [r for r in self.recs if r["patient_id"] == pid]

    def items(self, status=None):
        return [dict(v) for v in self._items.values() if status is None or v["status"] == status]

    def open_item(self, pid, owner, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"Q{len(self._items)}"
        self._items[iid] = {"id": iid, "patient_id": pid, "owner_id": owner, "family": family, "reason": reason,
                            "evidence": list(evidence), "urgency": urgency, "status": "open", "reasoning": reasoning or []}
        return iid

    def raise_urgency(self, iid, u, reason=""):
        assert u >= self._items[iid]["urgency"]
        self._items[iid]["urgency"] = u

    def alarm(self, ch, reason):
        pass

    def attest_all_clear(self, scope="all", note=""):
        raise AssertionError("the case wire never attests")

    def ehealth(self, pid):
        raise PermissionError("no consent")


def test_rules_v10_does_not_read_or_clear_a_case():
    log = CaseLog()
    log.add_record("RX", "Warfarin 3mg daily", patient_id=PID, at=T0, provenance="ehealth", kind="discharge_summary")
    log.append(patient_id=PID, kind="medication", record_id="RX", quote="Warfarin 3mg daily", provenance="ehealth",
               at=T0, actor="engine", body={"drug": "warfarin", "dose": "3mg", "dispensed": False})
    clinic = Clinic()
    eng = RulesHarness(version="1.0", case=log)
    eng.reset(clinic)
    assert getattr(eng, "case", None) is None
    assert len(log) == 1
    eng.meds[PID].append((T0, DISPENSE, "Rd"))
    assert eng.relevant_meds(PID, ["INR"], T2) == []
    w = Why()
    assert eng.context_step(w, PID, [("INR", 4.0)], T2, None) == []
    assert "Also recorded on the case" not in w.steps[-1]["text"]
    assert "No relevant medicines dispensed at this clinic" in w.steps[-1]["text"]


def test_shared_case_is_visible_across_journeys_and_as_of():
    log = CaseLog()
    clinic = Clinic()
    eng = suite("followon", "medication", "insurance", case=log)
    eng.reset(clinic)
    assert eng.case is log and len(log) == 0
    assert not hasattr(eng, "mx_meds") and not hasattr(eng, "fo_refs")
    clinic.add("consult_note", T0, CONSULT)
    clinic.add("dispensing", T0, DISPENSE)
    clinic.add("discharge_summary", T1, DISCHARGE, channel="ehealth")
    clinic.add("referral_letter", T2, REFERRAL)
    for rec in clinic.recs:
        clinic.now = datetime.fromisoformat(rec["available_at"])
        eng.on_cms(clinic, rec)

    early = log.project(PID, T0 - timedelta(days=1))
    assert early.medications == () and early.open_referrals() == () and early.admissions == ()
    at_dispense = log.project(PID, T0)
    assert any(m.drug_key == "amlodipine" and m.dispensed for m in at_dispense.medications)
    assert all(m.drug_key != "warfarin" for m in at_dispense.medications)
    assert at_dispense.open_referrals() == ()

    full = log.project(PID, T2)
    assert any(m.drug_key == "warfarin" for m in full.not_dispensed_here)
    assert any(m.drug_key == "amlodipine" for m in full.medications)
    assert any(a.reason.lower().startswith("atrial") for a in full.admissions)
    assert len(full.open_referrals()) == 1
    for fact in log.facts(PID):
        assert fact.quote in " ".join(log.record(fact.record_id).text.split())

    changes, stops, meds = eng._mx_state(PID, T2)
    assert "amlodipine" in meds and "warfarin" in {c["drug"] for c in changes}
    assert "gliclazide" in stops
    assert eng._mx_state(PID, T0)[2].keys() == {"amlodipine"}
    assert [r["rid"] for r in eng._fo_refs(PID, T2)] == [full.open_referrals()[0].record_id]
    assert eng._fo_refs(PID, T0) == []
    linked = eng.ins_linked_referral(PID, T2)
    assert linked is not None and linked["rid"] == full.open_referrals()[0].record_id
    assert eng.ins_linked_referral(PID, T0) is None

    w = Why()
    cited = eng.context_step(w, PID, [("INR", 4.0)], T2, None)
    assert any("arfarin" in (q or "") for _, q, _ in cited)
    assert "Also recorded on the case" in w.steps[-1]["text"]
    w0 = Why()
    assert eng.context_step(w0, PID, [("INR", 4.0)], T0, None) == []

    # The patient's own reply joins the same log, and a procedure reads the hospital warfarin.
    port = _PrecarePort()
    agent = PrecareAgent(port, case=log)
    assert agent.case is log
    agent.start("A1")
    for reply in ("Chan Tai Man", "11/02/1961", "yes", "Diabetes review", "no"):
        port.say(agent, reply)
    view = log.project(PID, port.now)
    assert any(m.provenance == "patient" and "Diabetes review" in m.quote for m in view.conditions)
    assert any(m.drug_key == "warfarin" for m in view.medications)
    assert any(it["family"] == "previsit_question" for it in port.items.values())

    note_patient(log, patient_id=PID, record_id="later", text="penicillin gives me a rash", at=T2 + timedelta(days=1),
                 step="allergies")
    assert log.project(PID, T2).allergies == ()
    assert any("penicillin" in a.quote for a in log.project(PID).allergies)

    eng.reset(clinic)
    assert eng.case is log and len(log) == 0
    assert log.project(PID).medications == ()


def test_standalone_journey_resets_a_private_log():
    clinic = Clinic()
    eng = suite("medication")
    eng.reset(clinic)
    first = eng.case
    clinic.add("dispensing", T0, DISPENSE)
    eng.on_cms(clinic, clinic.recs[-1])
    assert any(m.drug_key == "amlodipine" for m in first.project(PID).medications)
    eng.reset(clinic)
    assert eng.case is not first and len(eng.case) == 0


class _PrecarePort:
    def __init__(self):
        self.now = datetime(2027, 5, 1, 9, 0)
        self.appt = {"id": "A1", "patient_id": PID, "doctor_id": "dr_wong",
                     "start": "2027-05-04T09:00", "visit_type": "procedure", "tests": [],
                     "procedure": "excision", "lang": "en",
                     "clinic": {"name_en": "Harbourview Clinic", "name_zh": "海景診所", "phone": "2000 0000"}}
        self._patient = {"id": PID, "name_en": "CHAN Tai Man", "name_zh": "", "dob": "1961-02-11"}
        self._doctor = {"id": "dr_wong", "name_en": "Dr Chan", "name_zh": "陳醫生"}
        self.msgs, self.items, self.n = [], {}, 0

    def appointment(self, a):
        return self.appt

    def patient(self, p):
        return self._patient

    def doctor(self, d):
        return self._doctor

    def messages(self, a):
        return self.msgs

    def send(self, a, templates, lang, slot=None):
        self.n += 1
        self.msgs.append({"id": f"o{self.n}", "dir": "out", "text": "sent", "slot": slot})
        return f"o{self.n}"

    def say(self, agent, text):
        self.now += timedelta(minutes=2)
        self.n += 1
        mid = f"i{self.n}"
        self.msgs.append({"id": mid, "dir": "in", "text": text})
        agent.handle("A1", mid)

    def open_item(self, patient_id, owner_id, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"I{len(self.items) + 1}"
        self.items[iid] = {"id": iid, "family": family, "reason": reason, "reasoning": reasoning or [], "status": "open"}
        return iid

    def raise_urgency(self, item_id, urgency, reason=""):
        pass

    def alarm(self, channel, reason):
        pass

    def attest_all_clear(self, scope="all", note=""):
        raise AssertionError("precare never attests")
