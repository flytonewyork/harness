"""The WhatsApp care line (spec 004): its eval set, its safety layer, its authority line and its webhook."""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.careline import evals as ev
from backend.careline import texts
from backend.careline.agent import CareAgent, Inbound
from backend.careline.api import create_app
from backend.careline.clinic import PlaybackClinic, SimClinic, parse_dob
from backend.careline.settings import Settings
from backend.careline.store import AuthorityError, CareStore
from backend.careline.whatsapp import RecordingTransport, to_graph

ROOT = Path(__file__).resolve().parents[1]
RUN = str(ROOT / "tests" / "fixtures" / "careline_run.json")
CASES = json.loads((ROOT / "evals" / "careline" / "cases.json").read_text(encoding="utf-8"))["cases"]


def agent(mode="sim", testers=None, clinic=None):
    s = Settings.load()
    return CareAgent(clinic or SimClinic(RUN, s), s, CareStore(s), mode=mode, testers=testers or set())


# ---- the eval set ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_passes(case):
    r = ev.run_case(case, RUN)
    assert r["hard_fails"] == [], r
    assert r["pass"], r["misses"]


def test_cases_name_real_features_and_cover_the_menu():
    ids = {r["id"] for r in csv.DictReader((ROOT / "evals" / "agent_features.csv").open(encoding="utf-8"))}
    assert {c["feature"] for c in CASES} <= ids
    menu = {c["turns"][0]["tap"] for c in CASES if c["turns"] and "tap" in c["turns"][0] and c["turns"][0]["tap"].startswith("menu:")}
    assert {f"menu:{k}" for k, _, _ in texts.MENU} - {"menu:hospital"} <= menu | {"menu:hospital"}
    for must in ("HE-03", "HE-04", "AC-01", "AC-02", "AV-01", "AV-02", "MD-01", "RT-08"):
        assert any(c["feature"] == must for c in CASES), must


def test_evals_catch_a_missing_fixed_reply(monkeypatch):
    """The suite must fail when the agent is wrong, not only pass when it is right."""
    monkeypatch.setitem(texts.FIXED["red_flag"], "en", "Please book an appointment. We have told {doctor}.")
    from backend.careline import agent as agent_mod
    monkeypatch.setattr(agent_mod, "fixed", lambda key, lang, **kw: "Please book an appointment.")
    r = ev.run_case(next(c for c in CASES if c["id"] == "he03-chest-pain"), RUN)
    assert "missed_red_flag" in r["hard_fails"]


def test_evals_catch_clinical_advice_in_a_template(monkeypatch):
    monkeypatch.setitem(texts.T["results_tail"], "en", "Your result is normal, nothing to worry about.")
    r = ev.run_case(next(c for c in CASES if c["id"] == "rt08-results-status"), RUN)
    assert "clinical_advice" in r["hard_fails"]
    assert not r["pass"]


# ---- texts ----------------------------------------------------------------------------------------------------------
def test_every_text_in_both_languages_and_menu_fits_whatsapp():
    for k, v in {**texts.T, **texts.FIXED}.items():
        assert set(v) == {"en", "zh"}, k
    assert len(texts.MENU) <= 10
    for _, zh, en in texts.MENU:
        assert len(zh) <= 24 and len(en) <= 24
    for k in ("btn_join", "btn_no", "btn_better", "btn_same", "btn_worse", "btn_invites_yes", "btn_invites_no"):
        assert all(len(x) <= 20 for x in texts.T[k].values()), k


def test_no_template_names_the_demo_patient():
    blob = json.dumps(texts.T, ensure_ascii=False)
    assert "Tai Man" not in blob and "陳大文" not in blob and "1964" not in blob


# ---- authority -------------------------------------------------------------------------------------------------------
def test_agent_has_no_closing_powers():
    src = (ROOT / "backend" / "careline" / "agent.py").read_text(encoding="utf-8")
    for word in ("store.close(", "store.approve(", "store.claim(", ".dismiss(", ".downgrade(", ".reassure("):
        assert word not in src, word
    for name in dir(CareAgent):
        assert not any(w in name for w in ("close", "dismiss", "approve", "downgrade", "reassure")), name


def test_only_people_close_and_only_doctors_close_clinical():
    a = agent()
    now = datetime(2026, 11, 10, 10, 0)
    h = a.store.open_handoff(now, "x", "doctor", "AV-03", "q", None)
    with pytest.raises(AuthorityError):
        a.store.close(now, h["id"], "agent", "done")
    with pytest.raises(AuthorityError):
        a.store.close(now, h["id"], "front_desk", "done")
    a.store.close(now, h["id"], "dr_chan", "replied")
    assert a.store.handoffs[h["id"]]["status"] == "closed"


def test_overdue_handoff_moves_to_next_person():
    a = agent()
    h = a.store.open_handoff(datetime(2026, 11, 10, 10, 0), "x", "staff", "FD-08", "q", None)
    a.store.tick(datetime(2026, 11, 10, 11, 1))
    assert h["owner"] == "dr_chan" and h["history"][0]["moved_from"] == "front_desk"


# ---- hours -------------------------------------------------------------------------------------------------------------
def test_reply_by_counts_only_open_minutes():
    s = Settings.load()
    assert s.reply_by(datetime(2026, 11, 8, 23, 10), "doctor") == datetime(2026, 11, 9, 13, 0)      # Sunday night -> Monday
    assert s.reply_by(datetime(2026, 11, 10, 12, 30), "staff") == datetime(2026, 11, 10, 15, 0)     # across lunch
    assert s.reply_by(datetime(2026, 11, 8, 3, 0), "named doctor") == datetime(2026, 11, 8, 3, 15)  # red flags: from now


def test_dates_people_write():
    assert parse_dob("23/09/1964") == parse_dob("1964-09-23") == parse_dob("23-9-1964") == parse_dob("1964年9月23日") == "1964-09-23"
    assert parse_dob("31/02/1990") is None


# ---- tester mode, webhook, play-back ---------------------------------------------------------------------------------------
def test_tester_mode_keeps_nothing_from_strangers():
    a = agent(mode="tester", testers={"85291110000"})
    outs = a.handle(Inbound("85299998888", "my HIV result please", at=datetime(2026, 11, 10, 10, 0)))
    assert "isn't a medical service" in outs[0].body
    assert "85299998888" not in a.store.conversations
    assert not any("HIV" in json.dumps(e) for e in a.store.events)
    outs = a.handle(Inbound("85299998888", "I have chest pain", at=datetime(2026, 11, 10, 10, 1)))
    assert "Call 999 now" in outs[0].body                     # red flags still get the fixed reply
    outs = a.handle(Inbound("85291110000", "hi", at=datetime(2026, 11, 10, 10, 2)))
    assert outs[0].feature == "AC-01"


def _signed(client, secret, frm, msg):
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": frm, "id": f"wamid.{time.time_ns()}",
                                                                         "timestamp": "1794960000", **msg}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sig})


def test_webhook_verify_signature_and_reply(tmp_path):
    a = agent()
    t = RecordingTransport()
    c = TestClient(create_app(a, t, run_path=RUN, mode="sim", verify_token="vt", app_secret="s3", data_dir=tmp_path, wa_number="85260000000"))
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"}).text == "42"
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403
    assert c.post("/webhook/whatsapp", content=b"{}", headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 401
    r = _signed(c, "s3", "85291234567", {"type": "text", "text": {"body": "JOIN"}})
    assert r.json()["replies"] == 1
    sent = t.sent[-1]
    assert sent["type"] == "interactive" and sent["interactive"]["type"] == "button"
    r = _signed(c, "s3", "85291234567", {"type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "consent:yes", "title": "Yes"}}})
    assert "full name" in t.sent[-1]["text"]["body"]
    assert (tmp_path / "webhook-tape.jsonl").exists()
    assert c.get("/careline/qr.svg").status_code in (200, 501)
    assert "wa.me/85260000000" in c.get("/join").text


def test_unsigned_webhooks_refused_outside_sim(tmp_path):
    c = TestClient(create_app(agent(mode="tester"), RecordingTransport(), run_path=RUN, mode="tester", data_dir=tmp_path))
    assert c.post("/webhook/whatsapp", content=b"{}").status_code == 503


def test_person_endpoints_need_a_person(tmp_path):
    a = agent()
    c = TestClient(create_app(a, RecordingTransport(), run_path=RUN, mode="sim", data_dir=tmp_path))
    h = a.store.open_handoff(datetime(2026, 11, 10, 10, 0), "x", "doctor", "AV-03", "q", None)
    assert c.post("/api/careline/person/close", json={"id": h["id"]}).status_code == 403
    assert c.post("/api/careline/person/close", json={"id": h["id"]}, headers={"X-Person": "agent"}).status_code == 403
    assert c.post("/api/careline/person/close", json={"id": h["id"]}, headers={"X-Person": "front_desk"}).status_code == 403
    assert c.post("/api/careline/person/close", json={"id": h["id"]}, headers={"X-Person": "dr_chan"}).status_code == 200


def test_playback_records_and_replays(tmp_path):
    s = Settings.load()
    tape = tmp_path / "emr.jsonl"
    rec = PlaybackClinic(SimClinic(RUN, s), tape, mode="record")
    a1 = CareAgent(rec, s, CareStore(s))
    script = [("hi", None), ("", "consent:yes"), ("CHAN Tai Man", None), ("23/09/1964", None), ("", "menu:results")]
    t = datetime(2026, 11, 10, 10, 0)
    first = [o.body for txt, rid in script for o in a1.handle(Inbound("1", txt, rid, at=t))]
    a2 = CareAgent(PlaybackClinic(None, tape, mode="replay"), s, CareStore(s))
    again = [o.body for txt, rid in script for o in a2.handle(Inbound("1", txt, rid, at=t))]
    assert first == again
    with pytest.raises(LookupError):
        PlaybackClinic(None, tape, mode="replay").medicines("PF99D4DB079")


def test_live_mode_needs_sign_off(monkeypatch):
    from backend.careline import __main__ as m
    monkeypatch.delenv("CARELINE_I_HAVE_SIGN_OFF", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "--run", RUN, "--mode", "live"])
    with pytest.raises(SystemExit) as e:
        m.main()
    assert "sign" in str(e.value)


def test_graph_payloads_fit_whatsapp_limits():
    a = agent()
    t = datetime(2026, 11, 10, 10, 0)
    for txt, rid in [("hi", None), ("", "consent:yes"), ("CHAN Tai Man", None), ("23/09/1964", None), ("", "menu:book")]:
        for o in a.handle(Inbound("1", txt, rid, at=t)):
            g = to_graph(o)
            if g["type"] == "interactive":
                act = g["interactive"]["action"]
                assert len(act.get("buttons", [])) <= 3
                rows = [r for s_ in act.get("sections", []) for r in s_["rows"]]
                assert len(rows) <= 10 and all(len(r["title"]) <= 24 for r in rows)
