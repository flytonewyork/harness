"""The doctor's own line (spec 005): the first minutes for patient and doctor, paid follow-ups, the safety core,
the 24-hour window and the data boundary. Synthetic people only; fakes for WhatsApp, the model and Stripe."""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta

import pytest

from backend.line import drafting
from backend.line.agent import Line
from backend.line.config import Config
from backend.line.media import LocalMedia
from backend.line.models import FakeModel
from backend.line.payments import FakeStripe, verify_stripe
from backend.line.store import MemoryStore
from backend.line.transport import FakeTransport

DOC_WA, PAT_WA = "85290000001", "85261234567"
T0 = datetime(2026, 11, 24, 10, 15)          # a Tuesday morning, in Dr Hu's hours
DICTATION = ("Start Amlodipine 5 mg once a day in the morning. Come back or go to A&E if you get chest pain or a "
             "severe headache. Blood test for kidney function in 2 weeks. Check-in day 3.")


def doctor(**over):
    return {"id": "D1", "code": "TH01", "name_en": "Dr Thomas Hu", "name_zh": "胡醫生", "short_en": "Dr Hu", "short_zh": "胡醫生",
            "wa": DOC_WA, "fees": {"repeat": 200, "result": 300}, "stripe_account": "acct_FAKE0", "welcomed": "2026-11-20T09:00",
            "clinics": [{"id": "c1", "name_en": "Harbour Clinic (synthetic)", "name_zh": "海港診所（模擬）", "address_en": "1 Test Road",
                         "address_zh": "測試路1號", "phone": "2000 0000"}], **over}


@pytest.fixture
def line(tmp_path):
    cfg = Config(mode="sim", payments=True, base_url="https://line.test")
    store, tx = MemoryStore(), FakeTransport()
    ln = Line(cfg, store, tx, FakeModel(), FakeStripe(), LocalMedia(str(tmp_path / "media")))
    store.put("doctor", "D1", doctor())
    store.put("wa", DOC_WA, {"wa": DOC_WA, "last_in": (T0 - timedelta(hours=1)).isoformat(timespec="minutes"), "outbox": []})
    return ln


def doctor_got(ln, text):
    """Sent to the doctor, or waiting in their outbox behind a 'new items' template (their 24-hour window closed)."""
    w = ln.store.get("wa", DOC_WA) or {}
    return any(text in (s["body"] or "") for s in ln.tx.to(DOC_WA)) or any(text in o["body"] for o in w.get("outbox", []))


def msg(ln, wa, at, text="", **kw):
    m = {"wa": wa, "text": text, "at": at, "msg_id": f"in-{wa}-{at.isoformat()}-{text[:10]}-{kw.get('reply_id')}", "kind": kw.pop("kind", "text"), **kw}
    return ln.handle(m)


def last(ln, wa, n=1):
    return ln.tx.to(wa)[-n:]


def join(ln, at=T0, name="CHAN Tai Man", dob="23/09/1964"):
    msg(ln, PAT_WA, at, "Hi Dr Hu [TH01]")
    msg(ln, PAT_WA, at, "Yes, join", reply_id="consent:yes", kind="interactive")
    msg(ln, PAT_WA, at, name)
    msg(ln, PAT_WA, at, dob)
    return ln.store.one("patient", wa=PAT_WA)


def dictate(ln, at, words=DICTATION, context=None):
    ln.tx.media["m-voice"] = (words.encode(), "audio/ogg")
    return msg(ln, DOC_WA, at, "", kind="audio", media_id="m-voice", mime="audio/ogg", context_id=context)


def tap(ln, wa, at, rid):
    return msg(ln, wa, at, "", reply_id=rid, kind="interactive")


def joined_with_plan(ln):
    p = join(ln)
    dictate(ln, T0 + timedelta(minutes=1))
    plan = ln.store.one("plan", patient_id=p["id"])
    tap(ln, DOC_WA, T0 + timedelta(minutes=2), f"plan:send:{plan['id']}")
    return ln.store.get("patient", p["id"]), plan


# ---- the first minutes ----------------------------------------------------------------------------------------------
def test_patient_joins_and_doctor_is_told(line):
    msg(line, PAT_WA, T0, "Hi Dr Hu [TH01]")
    w = last(line, PAT_WA)[0]
    assert w["type"] == "buttons" and w["body"].startswith("Dr Thomas Hu's care line (automated, by tmrw)")
    assert "999" in w["body"] and "/privacy" in w["body"]
    p = join(line)
    assert p["name"] == "CHAN Tai Man" and p["dob"] == "1964-09-23" and p["consent"]["text_version"]
    assert "will send your plan here shortly" in last(line, PAT_WA)[0]["body"]
    note = last(line, DOC_WA)[0]
    assert "CHAN Tai Man (b. 1964) just joined" in note["body"]


def test_voice_note_becomes_an_approved_plan_within_the_visit(line):
    p = join(line)
    dictate(line, T0 + timedelta(minutes=1))                         # no swipe-reply needed: the patient who just joined
    draft, ask = last(line, DOC_WA, 2)
    assert "Draft plan for CHAN Tai Man" in draft["body"] and "Heard:" in draft["body"]
    assert "Amlodipine 5 mg" in draft["body"] and [b["title"] for b in ask["buttons"]] == ["Send", "Edit", "Skip"]
    assert not [s for s in line.tx.to(PAT_WA) if s["kind"] == "approved"], "nothing clinical before the doctor taps Send"
    plan = line.store.one("plan", patient_id=p["id"])
    tap(line, DOC_WA, T0 + timedelta(minutes=2), f"plan:send:{plan['id']}")
    sent = [s for s in line.tx.to(PAT_WA) if s["kind"] == "approved"]
    assert len(sent) == 1
    body = sent[0]["body"]
    assert body.startswith("Dr Thomas Hu's care line (approved by Dr Thomas Hu)")
    assert "Medicines\n• Start Amlodipine 5 mg" in body and "Next steps" in body and "胡醫生俾你嘅計劃" in body
    assert line.tx.to(PAT_WA)[-1]["type"] == "list"                    # then the menu
    items = line.store.find("item", patient_id=p["id"])
    assert {i["kind"] for i in items} == {"checkin", "reminder"}
    assert "Check-in on day 3, and 1 reminder(s)" in last(line, DOC_WA)[0]["body"]


def test_plan_items_must_quote_the_doctor(line):
    class Lying(FakeModel):
        def compose(self, **kw):
            d = super().compose(**kw)
            d["items"].append({"section": "take", "quote": "Take ibuprofen 400 mg three times a day", "en": "Ibuprofen 400 mg",
                               "zh": "", "med": "Ibuprofen 400 mg"})
            d["items"][0]["en"] = "Start Amlodipine 10 mg once a day"   # a dose the doctor didn't say
            return d
    line.model = Lying()
    join(line)
    dictate(line, T0 + timedelta(minutes=1))
    draft = last(line, DOC_WA, 2)[0]["body"]
    assert "Ibuprofen" not in draft.split("Left out")[0] and "10 mg" not in draft.split("Left out")[0]
    assert "Left out, because it wasn't in what you said" in draft
    plan = line.store.one("plan")
    assert {x["why"] for x in plan["dropped"]} == {"unsupported_claim", "numbers_not_said"}


def test_edit_sends_the_doctors_words_exactly(line):
    p = join(line)
    dictate(line, T0 + timedelta(minutes=1))
    plan = line.store.one("plan", patient_id=p["id"])
    tap(line, DOC_WA, T0 + timedelta(minutes=2), f"plan:edit:{plan['id']}")
    msg(line, DOC_WA, T0 + timedelta(minutes=3), "Amlodipine 5 mg every morning. Recheck BP in 2 weeks.")
    tap(line, DOC_WA, T0 + timedelta(minutes=4), f"plan:send:{plan['id']}")
    sent = [s for s in line.tx.to(PAT_WA) if s["kind"] == "approved"][-1]
    assert sent["body"].endswith("Amlodipine 5 mg every morning. Recheck BP in 2 weeks.")


# ---- questions ----------------------------------------------------------------------------------------------------------
def test_question_gets_reply_by_time_and_doctor_gets_a_draft(line):
    p, _ = joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Can I take the new tablet with food?")
    ack = last(line, PAT_WA)[0]["body"]
    assert "Dr Thomas Hu will read this and reply by today 15:15" in ack and "999" in ack
    q = last(line, DOC_WA)[0]
    assert "CHAN Tai Man asks" in q["body"] and "Draft reply" in q["body"]
    h = line.store.one("handoff", patient_id=p["id"], kind="question")
    tap(line, DOC_WA, T0 + timedelta(hours=2), f"q:send:{h['id']}")
    reply = [s for s in line.tx.to(PAT_WA) if s["kind"] == "approved"][-1]
    assert reply["body"].startswith("Dr Thomas Hu's care line (approved by Dr Thomas Hu)")
    assert line.store.get("handoff", h["id"])["status"] == "closed"


def test_doctor_typed_reply_goes_out_word_for_word_labelled_as_the_doctor(line):
    p, _ = joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "I feel a bit dizzy in the mornings")
    h = line.store.one("handoff", patient_id=p["id"], kind="question")
    tap(line, DOC_WA, T0 + timedelta(hours=2), f"q:write:{h['id']}")
    msg(line, DOC_WA, T0 + timedelta(hours=2, minutes=1), "Take it at night instead and check your BP for a week. Dr Hu")
    out = [s for s in line.tx.to(PAT_WA) if s["kind"] == "doctor"][-1]["body"]
    assert out == "Dr Thomas Hu (written by the doctor)\nTake it at night instead and check your BP for a week. Dr Hu"


def test_outside_hours_the_ack_says_when_and_nobody_is_on_call(line):
    joined_with_plan(line)
    msg(line, PAT_WA, datetime(2026, 11, 24, 23, 30), "Is it ok to stop the tablet?")
    ack = last(line, PAT_WA)[0]["body"]
    assert "isn't reading messages right now" in ack and "tomorrow 13:00" in ack and "999" in ack


def test_red_flag_gets_the_fixed_reply_and_the_doctor_is_alerted(line):
    joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "I have chest pain now")
    r = last(line, PAT_WA)[0]["body"]
    assert "Call 999 now" in r
    assert "may be an emergency" in last(line, DOC_WA)[0]["body"]


def test_crisis_reply_comes_first_even_before_joining(line):
    msg(line, PAT_WA, T0, "Hi Dr Hu [TH01]")
    msg(line, PAT_WA, T0, "I want to die")
    assert "Samaritans" in last(line, PAT_WA)[0]["body"]
    assert "may be an emergency" in last(line, DOC_WA)[0]["body"]


def test_stop_and_decline(line):
    msg(line, "85260000009", T0, "Hi Dr Hu [TH01]")
    msg(line, "85260000009", T0, "", reply_id="consent:no", kind="interactive")
    assert line.store.get("wa", "85260000009") is None and not line.store.find("patient", wa="85260000009")
    joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "STOP")
    assert line.store.one("patient", wa=PAT_WA)["state"] == "stopped"
    n = len(line.tx.to(PAT_WA))
    msg(line, PAT_WA, T0 + timedelta(hours=2), "hello?")
    assert len(line.tx.to(PAT_WA)) == n


def test_no_code_gets_the_scan_notice(line):
    msg(line, "85269999999", T0, "hello")
    assert "scan the QR code" in last(line, "85269999999")[0]["body"]


# ---- paid follow-ups ------------------------------------------------------------------------------------------------------
def test_paid_repeat_end_to_end(line):
    p, _ = joined_with_plan(line)
    tap(line, PAT_WA, T0 + timedelta(days=20), "menu:repeat")
    assert last(line, PAT_WA)[0]["rows"][0]["title"] == "Amlodipine 5 mg"
    tap(line, PAT_WA, T0 + timedelta(days=20), "med:0")
    assert "Nothing is charged unless Dr Thomas Hu approves" in last(line, PAT_WA)[0]["body"]
    h = line.store.one("handoff", kind="repeat")
    tap(line, DOC_WA, T0 + timedelta(days=20, minutes=5), f"rp:ok:{h['id']}")
    pay_msg = last(line, PAT_WA)[0]["body"]
    assert "Dr Thomas Hu's fee HK$200 + tmrw service fee HK$30 = HK$230" in pay_msg and "checkout.stripe.test" in pay_msg
    pay = line.store.one("payment")
    s = line.stripe.sessions[pay["session_id"]]
    assert (s["doctor_fee"], s["service_fee"], s["account"]) == (200, 30, "acct_FAKE0")
    line.paid(pay["id"], T0 + timedelta(days=20, minutes=7))
    line.paid(pay["id"], T0 + timedelta(days=20, minutes=8))            # Stripe retries: once only
    pick = last(line, PAT_WA)[0]
    assert pick["type"] == "list" and "Paid, thank you" in pick["body"]
    tap(line, PAT_WA, T0 + timedelta(days=20, minutes=9), pick["rows"][0]["id"])
    assert "Done: collect Amlodipine 5 mg at Harbour Clinic" in last(line, PAT_WA)[0]["body"]
    assert "will collect Amlodipine 5 mg" in last(line, DOC_WA)[0]["body"]
    assert line.store.get("handoff", h["id"])["status"] == "closed"


def test_repeat_needs_a_visit_charges_nothing(line):
    joined_with_plan(line)
    tap(line, PAT_WA, T0 + timedelta(hours=3), "med:0")
    h = line.store.one("handoff", kind="repeat")
    tap(line, DOC_WA, T0 + timedelta(hours=3), f"rp:visit:{h['id']}")
    assert "would like to see you" in last(line, PAT_WA)[0]["body"] and not line.store.all("payment")


def test_paid_result_review(line):
    p, _ = joined_with_plan(line)
    line.tx.media["m-rep"] = (b"%PDF synthetic report", "application/pdf")
    msg(line, PAT_WA, T0 + timedelta(days=14), "", kind="document", media_id="m-rep", mime="application/pdf")
    offer = last(line, PAT_WA)[0]
    assert "HK$300 + tmrw service fee HK$30 = HK$330" in offer["body"]
    tap(line, PAT_WA, T0 + timedelta(days=14), offer["buttons"][0]["id"])
    pay = line.store.one("payment", kind="result")
    line.paid(pay["id"], T0 + timedelta(days=14, minutes=2))
    assert "will review your report" in last(line, PAT_WA)[0]["body"]
    assert doctor_got(line, "paid for a result review") and doctor_got(line, "See it on your web page")


# ---- the 24-hour window ----------------------------------------------------------------------------------------------------
def test_checkin_after_24h_goes_as_an_approved_template(line):
    p, _ = joined_with_plan(line)
    line.tick(T0 + timedelta(days=3, hours=1))
    t = last(line, PAT_WA)[0]
    assert t["type"] == "template" and t["template"]["name"] == "line_checkin"
    assert t["template"]["params"] == ["Tai Man", "3", "Dr Thomas Hu"]
    tap(line, PAT_WA, T0 + timedelta(days=3, hours=2), "checkin:worse")
    assert "reply by" in last(line, PAT_WA)[0]["body"] and doctor_got(line, "check-in: Worse")
    assert last(line, DOC_WA)[0]["template"]["name"] == "line_doctor_new"        # the doctor's window had closed


def test_doctor_words_wait_in_the_outbox_until_the_patient_taps(line):
    p, _ = joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Do I need to fast for the blood test?")
    h = line.store.one("handoff", kind="question")
    later = T0 + timedelta(days=2)
    msg(line, DOC_WA, later, "queue")                                   # the doctor's window opens
    tap(line, DOC_WA, later, f"q:write:{h['id']}")
    msg(line, DOC_WA, later, "No fasting needed.")
    t = last(line, PAT_WA)[0]
    assert t["type"] == "template" and t["template"]["name"] == "line_waiting"
    tap(line, PAT_WA, later + timedelta(minutes=5), "read")
    assert last(line, PAT_WA)[0]["body"].endswith("No fasting needed.")


# ---- the guard and the data boundary -------------------------------------------------------------------------------------
def test_guard_blocks_unapproved_clinical_words(line):
    from backend.line.transport import Out
    p, plan = joined_with_plan(line)
    n = len(line.tx.to(PAT_WA))
    bad = Out(to=PAT_WA, body="Dr Thomas Hu's care line (automated, by tmrw)\nYour result is normal, nothing to worry about.", feature="x")
    line.deliver([bad], T0 + timedelta(hours=1))
    forged = Out(to=PAT_WA, body="Dr Thomas Hu's care line (approved by Dr Thomas Hu)\nDouble your dose.", feature="AV-01", kind="approved",
                 ctx={"patient_id": p["id"], "approved_hash": plan.get("approved_hash")})
    line.deliver([forged], T0 + timedelta(hours=1))
    assert len(line.tx.to(PAT_WA)) == n
    blocked = [e for e in line.store.all("event") if e["event"] == "blocked"]
    assert {e["fails"] for e in blocked} == {"clinical_advice", "unapproved_send"}


def test_events_never_carry_patient_text(line):
    joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "My name is CHAN and my chest feels fine")
    for e in line.store.all("event"):
        assert not ({"text", "body", "name", "dob", "wa", "quote", "transcript"} & set(e)), e
        assert "CHAN" not in str(e)
    with pytest.raises(ValueError):
        line.store.event(T0, "x", text="leak")


def test_staging_strangers_get_a_test_notice_and_nothing_is_kept(line):
    line.cfg.mode = "staging"
    msg(line, "85268888888", T0, "Hi Dr Hu [TH01]")
    assert "test" in last(line, "85268888888")[0]["body"]
    assert line.store.get("wa", "85268888888") is None


def test_doctor_commands_and_web_sign_in(line):
    joined_with_plan(line)
    msg(line, DOC_WA, T0 + timedelta(hours=1), "web")
    url = last(line, DOC_WA)[0]["body"].split("): ")[1]
    tok = url.rsplit("/", 1)[1]
    assert line.redeem(tok, T0 + timedelta(hours=1, minutes=5)) == "D1"
    assert line.redeem(tok, T0 + timedelta(hours=1, minutes=6)) is None       # one use
    msg(line, DOC_WA, T0 + timedelta(hours=1), "queue")
    assert "Nothing waiting" in last(line, DOC_WA)[0]["body"]


def test_duplicate_webhooks_are_handled_once(line):
    m = {"wa": PAT_WA, "text": "Hi Dr Hu [TH01]", "at": T0, "msg_id": "wamid.same", "kind": "text"}
    line.handle(dict(m))
    line.handle(dict(m))
    assert len(line.tx.to(PAT_WA)) == 1


def test_stripe_signature():
    body, secret, t = b'{"id":"evt"}', "whsec_test", int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_stripe(body, f"t={t},v1={sig}", secret)
    assert not verify_stripe(body, f"t={t},v1={'0' * 64}", secret)
    assert not verify_stripe(body, f"t={t - 1000},v1={sig}", secret)


def test_numbers_check_reads_chinese_numerals():
    assert drafting.numbers_ok("Blood test in 2 weeks", "兩個禮拜後驗血") == []
    assert drafting.numbers_ok("Take 10 mg", "食5 mg") == ["10"]


def test_vertex_adapter_builds_the_request_and_checks_quotes(monkeypatch):
    """The Vertex adapter with Google's client faked: audio goes in as bytes, JSON comes back, and the same quote checks
    apply. No network, no real model."""
    pytest.importorskip("google.genai")
    import json as _json
    from google import genai
    from backend.line.models import VertexGemini
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.update(model=model, parts=contents, mime=config.response_mime_type)
            return type("R", (), {"text": _json.dumps({"transcript": "開 Amlodipine 5 mg 每朝一粒。兩個禮拜後驗血。", "checkin_days": 3, "reminders": [],
                                                       "items": [{"section": "take", "quote": "開 Amlodipine 5 mg 每朝一粒", "en": "Amlodipine 5 mg, one every morning",
                                                                  "zh": "Amlodipine 5 mg，每朝一粒", "med": "Amlodipine 5 mg"},
                                                                 {"section": "next", "quote": "兩個禮拜後驗血", "en": "Blood test in 2 weeks", "zh": "兩個禮拜後驗血", "med": ""}]})})()

    class FakeClient:
        def __init__(self, **kw):
            seen["client"] = kw
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    v = VertexGemini("proj", "asia-east2", "gemini-2.5-flash")
    out = drafting.check(v.compose(kind="plan", audio=b"OggS...", mime="audio/ogg; codecs=opus"))
    assert seen["client"] == {"vertexai": True, "project": "proj", "location": "asia-east2"}
    assert seen["parts"][1].inline_data.mime_type == "audio/ogg" and seen["mime"] == "application/json"
    assert [i["med"] for i in out["items"]] == ["Amlodipine 5 mg", ""] and out["checkin_days"] == 3 and not out["dropped"]
    assert v.name == "vertex:gemini-2.5-flash@asia-east2"


def test_two_new_patients_means_the_line_asks_who_for(line):
    join(line)
    msg(line, "85261111111", T0, "Hi Dr Hu [TH01]")
    msg(line, "85261111111", T0, "", reply_id="consent:yes", kind="interactive")
    msg(line, "85261111111", T0, "LEE Siu Fong")
    msg(line, "85261111111", T0, "01/02/1950")
    dictate(line, T0 + timedelta(minutes=2))
    ask = last(line, DOC_WA)[0]
    assert "Which patient is this for?" in ask["body"] and {r["title"] for r in ask["rows"]} == {"CHAN Tai Man", "LEE Siu Fong"}
    assert not line.store.all("plan")
    lee = line.store.one("patient", name="LEE Siu Fong")
    tap(line, DOC_WA, T0 + timedelta(minutes=3), f"for:{lee['id']}")
    assert "Draft plan for LEE Siu Fong" in last(line, DOC_WA, 2)[0]["body"]


def test_commands_win_and_a_forgotten_reply_lapses(line):
    p, _ = joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Is the cough from the tablet?")
    h = line.store.one("handoff", kind="question")
    tap(line, DOC_WA, T0 + timedelta(hours=1), f"q:write:{h['id']}")
    n = len(line.tx.to(PAT_WA))
    msg(line, DOC_WA, T0 + timedelta(hours=1, minutes=1), "queue")                # a command, not a reply to the patient
    assert len(line.tx.to(PAT_WA)) == n
    msg(line, DOC_WA, T0 + timedelta(hours=2), "Bring all your tablets next time.")   # 59 minutes later: the reply mode lapsed
    assert len(line.tx.to(PAT_WA)) == n


def test_nothing_reaches_a_patient_who_stopped(line):
    p, _ = joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Can I drink alcohol with this?")
    h = line.store.one("handoff", kind="question")
    msg(line, PAT_WA, T0 + timedelta(hours=1, minutes=1), "STOP")
    n = len(line.tx.to(PAT_WA))
    tap(line, DOC_WA, T0 + timedelta(hours=2), f"q:send:{h['id']}")
    line.tick(T0 + timedelta(days=4))
    assert len(line.tx.to(PAT_WA)) == n


# ---- the patient membership (bet 9) ----------------------------------------------------------------------------------------
def test_membership_offer_join_and_what_it_includes(line):
    d = line.store.get("doctor", "D1")
    d["membership_hkd"] = 400
    line.store.put("doctor", "D1", d)
    p, _ = joined_with_plan(line)
    sent = last(line, DOC_WA)[0]
    assert [b["title"] for b in sent["buttons"]] == ["Offer membership"]
    tap(line, DOC_WA, T0 + timedelta(minutes=3), sent["buttons"][0]["id"])
    offer = last(line, PAT_WA)[0]
    assert "Dr Thomas Hu's fee HK$400 + tmrw service fee HK$40 = HK$440 a month" in offer["body"] and "Cancel any time" in offer["body"]
    assert not [e for e in line.store.all("event") if e["event"] == "blocked"], "the offer must pass the outbound checks"
    pay = line.store.one("payment", kind="membership")
    assert line.stripe.sessions[pay["session_id"]]["mode"] == "subscription"
    tap(line, DOC_WA, T0 + timedelta(minutes=4), sent["buttons"][0]["id"])                 # offered once only
    assert "was already offered membership" in last(line, DOC_WA)[0]["body"]
    line.paid(pay["id"], T0 + timedelta(minutes=10), subscription="sub_FAKE1")
    assert "Welcome to Dr Thomas Hu's care membership" in last(line, PAT_WA)[0]["body"]
    assert line.store.get("patient", p["id"])["member"]["active"]
    # a repeat is included: no payment link
    tap(line, PAT_WA, T0 + timedelta(days=20), "med:0")
    h = line.store.one("handoff", kind="repeat")
    tap(line, DOC_WA, T0 + timedelta(days=20, minutes=5), f"rp:ok:{h['id']}")
    assert "included in your membership" in line.tx.to(PAT_WA)[-2]["body"] and last(line, PAT_WA)[0]["type"] == "list"
    assert "Paid" not in last(line, PAT_WA)[0]["body"], "a member's repeat isn't paid for item by item"
    assert len(line.store.find("payment", kind="repeat")) == 0
    # a report goes straight to the doctor
    line.tx.media["m-r"] = (b"%PDF synthetic", "application/pdf")
    msg(line, PAT_WA, T0 + timedelta(days=21), "", kind="document", media_id="m-r", mime="application/pdf")
    assert "reply by" in last(line, PAT_WA)[0]["body"] and not line.store.find("payment", kind="result")
    assert doctor_got(line, "sent a report to review (included in their membership)")
    # cancel by message
    msg(line, PAT_WA, T0 + timedelta(days=30), "cancel membership")
    assert "membership is cancelled" in last(line, PAT_WA)[0]["body"] and line.stripe.cancelled == ["sub_FAKE1"]
    assert not line.store.get("patient", p["id"])["member"]["active"]


def test_membership_needs_a_price_and_can_be_declined(line):
    p, _ = joined_with_plan(line)
    assert not last(line, DOC_WA)[0]["buttons"]                       # no price set: no offer button
    assert "Set a monthly membership price" in line.offer_membership(line.store.get("doctor", "D1"), p["id"], T0)[0].body
    d = line.store.get("doctor", "D1")
    d["membership_hkd"] = 300
    line.store.put("doctor", "D1", d)
    line.deliver(line.offer_membership(d, p["id"], T0 + timedelta(minutes=5)), T0 + timedelta(minutes=5))
    tap(line, PAT_WA, T0 + timedelta(minutes=6), "mb:no")
    assert "Nothing changes" in last(line, PAT_WA)[0]["body"]
    assert line.membership_ended("sub_unknown", T0) == []


# ---- found in the final review (10 Nov) ---------------------------------------------------------------------------------------
def test_returning_patient_gets_a_new_plan_and_keeps_their_medicines(line):
    p, first = joined_with_plan(line)
    later = T0 + timedelta(days=30)
    msg(line, PAT_WA, later, "Hi Dr Hu [TH01]")                        # scans the card again at the next visit
    assert doctor_got(line, "CHAN Tai Man (b. 1964) is back")
    dictate(line, later + timedelta(minutes=1), "Add Metformin 500 mg twice a day. Review in 3 months.")
    plan = line.store.one("plan", patient_id=p["id"], status="draft")
    assert plan["job"] == "plan" and plan["id"] != first["id"]
    tap(line, DOC_WA, later + timedelta(minutes=2), f"plan:send:{plan['id']}")
    tap(line, PAT_WA, later + timedelta(minutes=3), "menu:repeat")
    assert [r["title"] for r in last(line, PAT_WA)[0]["rows"]] == ["Metformin 500 mg", "Amlodipine 5 mg"]
    assert line.store.get("patient", p["id"])["awaiting_plan"] is None


def test_a_later_message_to_a_patient_is_confirmed_first_and_is_not_a_new_plan(line):
    p, plan = joined_with_plan(line)
    sent_note = [s for s in line.tx.to(DOC_WA) if "Sent to CHAN Tai Man" in s["body"]][-1]
    n = len(line.tx.to(PAT_WA))
    msg(line, DOC_WA, T0 + timedelta(days=5), "Your blood test is booked for Friday at the clinic.", context_id=sent_note["id"])
    assert len(line.tx.to(PAT_WA)) == n, "typed text with no open question waits for Send"
    shown = last(line, DOC_WA, 2)[0]["body"]
    assert "Message to CHAN Tai Man, exactly as you wrote it" in shown and "Your blood test is booked for Friday" in shown
    draft = line.store.one("plan", patient_id=p["id"], status="draft")
    assert draft["job"] == "reply"
    tap(line, DOC_WA, T0 + timedelta(days=5, minutes=1), f"plan:send:{draft['id']}")
    line_out = [s for s in line.tx.sent if s["to"] == PAT_WA and "booked for Friday" in (s["body"] or "")]
    waiting = line.store.get("wa", PAT_WA).get("outbox", [])
    assert line_out or any("booked for Friday" in o["body"] for o in waiting)
    assert line.store.get("patient", p["id"])["plan_id"] == plan["id"], "the plan itself is unchanged"


def test_not_my_patient_closes_the_line_to_that_number(line):
    p = join(line)
    tap(line, DOC_WA, T0 + timedelta(minutes=1), f"conf:no:{p['id']}")
    assert "couldn't match this number" in last(line, PAT_WA)[0]["body"]
    n = len(line.tx.to(PAT_WA))
    msg(line, PAT_WA, T0 + timedelta(minutes=5), "hello? can I have my plan")
    assert len(line.tx.to(PAT_WA)) == n and not line.store.find("handoff", patient_id=p["id"])
    msg(line, PAT_WA, T0 + timedelta(minutes=6), "I have chest pain")       # safety still answers
    assert "Call 999 now" in last(line, PAT_WA)[0]["body"]
    dictate(line, T0 + timedelta(minutes=7))
    assert not line.store.all("plan")


def test_stop_half_way_through_joining_keeps_nothing(line):
    msg(line, PAT_WA, T0, "Hi Dr Hu [TH01]")
    msg(line, PAT_WA, T0, "", reply_id="consent:yes", kind="interactive")
    msg(line, PAT_WA, T0, "STOP")
    assert line.store.get("wa", PAT_WA) is None and not line.store.all("patient")
    assert "Nothing has been kept" in last(line, PAT_WA)[0]["body"]


def test_long_messages_are_split_to_whatsapps_limits(line):
    p = join(line)
    long = " ".join(f"Take Amlodipine 5 mg in the morning and note number {i}." for i in range(1, 140))
    dictate(line, T0 + timedelta(minutes=1), long)
    plan = line.store.one("plan", patient_id=p["id"])
    tap(line, DOC_WA, T0 + timedelta(minutes=2), f"plan:send:{plan['id']}")
    parts = [s for s in line.tx.to(PAT_WA) if s["kind"] == "approved"]
    assert len(parts) > 1 and all(len(s["body"]) <= 4096 for s in parts)
    assert all(s["body"].startswith("Dr Thomas Hu's care line (approved by Dr Thomas Hu)") for s in parts)
    assert all(len(s["body"]) <= 1024 for s in line.tx.sent if s["type"] in ("buttons", "list"))
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Is this right? " + "It is a very long question. " * 60)
    q = [s for s in line.tx.to(DOC_WA) if s["type"] == "buttons"][-1]
    assert len(q["body"]) <= 1024 and "I'll write" in [b["title"] for b in q["buttons"]]


def test_a_failed_send_does_not_stop_the_line(line):
    p, _ = joined_with_plan(line)
    real = line.tx.send

    def flaky(o):
        if o.to == PAT_WA:
            raise RuntimeError("WhatsApp send failed: 500")
        return real(o)
    line.tx.send = flaky
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Can I take it with food?")
    assert any(e["event"] == "send_failed" for e in line.store.all("event"))
    assert any("could not be sent" in s["body"] for s in line.tx.to(DOC_WA))
    assert any("CHAN Tai Man asks" in s["body"] for s in line.tx.to(DOC_WA)), "the doctor still gets the question"


def test_one_number_on_two_doctors_lines(line):
    line.store.put("doctor", "D2", doctor(id="D2", code="JS01", name_en="Dr Jose Santos", name_zh="山醫生", short_en="Dr Santos", wa="85290000002"))
    line.store.put("wa", "85290000002", {"wa": "85290000002", "last_in": T0.isoformat(timespec="minutes"), "outbox": []})
    pa, plan_a = joined_with_plan(line)
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Hi Dr Santos [JS01]")
    tap(line, PAT_WA, T0 + timedelta(hours=1), "consent:yes")
    msg(line, PAT_WA, T0 + timedelta(hours=1), "CHAN Tai Man")
    msg(line, PAT_WA, T0 + timedelta(hours=1), "23/09/1964")
    assert len(line.store.find("patient", wa=PAT_WA)) == 2
    line.tick(T0 + timedelta(days=3, hours=1))                          # Dr Hu's day-3 check-in, while Dr Santos's line is "current"
    assert not [e for e in line.store.all("event") if e["event"] == "blocked"]
    tap(line, PAT_WA, T0 + timedelta(days=3, hours=2), "checkin:worse")
    h = line.store.one("handoff", kind="question")
    assert h["doctor_id"] == "D1" and h["patient_id"] == pa["id"], "the answer goes to the doctor who asked"


def test_sensitive_reminders_never_name_the_test(line):
    p = join(line)
    dictate(line, T0 + timedelta(minutes=1), "Start Amlodipine 5 mg once a day. HIV test in 2 weeks.")
    plan = line.store.one("plan", patient_id=p["id"])
    tap(line, DOC_WA, T0 + timedelta(minutes=2), f"plan:send:{plan['id']}")
    msg(line, PAT_WA, T0 + timedelta(days=14), "hi")                    # opens their window
    n = len(line.tx.sent)
    line.tick(T0 + timedelta(days=14, hours=1))
    new = [s for s in line.tx.sent[n:] if s["to"] == PAT_WA]
    assert new and all("HIV" not in (s["body"] or "") + str(s.get("template")) for s in new)
    assert any("a next step in your plan" in (s["body"] or "") for s in new)


def test_editing_a_plan_drops_its_reminders_and_says_so(line):
    p = join(line)
    dictate(line, T0 + timedelta(minutes=1))
    plan = line.store.one("plan", patient_id=p["id"])
    assert "reminder on day 14" in last(line, DOC_WA, 2)[0]["body"]
    tap(line, DOC_WA, T0 + timedelta(minutes=2), f"plan:edit:{plan['id']}")
    msg(line, DOC_WA, T0 + timedelta(minutes=3), "Amlodipine 5 mg every morning.")
    assert "reminders were dropped" in last(line, DOC_WA, 2)[0]["body"]
    tap(line, DOC_WA, T0 + timedelta(minutes=4), f"plan:send:{plan['id']}")
    assert {i["kind"] for i in line.store.find("item", patient_id=p["id"])} == {"checkin"}


def test_a_draft_reply_with_a_number_the_plan_lacks_is_dropped(line):
    class Inventive(FakeModel):
        def draft_reply(self, **kw):
            return {"draft": "Yes, you can double it to 10 mg.", "uses": []}
    p, _ = joined_with_plan(line)
    line.model = Inventive()
    msg(line, PAT_WA, T0 + timedelta(hours=1), "Can I take more if my pressure is high?")
    q = [s for s in line.tx.to(DOC_WA) if "CHAN Tai Man asks" in s["body"]][-1]
    assert "Draft reply" not in q["body"] and "Send draft" not in [b["title"] for b in q["buttons"]]
    assert any(e["event"] == "draft_dropped" for e in line.store.all("event"))


def test_quotes_survive_punctuation_and_spoken_numbers():
    t = "OK, so start amlodipine 5mg — once a day, in the morning. 兩個禮拜後覆診，每次食半粒。"
    assert drafting.quoted("Start Amlodipine 5 mg once a day in the morning", t)
    assert not drafting.quoted("stop amlodipine", t)
    assert drafting.numbers_ok("Review in 2 weeks; 0.5 tablet each time", "兩個禮拜後覆診，每次食半粒") == []
    assert drafting.numbers_ok("25 mg for 20 days", "廿五毫克，食二十日") == []
    assert drafting.numbers_ok("2 times a day for 1 week at 9:00", "twice a day for a week at nine") == []
    assert drafting.numbers_ok("Take 10 mg", "take five milligrams") == ["10"]


def test_a_clinic_can_have_charging_switched_off(line):
    d = line.store.get("doctor", "D1")
    d["membership_hkd"] = 400
    d["clinics"][0]["charge"] = False
    line.store.put("doctor", "D1", d)
    p, _ = joined_with_plan(line)
    assert not last(line, DOC_WA)[0]["buttons"], "no membership offer at a clinic that doesn't charge"
    tap(line, PAT_WA, T0 + timedelta(hours=3), "med:0")
    assert "charged" not in last(line, PAT_WA)[0]["body"]
    h = line.store.one("handoff", kind="repeat")
    tap(line, DOC_WA, T0 + timedelta(hours=3), f"rp:ok:{h['id']}")
    assert last(line, PAT_WA)[0]["type"] == "list" and "Paid" not in last(line, PAT_WA)[0]["body"]
    line.tx.media["m-r"] = (b"%PDF synthetic", "application/pdf")
    msg(line, PAT_WA, T0 + timedelta(hours=4), "", kind="document", media_id="m-r", mime="application/pdf")
    assert "reply by" in last(line, PAT_WA)[0]["body"] and not line.store.all("payment")
