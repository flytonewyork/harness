"""The product's platform checks run on every item the engine opens (spec docs/specs/001).

Synthetic, opaque records only. These prove the in-product twin of the test bed's gate: the seven checks are
computed for every opened item, a good item passes them all, and a made-up quote or an unseen citation is a hard
fail, from port-visible data with no import of the test bed.
"""
from datetime import datetime, timedelta

from backend.engine import CHECKS, VerifyingClinic
from backend.engine.verify import check_item, clean_steps

NOW = datetime(2027, 5, 10, 9, 0)

R1_TEXT = (
    "PATHLAB REPORT\n"
    "Name: WONG, Siu Ming    DOB: 01/05/1960    A012XXX(X)\n"
    "Potassium   6.2   H   3.5-5.1\n"
)


class FakeClinic:
    """Just enough of a clinic port for the wrapper: reads, the roster, and a permissive open_item."""
    ServiceUnavailable = ConnectionError

    def __init__(self, now=NOW):
        self.now = now
        self._cms = []                                  # record dicts, in arrival order
        self._chart = {}                                # pid -> [record dicts]
        self._patients = {}
        self._staff = {}
        self._roster = {}                               # id -> set of rostered dates (None = every day)
        self._items = {}
        self.opened, self.raised, self.alarms, self.attests = [], [], [], []
        self._seq = 0

    # setup helpers
    def patient(self, pid, name_en, dob, hkid):
        self._patients[pid] = {"id": pid, "name_en": name_en, "dob": dob, "hkid": hkid}

    def doctor(self, did, name, rostered=None, joined=None, left=None):
        self._staff[did] = {"id": did, "name": name, "role": "gp", "joined": joined, "left": left}
        self._roster[did] = rostered                    # None => works every day

    def cms(self, r):
        self._cms.append(r)

    def chart_add(self, pid, r):
        self._chart.setdefault(pid, []).append(r)

    # port surface
    def cms_since(self, cursor=0):
        return [dict(r) for r in self._cms[cursor:]], len(self._cms)

    def portal_since(self, source, cursor=0):
        return [], 0

    def ehealth(self, pid):
        return []

    def chart(self, pid):
        return [dict(r) for r in self._chart.get(pid, [])]

    def patients(self):
        return list(self._patients.values())

    def staff(self):
        return list(self._staff.values())

    def roster(self, day):
        docs = [d for d, days in self._roster.items() if days is None or day in days]
        return {"date": day.isoformat(), "open": True, "closed_reason": "", "doctors": docs}

    def items(self, status=None):
        return [dict(v) for v in self._items.values() if status is None or v["status"] == status]

    def open_item(self, pid, owner, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        self._seq += 1
        iid = f"Q{self._seq:03d}"
        self._items[iid] = {"id": iid, "patient_id": pid, "owner_id": owner, "family": family, "reason": reason,
                            "evidence": list(evidence), "urgency": urgency, "status": "open",
                            "opened_at": self.now.isoformat(timespec="minutes"), "escalates": escalates}
        self.opened.append((iid, pid, owner, family, list(evidence)))
        return iid

    def raise_urgency(self, iid, urgency, reason=""):
        self.raised.append((iid, urgency, reason))

    def alarm(self, channel, reason):
        self.alarms.append((channel, reason))

    def attest_all_clear(self, scope="all", note=""):
        self.attests.append((scope, note))


def rec(rid, pid, kind="lab_report", text=R1_TEXT, available_at=None):
    return {"id": rid, "kind": kind, "channel": "fax", "patient_id": pid, "author": "dr1",
            "text": text, "data": {}, "available_at": (available_at or NOW).isoformat(timespec="minutes")}


def good_reasoning(rid, found=0):
    return [
        {"step": "source", "text": "PathLab report arrived by fax.", "cites": [{"record": rid, "quote": "Potassium 6.2 H 3.5-5.1"}]},
        {"step": "identity", "text": "Filed to WONG Siu Ming's chart.", "cites": [{"record": rid, "quote": "Name: WONG, Siu Ming DOB: 01/05/1960 A012XXX(X)"}]},
        {"step": "finding", "text": "Potassium 6.2, above the range.", "cites": [{"record": rid}]},
        {"step": "assessment", "text": "Working problem: hyperkalaemia. Plan: repeat electrolytes."},
        {"step": "loop_status", "text": "No action note since the result.",
         "cites": [{"record": rid}],
         "search": {"kinds": ["action_note"], "since": (NOW - timedelta(days=30)).isoformat(), "contains_any": [], "found": found}},
    ]


def built(**over):
    """A clinic with patient p1 and doctor dr1 (rostered daily), the engine having seen R1 via cms."""
    c = FakeClinic()
    c.patient("p1", "WONG Siu Ming", "1960-05-01", "A012XXX(X)")
    c.doctor("dr1", "Dr WONG", **over)
    c.cms(rec("R1", "p1"))
    w = VerifyingClinic(c)
    w.cms_since(0)                                       # the engine reads the feed: R1 becomes "seen"
    return c, w


# ---------------------------------------------------------------- the core guarantee
def test_seven_checks_run_on_every_opened_item():
    c, w = built()
    for _ in range(2):
        w.open_item("p1", "dr1", "abnormal_result", "K 6.2", ["R1"], 3, reasoning=good_reasoning("R1"))
    assert len(w.observations) == 2 == len(c.opened)
    for obs in w.observations:
        assert [ck["id"] for ck in obs["checks"]] == CHECKS      # all seven, in order, every time
        assert obs["item"] in c._items                           # recorded against the item the port opened


def test_good_item_passes_all_seven():
    c, w = built()
    iid = w.open_item("p1", "dr1", "abnormal_result", "K 6.2", ["R1"], 3, reasoning=good_reasoning("R1"))
    obs = w.observations[-1]
    assert obs["item"] == iid and obs["passed"] is True
    assert {ck["status"] for ck in obs["checks"]} <= {"pass", "na"}
    assert w.hard_fails == []


# ---------------------------------------------------------------- the two hard fails
def test_made_up_quote_is_a_hard_fail():
    c, w = built()
    bad = good_reasoning("R1")
    bad[0]["cites"][0]["quote"] = "Potassium 2.1 L"          # not in the record
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=bad)
    assert any(h["kind"] == "unsupported_claim" for h in w.hard_fails)
    quotes = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "quotes")
    assert quotes["status"] == "fail"
    assert w.observations[-1]["passed"] is False


def test_unseen_evidence_is_a_hard_fail():
    c, w = built()
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R404"], 3,       # never read through the port
                reasoning=[{"step": "assessment", "text": "x", "cites": [{"record": "R404"}]}])
    assert any(h["kind"] == "unsupported_evidence" for h in w.hard_fails)
    ev = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "evidence")
    assert ev["status"] == "fail"


def test_item_with_no_evidence_is_flagged():
    c, w = built()
    w.open_item("p1", "dr1", "abnormal_result", "K", [], 3, reasoning=[{"step": "assessment", "text": "x", "cites": []}])
    assert any(h["kind"] == "no_evidence" for h in w.hard_fails)


# ---------------------------------------------------------------- the warn/fail checks
def test_identity_mismatch_fails():
    c = FakeClinic()
    c.patient("p1", "CHAN Tai Man", "1980-01-01", "Z999XXX(X)")     # card disagrees with the report
    c.doctor("dr1", "Dr WONG")
    c.cms(rec("R1", "p1"))
    w = VerifyingClinic(c)
    w.cms_since(0)
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1"))
    ident = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "identity")
    assert ident["status"] == "fail"


def test_duplicate_of_an_open_item_warns():
    c, w = built()
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1"))
    w.open_item("p1", "dr1", "abnormal_result", "K again", ["R1"], 3, reasoning=good_reasoning("R1"))
    dup = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "duplicate")
    assert dup["status"] == "warn"


def test_owner_not_rostered_warns_and_departed_owner_fails():
    c, w = built(rostered=set())                            # dr1 is on nobody's roster
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1"))
    owner = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "owner")
    assert owner["status"] == "warn"

    c2, w2 = built(left="2027-01-01")                       # dr1 has left the clinic
    w2.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1"))
    owner2 = next(ck for ck in w2.observations[-1]["checks"] if ck["id"] == "owner")
    assert owner2["status"] == "fail"


def test_wrong_chart_search_fails():
    c, w = built()
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1", found=5))  # chart has 0
    srch = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "searches")
    assert srch["status"] == "fail"


def test_chart_search_rerun_matches():
    c = FakeClinic()
    c.patient("p1", "WONG Siu Ming", "1960-05-01", "A012XXX(X)")
    c.doctor("dr1", "Dr WONG")
    c.cms(rec("R1", "p1"))
    c.chart_add("p1", rec("A1", "p1", kind="action_note", text="Called patient", available_at=NOW - timedelta(days=1)))
    w = VerifyingClinic(c)
    w.cms_since(0)
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1", found=1))
    srch = next(ck for ck in w.observations[-1]["checks"] if ck["id"] == "searches")
    assert srch["status"] == "pass"


# ---------------------------------------------------------------- the wrapper stays a thin, honest port
def test_reads_and_writes_pass_through_unchanged():
    c, w = built()
    recs, cur = w.cms_since(0)
    assert [r["id"] for r in recs] == ["R1"] and cur == 1       # identical data back
    w.raise_urgency("Q001", 4, "escalate")
    w.alarm("fax", "quiet")
    w.attest_all_clear("all", "nothing outstanding")
    assert c.raised == [("Q001", 4, "escalate")] and c.alarms == [("fax", "quiet")] and c.attests == [("all", "nothing outstanding")]
    assert w.now == c.now                                       # the clock proxies to the wrapped port
    c.now = NOW + timedelta(days=1)
    assert w.now == c.now


def test_summary_rolls_up():
    c, w = built()
    w.open_item("p1", "dr1", "abnormal_result", "K", ["R1"], 3, reasoning=good_reasoning("R1"))
    s = w.summary()
    assert s["items"] == 1 and s["passed_all_checks"] == 1 and s["hard_fails"] == 0
    assert set(s["by_check"]) == set(CHECKS)


def test_check_item_is_callable_without_the_wrapper():
    """The checks are a plain function of port-visible data, so a driver can run them directly."""
    seen = {"R1": rec("R1", "p1")}
    card = {"id": "p1", "name_en": "WONG Siu Ming", "dob": "1960-05-01", "hkid": "A012XXX(X)"}
    staff = {"dr1": {"id": "dr1", "name": "Dr WONG", "joined": None, "left": None}}
    checks, problems = check_item(seen, [], [], None, card, staff, lambda d: True, NOW,
                                  "p1", "dr1", "abnormal_result", ("R1",), clean_steps(good_reasoning("R1")), 3, None)
    assert [c["id"] for c in checks] == CHECKS and problems == []
