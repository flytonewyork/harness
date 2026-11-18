"""Swappable care-line draft composer. Synthetic threads only. The model is mocked.

Grok is the default for a user-flow try. Sonnet 5.5 is named and not selected unless
asked for. A successful answer is cached by prompt version, model, and input hash.
A failed call is not stored and not scored. Consent, STOP, and crisis / 999 stay on
the fixed templates and do not reach the model.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from backend.careflow import GROK_MODEL, SONNET_MODEL, CareLine, DraftComposer
from backend.careflow.flow import QUOTE_MAX, CareFlow
from backend.careflow.models import (REASON, DraftCallError, cache_key, input_hash, open_reader, prompt_version,
                                     resolve, task_for)
from backend.careflow.naturalness import naturalness
from backend.extract.cursor_reader import CursorAgentReader
from backend.extract.reader import ClaudeCLIReader, pin
from tests.test_careline_contract import FakePort, assert_clean, kinds, scored

BOOKING = "Please move my booking to Thursday"
BOOKING_ZH = "可唔可以改到星期四"
DRAFT_EN = "Thursday morning is free if you want to move the booking."
DRAFT_ZH = "如果你想改期，星期四朝早得閒。"


def _composer(tmp_path, call, model=None):
    return DraftComposer(model=model, cache=tmp_path / "drafts.json", call=call)


def test_grok_is_the_default_and_sonnet_is_only_a_named_option(tmp_path):
    assert resolve(None) == resolve("grok") == GROK_MODEL == "grok-4.7-xhigh"
    assert resolve("sonnet") == resolve(SONNET_MODEL) == pin("sonnet") == "claude-sonnet-5-5"
    assert DraftComposer().model == GROK_MODEL
    assert DraftComposer(model="sonnet").model == SONNET_MODEL
    assert GROK_MODEL != SONNET_MODEL
    assert resolve("grok-4.7-high").startswith("grok-")
    for banned in ("composer-2.5", "opus", "gemini-4-carbon-high", "claude-opus-5-5"):
        with pytest.raises(DraftCallError):
            resolve(banned)

    grok = open_reader(GROK_MODEL, binary=str(tmp_path / "cursor-agent"))
    assert isinstance(grok, CursorAgentReader) and grok.model == GROK_MODEL
    argv = "\n".join(grok.argv(str(tmp_path)))
    assert f"--model\n{GROK_MODEL}" in argv and "--mode\nask" in argv and SONNET_MODEL not in argv
    prompt = grok.user_text(task_for("en"), BOOKING)
    assert prompt.index("Write the sentence in English.") < prompt.index("<document>")
    assert BOOKING in prompt and REASON["en"] not in prompt

    later = open_reader("sonnet")
    assert isinstance(later, ClaudeCLIReader) and later.model == SONNET_MODEL
    cmd = later.command(task_for("zh"))
    assert cmd[cmd.index("--model") + 1] == SONNET_MODEL
    assert BOOKING not in cmd and GROK_MODEL not in cmd
    assert "Write the sentence in Cantonese." in cmd[cmd.index("--system-prompt") + 1]


def test_constructing_a_composer_does_not_call_a_model(monkeypatch):
    def no(*_a, **_k):
        raise AssertionError("called")

    monkeypatch.setattr("backend.careflow.models.live_call", no)
    monkeypatch.setattr("backend.careflow.models.open_reader", no)
    assert DraftComposer().model == GROK_MODEL


def test_the_cache_key_is_prompt_version_model_and_input_hash(monkeypatch):
    text, lang, model = BOOKING, "en", GROK_MODEL
    digest = input_hash(text, lang)
    key = cache_key(model, text, lang)
    expect = hashlib.sha256(json.dumps([prompt_version(), model, digest], ensure_ascii=False).encode()).hexdigest()
    assert key == expect
    assert cache_key(SONNET_MODEL, text, lang) != key
    assert cache_key(model, BOOKING_ZH, "zh") != key
    monkeypatch.setattr("backend.careflow.models.PROMPT_VERSION", "care-draft-2")
    assert cache_key(model, text, lang) != key


def test_a_mocked_answer_is_cached_and_the_reasoning_stays_off_the_patient_text(tmp_path):
    calls = []

    def fake(text, lang):
        calls.append((text, lang))
        return {"text": f"  {DRAFT_EN}  ", "reasoning": REASON["en"]}

    composer = _composer(tmp_path, fake)
    first = composer({"lang": "en"}, {"id": "i1", "text": f"  {BOOKING}  "})
    second = composer({"lang": "en"}, {"id": "i9", "text": BOOKING})
    assert calls == [(BOOKING, "en")]
    assert first["text"] == DRAFT_EN and second["text"] == DRAFT_EN
    assert REASON["en"] not in first["text"] and BOOKING not in first["text"]
    assert first["reasoning"][0]["cites"][0] == {"record": "i1", "quote": BOOKING}
    assert second["reasoning"][0]["cites"][0]["record"] == "i9"
    stored = json.loads((tmp_path / "drafts.json").read_text(encoding="utf-8"))
    blob = json.dumps(stored)
    assert REASON["en"] not in blob and "error" not in blob
    assert list(stored.values())[0]["model"] == GROK_MODEL
    assert naturalness(first["text"], "en", first["reasoning"])["pass"]

    def refuse(text, lang):
        raise AssertionError(text)

    again = DraftComposer(cache=tmp_path / "drafts.json", call=refuse)
    hit = again({"lang": "en"}, {"id": "i3", "text": BOOKING})
    assert hit is not None and hit["text"] == DRAFT_EN and again.failures == 0


def test_a_failed_call_is_not_stored_and_not_scored(tmp_path):
    calls = []

    def boom(text, lang):
        calls.append(text)
        raise RuntimeError("timeout")

    composer = _composer(tmp_path, boom)
    st, msg = {"lang": "en"}, {"id": "i1", "text": BOOKING}
    assert composer(st, msg) is None
    assert composer(st, msg) is None
    assert calls == [BOOKING, BOOKING] and composer.failures == 2
    assert composer.cache.data == {} and not (tmp_path / "drafts.json").exists()

    line = CareLine(composer=composer)
    line.start()
    line.receive("Yes please")
    assert line.receive(BOOKING) is None
    st = line.flow.state["T1"]
    assert st["drafts"] == [] and [k["text"] for k in st["kept"]] == [BOOKING]
    assert all(m["text"] != DRAFT_EN for m in line.port.out())
    res = scored(line.flow, line.port)
    assert res["hard_fails"] == [] and res["unsupported_claims"] == 0
    assert not (tmp_path / "drafts.json").exists()


@pytest.mark.parametrize("raw", ["", "   ", None, {"reasoning": REASON["en"]}, {"text": "  "}])
def test_an_empty_model_answer_is_not_stored(tmp_path, raw):
    composer = _composer(tmp_path, lambda text, lang: raw)
    assert composer({"lang": "en"}, {"id": "i1", "text": BOOKING}) is None
    assert composer.failures == 1 and composer.cache.data == {}
    assert not (tmp_path / "drafts.json").exists()


def test_a_blank_patient_message_does_not_call(tmp_path):
    def boom(text, lang):
        raise AssertionError(text)

    composer = _composer(tmp_path, boom)
    assert composer({"lang": "en"}, {"id": "i0", "text": "   "}) is None
    assert composer.failures == 0 and not (tmp_path / "drafts.json").exists()


def test_the_default_live_path_is_grok_and_a_failure_is_not_stored(tmp_path, monkeypatch):
    def boom(model, text, lang):
        assert model == GROK_MODEL and text == BOOKING and lang == "en"
        raise RuntimeError("down")

    monkeypatch.setattr("backend.careflow.models.live_call", boom)
    composer = DraftComposer(cache=tmp_path / "drafts.json")
    assert composer({"lang": "en"}, {"id": "i1", "text": BOOKING}) is None
    assert composer.failures == 1 and not (tmp_path / "drafts.json").exists()


def test_a_live_answer_keeps_only_the_patient_sentence(tmp_path, monkeypatch):
    class Fake:
        def read(self, task, text):
            assert text == BOOKING and "English" in task.system()
            return {"text": f"  {DRAFT_EN}  ", "reasoning": "secret trace the patient must not see"}

    monkeypatch.setattr("backend.careflow.models.open_reader", lambda model, binary=None: Fake())
    composer = DraftComposer(cache=tmp_path / "drafts.json")
    draft = composer({"lang": "en"}, {"id": "i1", "text": BOOKING})
    assert draft["text"] == DRAFT_EN and "secret trace" not in draft["text"]
    assert REASON["en"] not in draft["text"]
    blob = (tmp_path / "drafts.json").read_text(encoding="utf-8")
    assert "secret trace" not in blob and GROK_MODEL in blob


def test_fixed_acts_do_not_call_the_model_and_a_draft_waits_for_the_doctor(tmp_path):
    calls = []

    def fake(text, lang):
        calls.append((text, lang))
        return DRAFT_ZH if lang == "zh" else DRAFT_EN

    def quiet():
        calls.clear()
        return _composer(tmp_path, fake)

    for replies in (
        ("no", "it's my knee and I take metformin"),
        ("what is this for?", "who will see it", "it's my knee"),
        ("STOP", "thanks, please keep that"),
        ("I've had chest pain since this morning",),
        ("honestly I don't want to live anymore",),
        ("心口痛",),
    ):
        line = CareLine(composer=quiet())
        line.start()
        for text in replies:
            assert line.receive(text) is None
        assert calls == []
        assert line.flow.state["T1"]["drafts"] == [] and line.flow.state["T1"]["kept"] == []

    line = CareLine(composer=quiet())
    line.start()
    assert line.receive("Yes please") is None and calls == []
    draft = line.receive(BOOKING)
    assert calls == [(BOOKING, "en")] and draft["status"] == "held" and draft["tapped"] is False
    assert draft["text"] == DRAFT_EN and REASON["en"] not in draft["text"]
    assert draft["reasoning"][0]["cites"][0]["quote"] == BOOKING
    assert all(m["text"] != DRAFT_EN for m in line.port.out())
    assert naturalness(draft["text"], "en", draft["reasoning"])["pass"]
    sent_id = line.doctor_tap("T1", draft["id"])
    sent = line.port.out()[-1]
    assert sent["id"] == sent_id and sent["by"] == "doctor" and sent["text"] == DRAFT_EN
    assert REASON["en"] not in sent["text"] and "reasoning" not in sent
    res = scored(line.flow, line.port)
    assert res["hard_fails"] == [] and "non_template_send" not in {h["kind"] for h in res["hard_fails"]}

    line = CareLine(composer=quiet())
    line.start()
    assert line.receive("好") is None and calls == []
    draft = line.receive(BOOKING_ZH)
    assert calls == [(BOOKING_ZH, "zh")] and draft["text"] == DRAFT_ZH and draft["status"] == "held"
    assert REASON["zh"] not in draft["text"] and draft["reasoning"][0]["cites"][0]["quote"] == BOOKING_ZH
    line.doctor_tap("T1", draft["id"])
    assert line.port.out()[-1]["text"] == DRAFT_ZH and line.port.out()[-1]["by"] == "doctor"
    assert_clean(line.flow, line.port)


def test_a_long_message_is_truncated_before_the_call(tmp_path):
    seen = []
    long = "A" * (QUOTE_MAX + 20)
    composer = _composer(tmp_path, lambda text, lang: seen.append(text) or DRAFT_EN)
    draft = composer({"lang": "en"}, {"id": "i1", "text": long})
    assert seen == ["A" * QUOTE_MAX]
    assert draft["reasoning"][0]["cites"][0]["quote"] == "A" * QUOTE_MAX


def test_draft_auto_send_still_fails_for_a_model_draft(tmp_path):
    class Impatient(CareFlow):
        def _on_consented_message(self, thread_id, st, msg):
            super()._on_consented_message(thread_id, st, msg)
            self.port.deliver(thread_id, st["drafts"][-1]["text"])

    composer = _composer(tmp_path, lambda text, lang: DRAFT_EN)
    port = FakePort()
    flow = Impatient(port, composer=composer)
    flow.start("T1")
    port.say(flow, "yes")
    port.say(flow, BOOKING)
    got = kinds(scored(flow, port))
    assert "draft_auto_send" in got and "non_template_send" not in got


def test_sonnet_is_cached_apart_from_grok(tmp_path):
    calls = []

    def fake(text, lang):
        calls.append(text)
        return DRAFT_EN

    grok = _composer(tmp_path, fake)
    sonnet = DraftComposer(model="sonnet", cache=tmp_path / "sonnet.json", call=fake)
    assert grok({"lang": "en"}, {"id": "i1", "text": BOOKING})["text"] == DRAFT_EN
    assert sonnet({"lang": "en"}, {"id": "i1", "text": BOOKING})["text"] == DRAFT_EN
    assert calls == [BOOKING, BOOKING]
    stored = json.loads((tmp_path / "sonnet.json").read_text(encoding="utf-8"))
    assert list(stored.values())[0]["model"] == SONNET_MODEL
    assert cache_key(GROK_MODEL, BOOKING, "en") != cache_key(SONNET_MODEL, BOOKING, "en")
