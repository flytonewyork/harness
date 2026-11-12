"""Follow-on journey: the readers (discharge summaries, specialist letters, care-line replies, identity headers)
and the journey's place in the suite. End-to-end behaviour is scored in LoopBench (scenarios/journeys/followon)."""
from backend.journeys import suite
from backend.journeys.followon import (FollowonJourney, classify_reply, header_identity, read_discharge,
                                       read_letter)

DC = """HOSPITAL AUTHORITY - Queen Elizabeth Hospital
DISCHARGE SUMMARY

Patient: CHEUNG, Wai Man (張慧敏)  F/66Y  HKID G598XXX(X)
Admitted: 09/04/2027   Discharged: 13/04/2027
Principal diagnosis: Acute pyelonephritis
Follow-up: Please follow up with family doctor in 1 week.
Pending at discharge: Blood culture x2 (taken on admission) - result pending; family doctor please check the result.
Medications on discharge: Warfarin 3mg daily (NEW); Gliclazide STOPPED; Metformin 500mg bd."""


def test_discharge_asks_pending_and_medicines():
    d = read_discharge(DC)
    assert d["review_days"] == 7
    assert d["asks"] == ["Please follow up with family doctor in 1 week"]
    assert d["pending"].startswith("Blood culture x2")
    assert d["med_changes"] == ["Warfarin 3mg daily (NEW)", "Gliclazide STOPPED"] and d["anticoag"]
    for q in d["asks"] + d["med_changes"]:
        assert q in DC                                    # every quote is verbatim


def test_discharge_hospital_keeps_the_follow_up():
    d = read_discharge(DC.replace("Please follow up with family doctor in 1 week.",
                                  "Surgical SOPC in 6 weeks, appointment given. No need for family doctor follow up.")
                         .replace("\nPending at discharge: Blood culture x2 (taken on admission) - result pending; family doctor please check the result.", ""))
    assert d["negated"] and not d["asks"] and not d["pending"] and not d["inr_days"]


def test_discharge_inr_and_review_in_weeks():
    d = read_discharge(DC.replace("Please follow up with family doctor in 1 week.",
                                  "Medical SOPC in 8 weeks (appointment given). Family doctor please check INR in 5 days and adjust warfarin dose."))
    assert d["inr_days"] == 5 and d["review_days"] is None
    assert any("check INR in 5 days" in a for a in d["asks"])
    d2 = read_discharge(DC.replace("in 1 week.", "in 2 weeks; repeat CXR in 6 weeks."))
    assert d2["review_days"] == 14 and any("repeat CXR in 6 weeks" in a for a in d2["asks"])


def test_letter_asks_chase_and_discharged_back():
    head = "Dr NGAI Tsz Ho, Specialist in Gastroenterology\n05/10/2027\n\nDear Dr CHAN,\n\nRe: TAM, Kin Wah  DOB 09/04/1966\n\n"
    body = ("OGD today: gastric ulcer, biopsy taken. Started H. pylori eradication therapy for 14 days. Please arrange urea breath "
            "test 4 weeks after completion of treatment. Please chase the biopsy result with us if you have not received it within 2 weeks.")
    d = read_letter(head + body + "\n\nYours sincerely,\nDr NGAI")
    assert len(d["asks"]) == 2 and len(d["chase"]) == 1 and d["soonest_days"] == 14
    assert all(a in head + body for a in d["asks"])
    back = read_letter(head + "Thank you for referring. Benign. No further follow-up needed; discharged back to your care.\n\nYours sincerely,")
    assert back["discharged_back"] and not back["asks"]


def test_reply_classes_bilingual_with_negation():
    assert classify_reply("仲係好喘，行幾步就要停低，夜晚要坐住瞓，腳都腫咗。")[0] == "red_flag"
    assert classify_reply("Still short of breath and my ankles are swollen")[0] == "red_flag"
    assert classify_reply("冇氣促，好返好多，多謝")[0] == "fine"            # negated symptom is not a red flag
    assert classify_reply("都係咁，仲有少少發燒，冇好到。")[0] == "not_better"
    assert classify_reply("唔好意思，我上星期唔記得咗去專科覆診，而家點算？")[0] == "appt_missed"
    assert classify_reply("醫院話我外科專科要排到後年先睇，係咪太耐？")[0] == "appt_too_long"
    assert classify_reply("好返好多，咳少咗，多謝！")[0] == "fine"


def test_identity_header():
    h = header_identity(DC)
    assert h["name"] == "CHEUNG Wai Man" and h["hkid"] == "G598"
    assert header_identity("Re: TAM, Kin Wah  DOB 09/04/1966")["dob"] == "1966-04-09"


def test_in_the_suite_and_claims():
    eng = suite("followon")
    assert isinstance(eng, FollowonJourney) and eng.name == "journey:followon"
    eng2 = suite()
    assert isinstance(eng2, FollowonJourney)
    assert eng.claims(None, {"kind": "discharge_summary", "channel": "ehealth"})
    assert eng.claims(None, {"kind": "specialist_letter", "channel": "fax"})


def test_background_distractors_stay_quiet():
    """Phrasings in the simulated clinic's own discharge summaries that are not asks for this clinic's doctor."""
    for fu in ("Disposal: discharged. See family doctor if not better.", "Follow-up: Follow up at GOPC as booked.",
               "Follow-up: Nil. Discharged to self-care; see family doctor if symptoms recur."):
        d = read_discharge(DC.replace("Follow-up: Please follow up with family doctor in 1 week.", fu)
                           .replace("\nPending at discharge: Blood culture x2 (taken on admission) - result pending; family doctor please check the result.", ""))
        assert not d["asks"] and not d["pending"], fu
    d = read_discharge(DC.replace("Please follow up with family doctor in 1 week.", "GOPC/family doctor follow up in 10 days for BP and RFT."))
    assert d["review_days"] == 10


def test_care_line_never_lowers_an_escalated_referral_item():
    """The open triage item may already be at 4 (escalation pages unactioned urgent items); a care-line message
    about the same referral must not 'raise' it to 3, which the platform rejects as lowering."""
    from datetime import datetime
    calls = []

    class API:
        now = datetime(2027, 5, 1, 10)

        def items(self, status=None):
            return [{"id": "I1", "urgency": 4, "patient_id": "P1"}]

        def raise_urgency(self, iid, u, reason=""):
            calls.append(u)

    eng = suite("followon")
    eng.fo_refs = {"P1": [{"t": datetime(2027, 4, 1), "rid": "R1", "urgent": True, "owner": "D1", "text": "Reason: x"}]}
    eng.fo_docs, eng.fo_watch, eng.raised = {}, {}, {("ref", "tri", "R1"): "I1"}
    eng.pname = lambda pid: "X"
    msg = {"id": "M1", "patient_id": "P1", "text": "個專科預約太耐喇", "kind": "message", "channel": "careline", "data": {}}
    eng.fo_reply(API(), msg, datetime(2027, 5, 1, 9))
    assert all(u >= 4 for u in calls)


def test_red_flag_on_a_later_line_is_the_quote():
    from datetime import datetime
    seen = []

    class API:
        now = datetime(2027, 5, 1, 10)

        def items(self, status=None):
            return []

    eng = suite("followon")
    eng.fo_refs, eng.fo_docs, eng.fo_watch = {}, {}, {}
    eng.pname = lambda pid: "X"
    eng.fo_raise = lambda api, key, pid, fam, reason, ev, u, due, w, r, **k: seen.append((u, w.steps))
    eng.chart_search = lambda api, pid, kinds, since: (0, {"kinds": kinds, "since": since.isoformat(), "found": 0})
    text = "多謝醫生覆我\n今朝開始心口好痛，透唔到氣"
    eng.fo_reply(API(), {"id": "M1", "patient_id": "P1", "text": text, "kind": "message", "channel": "careline", "data": {}},
                 datetime(2027, 5, 1, 9))
    (u, steps), = seen
    finding = next(s for s in steps if s["step"] == "finding")
    assert u == 4 and "透唔到氣" in finding["cites"][0]["quote"] and finding["cites"][0]["quote"] in text
