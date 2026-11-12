"""Shared journey checks: every journey package keeps to the engine port and composes into one suite."""
import inspect
import re

import pytest

from backend.engine import FAMILIES
from backend.journeys import all_families, load, names, suite
from backend.journeys.followup import classify

ALLOWED_API = {"now", "clinic", "staff", "roster", "patients", "patient", "find_patients", "cms_since", "portal_since",
               "ehealth", "chart", "record", "items", "open_item", "raise_urgency", "alarm", "attest_all_clear"}
FORBIDDEN = re.compile(r"\b(close_item|dismiss|downgrade|lower_urgency|send_message|send_whatsapp|reassure_patient)\s*\(")


def test_journeys_are_discovered():
    assert "followup" in names()
    fams = all_families()
    assert set(FAMILIES) <= set(fams) and "patient_reported" in fams


@pytest.mark.parametrize("name", names())
def test_journey_contract(name):
    mod = load(name)
    assert isinstance(mod.FAMILIES, dict) and mod.FAMILIES
    assert inspect.isclass(mod.JOURNEY)
    src = inspect.getsource(mod)
    calls = set(re.findall(r"\bapi\.(\w+)", src))
    assert calls <= ALLOWED_API, f"{name} uses port methods outside the contract: {calls - ALLOWED_API}"
    assert not FORBIDDEN.search(src), f"{name} calls a forbidden action"
    for meth in ("reset", "on_cms", "on_portal", "tick"):          # cooperative: always defer to the core first
        fn = mod.JOURNEY.__dict__.get(meth)
        if fn is not None:
            assert f"super().{meth}(" in inspect.getsource(fn), f"{name}.{meth} must call super().{meth} first"


def test_suite_composes_every_journey():
    eng = suite()
    assert eng.name == "suite" and eng.journeys == names()
    one = suite("followup")
    assert one.name == "journey:followup"
    assert hasattr(one, "push") and hasattr(one, "claims")


@pytest.mark.parametrize("text,label", [
    ("好返好多喇，多謝醫生！", "better"),
    ("已經冇再發燒，咳都少咗好多，唔使擔心。", "better"),
    ("唔係好返喎，反而差咗，夜晚咳到瞓唔到。", "worse"),
    ("仲咳得好犀利，今日開始發燒 38.6 度，啲痰黃色。", "worse"),
    ("Still coughing a lot and now I get breathless walking up the stairs.", "worse"),
    ("No more fever, feeling much better thanks", "better"),
    ("成晚透唔到氣，心口好翳好痛。", "red_flag"),
    ("今朝咳出啲血，係咪要即刻返嚟？", "red_flag"),
])
def test_followup_reads_replies(text, label):
    assert classify(text)[0] == label


def test_one_owner_per_message():
    from backend.engine.rules import RulesHarness
    from backend.journeys import Push
    from backend.journeys.followup import FollowupJourney

    class Meds:
        def bid(self, api, r):
            return 3 if "amlodipine" in r["text"] else 0

    eng = type("S", (Push, Meds, FollowupJourney, RulesHarness), {})(version="1.1")
    eng._route, eng.fu_out = {}, {}
    msg = {"id": "m1", "kind": "message", "channel": "careline", "patient_id": "P1", "data": {"direction": "in"},
           "available_at": "2027-03-01T10:00", "text": "please repeat my amlodipine"}
    assert eng.won(None, msg, Meds) and not eng.won(None, msg, FollowupJourney)
    other = dict(msg, id="m2", text="still coughing")
    assert eng.won(None, other, FollowupJourney)
