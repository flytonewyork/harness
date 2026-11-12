"""Medication journey: the reader, the suggested replies and the agent loop, on a fake clinic (no LoopBench)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from backend.engine import RulesHarness
from backend.journeys.medication import FAMILIES, MedicationJourney, ModelReader, RulesReader
from backend.journeys.medication import formulary as fx
from backend.journeys.medication import templates as tpl

T0 = datetime(2027, 3, 1, 8, 30)


class FakeClinic:
    """A clinic port with one doctor and a few patients; records released by time."""
    ServiceUnavailable = ConnectionError

    def __init__(self):
        self.now = T0
        self.recs: list[dict] = []
        self._items: dict[str, dict] = {}
        self.raised: list = []
        self.alarms: list = []
        self.n = 0

    def add(self, pid, kind, channel, at, text, author=None, data=None):
        self.n += 1
        r = {"id": f"R{self.n}", "kind": kind, "channel": channel, "available_at": at.isoformat(timespec="minutes"),
             "patient_id": pid, "author": author, "text": text, "data": data or {}}
        self.recs.append(r)
        return r["id"]

    def visible(self):
        return sorted((r for r in self.recs if datetime.fromisoformat(r["available_at"]) <= self.now),
                      key=lambda r: r["available_at"])

    # reading
    def clinic(self):
        return {"labs": [{"id": "pathlab", "name": "PathLab", "portal": False}]}

    def staff(self):
        return [{"id": "dr_wong", "name": "Dr WONG Ka Yan", "role": "doctor"}]

    def roster(self, d):
        return {"date": d.isoformat(), "open": d.weekday() != 6, "closed_reason": "Sunday" if d.weekday() == 6 else "",
                "doctors": ["dr_wong"] if d.weekday() != 6 else []}

    def patients(self):
        return [{"id": p, "name_en": n, "registered_doctor": "dr_wong", "dob": "1960-01-01", "ehealth_consent": False}
                for p, n in (("p1", "LAU Mei Ling"), ("p2", "CHEUNG Kwok Wai"), ("p3", "HO Siu Ming"))]

    def find_patients(self, name=None, dob=None, hkid=None):
        return []

    def cms_since(self, cur):
        vis = self.visible()
        return vis[cur:], len(vis)

    def portal_since(self, src, cur):
        return [], 0

    def ehealth(self, pid):
        raise PermissionError("no consent")

    def chart(self, pid):
        return [r for r in self.visible() if r["patient_id"] == pid]

    def items(self, status=None):
        return [dict(v) for v in self._items.values() if status is None or v["status"] == status]

    # writing: open or raise only
    def open_item(self, pid, owner, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"Q{len(self._items)}"
        self._items[iid] = {"id": iid, "patient_id": pid, "owner_id": owner, "family": family, "reason": reason,
                            "evidence": list(evidence), "urgency": urgency, "status": "open", "reasoning": reasoning or [],
                            "opened_at": self.now.isoformat(), "escalates": escalates}
        return iid

    def raise_urgency(self, iid, u, reason=""):
        assert u >= self._items[iid]["urgency"], "the engine must never lower urgency"
        self._items[iid]["urgency"] = u
        self.raised.append((iid, u, reason))

    def alarm(self, ch, reason):
        self.alarms.append((ch, reason))

    def attest_all_clear(self, scope="all", note=""):
        pass


class Journey(MedicationJourney, RulesHarness):
    def __init__(self, **kw):
        super().__init__(version="1.1", **kw)


def run(clinic, days=3, harness=None):
    h = harness or Journey()
    h.reset(clinic)
    for d in range(days):
        for hh, mm in ((8, 30), (13, 30), (19, 30)):
            clinic.now = datetime.combine(T0.date() + timedelta(days=d), datetime.min.time()).replace(hour=hh, minute=mm)
            h.tick(clinic)
    return h


def mine(clinic, family=None):
    return [i for i in clinic._items.values() if i["family"] in FAMILIES and (family is None or i["family"] == family)]


def quotes_are_verbatim(clinic):
    by_id = {r["id"]: r for r in clinic.recs}
    for it in clinic._items.values():
        for s in it["reasoning"]:
            for c in s["cites"]:
                if c.get("quote"):
                    assert " ".join(c["quote"].split()) in " ".join(by_id[c["record"]]["text"].split()), c


def start_amlodipine(c, pid="p1", day=0):
    t = T0 - timedelta(days=10 - day)
    c.add(pid, "consult_note", "cms", t, "S: BP 162/98 on home readings.\nP: Start amlodipine 5mg daily. RV 4/52.", "dr_wong")
    c.add(pid, "dispensing", "cms", t + timedelta(minutes=12), "Dispensed: Amlodipine 5 MG Oral Tablet x28 days. Label printed (Chinese/English).", "dr_wong")
    return t


# ----------------------------------------------------------------------------------------- reader

@pytest.mark.parametrize("text,classes,label,urgency", [
    ("My lips are swollen since the new pill", {"acei"}, "swelling of lips, tongue, face or throat", 4),
    ("仲差咗，嘴唇好腫，唞唔到氣", set(), "swelling of lips, tongue, face or throat", 4),
    ("食咗新嘅膽固醇藥之後，大髀好痛，小便好深色", {"statin"}, "dark urine", 3),
    ("出咗好多紅疹，好痕", {"xanthine_oxidase"}, "rash", 3),
    ("I have a rash", set(), "rash", 2),
    ("I fell this morning, a bit dizzy", {"ccb"}, "fall", 3),
    ("手震出冷汗，度咗血糖3.1", {"sulfonylurea"}, "low sugar symptoms", 3),
    ("black poo since yesterday", {"anticoagulant"}, "vomiting blood or black stools", 4),
    ("I don't want to be here any more", set(), "thoughts of self-harm", 4),
])
def test_reader_finds_reactions_with_class_urgency(text, classes, label, urgency):
    fs = RulesReader().read(text, classes)
    assert fs and fs[0]["label"] == label and fs[0]["urgency"] == urgency
    assert all(f["quote"] in text for f in fs)


@pytest.mark.parametrize("text", [
    "No rash, no swelling, all good thanks", "冇出疹，冇頭暈，食咗藥好返好多", "ok thanks", "My husband got a rash from it",
    "I had a rash with penicillin years ago", "多謝醫生",
])
def test_reader_stays_quiet_on_negated_third_party_or_past(text):
    assert RulesReader().read(text, {"statin", "ccb"}) == []


def test_third_party_emergency_still_reaches_the_doctor_today():
    fs = RulesReader().read("My mother's lips are swollen after her new tablet", set())
    assert fs and fs[0]["urgency"] == 3 and fs[0]["note"] == "about someone else"


def test_reader_kinds():
    r = RulesReader()
    assert r.read("Can I take two tablets if my BP is still up?")[0]["kind"] == "dose_question"
    assert r.read("I am still taking the old one and the new one, is that right?")[0]["kind"] == "confusion"
    assert r.read("Please repeat my amlodipine, running out")[0]["kind"] == "repeat"
    assert r.read("我停咗食個新藥，因為成日去廁所")[0]["kind"] == "adherence"


class Liar:
    """A model gone wrong: invents a quote, tries to lower urgency, and returns junk."""
    name = "liar"

    def run(self, text, ctx):
        return [{"kind": "side_effect", "label": "rash", "urgency": 1, "quote": "rash"},          # tries to lower the floor
                {"kind": "red_flag", "label": "anaphylaxis", "urgency": 4, "quote": "my throat is closing"},  # made up
                {"kind": "close_item", "label": "all fine", "urgency": 1, "quote": text[:5]}]       # not a kind it may use


def test_model_reader_cannot_lower_invent_or_act():
    m = ModelReader(Liar())
    fs = m.read("I have a rash on my arms", {"xanthine_oxidase"})
    assert [(f["label"], f["urgency"]) for f in fs] == [("rash", 3)]
    assert m.stats["dropped_unverified"] == 1 and m.stats["dropped_bad"] == 1


class Helper:
    name = "helper"

    def run(self, text, ctx):
        return [{"kind": "side_effect", "label": "rash", "urgency": 4, "quote": "rash"},
                {"kind": "side_effect", "label": "taste change", "urgency": 1, "quote": "metal taste"}]


def test_model_reader_can_add_and_raise():
    fs = ModelReader(Helper()).read("a rash and a metal taste", set())
    assert {(f["label"], f["urgency"]) for f in fs} == {("rash", 4), ("taste change", 1)}


def test_model_errors_fall_back_to_the_floor():
    class Broken:
        name = "broken"

        def run(self, text, ctx):
            raise TimeoutError
    m = ModelReader(Broken())
    assert m.read("I have a rash")[0]["label"] == "rash" and m.stats["errors"] == 1


# ---------------------------------------------------------------------------------------- templates

def test_templates_never_reassure_dose_or_instruct():
    for tid, langs in tpl.TEMPLATES.items():
        assert set(langs) == {"en", "zh"}, tid
        for lang, text in langs.items():
            low = text.lower()
            for w in tpl.FORBIDDEN:
                assert w.lower() not in low, (tid, lang, w)
        if tid != "repeat_needs_visit":
            assert "999" in langs["en"] and "999" in langs["zh"], tid


# ------------------------------------------------------------------------------------------ the agent

def test_side_effect_after_a_start_opens_med_change_with_quotes_and_context():
    c = FakeClinic()
    start_amlodipine(c)
    msg = c.add("p1", "message", "careline", T0 + timedelta(hours=2), "Dr Wong, my ankles are swollen since the new BP pill, is that ok?")
    run(c, 1)
    [it] = mine(c)
    assert it["family"] == "med_change" and it["urgency"] == 1 and it["owner_id"] == "dr_wong"
    assert msg in it["evidence"] and "amlodipine" in it["reason"]
    steps = {s["step"] for s in it["reasoning"]}
    assert {"source", "identity", "finding", "context", "assessment", "routing", "request"} <= steps
    req = next(s for s in it["reasoning"] if s["step"] == "request")["text"]
    assert "999" in req and "fixed template" in req
    quotes_are_verbatim(c)


def test_a_new_emergency_opens_a_linked_copy_with_its_own_quotes():
    c = FakeClinic()
    t = start_amlodipine(c)
    c.add("p1", "consult_note", "cms", t + timedelta(days=1), "P: Amlodipine switched to lisinopril 10mg daily.", "dr_wong")
    c.add("p1", "message", "careline", T0 + timedelta(hours=2), "有少少乾咳")
    c.add("p1", "message", "careline", T0 + timedelta(days=1, hours=2), "今朝嘴唇好腫，有啲唞唔到氣")
    c.add("p1", "message", "careline", T0 + timedelta(days=2, hours=2), "仲係好腫，有啲頭暈")
    run(c, 3)
    first, copy = mine(c)
    assert first["urgency"] == 1 and copy["urgency"] == 4 and copy["escalates"] == first["id"]
    assert any("嘴唇好腫" in (q.get("quote") or "") for s in copy["reasoning"] for q in s["cites"])
    quotes_are_verbatim(c)                                     # and the third message neither duplicates nor lowers


def test_fine_is_routine_thanks_and_handled_are_quiet():
    c = FakeClinic()
    start_amlodipine(c)
    c.add("p1", "message", "careline", T0 + timedelta(hours=1), "冇頭暈，冇腳腫，血壓好返好多，多謝醫生")
    c.add("p2", "message", "careline", T0 + timedelta(hours=1), "I have a rash since the new tablet")
    c.add("p2", "consult_note", "cms", T0 + timedelta(hours=3), "Phoned pt re rash: mild, photo reviewed, plan discussed.", "dr_wong")
    c.add("p3", "consult_note", "cms", T0 - timedelta(days=3), "P: Start omeprazole 20mg daily.", "dr_wong")
    c.add("p3", "message", "careline", T0 + timedelta(hours=1), "收到，多謝醫生 🙏")
    run(c, 1)
    [it] = mine(c)                    # only a doctor decides she is fine: a routine item; thanks and handled: nothing
    assert it["patient_id"] == "p1" and it["urgency"] == 1 and "nothing flagged" in it["reason"]


def test_repeat_request_flags_early_stopped_controlled_and_monitoring():
    c = FakeClinic()
    t = T0 - timedelta(days=10)
    c.add("p2", "dispensing", "cms", t, "Dispensed: Metformin 500 MG Oral Tablet x28 days.", "dr_wong")
    c.add("p2", "dispensing", "cms", t, "Dispensed: Zolpidem 10 MG Oral Tablet x14 days.", "dr_wong")
    c.add("p2", "dispensing", "cms", T0 - timedelta(days=200), "Dispensed: Atorvastatin 20 MG Oral Tablet x28 days.", "dr_wong")
    c.add("p2", "consult_note", "cms", T0 - timedelta(days=30), "A: myalgia. P: Stop atorvastatin. Recheck CK.", "dr_wong")
    c.add("p2", "message", "careline", T0 + timedelta(hours=1), "Please repeat my metformin, zolpidem and atorvastatin")
    run(c, 1)
    [it] = mine(c, "repeat_prescription")
    for f in ("metformin: early", "metformin: monitoring due", "zolpidem: controlled", "atorvastatin: stopped"):
        assert f in it["reason"], f
    assert it["urgency"] == 2
    assert not any(w in it["reason"].lower() for w in ("approved", "approve "))
    quotes_are_verbatim(c)


def test_running_out_of_a_no_gap_medicine_is_same_day_and_supply_end_raises():
    c = FakeClinic()
    c.add("p3", "dispensing", "cms", T0 - timedelta(days=27), "Dispensed: Warfarin 3 MG Oral Tablet x28 days.", "dr_wong")
    c.add("p3", "lab_report", "courier", T0 - timedelta(days=20), "LABORATORY REPORT\nINR: 2.4", None)
    c.add("p3", "message", "careline", T0 + timedelta(hours=1), "我嘅薄血丸食晒喇，可唔可以配返？")
    c.add("p1", "dispensing", "cms", T0 - timedelta(days=26), "Dispensed: Amlodipine 5 MG Oral Tablet x28 days.", "dr_wong")
    c.add("p1", "message", "careline", T0 + timedelta(hours=1), "Can I get a repeat of my amlodipine please")
    run(c, 3)
    w = next(i for i in mine(c) if i["patient_id"] == "p3")
    a = next(i for i in mine(c) if i["patient_id"] == "p1")
    assert w["family"] == "repeat_prescription" and w["urgency"] == 3 and "warfarin: run out" in w["reason"]
    assert a["urgency"] == 3 and any(i == a["id"] and "supply ends" in r for i, u, r in c.raised)


def test_stopped_medicine_dispensed_again_is_reconciled():
    c = FakeClinic()
    c.add("p3", "discharge_summary", "ehealth", T0 - timedelta(days=5),
          "DISCHARGE SUMMARY\nDiagnosis: upper GI bleed.\nMedication changes: Aspirin stopped. Omeprazole 40mg daily started.", "HA")
    c.add("p3", "dispensing", "cms", T0 + timedelta(hours=1), "Dispensed: Aspirin 80 MG Oral Tablet x28 days.", "dr_wong")
    run(c, 1)
    [it] = mine(c)
    assert it["family"] == "med_change" and "Stopped medicine dispensed again" in it["reason"] and it["urgency"] == 3
    quotes_are_verbatim(c)


def test_claims_only_its_own_messages():
    c = FakeClinic()
    h = Journey()
    h.reset(c)
    med = {"kind": "message", "channel": "careline", "patient_id": "p1", "text": "血壓藥食咗之後好頭暈", "available_at": T0.isoformat()}
    report = {"kind": "message", "channel": "careline", "patient_id": "p1", "text": "公司身體檢查報告話我膽固醇高", "available_at": T0.isoformat()}
    assert h.claims(c, med) and not h.claims(c, report)


def test_journey_writes_are_open_or_raise_only():
    src = "\n".join(p.read_text() for p in Path("backend/journeys/medication").glob("*.py"))
    calls = set(re.findall(r"api\.(\w+)\(", src))
    assert calls <= {"items", "raise_urgency", "open_item", "chart", "alarm"}, calls
    assert not re.search(r"\.(close|dismiss|downgrade|reassure|approve|prescribe|send)\w*\(", src)
    assert "loopbench" not in src


def test_formulary_basics():
    assert fx.drug_class("Lisinopril 10 MG Oral Tablet") == "acei"
    assert fx.generic("薄血丸") == "warfarin" and fx.monitoring_for("apixaban") == fx.DOAC_RENAL
    assert "statin" in fx.purposes("my cholesterol pills")


def test_safety_net_after_a_high_risk_change():
    c = FakeClinic()
    t = T0 - timedelta(days=5)
    c.add("p2", "consult_note", "cms", t, "A: AF. P: Start warfarin 3mg daily. INR in 5 days.", "dr_wong")
    c.add("p2", "message", "careline", T0 + timedelta(hours=1), "Something isn't right with me since Tuesday")
    c.add("p2", "message", "careline", T0 + timedelta(hours=2), "OK thanks doctor 🙏")
    c.add("p1", "consult_note", "cms", t, "P: Start amlodipine 5mg daily.", "dr_wong")
    c.add("p1", "message", "careline", T0 + timedelta(hours=1), "Something isn't right with me since Tuesday")
    run(c, 1)
    by = {i["patient_id"]: i for i in mine(c)}      # warfarin is high risk (2); amlodipine is not (1); thanks: nothing
    assert len(mine(c)) == 2 and by["p2"]["urgency"] == 2 and "please read" in by["p2"]["reason"]
    assert by["p1"]["urgency"] == 1
    quotes_are_verbatim(c)


@pytest.mark.parametrize("probe,allowed_misses", [("medication_probe.yaml", 0), ("medication_probe_2.yaml", 0),
                                                  ("medication_probe_3.yaml", 1)])
def test_probe_regression(probe, allowed_misses):
    """The probes were scored untuned first (first_pass in each file); this only guards against regressions.
    probe 3's one allowed miss is by design: someone else's emergency ('my colleague fainted') goes to the doctor."""
    from backend.journeys.medication.probe import score
    out = score(path=Path("evals") / probe)
    assert out["emergencies_caught"] == out["emergencies"]
    assert out["cases"] - out["correct"] <= allowed_misses, out["misses"]


def test_replay_reader_never_calls_a_model_and_counts_misses(tmp_path):
    from backend.journeys.medication.models import ReplayReader, _Cache, _key
    cache = tmp_path / "c.json"
    _Cache(cache).put(_key("m", "嘴唇腫", []), [{"kind": "red_flag", "label": "lip swelling", "urgency": 4, "quote": "嘴唇腫"}])
    rr = ReplayReader("m", cache)
    reader = ModelReader(rr)
    assert reader.read("嘴唇腫")[0]["urgency"] == 4
    assert reader.read("I have a rash")[0]["via"] == "rules" and rr.misses == 1      # a miss: the floor's name
