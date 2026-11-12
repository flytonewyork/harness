"""Post-checkup journey on a small fake clinic (no simulator): what it opens, what it leaves alone, and that every
quote it cites is word for word in the record it cites. The full eval is LoopBench's journey_eval.py."""
from datetime import date, datetime, timedelta

from backend.journeys import suite
from backend.journeys.post_checkup import TEMPLATE

DOC = "dr_chan"
PID = "P1"


def norm(s):
    return " ".join(str(s).split())


def lab_text(rows, lab_no="PA27-000123"):
    head = ["PATHLAB MEDICAL LABORATORIES LTD.", "LABORATORY REPORT", "",
            "Name : CHAN, Mei Ling  陳美玲        Sex/Age : F/52Y",
            f"HKID : A123XXX(X)        DOB : 17/05/1974        Lab No : {lab_no}",
            "Clinic : Harbourview Family Clinic        Doctor : Dr CHAN Siu Ming", "",
            f"{'TEST':<32}{'RESULT':>10}  {'FLAG':<4} {'UNIT':<14}REFERENCE RANGE"]
    body = [f"{t:<32}{v:>10g}  {f:<4} {u:<14}{rr}" for t, v, f, u, rr in rows]
    return "\n".join(head + body + ["", "** END OF REPORT **"])


NORMAL = [("Haemoglobin", 13.1, "", "g/dL", "11.5 - 15.5"), ("Sodium", 140, "", "mmol/L", "136 - 145"),
          ("ALT", 24, "", "U/L", "< 40"), ("LDL cholesterol", 2.6, "", "mmol/L", "< 3.4")]


class FakeClinic:
    """The engine port, over a list of records released by time. Only port methods exist."""
    ServiceUnavailable = ConnectionError

    def __init__(self, records):
        self.now = datetime(2027, 3, 8, 8, 30)
        self.recs = sorted(records, key=lambda r: r["available_at"])
        self._items = {}

    def clinic(self):
        return {"name": "Harbourview", "profile": "hk_solo", "system": "x", "capabilities": {}, "labs": [
            {"id": "pathlab", "name": "PathLab Medical Laboratories Ltd.", "portal": False, "paper": "courier"}],
            "imaging": "Imaging centre", "courier_runs": {}, "sessions": {}, "feeds": {}, "programmes": {}}

    def staff(self):
        return [{"id": DOC, "name": "Dr CHAN Siu Ming", "role": "doctor", "joined": None, "left": None}]

    def roster(self, d: date):
        open_ = d.weekday() != 6
        return {"date": d.isoformat(), "open": open_, "closed_reason": "" if open_ else "Sunday",
                "sessions": [], "doctors": [DOC] if open_ else []}

    def patients(self):
        return [{"id": PID, "name_en": "CHAN Mei Ling", "dob": "1974-05-17", "registered_doctor": DOC, "ehealth_consent": False}]

    def find_patients(self, name=None, dob=None, hkid=None):
        p = self.patients()[0]
        ok = (dob is None or dob == p["dob"]) and (name is None or set(name.upper().split()) <= set(p["name_en"].split()))
        return [p] if ok else []

    def _visible(self):
        return [r for r in self.recs if datetime.fromisoformat(r["available_at"]) <= self.now]

    def cms_since(self, cursor):
        vis = self._visible()
        return vis[cursor:], len(vis)

    def portal_since(self, source, cursor):
        return [], 0

    def ehealth(self, pid):
        return []

    def chart(self, pid):
        return [r for r in self._visible() if r["patient_id"] == pid]

    def items(self, status=None):
        return [dict(v) for v in self._items.values() if status is None or v["status"] == status]

    def open_item(self, pid, owner, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"Q{len(self._items)}"
        self._items[iid] = {"id": iid, "patient_id": pid, "owner_id": owner, "family": family, "reason": reason,
                            "evidence": list(evidence), "urgency": urgency, "status": "open", "reasoning": reasoning or [],
                            "opened_at": self.now.isoformat(), "resolved_at": None, "escalates": escalates}
        return iid

    def raise_urgency(self, iid, urgency, reason=""):
        assert urgency >= self._items[iid]["urgency"]
        self._items[iid]["urgency"] = urgency

    def alarm(self, channel, reason):
        pass

    def attest_all_clear(self, scope="all", note=""):
        raise AssertionError("the journey never attests")


def rec(rid, kind, t, text, data=None, channel="cms", pid=PID, author=DOC):
    return {"id": rid, "kind": kind, "channel": channel, "patient_id": pid, "author": author, "text": text,
            "data": data or {}, "available_at": t.isoformat(), "produced_at": t.isoformat()}


def health_check(t0=datetime(2027, 3, 8, 10, 20), imaging=False, rows=NORMAL, extra=()):
    note = ("Health check (standard package). Asymptomatic, here for a yearly check.\nBP 124/78.\n"
            "Plan: health-check bloods" + (", CXR" if imaging else "") + ". Go through all results with the patient when the reports are back.")
    out = [rec("N1", "consult_note", t0, note, {"visit": "health check"}),
           rec("O1", "lab_request", t0 + timedelta(minutes=5), "Lab request to PathLab: health check.", {"lab_no": "PA27-000123"}),
           rec("L1", "lab_report", t0 + timedelta(days=2), lab_text(rows), {"doc_type": "Lab report"}, channel="courier")]
    return out + list(extra)


def run(records, days=14):
    api = FakeClinic(records)
    eng = suite("post_checkup")
    eng.reset(api)
    end = api.now + timedelta(days=days)
    while api.now < end:
        for h in (8, 13, 19):
            api.now = api.now.replace(hour=h, minute=30)
            eng.tick(api)
        api.now += timedelta(days=1)
    return api, eng


def assert_verbatim(api, it):
    texts = {r["id"]: r["text"] for r in api.recs}
    for step in it["reasoning"]:
        for c in step["cites"]:
            if c.get("quote"):
                assert norm(c["quote"]) in norm(texts[c["record"]]), (c["record"], c["quote"])


def test_normal_health_check_nobody_told_opens_one_results_item():
    api, eng = run(health_check(), days=9)          # before the core's day-7 escalation of an unactioned item
    items = api.items()
    assert [i["family"] for i in items] == ["checkup_result"]
    it = items[0]
    assert it["urgency"] == 1 and set(it["evidence"]) == {"N1", "L1"}
    assert any(TEMPLATE in s["text"] for s in it["reasoning"])
    assert any(s.get("search") for s in it["reasoning"])            # a chart search the platform re-runs
    assert_verbatim(api, it)
    # it waited for the clinic: not before 3 open days after the report
    assert datetime.fromisoformat(it["opened_at"]) >= datetime(2027, 3, 13)


def test_results_already_gone_through_stays_quiet():
    seen = rec("N2", "consult_note", datetime(2027, 3, 11, 15, 0), "Results visit: health-check results gone through.")
    api, _ = run(health_check(extra=[seen]))
    assert api.items() == []


def test_abnormal_health_check_raises_one_item_tied_to_the_check():
    rows = NORMAL + [("HbA1c", 7.6, "H", "%", "4 - 6")]
    api, _ = run(health_check(rows=rows))
    items = api.items()
    assert [i["family"] for i in items] == ["abnormal_result"]       # it carries the results conversation too
    it = items[0]
    assert it["urgency"] >= 2                                          # a new finding in a well patient: soon
    assert any(s["step"] == "context" and "health-check bloods" in s["text"] for s in it["reasoning"])
    assert_verbatim(api, it)


def cxr(impression, finding="右上肺見8毫米結節。"):
    text = "\n".join(["IMAGING CENTRE", "CHEST X-RAY 報告 REPORT", "",
                      "Name: CHAN, Mei Ling   DOB: 17/05/1974   HKID: A123XXX(X)", "檢查日期 Date of exam: 09/03/2027", "",
                      f"所見 FINDINGS: {finding}", f"結論 IMPRESSION: {impression}"])
    return rec("I1", "imaging_report", datetime(2027, 3, 10, 15, 0), text, {"doc_type": "Imaging report"}, channel="fax")


def test_chinese_imaging_recommendation_is_flagged_with_its_own_words():
    api, _ = run(health_check(imaging=True, extra=[cxr("右上肺結節，建議三個月後覆查電腦掃描。")]))
    fam = {i["family"]: i for i in api.items()}
    assert "followup_recommended" in fam
    quotes = [c.get("quote", "") for s in fam["followup_recommended"]["reasoning"] for c in s["cites"]]
    assert any("建議三個月後覆查電腦掃描" in q for q in quotes)
    assert_verbatim(api, fam["followup_recommended"])


def test_conditional_or_normal_imaging_is_left_for_the_results_visit():
    for imp in ("Cholelithiasis. Advise surgical consultation if symptomatic.", "Normal study."):
        api, _ = run(health_check(imaging=True, extra=[cxr(imp, finding="Lungs clear.")]))
        assert [i["family"] for i in api.items()] == ["checkup_result"], imp
        assert set(api.items()[0]["evidence"]) == {"N1", "L1", "I1"}


def test_template_carries_no_result_and_no_reassurance():
    low = TEMPLATE.lower()
    for word in ("normal", "fine", "nothing to worry", "放心", "正常", "冇問題", "abnormal", "異常"):
        assert word not in low, word
