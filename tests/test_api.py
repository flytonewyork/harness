"""The follow-up list service: views at a moment in time, and asymmetric authority. Synthetic data only."""
from fastapi.testclient import TestClient

from backend.api import Store, create_app

RUN = {
    "format": "harness-run/1",
    "source": {"kind": "test", "simulated": True, "clinic_name": "Test Clinic", "start": "2027-07-01T00:00", "end": "2027-08-31T23:59"},
    "staff": [{"id": "dr_a", "name": "Dr A", "role": "principal"}, {"id": "dr_b", "name": "Dr B", "role": "locum"}],
    "patients": {"P1": {"name_en": "CHAN Tai Man", "name_zh": "陳大文", "hkid": "A123XXX(X)", "sex": "M", "dob": "1964-09-23",
                        "mrn": "1", "cdcc": False}},
    "documents": {"R1": {"id": "R1", "kind": "referral_letter", "channel": "cms", "available_at": "2027-06-05T10:54",
                         "author": "Dr A", "patient_id": "P1", "text": "REFERRAL LETTER", "data": {}}},
    "items": [
        {"id": "Q1", "patient_id": "P1", "owner_id": "dr_a", "family": "referral", "reason": "No booking or reply for referral",
         "urgency_at_open": 2, "urgency": 3, "history": [["2027-07-24T08:30", "raised", 2, 3, "Unactioned for 7 days"]],
         "opened_at": "2027-07-17T13:30", "due": "2027-08-16T13:30", "resolved_at": "2027-08-02T13:00", "resolution": "linked_done",
         "escalates": None, "evidence": ["R1"], "reasoning": [], "checks": [{"id": "evidence", "status": "pass"}]},
        {"id": "Q2", "patient_id": "P1", "owner_id": "dr_b", "family": "referral", "reason": "Escalated (day 14): No booking",
         "urgency_at_open": 3, "urgency": 3, "history": [], "opened_at": "2027-07-31T08:30", "due": "2027-08-01T08:30",
         "resolved_at": "2027-07-31T13:00", "resolution": "acted", "escalates": "Q1", "evidence": ["R1"], "reasoning": [], "checks": []},
    ],
    "feeds": [{"channel": "portal:pathlab", "alarm_at": "2027-05-17T08:30", "detail": "portal unavailable", "recovered_at": "2027-05-19T13:30"}],
    "evaluation": {"headline": {"off_per_1000py": 180.0, "on_per_1000py": 14.0}},
}


def client():
    return TestClient(create_app(Store(RUN)))


def test_the_list_as_it_stood_at_a_moment():
    c = client()
    day3 = c.get("/api/items", params={"as_of": "2027-07-20T09:00"}).json()
    assert [(i["id"], i["urgency"]) for i in day3["items"]] == [("Q1", 2)]
    day8 = c.get("/api/items", params={"as_of": "2027-07-25T09:00"}).json()["items"][0]
    assert day8["urgency"] == 3 and day8["history"][0]["from"] == 2            # the day-7 raise
    day14 = c.get("/api/items", params={"as_of": "2027-07-31T09:00"}).json()["items"]
    assert {i["id"] for i in day14} == {"Q1", "Q2"} and next(i for i in day14 if i["id"] == "Q1")["escalated_to"] == ["Q2"]
    assert c.get("/api/items", params={"as_of": "2027-08-03T09:00"}).json()["count"] == 0


def test_feed_banner_follows_the_alarm():
    c = client()
    assert c.get("/api/feeds", params={"as_of": "2027-05-18T10:00"}).json()["down"][0]["channel"] == "portal:pathlab"
    after = c.get("/api/feeds", params={"as_of": "2027-05-20T10:00"}).json()
    assert after["down"] == [] and after["all_clear_allowed"]


def test_agent_opens_and_raises_but_never_lowers_or_closes():
    c = client()
    t = {"as_of": "2027-07-20T09:00"}
    body = {"patient_id": "P1", "owner_id": "dr_a", "family": "referral", "reason": "test", "evidence": ["R1"], "urgency": 2}
    assert c.post("/api/agent/items", json=body, params=t).status_code == 403                    # no role
    new = c.post("/api/agent/items", json=body, params=t, headers={"X-Role": "agent"}).json()["id"]
    assert c.post(f"/api/agent/items/{new}/raise", json={"urgency": 1}, params=t, headers={"X-Role": "agent"}).status_code == 403
    assert c.post(f"/api/agent/items/{new}/raise", json={"urgency": 3}, params=t, headers={"X-Role": "agent"}).status_code == 200
    assert c.post(f"/api/doctor/items/{new}/close", json={"resolution": "acted"}, params=t,
                  headers={"X-Role": "agent"}).status_code == 403                                  # the agent cannot close
    assert c.post(f"/api/agent/items/{new}/close", json={"resolution": "acted"}, params=t,
                  headers={"X-Role": "agent"}).status_code in (404, 405)                           # there is no such endpoint
    ok = c.post(f"/api/doctor/items/{new}/close", json={"resolution": "acted"}, params=t, headers={"X-Role": "doctor"})
    assert ok.status_code == 200 and ok.json()["status"] == "acted"
    bad = dict(body, evidence=["NOT-A-DOC"])
    assert c.post("/api/agent/items", json=bad, params=t, headers={"X-Role": "agent"}).status_code == 422
