"""Urgency screening by the shared reader: it can only raise a care-line reply, with the patient's own words."""
import pytest

from backend.extract.reader import FakeReader, doc_key
from backend.extract.triage import TASK, screen
from backend.journeys import suite


def test_the_schema_cannot_say_a_message_is_fine():
    props = set(TASK.schema["properties"])
    assert props == {"red_flag", "red_flag_quote", "worse", "worse_quote"}
    assert not any(w in TASK.instructions.lower() for w in ("reassur", "downgrade", "dismiss"))


@pytest.mark.parametrize("label,ans,out", [
    ("better", {"red_flag": True, "red_flag_quote": "lips blue"}, ("red_flag", "lips blue", True)),
    ("unclear", {"worse": True, "worse_quote": "still bad"}, ("worse", "still bad", True)),
    ("red_flag", {"red_flag": False, "worse": True, "worse_quote": "x y"}, ("red_flag", "hit", False)),   # never lowered
    ("worse", {"red_flag": False, "worse": False}, ("worse", "hit", False)),
    ("better", {"red_flag": True, "red_flag_quote": None}, ("better", "hit", False)),                     # no words, no raise
    ("better", None, ("better", "hit", False)),
])
def test_screen_is_union_only(label, ans, out):
    assert screen(label, "hit", ans) == out


def _engine(answers):
    eng = suite("followup")
    eng.reader = FakeReader({doc_key(TASK, t): a for t, a in answers.items()}, name="fake")
    eng.fu_stats = {}
    return eng


def test_followup_takes_a_verified_red_flag_from_the_model():
    text = "我老公話我面色好青，講嘢都唔清楚"
    eng = _engine({text: {"red_flag": True, "red_flag_quote": "講嘢都唔清楚", "worse": False, "worse_quote": None}})
    label, hit, who = eng.fu_read(text, reply=True)
    assert (label, hit) == ("red_flag", "講嘢都唔清楚") and who == "model fake"


def test_followup_drops_an_invented_quote_and_keeps_its_own_reading():
    text = "好返好多喇，多謝醫生！"
    eng = _engine({text: {"red_flag": True, "red_flag_quote": "透唔到氣", "worse": False, "worse_quote": None}})
    label, _, who = eng.fu_read(text, reply=True)
    assert label == "better" and eng.fu_stats["model_fields_dropped"] == 1 and who == "followup-keyword-v1 + model fake"


def test_followup_never_lowers_and_says_when_it_fell_back():
    text = "成晚透唔到氣，心口好翳好痛。"
    eng = _engine({text: {"red_flag": False, "red_flag_quote": None, "worse": False, "worse_quote": None}})
    assert eng.fu_read(text, reply=True)[0] == "red_flag"
    label, _, who = eng.fu_read("no answer for this one", reply=True)
    assert label == "unclear" and "fell back" in who and eng.fu_stats["fell_back"] == 1


def test_without_a_reader_nothing_changes():
    eng = suite("followup")
    assert eng.fu_read("好返好多喇", reply=True) == ("better", None, "followup-keyword-v1")
