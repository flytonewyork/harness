"""A critical lab outside the three daily ticks is raised for the named doctor. Nothing is sent to the patient."""
from datetime import datetime

from backend.engine.overnight import TICKS, wake_critical_lab
from backend.engine.rules import RulesHarness

POST = datetime(2027, 1, 5, 22, 15)
LAB = """PATHLAB REPORT
Name: CHEUNG, Wai Man    DOB: 01/05/1960
Lab No: PA27-000001
Doctor: Dr CHAN Siu Ming
Potassium  6.8  H  mmol/L  3.5-5.1
"""


def _record(value=6.8, when=POST):
    return {"id": "R1", "kind": "lab_report", "channel": "cms", "available_at": when.isoformat(timespec="minutes"),
            "patient_id": "P1", "author": "dr_chan", "text": LAB.replace("6.8", f"{value:g}"),
            "data": {"results": [{"test": "Potassium", "value": value, "flag": "H", "unit": "mmol/L",
                                  "ref_low": 3.5, "ref_high": 5.1}],
                     "lab_no": "PA27-000001"}}


class Port:
    ServiceUnavailable = ConnectionError

    def __init__(self, now, record):
        self.now = now
        self.record = record
        self.opened = []

    def clinic(self):
        return {"labs": [{"id": "pathlab", "name": "PathLab Medical Laboratories Ltd.", "portal": True, "paper": "courier"}]}

    def staff(self):
        return [{"id": "dr_chan", "name": "Dr CHAN Siu Ming", "role": "gp", "joined": None, "left": None}]

    def patients(self):
        return [{"id": "P1", "name_en": "CHEUNG Wai Man", "dob": "1960-05-01", "hkid": "",
                 "registered_doctor": "dr_chan", "ehealth_consent": False}]

    def roster(self, day):
        return {"date": day.isoformat(), "open": True, "closed_reason": "", "doctors": ["dr_chan"]}

    def chart(self, pid):
        return [self.record]

    def find_patients(self, name=None, dob=None, hkid=None):
        return self.patients()

    def open_item(self, pid, owner, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        self.opened.append({"pid": pid, "owner": owner, "family": family, "urgency": urgency, "evidence": evidence,
                            "reasoning": reasoning})
        return "Q001"


def _wake(now, value):
    record = _record(value, now)
    port = Port(now, record)
    harness = RulesHarness(version="1.0")
    harness.reset(port)
    got = wake_critical_lab(harness, port, record)
    return got, port


def test_ticks_are_the_three_daily_runs():
    assert TICKS == (datetime(2027, 1, 5, 8, 30).time(), datetime(2027, 1, 5, 13, 30).time(),
                     datetime(2027, 1, 5, 19, 30).time())


def test_a_critical_lab_off_tick_raises_for_the_named_doctor_and_sends_nothing():
    got, port = _wake(POST, 6.8)
    assert got == {"raised": True, "reason": "raised for the named doctor", "patient_message": False}
    assert len(port.opened) == 1
    item = port.opened[0]
    assert item["owner"] == "dr_chan" and item["urgency"] == 4 and item["family"] == "abnormal_result"
    assert item["evidence"] == ["R1"]


def test_a_tick_and_a_non_critical_lab_are_not_raised_here():
    on_tick, port = _wake(datetime(2027, 1, 5, 19, 30), 6.8)
    assert on_tick["raised"] is False and on_tick["reason"] == "on a scheduled tick" and port.opened == []
    mild, port = _wake(POST, 5.2)
    assert mild["raised"] is False and mild["reason"] == "not critical" and port.opened == []
