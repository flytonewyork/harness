"""The line's server: webhooks and their signatures, the scheduler's secret, the doctor's sign-in and what a doctor
may see, the QR card, and the simulator end to end. Synthetic people, fakes behind."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.line.api import create_app
from backend.line.config import Config


def cfg(**over):
    c = Config(mode="sim", payments=True, base_url="http://testserver", admin_token="adm", cron_secret="cron", **over)
    return c


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(cfg(media_dir=str(tmp_path / "m"))))


def sim(c, wa, text="", **kw):
    r = c.post("/api/sim/send", json={"wa": wa, "text": text, **kw})
    assert r.status_code == 200
    return c.get("/api/sim/log", params={"wa": wa}).json()["sent"]


def test_sim_first_minutes_end_to_end(client):
    sim(client, "85261234567", "Hi Dr Hu [TH01]")
    sim(client, "85261234567", "Yes, join", reply_id="consent:yes", kind="interactive")
    sim(client, "85261234567", "WONG Ka Yan")
    sim(client, "85261234567", "31/12/1980")
    d = sim(client, "85290000001", audio_text="Start Metformin 500 mg twice a day with meals. Blood test in 3 months.")
    send = [b for s in d for b in s.get("buttons", []) if b["title"] == "Send"][0]
    sim(client, "85290000001", "Send", reply_id=send["id"], kind="interactive")
    p = client.get("/api/sim/log", params={"wa": "85261234567"}).json()["sent"]
    assert any("Metformin 500 mg" in (s.get("body") or "") and s.get("kind") == "approved" for s in p)
    assert any(s.get("mine") and "WONG Ka Yan" in s["body"] for s in p)


def test_webhook_needs_a_valid_signature_outside_sim(tmp_path):
    c = Config(mode="sim", base_url="http://testserver", media_dir=str(tmp_path))
    app = create_app(c)
    c.mode, c.wa_app_secret = "staging", "s3cret"             # as staging checks it
    tc = TestClient(app)
    body = json.dumps({"entry": []}).encode()
    assert tc.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert tc.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sig}).status_code == 200


def test_webhook_verify_and_cron_secret(tmp_path):
    c = cfg(media_dir=str(tmp_path), wa_verify_token="vt")
    app = create_app(c)
    tc = TestClient(app)
    assert tc.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"}).text == "42"
    assert tc.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403
    c.mode = "staging"
    assert tc.post("/cron/tick").status_code == 403
    assert tc.post("/cron/tick", headers={"X-Cron-Secret": "cron"}).status_code == 200


def test_stripe_webhook_marks_paid(tmp_path):
    c = cfg(media_dir=str(tmp_path), stripe_webhook_secret="whsec")
    app = create_app(c)
    line = app.state.line
    line.store.put("patient", "P1", {"id": "P1", "wa": "85261234567", "doctor_id": "D1", "lang": "en", "state": "joined",
                                     "consent": {"at": "x"}, "name": "TEST Person"})
    line.store.put("wa", "85261234567", {"wa": "85261234567", "current": "P1", "last_in": "2099-01-01T00:00"})
    line.store.put("payment", "PAY1", {"id": "PAY1", "patient_id": "P1", "doctor_id": "D1", "kind": "result", "doctor_fee": 300,
                                       "service_fee": 30, "total": 330, "status": "pending"})
    c.mode = "staging"
    tc = TestClient(app)
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {"payment_status": "paid", "metadata": {"payment_id": "PAY1"}}}}).encode()
    t = int(time.time())
    sig = hmac.new(b"whsec", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert tc.post("/webhook/stripe", content=body, headers={"Stripe-Signature": f"t={t},v1=bad"}).status_code == 400
    assert tc.post("/webhook/stripe", content=body, headers={"Stripe-Signature": f"t={t},v1={sig}"}).status_code == 200
    assert line.store.get("payment", "PAY1")["status"] == "paid"


def test_doctor_sign_in_and_only_own_patients(client):
    sim(client, "85261234567", "Hi Dr Hu [TH01]")
    sim(client, "85261234567", "Yes", reply_id="consent:yes", kind="interactive")
    sim(client, "85261234567", "CHAN Tai Man")
    sim(client, "85261234567", "23/09/1964")
    assert client.get("/api/doctor/state").status_code == 401
    url = client.get("/api/sim/login").json()["url"]
    assert "Sign in" in client.get(url).text and "Sign in" in client.get(url).text      # opening the link doesn't use it up
    assert client.get("/api/doctor/state").status_code == 401
    r = client.post(url, follow_redirects=False)
    assert r.status_code == 303 and "line_session" in r.cookies
    s = client.get("/api/doctor/state").json()
    assert s["stats"]["joined"] == 1 and s["money"]["shadow_subscription_hkd"] == 480
    pid = s["patients"][0]["id"]
    assert client.get(f"/api/doctor/patient/{pid}").json()["patient"]["name"] == "CHAN Tai Man"
    line = client.app.state.line
    line.store.put("patient", "POTHER", {"id": "POTHER", "doctor_id": "D2", "wa": "85260000000", "reports": [{"ref": "abc"}]})
    assert client.get("/api/doctor/patient/POTHER").status_code == 404
    assert client.get("/api/doctor/media/abc").status_code == 404
    assert client.post(url, follow_redirects=False).status_code == 401         # one use


def test_qr_card_and_privacy(client):
    r = client.get("/j/TH01")
    assert r.status_code == 200 and "Dr Thomas Hu's care line" in r.text and "wa.me" in r.text and "999" in r.text
    assert client.get("/j/TH01.png").headers["content-type"] == "image/png"
    assert client.get("/j/NOPE").status_code == 404
    assert "Personal Data (Privacy) Ordinance" in client.get("/privacy").text


def test_admin_adds_a_doctor(client):
    assert client.post("/api/admin/doctor", json={}).status_code == 403
    r = client.post("/api/admin/doctor", headers={"Authorization": "Bearer adm"},
                    json={"id": "D2", "code": "JS01", "name_en": "Dr Jose S", "wa": "85290000002"})
    assert r.status_code == 200 and r.json()["qr"].endswith("/j/JS01")


def test_live_mode_refuses_without_sign_off():
    with pytest.raises(SystemExit):
        Config(mode="live").check()
    with pytest.raises(SystemExit):
        Config(mode="live", signed_off=True, store="memory", model="fake", wa_token="x", wa_phone_number_id="x", wa_app_secret="x",
               wa_verify_token="x", wa_number="x", cron_secret="x", session_secret="y").check()


def test_subscription_cancelled_in_stripe_ends_the_membership(tmp_path):
    c = cfg(media_dir=str(tmp_path))
    app = create_app(c)
    line = app.state.line
    line.store.put("patient", "P1", {"id": "P1", "wa": "85261234567", "doctor_id": "D1", "lang": "en", "state": "joined", "consent": {"at": "x"},
                                     "name": "TEST Person", "member": {"active": True, "subscription": "sub_1"}})
    line.store.put("payment", "PAYM", {"id": "PAYM", "patient_id": "P1", "doctor_id": "D1", "kind": "membership", "status": "paid", "subscription": "sub_1"})
    tc = TestClient(app)
    r = tc.post("/webhook/stripe", content=json.dumps({"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1"}}}))
    assert r.status_code == 200 and not line.store.get("patient", "P1")["member"]["active"]


def test_webhook_times_are_hong_kong_time_and_ids_with_slashes_are_safe(client):
    from backend.line.store import FirestoreStore
    from datetime import datetime, timezone
    ts = int(datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc).timestamp())      # 12:00 UTC is 20:00 in Hong Kong
    payload = {"entry": [{"changes": [{"value": {"messages": [{"from": "85261234567", "id": "wamid.HBg/M+x==", "timestamp": str(ts),
                                                                "type": "text", "text": {"body": "Hi Dr Hu [TH01]"}}]}}]}]}
    assert client.post("/webhook/whatsapp", content=json.dumps(payload)).status_code == 200
    line = client.app.state.line
    assert line.store.get("wa", "85261234567")["last_in"] == "2026-11-24T20:00"
    assert FirestoreStore._key("wamid.HBg/M+x==") == "wamid.HBg_M+x==" and FirestoreStore._key("..") == "id_.." and FirestoreStore._key("__x__") == "id___x__"


def test_one_qr_card_per_clinic(client):
    line = client.app.state.line
    d = line.store.get("doctor", "D1")
    d["clinics"].append({"id": "c2", "name_en": "Peak Clinic (synthetic)", "name_zh": "山頂診所（模擬）", "address_en": "2 Test Road",
                         "address_zh": "測試路2號", "phone": "2000 0002"})
    line.store.put("doctor", "D1", d)
    card = client.get("/j/TH01", params={"c": 2}).text
    assert "%5BTH01.2%5D" in card and "Peak Clinic" in card and "TH01.png?c=2" in card
    assert "%5BTH01%5D" in client.get("/j/TH01", params={"c": 7}).text       # no such clinic: the first one
    sim(client, "85261234567", "Hi Dr Hu [TH01.2]")
    sim(client, "85261234567", "Yes", reply_id="consent:yes", kind="interactive")
    sim(client, "85261234567", "CHAN Tai Man")
    sim(client, "85261234567", "23/09/1964")
    assert line.store.one("patient", wa="85261234567")["clinic_id"] == "c2"
    waiting = (line.store.get("wa", "85290000001") or {}).get("outbox", [])     # the doctor hasn't written yet: it waits behind a template
    assert any("joined your line at Peak Clinic" in (m.get("body") or "") for m in line.tx.to("85290000001") + waiting)
