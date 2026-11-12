"""Precare agent: identity, consent, intake, prep, red flags, questions, and the doctor's brief. Synthetic only."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from backend.precare import PrecareAgent
from backend.precare import safety
from backend.precare.agent import name_matches, parse_dob
from backend.precare.texts import ASK, CATALOGUE, FIXED, PREP, compose, values_for

T0 = datetime(2026, 11, 16, 9, 0)


class FakePort:
    def __init__(self, visit_type="chronic_review", tests=("fasting_glucose", "lipids"), procedure=None, hours=72):
        self.now = T0
        self.appt = {"id": "A1", "patient_id": "P1", "doctor_id": "D1", "start": (T0 + timedelta(hours=hours)).isoformat(),
                     "visit_type": visit_type, "tests": list(tests), "procedure": procedure,
                     "clinic": {"name_en": "Harbourview Clinic", "name_zh": "海景診所", "phone": "2000 0000"}}
        self.msgs, self.items, self.alarms, self.raised, self.n = [], {}, [], [], 0

    def appointment(self, a): return self.appt
    def patient(self, p): return {"id": "P1", "name_en": "LEUNG Wai Kit", "name_zh": "梁偉傑", "dob": "1961-02-11"}
    def doctor(self, d): return {"id": "D1", "name_en": "Dr Chan", "name_zh": "陳醫生"}
    def messages(self, a): return self.msgs

    def send(self, a, templates, lang, slot=None):
        text = compose(templates, lang, values_for(self.appt, self.doctor("D1"), lang))
        self.n += 1
        self.msgs.append({"id": f"o{self.n}", "dir": "out", "text": text, "slot": slot, "at": self.now})
        return f"o{self.n}"

    def say(self, agent, text):
        self.now += timedelta(minutes=2)
        self.n += 1
        mid = f"i{self.n}"
        self.msgs.append({"id": mid, "dir": "in", "text": text, "at": self.now})
        agent.handle("A1", mid)
        return mid

    def open_item(self, patient_id, owner_id, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"I{len(self.items) + 1}"
        self.items[iid] = dict(patient_id=patient_id, owner=owner_id, family=family, reason=reason, evidence=evidence,
                               urgency=urgency, due=due, reasoning=reasoning or [], escalates=escalates, status="open")
        return iid

    def raise_urgency(self, item_id, urgency, reason=""): self.raised.append((item_id, urgency))
    def alarm(self, channel, reason): self.alarms.append((channel, reason))
    def attest_all_clear(self, scope="all", note=""): raise AssertionError("precare never attests")

    def out(self): return [m for m in self.msgs if m["dir"] == "out"]
    def fam(self, f): return [i for i in self.items.values() if i["family"] == f]


def run(port, *replies):
    ag = PrecareAgent(port)
    ag.start("A1")
    for r in replies:
        port.say(ag, r)
    return ag


HAPPY = ["Leung Wai Kit", "11/02/1961", "yes", "Diabetes review and my blood test", "metformin 500mg twice a day, amlodipine",
         "penicillin gives me a rash", "no", "ok"]


def quotes_verbatim(port):
    texts = {m["id"]: m["text"] for m in port.msgs if m["dir"] == "in"}
    for it in port.items.values():
        for s in it["reasoning"]:
            for c in s.get("cites", []):
                if c.get("quote"):
                    assert c["quote"] in texts[c["record"]], c


def test_happy_path_brief_quotes_the_patient_word_for_word():
    p = FakePort()
    ag = run(p, *HAPPY)
    assert ag.state["A1"]["step"] == "done"
    [brief] = p.fam("previsit_brief")
    assert brief["owner"] == "D1" and brief["due"] == datetime.fromisoformat(p.appt["start"])
    quoted = [c["quote"] for s in brief["reasoning"] for c in s.get("cites", [])]
    assert "Diabetes review and my blood test" in quoted and "penicillin gives me a rash" in quoted
    quotes_verbatim(p)
    prep = p.out()[-2]["text"]
    assert PREP["fasting"]["en"] in prep and PREP["bring_meds"]["en"] in prep
    # metformin + fasting test -> a question for the doctor; the line never says what to do with it
    [q] = p.fam("previsit_question")
    assert "metformin" in q["reasoning"][2]["text"]
    assert not any(re.search(r"(skip|stop|hold|take) (your )?metformin", m["text"], re.I) for m in p.out())


def test_every_outbound_message_is_labelled_and_passes_the_guard():
    p = FakePort()
    ag = run(p, *HAPPY)
    assert not ag.blocked
    for m in p.out():
        assert m["text"].startswith("Care team (automated)")
        assert safety.check_outbound(m["text"], "Care team (automated)") == []


def test_cantonese_flow():
    p = FakePort(visit_type="new_problem", tests=())
    ag = run(p, "梁偉傑", "1961年2月11日", "同意", "咳咗兩個禮拜", "兩個禮拜", "冇", "冇", "冇", "OK")
    assert ag.state["A1"]["step"] == "done" and ag.state["A1"]["lang"] == "zh"
    assert all(m["text"].startswith("護理團隊（自動回覆）") for m in p.out()[1:])
    assert PREP["fasting"]["zh"] not in "\n".join(m["text"] for m in p.out())       # no fasting test booked
    quotes_verbatim(p)


@pytest.mark.parametrize("at", [3, 4, 6, 7])
def test_red_flag_at_any_step_gets_999_an_alarm_and_an_urgent_item(at):
    p = FakePort()
    replies = HAPPY[:at] + ["I've had chest pain since this morning"]
    ag = run(p, *replies)
    assert p.out()[-1]["text"].endswith(FIXED["red_flag"]["en"].format(doctor="Dr Chan"))
    [rf] = p.fam("previsit_red_flag")
    assert rf["urgency"] == 4 and p.alarms
    assert ag.state["A1"]["step"] == "escalated"
    quotes_verbatim(p)
    p.say(ag, "what should I do")                                # no more questions, no reassurance
    assert p.out()[-1]["text"].endswith(FIXED["after_alarm"]["en"])


def test_red_flag_before_identity_is_still_answered():
    p = FakePort()
    run(p, "help my father collapsed and is unconscious")
    assert "999" in p.out()[-1]["text"] and p.alarms and p.fam("previsit_red_flag")


def test_crisis_gets_the_crisis_reply():
    p = FakePort()
    run(p, *HAPPY[:6], "honestly I don't want to live anymore")
    assert "2896 0000" in p.out()[-1]["text"]


def test_wrong_identity_twice_keeps_nothing():
    p = FakePort()
    ag = run(p, "Leung Wai Kit", "12/02/1961", "Leung Wai Kit", "1/1/1970", "Diabetes review")
    assert ag.state["A1"]["step"] == "identity_failed"
    [b] = p.fam("previsit_brief")
    assert b["evidence"] == [] and not any(c for s in b["reasoning"] for c in s.get("cites", []))


def test_declined_consent_keeps_nothing():
    p = FakePort()
    ag = run(p, "Leung Wai Kit", "11/02/1961", "no", "it's my knee")
    assert ag.state["A1"]["step"] == "declined"
    [b] = p.fam("previsit_brief")
    assert b["evidence"] == []


def test_question_is_routed_not_answered():
    p = FakePort()
    run(p, *HAPPY[:6], "Is my cholesterol result from last time normal?")
    assert p.fam("previsit_question")
    assert any(m["text"].endswith(FIXED["question_routed"]["en"].format(doctor="Dr Chan")) for m in p.out())


def test_blood_thinner_before_procedure_goes_to_doctor():
    p = FakePort(visit_type="procedure", tests=(), procedure="removal of a skin lump")
    run(p, "Leung Wai Kit", "11/02/1961", "yes", "lump on my back to remove", "warfarin 3mg", "none", "no", "ok")
    [q] = p.fam("previsit_question")
    assert "warfarin" in q["reasoning"][2]["text"]


def test_no_reply_gets_one_reminder_then_a_brief_before_the_visit():
    p = FakePort(hours=72)
    ag = PrecareAgent(p)
    ag.start("A1")
    for h in range(1, 72):
        p.now = T0 + timedelta(hours=h)
        ag.tick("A1")
    assert sum(FIXED["reminder"]["en"][:20] in m["text"] for m in p.out()) == 1
    [b] = p.fam("previsit_brief")
    assert "no_reply" in b["reason"]


def test_stop_ends_the_line():
    p = FakePort()
    ag = run(p, *HAPPY[:4], "STOP")
    assert ag.state["A1"]["step"] == "stopped" and p.fam("previsit_brief")


def test_parsers():
    assert parse_dob("23/09/1964").isoformat() == "1964-09-23"
    assert parse_dob("1964年9月23日").isoformat() == "1964-09-23"
    assert parse_dob("23 Sep 1964").isoformat() == "1964-09-23"
    assert parse_dob("31/02/1964") is None
    pt = {"name_en": "CHAN Tai-man", "name_zh": "陳大文"}
    assert name_matches("Chan Tai Man", pt) and name_matches("tai man chan", pt) and name_matches("陳大文", pt)
    assert not name_matches("Chan Tai Ming", pt) and not name_matches("Chan", pt)


def test_authority_the_agent_has_no_close_dismiss_or_downgrade():
    for bad in ("close", "dismiss", "downgrade", "reassure", "close_item", "lower_urgency"):
        assert not hasattr(PrecareAgent, bad)
    import inspect
    from backend.precare import agent as mod
    src = inspect.getsource(mod)
    assert not re.search(r"port\.(close|dismiss|downgrade|resolve|lower)", src)


def test_templates_pass_the_guard():
    from backend.precare.texts import LABEL
    vals = {"doctor": "Dr Chan", "when": "Mon", "clinic": "c", "phone": "1"}
    for k in CATALOGUE:
        for lang in ("en", "zh"):
            body = compose([k], lang, vals)
            assert safety.check_outbound(body, LABEL[lang]) == [], (k, lang)


def test_compose_refuses_free_text_and_unknown_values():
    with pytest.raises(KeyError):
        compose(["free text"], "en", {})
    with pytest.raises(KeyError):
        compose(["ask.reason"], "en", {"patient_said": "x"})


def test_after_stop_only_emergency_replies_go():
    p = FakePort()
    ag = run(p, *HAPPY[:4], "STOP")
    n = len(p.out())
    p.say(ag, "thanks")
    ag.tick("A1")
    assert len(p.out()) == n
    p.say(ag, "now I can't breathe")
    assert "999" in p.out()[-1]["text"]


# ---- contract: the conversation layer has the same authority as a journey ------------------------------------------
def test_contract_send_takes_template_ids_and_only_the_four_writes():
    import ast
    import inspect
    from backend.precare import agent as mod
    tree = ast.parse(inspect.getsource(mod))
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "port"}
    assert calls <= {"send", "open_item", "raise_urgency", "alarm", "attest_all_clear", "appointment", "patient",
                     "doctor", "messages", "now"}, calls
    assert "attest_all_clear" not in calls                     # precare never attests
    for n in ast.walk(tree):                                   # every send passes a list of catalogue ids
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "send":
            assert len(n.args) == 4 and isinstance(n.args[1], ast.Name) and n.args[1].id == "ids"
    from backend.precare.port import PrecarePort
    for bad in ("close", "dismiss", "downgrade", "reassure", "close_item", "resolve"):
        assert not hasattr(PrecarePort, bad)


def test_every_id_the_agent_names_is_in_the_catalogue():
    import inspect
    from backend.precare import agent as mod
    src = inspect.getsource(mod)
    named = set(re.findall(r'"((?:ask|fixed|prep)\.[a-z_]+)"', src))
    named |= {f"ask.{s}" for s in ("name", "dob", "consent", "reason", "duration", "meds", "allergies", "anything_else", "prep_ack")}
    named |= {f"prep.{k}" for k in PrecareAgent(FakePort()).prep_keys({"tests": ["lipids"], "visit_type": "procedure", "procedure": "x"})}
    assert named <= set(CATALOGUE), named - set(CATALOGUE)


# ---- screen v3 and agent v2 behaviours found by the LoopBench stress set ------------------------------------------------
@pytest.mark.parametrize("text", ["feels like an elephant is sitting on my chest", "chest pian when walking",
                                  "I can't catch my breath", "坐低都條氣唔順", "my sugar dropped to 2.8 and I was shaking",
                                  "my tongue feels thick and my throat is tight", "心口有啲 tight", "Dad collapsed"])
def test_screen_v3_catches_descriptions_and_typos(text):
    assert safety.screen(text) == "red_flag"


@pytest.mark.parametrize("text", ["sometimes I think everyone would be better off if I was dead", "好想一覺瞓咗唔再醒",
                                  "I've been thinking about ending my life"])
def test_screen_v3_crisis(text):
    assert safety.screen(text) == "crisis"


@pytest.mark.parametrize("text", ["need a chest x-ray for my insurance", "sugar was 6.2 this morning", "my knee is sore"])
def test_screen_stays_quiet(text):
    assert safety.screen(text) is None


def test_consent_and_stop_phrasings():
    from backend.precare.agent import is_yes
    for y in ("Yes please", "ok 👍", "sure, go ahead", "同意呀", "好呀冇問題", "可以 👍"):
        assert is_yes(y), y
    for n in ("no", "not really", "唔同意", "what is this for?", "who will see it"):
        assert not is_yes(n), n
    for s in ("stop.", "Stop please", "please stop messaging me", "唔好再搵我"):
        assert safety.is_stop(s), s
    assert not safety.is_stop("losartan. should I stop it before the visit?")


def test_misspelt_medicine_still_found():
    assert safety.mentions(safety.GLUCOSE_LOWERING, "metphormin 2 times a day")
    assert not safety.mentions(safety.GLUCOSE_LOWERING, "amlodipine")
