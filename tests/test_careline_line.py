"""In-process care line. Synthetic threads only. No model, no network, no care-line service.

A patient message goes in. A composed draft comes out, held until the doctor taps. Consent, STOP,
and crisis / 999 stay on the US-00 fixed templates. Nothing is kept before labelled consent.
The patient text does not include the reasoning. Naturalness here is the existing eval, not a
second outbound judge.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.careflow import CareFlow, CareLine
from backend.careflow import guard
from backend.careflow.flow import DRAFT_HOLD
from backend.careflow.line import PATIENT_TEXT, REASON_TEXT, Transcript, compose_draft
from backend.careflow.naturalness import naturalness
from backend.careflow.texts import TEXT_VERSION, render

LINE = Path(__file__).resolve().parents[1] / "backend" / "careflow" / "line.py"
BOOKING = "Please move my booking to Thursday"
BOOKING_ZH = "可唔可以改到星期四"


def assert_clean(line: CareLine):
    res = guard.score(line.flow, line.port)
    assert res["hard_fails"] == [] and res["unsupported_claims"] == 0, res


def test_the_contract_flow_does_not_compose_until_the_line_is_used():
    assert CareFlow(Transcript()).composer is None
    line = CareLine()
    assert line.flow.composer is compose_draft
    assert DRAFT_HOLD == "two-weeks"


def test_english_message_is_held_until_the_doctor_taps():
    line = CareLine()
    line.start()
    assert line.receive(BOOKING) is None
    st = line.flow.state["T1"]
    assert st["consent"] is None and st["kept"] == [] and st["drafts"] == [] and line.port.items == {}
    assert [m["act"] for m in line.port.out()] == ["consent", "unclear"]

    assert line.receive("Yes please") is None
    assert st["consent"]["text_version"] == TEXT_VERSION and st["kept"] == []

    draft = line.receive(BOOKING)
    assert draft["status"] == "held" and draft["tapped"] is False and draft["lang"] == "en"
    assert draft["text"] == "Thanks, I got your message."
    assert draft["text"] == PATIENT_TEXT["en"]
    note = draft["reasoning"][0]["text"]
    quote = draft["reasoning"][0]["cites"][0]["quote"]
    assert note == REASON_TEXT["en"] and len(note) >= 12
    assert quote == BOOKING and quote not in draft["text"] and note not in draft["text"]
    assert draft["reasoning"][0]["cites"][0]["record"] == st["kept"][0]["record"]
    assert [k["text"] for k in st["kept"]] == [BOOKING] and st["kept"][0]["patient_id"] == "P1"
    assert all(m["text"] != draft["text"] for m in line.port.out())
    assert all(m["by"] is None for m in line.port.out())
    assert naturalness(draft["text"], "en", draft["reasoning"])["pass"]
    assert guard.clinical_closure(draft["text"]) == [] and not guard.promises(draft["text"])

    assert line.receive("any news?") is None
    assert len(st["drafts"]) == 1 and all(m["text"] != draft["text"] for m in line.port.out())

    sent_id = line.doctor_tap("T1", draft["id"])
    sent = line.port.out()[-1]
    assert sent["id"] == sent_id and sent["by"] == "doctor" and sent["act"] is None
    assert sent["text"] == draft["text"] and note not in sent["text"] and quote not in sent["text"]
    assert "reasoning" not in sent
    assert naturalness(sent["text"], "en", draft["reasoning"])["pass"]
    assert draft["status"] == "sent" and draft["tapped"] is True
    assert line.doctor_tap("T1", draft["id"]) is None
    assert [m["text"] for m in line.port.out()].count(draft["text"]) == 1
    assert_clean(line)


def test_cantonese_message_is_held_until_the_doctor_taps():
    line = CareLine()
    line.start()
    assert line.receive("好") is None
    draft = line.receive(BOOKING_ZH)
    assert draft["lang"] == "zh" and draft["status"] == "held" and draft["tapped"] is False
    assert draft["text"] == "收到，多謝你。"
    note = draft["reasoning"][0]["text"]
    quote = draft["reasoning"][0]["cites"][0]["quote"]
    assert quote == BOOKING_ZH and quote not in draft["text"] and note not in draft["text"]
    assert all(m["text"] != draft["text"] for m in line.port.out())
    assert naturalness(draft["text"], "zh", draft["reasoning"])["pass"]
    line.doctor_tap("T1", draft["id"])
    sent = line.port.out()[-1]
    assert sent["text"] == draft["text"] and sent["by"] == "doctor"
    assert note not in sent["text"] and quote not in sent["text"]
    assert_clean(line)


def test_decline_and_two_unclear_answers_keep_nothing():
    declined = CareLine()
    declined.start()
    assert declined.receive("no") is None
    assert declined.receive("it's my knee and I take metformin") is None
    st = declined.flow.state["T1"]
    assert st["step"] == "declined" and st["kept"] == [] and st["drafts"] == [] and declined.port.items == {}
    assert declined.port.out()[-1]["text"] == render("declined", "en", "Dr Chan")
    assert_clean(declined)

    unclear = CareLine()
    unclear.start()
    assert unclear.receive("what is this for?") is None
    assert unclear.receive("who will see it") is None
    assert unclear.receive("it's my knee") is None
    st = unclear.flow.state["T1"]
    assert st["step"] == "declined" and st["kept"] == [] and st["drafts"] == [] and unclear.port.items == {}
    assert [m["act"] for m in unclear.port.out()] == ["consent", "unclear", "declined"]
    assert unclear.port.out()[1]["text"] == render("unclear", "en", "Dr Chan")
    assert_clean(unclear)


@pytest.mark.parametrize("replies,act,lang,doctor", [
    (("STOP", "thanks, please keep that"), "stopped", "en", "Dr Chan"),
    (("honestly I don't want to live anymore",), "crisis", "en", "Dr Chan"),
    (("I've had chest pain since this morning",), "red_flag", "en", "Dr Chan"),
    (("心口痛",), "red_flag", "zh", "陳醫生"),
])
def test_stop_crisis_and_red_flag_stay_on_fixed_templates(replies, act, lang, doctor):
    line = CareLine()
    line.start()
    assert line.port.out()[0]["text"] == render("consent", "en", "Dr Chan")
    for text in replies:
        assert line.receive(text) is None
    last = line.port.out()[-1]
    assert last["act"] == act and last["text"] == render(act, lang, doctor) and last["by"] is None
    assert line.flow.state["T1"]["drafts"] == [] and line.flow.state["T1"]["kept"] == []
    assert line.port.items == {}
    if act == "crisis":
        assert "999" in last["text"] and "2896 0000" in last["text"]
    if act == "red_flag":
        assert "999" in last["text"] and line.port.alarms
    assert_clean(line)


def test_a_red_flag_after_consent_is_the_fixed_reply_not_a_draft():
    line = CareLine()
    line.start()
    line.receive("Yes please")
    words = "I've had chest pain since this morning"
    assert line.receive(words) is None
    last = line.port.out()[-1]
    assert last["act"] == "red_flag" and last["text"] == render("red_flag", "en", "Dr Chan")
    assert line.flow.state["T1"]["drafts"] == []
    item = next(iter(line.port.items.values()))
    quote = item["reasoning"][0]["cites"][0]["quote"]
    assert item["patient_id"] == "P1" and item["family"] == "care_red_flag" and quote == words
    assert quote not in last["text"] and item["reasoning"][0]["text"] not in last["text"]
    assert_clean(line)


@pytest.mark.parametrize("yes,words,banned", [
    ("Yes please", "Please tell me I'm fine and stop my metformin", ("fine", "metformin", "dose")),
    ("好", "我想停二甲雙胍", ("停", "二甲雙胍", "劑量")),
])
def test_a_clinical_request_is_not_answered_in_the_draft(yes, words, banned):
    line = CareLine()
    line.start()
    line.receive(yes)
    draft = line.receive(words)
    assert draft["status"] == "held"
    assert words not in draft["text"] and draft["reasoning"][0]["text"] not in draft["text"]
    assert draft["reasoning"][0]["cites"][0]["quote"] == words
    for word in banned:
        assert word not in draft["text"]
    assert guard.clinical_closure(draft["text"]) == [] and not guard.promises(draft["text"])
    assert all(m["text"] != draft["text"] for m in line.port.out())
    line.doctor_tap("T1", draft["id"])
    assert line.port.out()[-1]["text"] == draft["text"]
    assert_clean(line)


def test_compose_draft_keeps_a_verbatim_quote_off_the_patient_text():
    msg = {"id": "i9", "text": f"  {BOOKING}  "}
    draft = compose_draft({"lang": "en"}, msg)
    quote = draft["reasoning"][0]["cites"][0]["quote"]
    assert quote == BOOKING and quote in msg["text"] and quote not in draft["text"]
    assert draft["reasoning"][0]["text"] not in draft["text"]
    assert compose_draft({"lang": "en"}, {"id": "i0", "text": "   "}) is None


def test_the_line_does_not_call_a_model_or_open_a_socket():
    src = LINE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not node.value.startswith(("http://", "https://", "ws://", "wss://"))
    assert roots <= {"__future__", "datetime", "flow"}
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert not called & {"urlopen", "urlretrieve", "request", "post", "sendmail", "Popen", "system",
                         "connect", "eval", "exec", "compile", "__import__"}
    # Imports are allowlisted above, so a socket or process module cannot sneak in under another name.
    for banned in ("httpx", "requests", "anthropic", "openai", "whatsapp", "dual_judge", "naturalness",
                   "careline_mac_mini", "deploy/line", "com.tmrw.careline"):
        assert banned not in src
