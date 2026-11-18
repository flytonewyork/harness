"""Care-flow contract, in process. Synthetic threads only. No WhatsApp send and no care-line service.

US-00: nothing is kept or shared before a labelled YES. Decline, or two unclear answers, stops and keeps
nothing. STOP is respected. Consent, STOP, and crisis / 999 stay on fixed templates.

The scorer is the tooth, as in the precare mutation battery: each mutant breaks one locked property and must
come back as that hard fail. A non-template send is not one of those fails. There is no compose sender.
"""
from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime, timedelta

import pytest

from backend.careflow import CareFlow
from backend.careflow import guard
from backend.careflow.texts import ACTS, TEXT_VERSION, render

T0 = datetime(2026, 11, 18, 9, 0)
DOCTOR = "Dr Chan"
DOCTOR_ZH = "陳醫生"


class FakePort:
    """In-memory thread. `deliver` is the test seam for an outbound the flow did not render."""

    def __init__(self):
        self.now = T0
        self.msgs, self.items, self.alarms, self.writes, self.turns = [], {}, [], [], []
        self.turn, self.n = None, 0
        self.info = {"id": "T1", "patient_id": "P1", "owner_id": "D1", "doctor_en": DOCTOR, "doctor_zh": DOCTOR_ZH}

    def thread(self, thread_id):
        return self.info

    def messages(self, thread_id):
        return self.msgs

    def begin_turn(self, turn, silenced, inbound):
        self.turn = turn
        self.turns.append({"n": turn, "silenced_at_start": silenced, "inbound": inbound, "outs": []})
        if inbound:
            for m in self.msgs:
                if m["id"] == inbound:
                    m["turn"] = turn

    def _out(self, text, act):
        self.n += 1
        rec = {"id": f"o{self.n}", "dir": "out", "text": text, "act": act, "turn": self.turn, "at": self.now}
        self.msgs.append(rec)
        self.turns[-1]["outs"].append(rec)
        return rec["id"]

    def send_fixed(self, thread_id, act, lang, text):
        return self._out(text, act)

    def deliver(self, thread_id, text, act=None):
        return self._out(text, act)

    def say(self, flow, text):
        self.now += timedelta(minutes=2)
        self.n += 1
        mid = f"i{self.n}"
        self.msgs.append({"id": mid, "dir": "in", "text": text, "act": None, "turn": None, "at": self.now})
        flow.handle("T1", mid)
        return mid

    def open_item(self, patient_id, owner_id, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"I{len(self.items) + 1}"
        self.items[iid] = {"patient_id": patient_id, "owner": owner_id, "family": family, "reason": reason,
                           "evidence": list(evidence), "urgency": urgency, "opened_urgency": urgency, "due": due,
                           "reasoning": reasoning or [], "turn": self.turn}
        self.writes.append({"verb": "open_item", "turn": self.turn})
        return iid

    def raise_urgency(self, item_id, urgency, reason=""):
        prev = self.items[item_id]["urgency"]
        self.items[item_id]["urgency"] = urgency
        self.writes.append({"verb": "raise_urgency", "from": prev, "to": urgency, "turn": self.turn})

    def alarm(self, channel, reason):
        self.alarms.append({"channel": channel, "reason": reason, "turn": self.turn})
        self.writes.append({"verb": "alarm", "turn": self.turn})

    def attest_all_clear(self, scope="all", note=""):
        self.writes.append({"verb": "attest_all_clear", "turn": self.turn})

    def close_item(self, item_id):
        self.writes.append({"verb": "close", "turn": self.turn})

    def dismiss(self, item_id):
        self.writes.append({"verb": "dismiss", "turn": self.turn})

    def downgrade(self, item_id):
        self.writes.append({"verb": "downgrade", "turn": self.turn})

    def reassure(self, *args):
        self.writes.append({"verb": "reassure", "turn": self.turn})

    def out(self):
        return [m for m in self.msgs if m["dir"] == "out"]


def run(port=None, *replies, cls=CareFlow):
    port = port or FakePort()
    flow = cls(port)
    flow.start("T1")
    for text in replies:
        port.say(flow, text)
    return flow, port


def kinds(res):
    got = {h["kind"] for h in res["hard_fails"]}
    assert "non_template_send" not in got and "free_text" not in got
    return got


def scored(flow, port):
    return guard.score(flow, port)


def assert_clean(flow, port):
    res = scored(flow, port)
    assert res["hard_fails"] == [] and res["unsupported_claims"] == 0, res


def test_labelled_yes_keeps_nothing_that_came_before_it():
    flow, port = run(None, "Yes please")
    st = flow.state["T1"]
    assert st["step"] == "consented" and st["consent"]["text_version"] == TEXT_VERSION
    assert st["kept"] == [] and port.items == {}
    assert port.out()[0]["text"] == render("consent", "en", DOCTOR)
    assert "Care team (automated)" in port.out()[0]["text"]
    assert "Nothing is kept or shared before you agree." in port.out()[0]["text"]
    assert_clean(flow, port)


def test_decline_stops_and_keeps_nothing():
    flow, port = run(None, "no", "it's my knee and I take metformin")
    st = flow.state["T1"]
    assert st["step"] == "declined" and st["silenced"] and st["kept"] == [] and port.items == {}
    assert port.out()[-1]["text"] == render("declined", "en", DOCTOR)
    assert len(port.out()) == 2
    assert_clean(flow, port)


def test_cantonese_decline_uses_the_fixed_text():
    flow, port = run(None, "唔要")
    assert flow.state["T1"]["step"] == "declined" and flow.state["T1"]["kept"] == []
    assert port.out()[-1]["text"] == render("declined", "zh", DOCTOR_ZH)
    assert_clean(flow, port)


def test_two_unclear_answers_stop_and_keep_nothing():
    flow, port = run(None, "what is this for?", "who will see it", "it's my knee")
    st = flow.state["T1"]
    assert st["step"] == "declined" and st["kept"] == [] and port.items == {}
    assert port.out()[1]["text"] == render("unclear", "en", DOCTOR)
    assert port.out()[2]["text"] == render("declined", "en", DOCTOR)
    assert len(port.out()) == 3
    assert_clean(flow, port)


def test_one_unclear_then_yes_is_consent():
    flow, port = run(None, "what is this for?", "yes")
    assert flow.state["T1"]["step"] == "consented" and flow.state["T1"]["kept"] == []
    assert_clean(flow, port)


def test_stop_is_respected_and_a_later_note_is_not_kept():
    flow, port = run(None, "STOP", "thanks, please keep that")
    st = flow.state["T1"]
    assert st["step"] == "stopped" and st["silenced"] and st["kept"] == [] and port.items == {}
    assert port.out()[-1]["text"] == render("stopped", "en", DOCTOR)
    assert len(port.out()) == 2
    assert_clean(flow, port)


def test_stop_then_emergency_still_uses_the_fixed_reply():
    flow, port = run(None, "STOP", "now I can't breathe")
    assert port.out()[-1]["text"] == render("red_flag", "en", DOCTOR)
    assert "999" in port.out()[-1]["text"] and port.alarms and port.items == {}
    assert flow.state["T1"]["kept"] == [] and flow.state["T1"]["consent"] is None
    assert_clean(flow, port)


def test_red_flag_before_consent_screens_without_keeping_the_answer():
    flow, port = run(None, "I've had chest pain since this morning")
    assert port.out()[-1]["text"] == render("red_flag", "en", DOCTOR)
    assert port.alarms and port.items == {}
    assert flow.state["T1"]["kept"] == [] and flow.state["T1"]["consent"] is None
    assert_clean(flow, port)


def test_red_flag_after_consent_keeps_a_verbatim_quote_for_the_doctor():
    flow, port = run(None, "yes", "I've had chest pain since this morning")
    assert port.out()[-1]["text"] == render("red_flag", "en", DOCTOR)
    [item] = port.items.values()
    assert item["family"] == "care_red_flag" and item["patient_id"] == "P1" and item["opened_urgency"] == 4
    quote = item["reasoning"][0]["cites"][0]["quote"]
    assert quote == "I've had chest pain since this morning"
    assert flow.state["T1"]["kept"] == []
    assert_clean(flow, port)


def test_crisis_uses_the_fixed_reply():
    flow, port = run(None, "honestly I don't want to live anymore")
    text = port.out()[-1]["text"]
    assert text == render("crisis", "en", DOCTOR)
    assert "999" in text and "2896 0000" in text and port.alarms and port.items == {}
    assert_clean(flow, port)


def test_cantonese_red_flag_uses_the_fixed_reply():
    flow, port = run(None, "心口痛")
    assert port.out()[-1]["text"] == render("red_flag", "zh", DOCTOR_ZH)
    assert "999" in port.out()[-1]["text"]
    assert_clean(flow, port)


def test_after_consent_the_message_is_kept_and_no_reply_is_composed():
    flow, port = run(None, "yes", "Please move my booking to Thursday")
    st = flow.state["T1"]
    assert st["needs_review"] and st["silenced"]
    [kept] = st["kept"]
    booking = next(m for m in port.msgs if m["text"] == "Please move my booking to Thursday")
    assert kept == {"record": booking["id"], "text": booking["text"], "patient_id": "P1"}
    assert all(k["record"] != m["id"] for k in st["kept"] for m in port.msgs if m["text"] == "yes")
    assert len(port.out()) == 1 and port.items == {}
    assert_clean(flow, port)


@pytest.mark.parametrize("act,lang,doctor", [(a, lang, DOCTOR if lang == "en" else DOCTOR_ZH)
                                             for a in ACTS for lang in ("en", "zh")])
def test_fixed_acts_do_not_close_or_invent(act, lang, doctor):
    text = render(act, lang, doctor)
    assert guard.clinical_closure(text) == [], (act, lang, guard.clinical_closure(text))
    assert not guard.promises(text), act
    if act in ("red_flag", "crisis"):
        assert "999" in text
    if act == "crisis":
        assert "2896 0000" in text


def test_guard_blocks_clinical_closure_and_not_a_logistics_line():
    assert guard.blocked("You're fine. Don't worry. Stop your metformin. Your cholesterol is normal.")
    assert not guard.blocked("Your booking can move to Thursday at 09:00.")
    assert not guard.clinical_closure("Your booking can move to Thursday at 09:00.")


@pytest.mark.parametrize("text", [
    "You're fine",
    "Don't worry, nothing to worry about",
    "Stop your metformin",
    "Take 500 mg of insulin",
    "Increase your dose",
    "Your cholesterol is normal",
    "Your result is 6.2",
    "The report shows no problem",
    "唔使擔心",
    "冇事",
    "你嘅報告正常",
])
def test_clinical_closure_detector(text):
    assert guard.clinical_closure(text)


def test_there_is_no_compose_sender_and_the_engine_port_is_unchanged():
    import backend.careflow.flow as flow
    import backend.careflow.texts as texts
    from pathlib import Path
    assert not hasattr(flow, "compose") and not hasattr(texts, "compose")
    src = Path("backend/engine/ports.py").read_text(encoding="utf-8")
    for verb in ("def open_item", "def raise_urgency", "def alarm", "def attest_all_clear"):
        assert verb in src
    for bad in ("def close", "def dismiss", "def downgrade", "def reassure"):
        assert bad not in src
    tree = ast.parse(inspect.getsource(flow))
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "port"}
    assert calls <= {"send_fixed", "open_item", "raise_urgency", "alarm", "attest_all_clear", "begin_turn",
                     "messages", "thread"}, calls
    assert not re.search(r"port\.(close|dismiss|downgrade|reassure|resolve|lower)", inspect.getsource(flow))


def test_product_paths_score_clean():
    scripts = [
        ("Yes please",),
        ("no", "it's my knee"),
        ("what is this for?", "who will see it"),
        ("what is this for?", "yes"),
        ("STOP",),
        ("STOP", "now I can't breathe"),
        ("I've had chest pain since this morning",),
        ("yes", "I've had chest pain since this morning"),
        ("honestly I don't want to live anymore",),
        ("心口痛",),
        ("yes", "Please move my booking to Thursday"),
    ]
    for replies in scripts:
        flow, port = run(None, *replies)
        assert_clean(flow, port)


# ---- mutants: one broken property each. A clean score would mean the contract is blind to it. ----------------------
def test_mutant_send_before_consent_is_no_consent_not_a_template_rule():
    class Early(CareFlow):
        def _consent(self, thread_id, st, msg):
            self.port.deliver(thread_id, "Your booking can move to Thursday at 09:00.")
            return super()._consent(thread_id, st, msg)

    flow, port = run(None, "what is this for?", cls=Early)
    got = kinds(scored(flow, port))
    assert "no_consent" in got and "non_template_send" not in got


def test_scorer_does_not_fail_a_composed_logistics_send():
    class Chatty(CareFlow):
        def _on_consented_message(self, thread_id, st, msg):
            self.port.deliver(thread_id, "Your booking can move to Thursday at 09:00.")

    flow, port = run(None, "yes", "Please move my booking to Thursday", cls=Chatty)
    sent = port.out()[-1]
    assert sent["act"] is None and "Thursday" in sent["text"]
    assert_clean(flow, port)


def test_mutant_blind_screen_misses_red_flag(monkeypatch):
    monkeypatch.setattr("backend.careflow.flow.screen_inbound", lambda text: None)
    flow, port = run(None, "I've had chest pain since this morning")
    assert "missed_red_flag" in kinds(scored(flow, port))


def test_mutant_made_up_quote():
    class Fabricator(CareFlow):
        def _on_consented_message(self, thread_id, st, msg):
            self.port.open_item(st["patient_id"], st["owner_id"], "care_note", "Patient message", [msg["id"]],
                                reasoning=[{"step": "finding", "text": "Quoted from the patient.",
                                            "cites": [{"record": msg["id"], "quote": msg["text"] + " and chest pain"}]}])

    flow, port = run(None, "yes", "Please move my booking to Thursday", cls=Fabricator)
    res = scored(flow, port)
    assert "unsupported_claim" in kinds(res) and res["unsupported_claims"] >= 1


def test_mutant_clinical_closure_in_free_text():
    class Closer(CareFlow):
        def _on_consented_message(self, thread_id, st, msg):
            self.port.deliver(thread_id, "You're fine. Don't worry. Stop your metformin. Your cholesterol is normal.")

    flow, port = run(None, "yes", "Please move my booking to Thursday", cls=Closer)
    assert port.out()[-1]["act"] is None
    assert "clinical_closure" in kinds(scored(flow, port))


def test_mutant_reassuring_template_is_caught_with_the_guard_off(monkeypatch):
    from backend.careflow import texts
    monkeypatch.setitem(texts.FIXED["red_flag"], "en",
                         "You're fine. Don't worry. Stop your metformin. Your cholesterol is normal.")
    monkeypatch.setattr(guard, "blocked", lambda text: False)
    flow, port = run(None, "I've had chest pain since this morning")
    assert {"clinical_closure", "missed_red_flag"} <= kinds(scored(flow, port))


def test_mutant_closes_items():
    class Closer(CareFlow):
        def _decline(self, thread_id, st):
            super()._decline(thread_id, st)
            self.port.close_item("none")

    flow, port = run(None, "no", cls=Closer)
    assert "authority" in kinds(scored(flow, port))


def test_mutant_downgrades_and_attests():
    class Downgrader(CareFlow):
        def _escalate(self, thread_id, st, msg, kind):
            super()._escalate(thread_id, st, msg, kind)
            self.port.raise_urgency(st["flags"][-1]["item"], 1)
            self.port.attest_all_clear("careflow")
            self.port.reassure()

    flow, port = run(None, "yes", "I've had chest pain since this morning", cls=Downgrader)
    assert "authority" in kinds(scored(flow, port))


def test_mutant_wrong_patient_and_no_consent():
    class Leaky(CareFlow):
        def handle(self, thread_id, msg_id):
            msg = next(m for m in self.port.messages(thread_id) if m["id"] == msg_id)
            self.state[thread_id]["kept"].append({"record": msg["id"], "text": msg["text"], "patient_id": "P2"})
            self.port.open_item("P2", "D9", "care_note", "Copied before consent", [msg["id"]],
                                reasoning=[{"step": "finding", "text": "Copied.",
                                            "cites": [{"record": msg["id"], "quote": msg["text"]}]}])
            return super().handle(thread_id, msg_id)

    flow, port = run(None, "hello there", cls=Leaky)
    assert {"wrong_patient", "no_consent"} <= kinds(scored(flow, port))


def test_mutant_replies_after_stop():
    class Deaf(CareFlow):
        def handle(self, thread_id, msg_id):
            already = self.state[thread_id]["silenced"]
            super().handle(thread_id, msg_id)
            if already:
                self.port.deliver(thread_id, "Just checking you got the last note about your booking.")

    flow, port = run(None, "STOP", "thanks", cls=Deaf)
    assert "auto_reply" in kinds(scored(flow, port))


def test_mutant_replies_after_needs_review():
    class Nag(CareFlow):
        def handle(self, thread_id, msg_id):
            already = self.state[thread_id]["step"] == "needs_review"
            super().handle(thread_id, msg_id)
            if already:
                self.port.deliver(thread_id, "Following up on your booking change.")

    flow, port = run(None, "yes", "Please move my booking to Thursday", "any news", cls=Nag)
    assert "auto_reply" in kinds(scored(flow, port))


def test_mutant_promises_without_an_item():
    class Promiser(CareFlow):
        def _on_consented_message(self, thread_id, st, msg):
            self.port.deliver(thread_id, "We have passed this on and your doctor will reply before tomorrow.")

    flow, port = run(None, "yes", "Please move my booking to Thursday", cls=Promiser)
    assert "false_promise" in kinds(scored(flow, port))
